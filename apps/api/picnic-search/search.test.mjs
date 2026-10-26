import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { execute, failureDetails, searchProducts } from "../app/connectors/picnic-search.mjs";

const require = createRequire(import.meta.url);
const library = require("picnic-api");
const Client = library.default || library;
const input = {country:"FR",version:"15",token:"test-private-token",device:"test-device",agent:"30100;1.251.0-15611;",query:"Nutella"};

test("search diagnostics distinguish missing configuration and decoder failures without logging raw error text",()=>{
  assert.equal(failureDetails(new Error("The bootstrap data has no client_component_cdn_base_url")).reason,"missing-client-config");
  assert.equal(failureDetails(new Error("Unexpected fetchSearchResults response")).reason,"unexpected-action-response");
  assert.equal(failureDetails(new Error("RSC reference cycle at private-session-value")).reason,"rsc-reference-cycle");
  assert.equal(failureDetails(new SyntaxError("private response text")).reason,"decode-error");
  assert.ok(!JSON.stringify(failureDetails(new Error("private account details"))).includes("private"));
});

test("real client follows RSC search, discovers action, preserves version and keeps CDN requests unauthenticated", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push(String(url));
    const path = new URL(url).pathname;
    if (String(url).startsWith("https://cdn.example/")) {
      assert.equal(new Headers(options.headers).get("x-picnic-auth"), null);
      return new Response("const id='act_fetchSearchResults_abcd1234';");
    }
    assert.equal(new Headers(options.headers).get("x-picnic-auth"), input.token);
    if (path.endsWith("/pages/search-page-results")) {
      const lines = [
        '1:I'+JSON.stringify(["./search-page-params/search-page-params-provider.tsx",[],"default"]),
        '2:I'+JSON.stringify(["./search-page-content/results/index.tsx",["search-chunk.123"],"default"]),
        '0:'+JSON.stringify(["$","$L1",null,{params:{dayOffset:0,requestedEntityType:"SUPERMARKET",searchId:"id",searchSessionId:"session"},children:["$","$L2",null,{}]}]),
      ];
      return new Response(lines.join("\n"),{headers:{"Content-Type":"text/x-component","X-Page-Version":"page-version"}});
    }
    if (path.endsWith("/bootstrap")) {
      assert.equal(new Headers(options.headers).get("x-picnic-agent"),input.agent);
      assert.equal(new Headers(options.headers).get("x-picnic-did"),input.device);
      return Response.json({in_app_feature_config:{client_component_cdn_base_url:"https://cdn.example"}});
    }
    assert.ok(path.endsWith("/pages/action/act_fetchSearchResults_abcd1234"));
    assert.equal(options.method,"POST");
    assert.equal(new Headers(options.headers).get("x-page-version"),"page-version");
    assert.equal(JSON.parse(JSON.parse(options.body).params)[0].searchQuery,"Nutella");
    const tile = {type:"sellable",id:"s123",title:{title:"Nutella"},price:{priceValue:3.99},suffixes:[{type:"TEXT",text:"400 g"}]};
    const root = ["$","div",null,{searchQuery:"Nutella",storeResults:{SUPERMARKET:{sections:[{items:[tile,tile,{...tile,id:"s999",unavailability:{reason:"stock"}}]}]}}}];
    return new Response('0:'+JSON.stringify(root),{headers:{"Content-Type":"text/x-component"}});
  };
  try {
    assert.deepEqual(await searchProducts(input,Client),[{id:"s123",name:"Nutella",eans:[],price_cents:399,size:"400 g"}]);
    assert.equal(requests.length,4);
    assert.ok(requests.every(url=>!url.includes("/search?")));
  } finally { globalThis.fetch = original; }
});

test("empty product results are valid but malformed responses fail", async () => {
  class Empty { catalog = {getSearchResults:async()=>({products:null})}; }
  class Malformed { catalog = {getSearchResults:async()=>({unknown:[]})}; }
  assert.deepEqual(await searchProducts(input,Empty),[]);
  await assert.rejects(searchProducts(input,Malformed));
});

test("French accounts receiving a Fusion search page can still search", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({body:{child:{type:"SELLING_UNIT_TILE",sellingUnit:{id:"s123",name:"Nutella",display_price:375,unit_quantity:"400 g",max_count:10}}}});
  try {assert.deepEqual(await searchProducts(input,Client),[{id:"s123",name:"Nutella",eans:[],price_cents:375,size:"400 g"}]);}
  finally {globalThis.fetch = original;}
});

test("upstream search failure identifies its step without returning response bodies", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response("private-token private-password",{status:400});
  try {
    await assert.rejects(searchProducts(input,Client),error=>{
      const details = failureDetails(error);
      assert.equal(details.step,"search-page");
      assert.equal(details.http_status,400);
      assert.ok(!JSON.stringify(details).includes("private"));
      return true;
    });
  } finally {globalThis.fetch = original;}
});

test("an EAN-resolved product is fetched through the package catalog", async () => {
  class ProductClient {catalog={getProduct:async id=>({id,name:"Nutella",displayPrice:375,unitQuantity:"400 g",maxCount:10})};}
  const result = await execute({...input,action:"product",product_id:"s1035674"},ProductClient);
  assert.equal(result.data.id,"s1035674");
  assert.equal(result.data.size,"400 g");
});

test("package login and SMS verification return private session updates", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/user/login")) {
      assert.equal(JSON.parse(options.body).key,"test@example.invalid");
      return Response.json({second_factor_authentication_required:true},{headers:{"x-picnic-auth":"provisional"}});
    }
    assert.equal(new Headers(options.headers).get("x-picnic-auth"),"provisional");
    if (url.endsWith("/2fa/verify")) {
      assert.equal(JSON.parse(options.body).otp,"123456");
      return new Response(null,{status:204,headers:{"x-picnic-auth":"verified"}});
    }
    assert.ok(url.endsWith("/2fa/generate"));
    return new Response(null,{status:204});
  };
  try {
    const login = await execute({...input,action:"login",username:"test@example.invalid",password:"test-password"},Client);
    assert.deepEqual(login.session,{token:"provisional",otp_required:true});
    await execute({...input,...login.session,action:"send-code"},Client);
    const verified = await execute({...input,...login.session,action:"verify-code",code:"123456"},Client);
    assert.deepEqual(verified.session,{token:"verified",otp_required:false});
  } finally {globalThis.fetch = original;}
});

test("absolute quantity retries read package-normalized cart and do not double-add after lost response", async () => {
  const original = globalThis.fetch;
  let quantity = 0;
  let mutations = 0;
  globalThis.fetch = async (url,options) => {
    if (options.method === "GET") {
      return Response.json({type:"ORDER",id:"cart",items:quantity?[{id:"line",display_price:quantity*399,items:[{id:"s123",name:"Nutella",decorators:[{type:"QUANTITY",quantity}]}]}]:[],checkout_total_price:quantity*399});
    }
    const data = JSON.parse(options.body);
    assert.equal(data.product_id,"s123");
    quantity += url.endsWith("add_product") ? data.count : -data.count;
    mutations++;
    if (mutations===1) throw new TypeError("response lost after mutation");
    return Response.json({type:"ORDER",id:"cart",items:[],checkout_total_price:0});
  };
  try {
    const command = {...input,action:"set-quantity",product_id:"s123",quantity:2};
    await assert.rejects(execute(command,Client));
    const retried = await execute(command,Client);
    assert.equal(mutations,1);
    assert.equal(retried.data.lines[0].quantity,2);
    assert.equal(retried.data.total_cents,798);
    await execute({...command,quantity:0},Client);
    assert.equal(quantity,0);
    assert.equal(mutations,2);
  } finally {globalThis.fetch = original;}
});

test("an absent upstream checkout amount is preserved as unknown", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json({type:"ORDER",id:"cart",items:[]});
  try {assert.equal((await execute({...input,action:"cart"},Client)).data.total_cents,null);}
  finally {globalThis.fetch = original;}
});
