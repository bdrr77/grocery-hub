// Offline evaluation of the pinned picnic-api distribution already installed for search.
const test = require("node:test");
const assert = require("node:assert/strict");
const library = require("../apps/api/picnic-search/node_modules/picnic-api");
const Client = library.default || library;

test("cart normalization applies quantity overrides and exposes checkout total, deposits and fees", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({
    id:"cart", total_count:3, total_price:1197, checkout_total_price:1497,
    items:[{id:"line",price:1197,display_price:1197,items:[{id:"s123",name:"Nutella",decorators:[{type:"QUANTITY",quantity:1}]}]}],
    decorator_overrides:{line:[{type:"QUANTITY",quantity:3}]},
    deposit_breakdown:[{type:"BAG",value:100,count:1}],fees:[{name:"Service",amount:200}],
  });
  try {
    const client = new Client({countryCode:"FR",authKey:"test-token"});
    const cart = await client.cart.getCart();
    assert.equal(cart.totalPrice,1497);
    assert.equal(cart.items[0].quantity,3);
    assert.equal(cart.depositTotal,100);
    assert.equal(cart.fees[0].amount,200);
  } finally { globalThis.fetch = originalFetch; }
});

test("missing upstream total is normalized to zero and needs an application completeness guard", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({id:"cart",items:[]});
  try {
    const cart = await new Client({countryCode:"FR",authKey:"test-token"}).cart.getCart();
    assert.equal(cart.totalPrice,0);
  } finally { globalThis.fetch = originalFetch; }
});
