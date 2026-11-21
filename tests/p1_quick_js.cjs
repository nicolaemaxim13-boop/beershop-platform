// BeerShop P1: quick, dependency-free JS regression checks.
// Reads production-source JS but never contacts the network or a database.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '..');

async function testAnonymousQueue(enabled) {
  const source = fs.readFileSync(path.join(ROOT, 'web', 'ux-p1.js'), 'utf8');
  const posted = [];
  let resolveCapability;
  const delayed = new Promise(resolve => { resolveCapability = resolve; });
  const window = {};
  const context = {
    window,
    document: {getElementById: () => null, addEventListener: () => {}},
    location: {pathname: '/'},
    fetch: (url, options) => {
      if (url === '/api/p1/ux-capabilities') return delayed;
      assert.equal(url, '/api/p1/ux-event');
      const payload = JSON.parse(options.body);
      assert.deepEqual(Object.keys(payload), ['event']);
      posted.push(payload.event);
      return Promise.resolve({ok:true});
    },
    MutationObserver: function () {},
  };
  vm.runInNewContext(source, context, {filename: 'ux-p1.js'});
  window.BeerShopP1.event('checkout_open');
  window.BeerShopP1.event('checkout_attempt');
  window.BeerShopP1.event('not_an_allowed_event');
  assert.deepEqual(posted, [], 'No network events before explicit capability');
  resolveCapability({ok:true,json:async()=>({analytics_enabled:enabled})});
  for (let i=0;i<6;i++) await Promise.resolve();
  if (enabled) {
    assert.deepEqual(posted, ['checkout_open','checkout_attempt','catalog_view']);
  } else {
    assert.deepEqual(posted, [], 'Disabled analytics must discard queued events');
  }
  window.BeerShopP1.event('checkout_success');
  if (enabled) assert.deepEqual(posted.at(-1), 'checkout_success');
  else assert.deepEqual(posted, []);
}

async function testCheckoutGuard() {
  const code = fs.readFileSync(path.join(ROOT, 'web', 'store-fx.js'), 'utf8');
  const events=[],statuses=[],messages=[],requests=[];
  const button={disabled:false};
  const confirmation={
    textContent:'',classList:{remove:()=>{}},scrollIntoView:()=>{}
  };
  const document={getElementById:id=>
    id==='orderbtn'?button:id==='checkout-confirmation'?confirmation:null};
  const context={
    document,
    window:{BeerShopP1:{event:name=>events.push(name),status:name=>statuses.push(name)}},
    cart:{42:{qty:1,price:135}},
    pendingKey:null,
    renderCart:()=>{}, toggleCart:()=>{}, reload:()=>{},
    msg:s=>messages.push(s), fmt:n=>String(n),
    crypto:{randomUUID:()=> 'synthetic-key-12345678'},
    FormData:class {constructor(){
      return [['name','Test Buyer'],['phone','+380501234567'],
              ['email','not-real@example.test'],['delivery_address','Local test']];
    }},
    fetch:async(url)=>{
      requests.push(url);
      if (url==='/api/quote')return {ok:true,json:async()=>({
        total:'136.00',items:[{variant_id:42,unit_price_uah:'136.00'}]})};
      if (url==='/api/orders')return {ok:true,json:async()=>({id:88,total:'136.00'})};
      throw Error('Unexpected network request');
    },
    setInterval:()=>{},
  };
  vm.runInNewContext(code,context,{filename:'store-fx.js'});
  const e={preventDefault:()=>{},target:{reset:()=>{}}};
  await context.placeOrder(e);
  assert(!requests.includes('/api/orders'),
    'Changed quote MUST require second explicit checkout confirmation');
  assert(events.includes('checkout_price_changed'));
  await context.placeOrder(e);
  assert.equal(requests.filter(x=>x==='/api/orders').length,1);
  assert(events.includes('checkout_success'));
  assert.match(confirmation.textContent,/Оплата ще не підтверджена/);
  assert.equal(button.disabled,false);
}
async function testRapidDoubleTapAndServerError() {
  const code = fs.readFileSync(path.join(ROOT, 'web', 'store-fx.js'), 'utf8');
  let releaseQuote;
  const deferred = new Promise(resolve => {releaseQuote = resolve;});
  const requests = [], events = [], errors = [];
  const button = {disabled:false};
  const context = {
    document: {getElementById: id => id === 'orderbtn' ? button : null},
    window: {BeerShopP1: {event: event => events.push(event), status: () => {}}},
    cart: {42: {qty:1,price:135}}, pendingKey: null,
    renderCart:()=>{}, toggleCart:()=>{}, reload:()=>{},
    msg: error => errors.push(error), fmt: String,
    crypto: {randomUUID:() => 'synthetic-key'},
    FormData: class {constructor() {
      return [['name','Synthetic Customer'],['email','only@example.test']];
    }},
    fetch: async url => {
      requests.push(url);
      if (url === '/api/quote') {
        await deferred;
        return {ok:true, json: async () => ({
          total:'135.00',items:[{variant_id:42,unit_price_uah:'135.00'}]
        })};
      }
      if (url === '/api/orders') {
        return {ok:false, json: async () => ({detail:'Synthetic order error'})};
      }
      throw Error('Unexpected request');
    },
    setInterval: () => {},
  };
  vm.runInNewContext(code, context, {filename:'store-fx.js'});
  const e = {preventDefault:()=>{},target:{reset:()=>{
    throw Error('Never reset form on failed order');
  }}};
  const first = context.placeOrder(e);
  const second = context.placeOrder(e);
  assert.equal(requests.filter(r=>r==='/api/quote').length,1);
  assert.equal(events.filter(x=>x==='checkout_attempt').length,1);
  releaseQuote();
  await Promise.all([first,second]);
  assert.equal(requests.filter(r=>r==='/api/orders').length,1);
  assert.equal(events.filter(x=>x==='checkout_error').length,1);
  assert.equal(errors.at(-1),'Synthetic order error');
  assert.equal(button.disabled,false);
}

(async()=>{
  await testAnonymousQueue(true);
  await testAnonymousQueue(false);
  await testCheckoutGuard();
  await testRapidDoubleTapAndServerError();
  console.log('P1 quick JS passed: feature-gated PII-free buffering and server-quote reconfirmation');
})().catch(error=>{console.error(error);process.exitCode=1});
