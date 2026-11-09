// Viewport regression checks: storefront, admin, React CRM are tested on a local fixture only.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const baseURL=process.env.CRM_TEST_URL || 'http://127.0.0.1:8018';
const origin=new URL(baseURL);
assert(['127.0.0.1','localhost'].includes(origin.hostname),'Local test server required');
const sizes=[320,360,390,600,768,1024,1366,1920];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']});
 try{
  const context=await browser.newContext({baseURL,viewport:{width:1366,height:900}});
  await context.route('**/api/categories',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify([{id:1,slug:'coolers',name:'Охолоджувачі'}])}));
  await context.route('**/api/products?*',route=>{
   const url=new URL(route.request().url());
   const q=url.searchParams.get('q');
   const items=q==='not-existing-product'?[]:[{id:1,slug:'cooler-1',name:'Охолоджувач для пива',description:'Професійна система охолодження для бару',active:true,variants:[{id:1,sku:'COOL-001',stock:5,price:'1500.00',currency:'UAH',attributes:{power:'120W'},active:true}]}];
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items,total:items.length,page:1,limit:12})});
  });
  const page=await context.newPage();
  const pageErrors=[];page.on('pageerror',error=>pageErrors.push(error.message));
  const fit=async(label,width)=>{
   const metrics=await page.evaluate(()=>({
    viewport:window.innerWidth,document:document.documentElement.scrollWidth,
    body:document.body.scrollWidth
   }));
   assert(metrics.document<=metrics.viewport+1, `${label} width ${width}: document overflow ${JSON.stringify(metrics)}`);
  };
  for(const width of sizes){
   await page.setViewportSize({width,height:844});
   await page.goto('/');
   await page.locator('#cards .card').first().waitFor();
   await fit('storefront',width);
   assert(await page.locator('#cards .card').first().isVisible());
   assert(await page.locator('.cta').isVisible());
   await page.locator('#cart-toggle').click();
   assert.equal(await page.locator('#cart').getAttribute('aria-hidden'),'false');
   assert.equal(await page.locator('#cart-toggle').getAttribute('aria-expanded'),'true');
   await fit('open cart',width);
   const cartWidth=await page.locator('#cart').evaluate(el=>el.scrollWidth-el.clientWidth);
   assert(cartWidth<=1,`Cart internal horizontal overflow at ${width}: ${cartWidth}`);
   await page.keyboard.press('Escape');
   assert(await page.locator('#cart').evaluate(el=>el.classList.contains('hidden')),`Escape failed at ${width}`);
   assert.equal(await page.locator('#cart-toggle').getAttribute('aria-expanded'),'false');
   if([320,390,768,1366].includes(width))await page.screenshot({path:`/tmp/beershop-store-${width}.png`,fullPage:true});
  }
  for(const width of sizes){
   await page.setViewportSize({width,height:844});
   await page.goto('/manage');
   await fit('admin',width);
   const nav=page.locator('nav[aria-label="Розділи адмінпанелі"]');
   assert(await nav.isVisible(),`Navigation missing at ${width}`);
   await nav.getByRole('button',{name:'Замовлення'}).click();
   assert(await page.locator('#orders').evaluate(el=>el.classList.contains('active')),`Admin tab failed at ${width}`);
   assert.equal(await nav.getByRole('button',{name:'Замовлення'}).getAttribute('aria-pressed'),'true');
   await fit('admin orders',width);
   if([320,390,768,1366].includes(width))await page.screenshot({path:`/tmp/beershop-admin-${width}.png`,fullPage:true});
  }
  assert.deepEqual(pageErrors,[]);
  console.log('Responsive layout passed: storefront and admin at 320, 360, 390, 600, 768, 1024, 1366, 1920px; cart keyboard and ARIA controls.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
