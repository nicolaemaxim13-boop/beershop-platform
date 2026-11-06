/* BeerShop 4.0 — manual and audited operations; integrations NOT connected. */
let opsSelected=null;
const opsById=new Map();
function opsEl(id){return document.getElementById(id)}
function opsEsc(value){return esc(String(value??''))}
async function opsLoad(){
 if(!token){msg('Спочатку підключіться як адміністратор');return}
 try{
  const [summary,orders]=await Promise.all([
   api('/api/admin/ops/summary'),api('/api/admin/ops/orders?limit=50')]);
  opsEl('opsStats').textContent='Замовлень: '+summary.orders+' · Очікують оплати: '+summary.awaiting_payment+
   ' · Підтверджено вручну: '+summary.payments_confirmed+' · Без запису в CRM: '+summary.pending_keycrm+
   ' · Без ТТН: '+summary.pending_shipment;
  opsById.clear();
  orders.forEach(o=>opsById.set(o.id,o));
  opsEl('opsList').innerHTML=orders.length?orders.map(o=>
    '<article class="ops-card"><div><strong>№'+o.id+' · '+opsEsc(o.total)+' ₴</strong>'+
    '<p class="muted">'+opsEsc(o.buyer)+' · '+opsEsc(o.order_status)+'</p>'+
    '<small>Оплата: '+(o.bank_confirmed?'підтверджена оператором':'не підтверджена')+
    ' · CRM: '+(o.crm_synced?'записано ID':'не підтверджено')+
    ' · ТТН: '+opsEsc(o.tracking_number||'немає')+'</small></div>'+
    '<button type="button" data-ops-select="'+o.id+'">Відкрити</button></article>'
  ).join(''):'<p class="muted">Замовлень поки немає. Після першого продажу вони з’являться тут.</p>';
  opsEl('opsList').querySelectorAll('[data-ops-select]').forEach(b=>b.addEventListener('click',()=>opsOpen(Number(b.dataset.opsSelect))));
  if(opsSelected!==null && opsById.has(opsSelected))opsOpen(opsSelected,false);
 }catch(e){msg('Операційний центр: '+e.message)}
}
function opsOpen(id,scroll=true){
 const o=opsById.get(id);if(!o)return;
 opsSelected=id;opsEl('opsDetail').hidden=false;
 opsEl('opsHeading').textContent='Замовлення BS-'+String(id).padStart(6,'0');
 opsEl('opsAmount').textContent=opsEsc(o.total)+' ₴';
 opsEl('opsBankReference').value='Оплата замовлення BS-'+String(id).padStart(6,'0');
 opsEl('opsManualPayment').checked=false;
 opsEl('opsManualWaybill').checked=false;
 opsEl('opsManualCrm').checked=false;
 opsEl('opsManualDelivery').checked=false;
 opsEl('opsBank').hidden=o.bank_confirmed;
 opsEl('opsDelivery').hidden=!o.tracking_number;
 opsEl('opsWaybill').hidden=!!o.tracking_number;
 opsEl('opsCrm').hidden=o.crm_synced;
 opsEl('opsPaidStatus').textContent=o.bank_confirmed?'Оплату підтвердив оператор за банківською випискою.':'Фактичну оплату ще не підтверджено.';
 opsEl('opsCrmStatus').textContent=o.crm_synced?'CRM ID: '+o.crm_order_id:'Зв’язок KeyCRM не підключено. Експорт лише вручну.';
 opsEl('opsDeliveryStatus').textContent='Доставка: '+o.delivery_status;
 opsEl('opsAudit').textContent='';
 opsEl('opsPayload').textContent='';
 if(scroll)opsEl('opsDetail').scrollIntoView({behavior:'smooth',block:'start'});
}
async function opsConfirmPayment(){
 if(!opsSelected)return;
 if(!opsEl('opsManualPayment').checked)return msg('Спочатку підтвердіть, що особисто перевірили банківську виписку.');
 const tx=opsEl('opsTransaction').value.trim();
 const ref=opsEl('opsBankReference').value.trim();
 if(tx.length<6||ref.length<6)return msg('Вкажіть справжній ідентифікатор транзакції та призначення платежу.');
 if(!confirm('Ви особисто звірили надходження '+opsById.get(opsSelected).total+' ₴ у банку? Це запише статус «Оплачено».'))return;
 try{
  await api('/api/admin/ops/orders/'+opsSelected+'/bank-confirm',{
   method:'POST',body:JSON.stringify({transaction_id:tx,amount:opsById.get(opsSelected).total,
    currency:'UAH',reference:ref,attestation:'BANK_STATEMENT_CHECKED'})});
  msg('Платіж зареєстровано як підтверджений оператором; автоматичної перевірки банку немає.');
  await opsLoad();
 }catch(e){msg('Підтвердження платежу: '+e.message)}
}
async function opsExportCrm(){
 if(!opsSelected)return;
 const sourceId=Number(opsEl('opsSourceId').value);
 if(!Number.isSafeInteger(sourceId)||sourceId<=0)return msg('Введіть реальний source_id з KeyCRM');
 try{
  const payload=await api('/api/admin/ops/orders/'+opsSelected+'/keycrm-payload?source_id='+sourceId);
  opsEl('opsPayload').textContent=JSON.stringify(payload,null,2);
  msg('Підготовлено дані для ручного перенесення, жодного запиту до KeyCRM не відправлено.');
 }catch(e){msg('KeyCRM: '+e.message)}
}
async function opsCopyPayload(){
 const text=opsEl('opsPayload').textContent;
 if(!text)return msg('Спочатку згенеруйте попередній перегляд');
 try{await navigator.clipboard.writeText(text);msg('Дані скопійовано. Вони містять персональні дані покупця.');}
 catch(e){msg('Копіювання недоступне: '+e.message)}
}
async function opsConfirmCrm(){
 if(!opsSelected)return;
 if(!opsEl('opsManualCrm').checked)return msg('Перевірте відповідне замовлення у KeyCRM.');
 const external=opsEl('opsCrmId').value.trim();
 if(!external)return msg('Вкажіть справжній ID замовлення в KeyCRM.');
 if(!confirm('Підтвердити, що замовлення існує у KeyCRM під ID '+external+'?'))return;
 try{
  await api('/api/admin/ops/orders/'+opsSelected+'/keycrm-confirm',{
   method:'POST',body:JSON.stringify({external_order_id:external,attestation:'ORDER_CONFIRMED_IN_KEYCRM'})});
  msg('ID KeyCRM збережено як підтверджений оператором.');await opsLoad();
 }catch(e){msg('KeyCRM: '+e.message)}
}
async function opsSaveWaybill(){
 if(!opsSelected)return;
 if(!opsEl('opsManualWaybill').checked)return msg('Спочатку перевірте ТТН у кабінеті Нової пошти.');
 const tracking=opsEl('opsTracking').value.trim();
 if(!/^[0-9]{14}$/.test(tracking))return msg('ТТН повинна містити 14 цифр.');
 try{
  await api('/api/admin/ops/orders/'+opsSelected+'/nova-poshta/waybill',{
   method:'POST',body:JSON.stringify({tracking_number:tracking,attestation:'WAYBILL_CHECKED_IN_NOVA_POSHTA'})});
  msg('ТТН записано після ручного підтвердження.');await opsLoad();
 }catch(e){msg('Нова пошта: '+e.message)}
}
async function opsUpdateDelivery(){
 if(!opsSelected)return;
 if(!opsEl('opsManualDelivery').checked)return msg('Спочатку перевірте статус у кабінеті Нової пошти.');
 try{
  await api('/api/admin/ops/orders/'+opsSelected+'/nova-poshta/status',{
   method:'PATCH',body:JSON.stringify({status:opsEl('opsNewStatus').value,attestation:'STATUS_CHECKED_IN_NOVA_POSHTA'})});
  msg('Статус доставки вручну підтверджено.');await opsLoad();
 }catch(e){msg('Статус доставки: '+e.message)}
}
async function opsLoadAudit(){
 if(!opsSelected)return;
 try{const audit=await api('/api/admin/ops/orders/'+opsSelected+'/audit');
  opsEl('opsAudit').textContent=JSON.stringify(audit,null,2)||'Подій немає';}
 catch(e){msg('Журнал дій: '+e.message)}
}
