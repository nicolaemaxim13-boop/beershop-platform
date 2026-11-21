// Browser-only own-CRM UI test against local synthetic fixture.
// Toggle non-secret feature flag in Playwright without changing the server DB.
const assert=require('node:assert/strict');
const {chromium}=require('../frontend/node_modules/playwright');
const baseURL=process.env.CRM_TEST_URL||'http://127.0.0.1:8018';
const loc=new URL(baseURL);
assert(['localhost','127.0.0.1'].includes(loc.hostname),'Never run against production');
(async()=>{
 const browser=await chromium.launch({headless:true,
   ...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),
   args:['--no-sandbox']});
 try{
   const context=await browser.newContext({baseURL});
   await context.route('**/*',route=>{
     const url=new URL(route.request().url());
     if(url.origin!==loc.origin)return route.abort();
     if(url.pathname==='/api/v91/features'){
       return route.fulfill({status:200,contentType:'application/json',
         body:JSON.stringify({own_crm_only:true,crm_name:'BeerShop CRM'})});
     }
     return route.continue();
   });
   const page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto('/manage');
   await page.waitForFunction(()=>
     document.getElementById('opsExternalCrmLegacy')?.hidden===true &&
     document.getElementById('conn41ExternalCrmLegacy')?.hidden===true &&
     document.getElementById('opsOwnCrmV91')?.hidden===false &&
     document.getElementById('conn41OwnCrmV91')?.hidden===false
   ,null,{timeout:10000});
   assert.equal(await page.locator('#opsOwnCrmV91 a').getAttribute('href'),'/crm');
   assert.equal(await page.locator('#conn41OwnCrmV91 a').getAttribute('href'),'/crm');
   assert.equal(await page.locator('#opsExternalCrmLegacy').isVisible(),false);
   assert.equal(await page.locator('#conn41ExternalCrmLegacy').isVisible(),false);
   assert.equal(await page.locator('#conn41FetchSources').isDisabled(),true);
   for(const width of [390,1366]){
     await page.setViewportSize({width,height:844});
     const dims=await page.evaluate(()=>({
       width:innerWidth,scrollWidth:document.documentElement.scrollWidth
     }));
     assert(dims.scrollWidth<=dims.width+1,'Own CRM overflow '+JSON.stringify(dims));
   }
   assert.deepEqual(errors,[]);
   console.log('BeerShop own CRM-only admin staging UI verified');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
