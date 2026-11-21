// v9 readiness audit browser test against a local synthetic database only.
const assert=require('node:assert/strict');
const {chromium}=require('../frontend/node_modules/playwright');
const baseURL=process.env.CRM_TEST_URL||'http://127.0.0.1:8018';
const target=new URL(baseURL);
assert(['127.0.0.1','localhost'].includes(target.hostname),'Reject nonlocal test fixture');
const token=process.env.CRM_TEST_TOKEN||'t'.repeat(40);
(async()=>{
 const browser=await chromium.launch({headless:true,
   ...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),
   args:['--no-sandbox']});
 try{
   const context=await browser.newContext({baseURL});
   await context.route('**/*',route=>new URL(route.request().url()).origin===target.origin?
     route.continue():route.abort());
   const page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   const unauthorized=await context.request.get('/api/admin/v9/readiness');
   assert.equal(unauthorized.status(),401,'Unauthenticated readiness must be denied');
   for(const width of [320,390,768,1366]){
     await page.setViewportSize({width,height:844});
     await page.goto('/manage');
     await page.locator('#token').fill(token);
     await page.getByRole('button',{name:'Підключитися'}).click();
     await page.getByRole('button',{name:'Готовність v9'}).click();
     await page.waitForFunction(() =>
       document.querySelector('#readiness9-status')?.textContent.includes('НЕ ГОТОВО'),
       null, {timeout:12000});
     assert(await page.locator('#readiness9-metrics .readiness-metric').count()>=5);
     assert(await page.locator('.readiness-check.block').count()>=3);
     assert((await page.locator('#readiness9-checks').textContent()).includes('IBAN'));
     assert((await page.locator('#readiness9-checks').textContent()).includes('Фізичний склад'));
     const size=await page.evaluate(()=>({
       width:innerWidth, doc:document.documentElement.scrollWidth
     }));
     assert(size.doc<=size.width+1,'Readiness mobile overflow: '+JSON.stringify(size));
     if(width===390||width===1366)
       await page.screenshot({path:'/tmp/beershop-v9-readiness-'+width+'.png',fullPage:true});
   }
   // A scoped auditor can connect to readiness, but not admin orders.
   const scopedToken='a'.repeat(40);
   const denied=await context.request.get('/api/admin/orders',{
     headers:{Authorization:'Bearer '+scopedToken}
   });
   assert.equal(denied.status(),401);
   await page.goto('/manage');
   await page.locator('#token').fill(scopedToken);
   await page.getByRole('button',{name:'Підключитися'}).click();
   await page.waitForFunction(() =>
     document.querySelector('#readiness9-status')?.textContent.includes('НЕ ГОТОВО'),
     null,{timeout:12000});
   assert(await page.locator('#readiness9').evaluate(el=>el.classList.contains('active')));
   assert.deepEqual(errors,[],errors.join('; '));
   console.log('BeerShop v9 readiness UI tested at 320/390/768/1366px; read-only and authenticated');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
