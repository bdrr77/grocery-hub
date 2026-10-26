import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

// Picnic's search uses React Server Components and dynamically discovered server actions.
// Delegate that protocol to the maintained client instead of guessing an endpoint.
function createClient(input, Client) {
  const client = new Client({countryCode:input.country,apiVersion:input.version,authKey:input.token,deviceId:input.device,agent:input.agent});
  if (typeof client.sendRequestWithHeaders === "function") {
    const original = client.sendRequestWithHeaders.bind(client);
    client.sendRequestWithHeaders = async (...args) => {
      // Search pages and their feature configuration must use the same app identity.
      // The pinned client omits these headers for its internal bootstrap request.
      if (args[1]==="/bootstrap") args[3]={...args[3],includePicnicHeaders:true};
      try {return await original(...args);}
      catch(error) {
        const path = String(args[1]);
        error.step = path.includes("/pages/action/") ? "search-action" : path.includes("search-page-results") ? "search-page" : path.endsWith("/bootstrap") ? "bootstrap" : path.endsWith(".script.bundle") ? "client-chunk" : path.includes("product-details-page-root") ? "product-page" : undefined;
        throw error;
      }
    };
  }
  return client;
}

function* objects(value) {
  if (!value || typeof value!=="object") return;
  if (!Array.isArray(value)) yield value;
  for (const child of Object.values(value)) yield* objects(child);
}

export function fusionProducts(page) {
  const body = page?.layout?.body ?? page?.body;
  if (!body || typeof body!=="object") throw Object.assign(new Error("Invalid Fusion page"),{step:"search-format"});
  const products = new Map();
  for (const node of objects(body)) {
    const item = node.type==="SELLING_UNIT_TILE" ? node.sellingUnit : null;
    if (!item?.id || !item.name || item.max_count===0 || item.decorators?.some(decorator=>decorator.type==="UNAVAILABLE")) continue;
    products.set(String(item.id), {id:String(item.id),name:item.name,eans:[],price_cents:Number.isInteger(item.display_price) ? item.display_price : null,size:item.unit_quantity || "",...(typeof item.brand==="string" && item.brand ? {brand:item.brand}:{})});
  }
  return [...products.values()].slice(0,30);
}

export async function searchProducts(input, Client) {
  const client = createClient(input,Client);
  let results;
  try {results = await client.catalog.getSearchResults(input.query);}
  catch(error) {
    // Accounts/countries can still receive JSON/PML rather than RSC for this page.
    if (error.receivedFormat==="fusion" && Client.isUnexpectedPageFormatError?.(error)) {
      const page = await client.app.getPage(`search-page-results?search_term=${encodeURIComponent(input.query)}`);
      if (page.format==="fusion") return fusionProducts(page.page);
    }
    if (!error.step) error.step = error.message==="The search page has no search parameters" ? "search-parameters" : error.message==="The search page has no fetchSearchResults action" ? "search-action-discovery" : error.message==="The bootstrap data has no client_component_cdn_base_url" ? "bootstrap" : "search-format";
    throw error;
  }
  if (!results || !Object.hasOwn(results, "products")) throw new Error("Unexpected search format");
  if (results.products === null) return [];
  if (!Array.isArray(results.products.sections)) throw new Error("Unexpected search sections");
  const products = new Map();
  for (const section of results.products.sections) {
    if (!Array.isArray(section.products)) throw new Error("Unexpected search products");
    for (const item of section.products) {
      if (!item.id || !item.name || item.isUnavailable) continue;
      products.set(String(item.id), {
        id: String(item.id), name: item.name, eans: [],
        price_cents: Number.isInteger(item.displayPrice) && item.displayPrice>0 ? item.displayPrice : null,
        size: item.unitQuantity || "",
        ...(typeof item.brand==="string" && item.brand ? {brand:item.brand}:{}),
      });
    }
  }
  return [...products.values()].slice(0, 30);
}

export function snapshotFromCart(raw, cart) {
  if (raw?.type !== "ORDER" || !Array.isArray(raw.items)) throw new Error("Invalid cart response");
  const integer = value => Number.isInteger(value) && value >= 0 ? value : null;
  const lines = cart.items.map(item => {
    const source = raw.items.find(line => line.id === item.id);
    const article = source?.items?.[0];
    const decorators = [...(article?.decorators || []), ...(raw.decorator_overrides?.[article?.id] || []), ...(source?.decorators || []), ...(raw.decorator_overrides?.[source?.id] || [])];
    const price = decorators.filter(d => d.type === "PRICE").at(-1)?.display_price ?? source?.display_price;
    const explicitQuantity = decorators.filter(d => d.type === "QUANTITY").at(-1)?.quantity;
    const quantity = explicitQuantity ?? (source?.items?.length > 1 ? source.items.length : item.quantity);
    if (!item.productId || !Number.isInteger(quantity) || quantity < 0) throw new Error("Invalid cart line");
    return {id:item.productId, name:item.name, quantity, size:item.unitQuantity, line_total_cents:integer(price)};
  });
  return {
    lines, total_cents:integer(raw.checkout_total_price), currency:"EUR",
    deposit_cents:Array.isArray(raw.deposit_breakdown) ? cart.depositTotal : null,
    fees_cents:Array.isArray(raw.fees) ? cart.fees.reduce((sum,fee)=>sum+fee.amount,0) : null,
    discount_cents:integer(raw.total_savings),
  };
}

export async function execute(input, Client) {
  if (input.action === "search") return {data:await searchProducts(input,Client)};
  const client = createClient(input,Client);
  let otpRequired = !!input.otp_required;
  // Capture the raw cart alongside the package normalizer without exposing private fields.
  async function readCart() {
    let raw;
    const original = client.sendRequest.bind(client);
    client.sendRequest = async (...args) => {
      const result = await original(...args);
      if (args[1] === "/cart") raw = result;
      return result;
    };
    try {
      const cart = await client.cart.getCart();
      return snapshotFromCart(raw,cart);
    } finally { client.sendRequest = original; }
  }
  let data = null;
  switch (input.action) {
    case "login": {
      const login = await client.auth.login(input.username,input.password);
      otpRequired = !!login.secondFactorAuthenticationRequired;
      break;
    }
    case "health": {
      const user = await client.sendRequest("GET","/user");
      if (!user || typeof user!=="object" || !Object.keys(user).length || user.error) throw new Error("Invalid authenticated user response");
      break;
    }
    case "send-code": await client.auth.generate2FACode("SMS"); break;
    case "verify-code": await client.auth.verify2FACode(input.code); otpRequired = false; break;
    case "cart": data = await readCart(); break;
    case "product": {
      try {
        const product = await client.catalog.getProduct(input.product_id);
        data = product.maxCount===0 ? null : {id:product.id,name:product.name,eans:[],price_cents:Number.isInteger(product.displayPrice) ? product.displayPrice : null,size:product.unitQuantity || ""};
        if (data && (!data.id || !data.name)) throw Object.assign(new Error("Invalid product"),{step:"product-page"});
      } catch(error) {
        if (error.status===404 || Client.isProductNotFoundError?.(error)) data = null;
        else throw error;
      }
      break;
    }
    case "set-quantity": {
      const before = await readCart();
      const current = before.lines.filter(line=>line.id===input.product_id).reduce((sum,line)=>sum+line.quantity,0);
      const delta = input.quantity-current;
      if (delta>0) await client.cart.addProductToCart(input.product_id,delta);
      if (delta<0) await client.cart.removeProductFromCart(input.product_id,-delta);
      data = await readCart();
      const actual = data.lines.filter(line=>line.id===input.product_id).reduce((sum,line)=>sum+line.quantity,0);
      if (actual !== input.quantity) throw Object.assign(new Error("Quantity mismatch"),{code:"QUANTITY_MISMATCH"});
      break;
    }
    default: throw new Error("Unknown action");
  }
  return {data,session:{token:client.authKey,otp_required:otpRequired}};
}

export function failureDetails(error) {
  const stages = new Set(["search-page","search-action","bootstrap","client-chunk","search-format","product-page","search-parameters","search-action-discovery"]);
  const reasons = new Map([
    ["The bootstrap data has no client_component_cdn_base_url","missing-client-config"],
    ["The search page has no search parameters","missing-search-parameters"],
    ["The search page has no fetchSearchResults action","missing-search-action"],
    ["Unexpected fetchSearchResults response","unexpected-action-response"],
    ["Invalid Fusion page","unexpected-fusion-layout"],
    ["Unexpected search format","unexpected-search-result"],
    ["Unexpected search sections","unexpected-search-sections"],
    ["Unexpected search products","unexpected-search-products"],
  ]);
  const reason = reasons.get(error?.message) || (/^RSC reference cycle at /.test(error?.message || "") ? "rsc-reference-cycle" : error instanceof SyntaxError ? "decode-error" : error instanceof TypeError ? "client-type-error" : "unspecified");
  return {error:"request_failed",reason,http_status:Number.isInteger(error?.status) ? error.status : null,step:stages.has(error?.step) ? error.step : null,received_format:["fusion","rsc"].includes(error?.receivedFormat) ? error.receivedFormat : null,code:error?.code==="QUANTITY_MISMATCH" ? error.code : null};
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let text = "";
    for await (const chunk of process.stdin) text += chunk;
    const require = createRequire(process.env.PICNIC_SEARCH_PACKAGE || "/opt/picnic-search/package.json");
    const library = require("picnic-api");
    process.stdout.write(JSON.stringify(await execute(JSON.parse(text), library.default || library)));
  } catch (error) {
    // Upstream errors may contain tokens/PII. Never forward their text to logs or UI.
    process.stdout.write(JSON.stringify(failureDetails(error)));
    process.exitCode = 1;
  }
}
