// React lifecycle regression checks. Every admin request is a local fixture.
const {chromium} = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.CRM_TEST_URL || 'http://127.0.0.1:8018';
const origin = new URL(baseURL);
assert(['127.0.0.1','localhost'].includes(origin.hostname),'Local test server required');
const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};
const order={id:1,invoice:'BS-000001',name:'Fixture',email:'fixture@example.test',phone:'+380501234567',total:'250.00',status:'pending',payment_status:'unpaid',customer_id:1,priority:'normal',manager_id:7,manager:'Fixture Manager',created_at:null,version:1,delivery_status:'not_created',tracking_number:null};
const detail={...order,currency:'UAH',payment_method:'iban',delivery_address:'Fixture address',items:[],customer:{id:1,name:'Fixture',email:'fixture@example.test',phone:'+380501234567',version:1},notes:[],audit:[],inventory:[]};
const stock={variant_id:1,product_id:1,name:'Fixture stock',sku:'TEST-1',image_url:'/assets/crm-icons/store.svg',active:true,available:9,reserved:1,on_hand:10,reservations:[],movements:[]};

(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']});
 try{
  const context=await browser.newContext({baseURL,viewport:{width:1366,height:900}});
  const errors=[],requests=[];
  let stale=null,managers=null,unauthorized=false,failOrders=false;
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==origin.origin)return route.abort();
   if(!url.pathname.startsWith('/api/admin/'))return route.continue();
   requests.push({path:url.pathname,query:url.search,method:request.method()});
   const reply=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(()=>{});
   if(unauthorized)return reply({detail:'Unauthorized'},401);
   if(url.pathname.endsWith('/index-orders'))return reply({indexed:0});
   if(url.pathname==='/api/admin/crm/managers'){
    if(managers)await managers.promise;
    return reply([{id:7,name:'Fixture Manager',active:true}]);
   }
   if(url.pathname==='/api/admin/crm/summary')return reply({orders:26,statuses:{pending:26},awaiting_payment:26,customers:1});
   if(url.pathname==='/api/admin/crm/orders'){
    if(failOrders)return reply({detail:'Temporary fixture error'},503);
    if(url.searchParams.get('q')==='slow'){
     await stale.promise;
     return reply({items:[{...order,invoice:'STALE RESPONSE'}],total:1,page:1,limit:25});
    }
    return reply({items:[order],total:26,page:Number(url.searchParams.get('page')),limit:25});
   }
   if(url.pathname==='/api/admin/crm/orders/1')return reply(detail);
   if(url.pathname==='/api/admin/inventory/summary')return reply({skus:1,available:9,reserved:1,on_hand:10});
   if(url.pathname==='/api/admin/inventory')return reply({items:[stock],total:1,page:1,limit:25});
   if(url.pathname==='/api/admin/inventory/1')return request.method()==='PATCH'?reply({detail:'Stock snapshot changed'},409):reply(stock);
   return reply({detail:'Unexpected fixture request'},500);
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const login=async()=>{await page.locator('#admin-token').fill('t'.repeat(40));await page.locator('#login-form button').click();await page.locator('#list [data-action="order"]').waitFor()};
  await page.goto('/crm');await login();
  await page.locator('#next').click();
  await page.waitForFunction(()=>document.querySelector('#page-number')?.textContent==='2 / 2');
  assert(requests.some(r=>r.path.endsWith('/orders')&&new URLSearchParams(r.query).get('page')==='2'));
  await page.locator('#status-filter').selectOption('confirmed');
  await page.locator('#loading').waitFor({state:'hidden'});
  assert(requests.some(r=>new URLSearchParams(r.query).get('status')==='confirmed'));
  stale=deferred();await page.locator('#query').fill('slow');await page.locator('#query').press('Enter');
  await page.locator('#loading').waitFor({state:'visible'});
  await page.locator('#query').fill('fast');await page.locator('#query').press('Enter');
  await page.locator('#list [data-action="order"]').waitFor();
  stale.resolve();await page.waitForTimeout(100);
  assert(!await page.locator('#list').textContent().then(t=>t.includes('STALE RESPONSE')));

  managers=deferred();await page.locator('#refresh').click();
  await page.locator('#list [data-action="order"]').click();
  await page.locator('#detail').waitFor({state:'visible'});
  assert.equal(await page.locator('#assignment-form').count(),0);
  managers.resolve();managers=null;
  await page.locator('#assigned').waitFor();
  assert.equal(await page.locator('#assigned option:checked').textContent(),'Fixture Manager');
  await page.keyboard.press('Escape');
  await page.locator('#detail').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.action),'order');

  await page.locator('[data-view="inventory"]').click();await page.locator('#list [data-action="stock"]').click();
  await page.locator('[data-action="edit-stock"]').click();await page.locator('#stock-reason').fill('Fixture count');
  await page.locator('#save-edit').click();await page.locator('#editor-error').waitFor();
  assert(await page.locator('#save-edit').isDisabled());
  await page.keyboard.press('Escape');await page.locator('#editor').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.action),'edit-stock');
  await page.keyboard.press('Escape');
  await page.locator('[data-view="orders"]').click();await page.locator('#list [data-action="order"]').waitFor();
  failOrders=true;await page.locator('#refresh').click();await page.getByText('Temporary fixture error',{exact:true}).waitFor();
  assert.equal(await page.locator('#list').textContent(),'');
  failOrders=false;await page.getByRole('button',{name:'Повторити',exact:true}).click();await page.locator('#list [data-action="order"]').waitFor();

  stale=deferred();await page.locator('#query').fill('slow');await page.locator('#query').press('Enter');
  await page.locator('#loading').waitFor({state:'visible'});await page.locator('#logout').click();
  stale.resolve();await page.waitForTimeout(100);
  assert.equal(await page.locator('#list').textContent(),'');
  assert.equal(await page.locator('#workspace').count(),0);
  assert(await page.evaluate(()=>!localStorage.length&&!sessionStorage.length));
  await login();unauthorized=true;await page.locator('#refresh').click();
  await page.locator('#login').waitFor();assert.equal(await page.locator('#workspace').count(),0);
  assert.equal(await page.locator('#admin-token').inputValue(),'');
  assert.deepEqual(errors,[]);
  console.log('React lifecycle passed: filters/pagination, stale requests, slow managers, focus/Escape, stock conflict, error/retry, logout cancellation and 401.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
