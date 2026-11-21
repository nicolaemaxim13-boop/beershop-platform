// v9.1 first-party React CRM with isolated MFA login and role-specific UI.
const assert=require('node:assert/strict');
const {chromium}=require('../frontend/node_modules/playwright');
const baseURL=process.env.CRM_TEST_URL||'http://127.0.0.1:8018';
const origin=new URL(baseURL);
assert(['127.0.0.1','localhost'].includes(origin.hostname),'Only local synthetic fixture');
const token='synthetic-staff-token-'+'x'.repeat(40);
const order={id:1,invoice:'BS-000001',name:'Fixture',email:'fixture@example.test',
  phone:'+380501234567',total:'251.00',status:'pending',payment_status:'unpaid',
  customer_id:1,priority:'normal',manager_id:1,manager:'Fixture Manager',
  created_at:null,version:1,delivery_status:'not_created',tracking_number:null};
const detail={...order,currency:'UAH',payment_method:'iban',
  delivery_address:'Synthetic staging',items:[],customer:{id:1,name:'Fixture',
  email:'fixture@example.test',phone:'+380501234567',version:1},
  notes:[],audit:[],inventory:[]};
const stock={variant_id:1,product_id:1,name:'Synthetic equipment',sku:'V91-SKU-1',
  image_url:'/assets/crm-icons/store.svg',active:true,available:2,reserved:0,
  on_hand:2,reservations:[],movements:[]};

(async()=>{
 const browser=await chromium.launch({headless:true,
   ...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),
   args:['--no-sandbox']});
 try{
  const context=await browser.newContext({baseURL,viewport:{width:390,height:844}});
  let role='operations',revokes=0,summaryRequests=0,legacyWrites=0;
  const errors=[];
  await context.route('**/*',route=>{
   const req=route.request(),url=new URL(req.url()),method=req.method();
   if(url.origin!==origin.origin)return route.abort();
   const reply=(obj,status=200)=>route.fulfill({
      status,contentType:'application/json',body:JSON.stringify(obj)});
   if(url.pathname==='/api/v91/features')return reply({
     own_crm_only:true,staff_crm_login:true,crm_name:'BeerShop CRM'});
   if(url.pathname==='/api/admin/v9/staff/login'){
     assert.equal(method,'POST');
     const body=JSON.parse(req.postData());
     assert.equal(body.username,'test.operator');
     assert.equal(body.password,'synthetic-only-password');
     assert.equal(body.otp,'123456');
     return reply({access_token:token,token_type:'bearer',expires_in:900,role});
   }
   if(url.pathname==='/api/admin/v9/staff/logout'){
     assert.equal(req.headers()['authorization'],'Bearer '+token);
     revokes++;return reply({revoked:true});
   }
   if(!url.pathname.startsWith('/api/admin/'))return route.continue();
   assert.equal(req.headers()['authorization'],'Bearer '+token,
      'No shared admin token or missing MFA session: '+url.pathname);
   if(method!=='GET')legacyWrites++;
   if(url.pathname==='/api/admin/crm/summary'){
     summaryRequests++;return reply({orders:1,statuses:{pending:1},
       awaiting_payment:1,customers:1});}
   if(url.pathname==='/api/admin/crm/managers')return reply([
     {id:1,name:'Fixture Manager',active:true}]);
   if(url.pathname==='/api/admin/crm/orders')return reply({items:[order],total:1,page:1,limit:25});
   if(url.pathname==='/api/admin/crm/orders/1')return reply(detail);
   if(url.pathname==='/api/admin/inventory/summary')return reply({skus:1,available:2,reserved:0,on_hand:2});
   if(url.pathname==='/api/admin/inventory')return reply({items:[stock],total:1,page:1,limit:25});
   if(url.pathname==='/api/admin/inventory/1')return reply(stock);
   return reply({detail:'Unexpected synthetic endpoint '+url.pathname},500);
  });
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  const login=async()=>{
    await page.goto('/crm/');
    await page.locator('#staff-username').fill('test.operator');
    await page.locator('#staff-password').fill('synthetic-only-password');
    await page.locator('#staff-otp').fill('123456');
    await page.getByRole('button',{name:'Увійти з MFA'}).click();
  };
  await page.goto('/crm/');
  assert.equal(await page.locator('#admin-token').count(),0,
    'Shared token cannot be offered in strict staff mode');
  await page.locator('#staff-username').fill('test.operator');
  await page.locator('#staff-password').fill('synthetic-only-password');
  await page.locator('#staff-otp').fill('123456');
  await page.getByRole('button',{name:'Увійти з MFA'}).click();
  await page.locator('#list [data-action="order"]').waitFor();
  assert.equal(legacyWrites,0,'MFA login must not POST index-orders');
  assert(summaryRequests>0);
  assert(await page.locator('a[href="/staff-v9"]').count()>0);
  for(const width of [320,390,768,1366]){
    await page.setViewportSize({width,height:844});
    const dims=await page.evaluate(()=>({
      doc:document.documentElement.scrollWidth,window:innerWidth
    }));
    assert(dims.doc<=dims.window+1,'Staff CRM overflow: '+JSON.stringify(dims));
    if(width===390)await page.screenshot({
      path:'/tmp/beershop-v91-own-crm-mfa.png',fullPage:true});
  }
  await page.locator('#list [data-action="order"]').click();
  await page.locator('#detail').waitFor({state:'visible'});
  assert.equal(await page.locator('#detail [data-action="bank"]').count(),0,
    'Operations cannot confirm payment');
  await page.keyboard.press('Escape');
  await page.locator('#detail').waitFor({state:'hidden'});
  await page.locator('[data-view="inventory"]').click();
  await page.locator('#list [data-action="stock"]').first().click();
  await page.locator('#detail').waitFor({state:'visible'});
  assert.equal(await page.locator('#detail [data-action="edit-stock"]').count(),0,
    'Operations cannot change physical inventory');
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  await page.locator('#logout').click();
  await page.locator('#staff-username').waitFor();
  await page.waitForFunction(()=>document.querySelector('#staff-password')?.value==='');
  await page.waitForTimeout(150);
  assert.equal(revokes,1,'Logout must revoke staff token on server');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);

  role='auditor';await login();
  await page.getByRole('alert').getByText('Для цієї ролі',{exact:false}).waitFor();
  assert.equal(await page.locator('#workspace').count(),0,
    'Auditor must never enter customer PII workspace');
  await page.waitForTimeout(150);
  assert.equal(revokes,2,'Unsupported CRM role session must be revoked');
  assert.deepEqual(errors,[]);
  console.log('v9.1 React CRM: staging MFA, role isolation, stock/bank restrictions, logout/revoke and mobile width passed');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
