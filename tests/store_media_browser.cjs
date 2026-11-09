// Public storefront media checks. No production API or provider calls.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const baseURL=process.env.CRM_TEST_URL||'http://127.0.0.1:8018';
const origin=new URL(baseURL);
assert(['localhost','127.0.0.1'].includes(origin.hostname),'Local fixture required');
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const name='Колона "Кобра" <img src=x onerror=alert(1)>';
const product=(id,image_url)=>({id,name,slug:'fixture-'+id,description:'Fixture',category_id:1,image_url,variants:[{id,sku:'TEST-'+id,price:'250.00',active:true,stock:3,attributes:{}}]});
const items=[product(1,'/fixture-photo.png'),product(2,''),product(3,'/missing-photo.png'),product(4,'javascript:alert(1)')];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']});
 try{
  const context=await browser.newContext({baseURL,viewport:{width:1366,height:900}});
  const errors=[];
  await context.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.origin!==origin.origin)return route.abort();
   if(url.pathname==='/fixture-photo.png')return route.fulfill({status:200,contentType:'image/png',body:png});
   if(url.pathname==='/missing-photo.png')return route.fulfill({status:404,body:''});
   if(url.pathname==='/api/categories')return route.fulfill({json:[{id:1,slug:'fixture',name:'Fixture'}]});
   if(url.pathname==='/api/fx/usd-uah')return route.fulfill({json:{rate:'40',effective_date:'2026-11-09',delayed:false}});
   if(url.pathname==='/api/products'){
    const page=Number(url.searchParams.get('page')||1);
    return route.fulfill({json:{items:page===1?items:[product(5,'/fixture-photo.png')],total:5,page,limit:12}});
   }
   if(url.pathname.startsWith('/api/'))throw Error('Unexpected API request: '+url.pathname);
   return route.continue();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');await page.locator('#cards .card').first().waitFor();
  await page.locator('#cards').scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>document.querySelector('.catalog-photo')?.naturalWidth>0);
  const cards=page.locator('#cards .card');
  assert.equal(await cards.nth(0).locator('h3').textContent(),name);
  assert.equal(await cards.nth(0).locator('.catalog-photo').getAttribute('alt'),name);
  assert.equal(await cards.nth(0).locator('[onerror]').count(),0);
  assert(await cards.nth(1).getByText('Фото відсутнє',{exact:true}).isVisible());
  await cards.nth(2).scrollIntoViewIfNeeded();await cards.nth(2).getByText('Фото недоступне',{exact:true}).waitFor();
  assert(await cards.nth(2).locator('.product-photo-button').isHidden());
  assert.equal(await cards.nth(3).locator('.catalog-photo').count(),0);
  assert.equal(await cards.nth(0).locator('.catalog-photo').evaluate(el=>getComputedStyle(el).objectFit),'contain');
  for(const width of [1366,390,320]){
   await page.setViewportSize({width,height:900});await cards.nth(0).scrollIntoViewIfNeeded();
   const before=await cards.nth(0).boundingBox();
   await cards.nth(0).locator('.product-photo-button').click();
   await page.locator('#product-photo-dialog').waitFor({state:'visible'});
   await page.waitForFunction(()=>document.querySelector('.enlarged-photo')?.naturalWidth>0);
   assert.equal(await page.locator('#product-photo-title').textContent(),name);
   assert(await page.locator('#product-photo-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Storefront overflow at '+width);
   await page.screenshot({path:'/tmp/store-photo-modal-'+width+'.png',fullPage:true});
   await page.keyboard.press('Escape');await page.locator('#product-photo-dialog').waitFor({state:'hidden'});
   assert.equal(await page.evaluate(()=>document.activeElement.className),'product-photo-button');
   assert.equal((await cards.nth(0).boundingBox()).height,before.height);
  }
  await page.locator('#more').click();await page.waitForFunction(()=>document.querySelectorAll('#cards .card').length===5);
  await cards.nth(4).locator('.product-photo-button').click();await page.locator('#product-photo-dialog').waitFor({state:'visible'});
  await page.getByRole('button',{name:'Закрити фото',exact:true}).click();
  await cards.nth(0).locator('.product-photo-button').evaluate(button=>{button.dataset.photoSrc='/missing-photo.png?full=1'});
  await cards.nth(0).locator('.product-photo-button').click();await page.locator('.photo-error').waitFor({state:'visible'});
  assert(await page.locator('.enlarged-photo').isHidden());await page.keyboard.press('Escape');
  assert.equal(await page.locator('#count').textContent(),'0');
  assert.deepEqual(errors,[]);
  console.log('Storefront photos passed: images, fallback, safe URLs/text, pagination, zoom/Escape/focus, full-image failure, desktop/mobile layout.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
