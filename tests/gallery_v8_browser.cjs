// v8 gallery end-to-end: synthetic URLs and isolated SQLite fixture only.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const baseURL=process.env.CRM_TEST_URL||'http://127.0.0.1:8018';
const origin=new URL(baseURL);
assert(['127.0.0.1','localhost'].includes(origin.hostname),'No production browser fixture');
const token=process.env.CRM_TEST_TOKEN||'t'.repeat(40);

(async()=>{
 const browser=await chromium.launch({
   headless:true,
   ...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),
   args:['--no-sandbox']
 });
 try{
   const context=await browser.newContext({baseURL,viewport:{width:390,height:844}});
   await context.route('**/*',route=>{
     const url=new URL(route.request().url());
     if(url.origin===origin.origin)return route.continue();
     if(url.hostname==='gallery.example.test')return route.fulfill({
       status:200,contentType:'image/svg+xml',
       body:'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="180"><rect width="240" height="180" fill="#cce9d0"/></svg>'
     });
     return route.abort();
   });
   const page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   const api=async(method,path,data)=>{
     const resp=await context.request.fetch(path,{
       method,headers:{Authorization:'Bearer '+token},data
     });
     assert(resp.ok(),path+': '+await resp.text());
     return resp.json();
   };
   const stamp=Date.now().toString();
   const categories=await api('GET','/api/categories');
   const created=await api('POST','/api/admin/products',{
     category_id:categories[0].id,name:'Gallery V8 Fixture '+stamp,
     slug:'gallery-v8-'+stamp,
     description:'Synthetic image gallery, no real product',
     image_url:'https://gallery.example.test/first.png'
   });
   await api('POST','/api/admin/products/'+created.id+'/variants',{
     sku:'GALLERY-V8-'+stamp,price:'349.00',currency:'UAH',
     stock:3,attributes:{finish:'metal'}
   });
   const base='https://gallery.example.test/';
   const photoNames=['first.png','second.png'];
   const photos=photoNames.map(name=>base+name);
   const gallery=await api('PUT','/api/admin/products/'+created.id+'/gallery',{images:photos});
   assert.deepEqual(gallery.images,photos);

   await page.goto('/manage');
   await page.locator('#token').fill(token);
   await page.getByRole('button',{name:'Підключитися'}).click();
   await page.getByRole('button',{name:'Каталог 3.2'}).click();
   await page.locator('#c32q').fill('Gallery V8 Fixture '+stamp);
   await page.locator('#c32q').press('Enter');
   await page.locator('#c32cards [data-edit="'+created.id+'"]').waitFor();
   await page.locator('#c32cards [data-edit="'+created.id+'"]').click();
   await page.locator('#c32gallery .c32gallery-entry').first().waitFor();
   assert.equal(await page.locator('#c32image').inputValue(),photos[0]);
   await page.locator('#c32newimage').fill(base+'third.png');
   await page.getByRole('button',{name:'+ Фото'}).click();
   assert.equal(await page.locator('#c32gallery .c32gallery-entry').count(),2);
   await page.locator('#c32gallery .c32gallery-entry').last().locator('[data-action=main]').click();
   assert.equal(await page.locator('#c32image').inputValue(),base+'third.png');
   await page.locator('#c32save').click();
   await page.locator('#c32editor').waitFor({state:'hidden'});
   const updated=await api('GET','/api/admin/catalog32/'+created.id);
   assert.deepEqual(updated.images,[base+'third.png',base+'second.png',base+'first.png']);

   for(const width of [320,390,768,1366]){
     await page.setViewportSize({width,height:844});
     await page.goto('/products/'+created.slug);
     await page.locator('#product-view h1').waitFor();
     const thumbnails=page.locator('.product-gallery-thumb');
     assert.equal(await thumbnails.count(),3);
     assert.equal(await thumbnails.first().getAttribute('aria-pressed'),'true');
     await thumbnails.nth(1).click();
     assert.equal(await thumbnails.nth(1).getAttribute('aria-pressed'),'true');
     assert.equal(await page.locator('#detail-main-photo img').getAttribute('src'),base+'second.png');
     assert((await page.locator('#detail-photo-count').textContent()).includes('2 із 3'));
     const size=await page.evaluate(()=>({
       viewport:innerWidth,doc:document.documentElement.scrollWidth
     }));
     assert(size.doc<=size.viewport+1,'Gallery overflow '+width+': '+JSON.stringify(size));
     if(width===390||width===1366)await page.screenshot({
       path:'/tmp/beershop-v8-gallery-'+width+'.png',fullPage:true
     });
   }
   assert.deepEqual(errors,[]);
   console.log('BeerShop gallery v8: admin reorder/save, public thumbnails and 320–1366px passed.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
