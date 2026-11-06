/* BeerShop 4.1 — opt-in KeyCRM API + read-only Nova Poshta tracking.
   Never exposes provider keys; all calls are authenticated via manage.html's api(). */
let conn41Ready=null;
const conn41=(id)=>document.getElementById(id);
async function conn41Load(){
 if(!token){msg('Спочатку підключіться як адміністратор');return}
 try{
  const result=await api('/api/admin/connectors41/status');
  conn41Ready=result;
  conn41('conn41KeyStatus').textContent=result.keycrm.configured?
   (result.keycrm.outbound_enabled?'Ключ налаштовано. Дозволено ручне відправлення замовлення.':'Ключ налаштовано. Відправлення замовлень вимкнено до KEYCRM_SYNC_ENABLED=1.'):'Ключ KeyCRM ще не налаштовано в Render.';
  conn41('conn41NovaStatus').textContent=result.nova_poshta.configured?
   'Ключ Нової пошти налаштовано. Можна перевірити існуючу ТТН.':'Ключ Нової пошти ще не налаштовано в Render.';
  conn41('conn41BankStatus').textContent=result.privatbank.configured?
   'Токен Автоклієнта налаштовано. Доступ до виписок ще не підтверджено.':'Токен Автоклієнта не налаштовано. Автоматичне підтвердження оплати вимкнено.';
  conn41('conn41FetchSources').disabled=!result.keycrm.configured;
  conn41('conn41Send').disabled=!(result.keycrm.configured && result.keycrm.outbound_enabled);
  conn41('conn41Track').disabled=!result.nova_poshta.configured;
  conn41('conn41Refresh').disabled=!result.keycrm.configured;
  conn41('conn41SyncTracking').disabled=!result.nova_poshta.configured;
  conn41('conn41BankCheck').disabled=!(result.privatbank.configured && result.privatbank.read_enabled);
 }catch(e){msg('Інтеграції: '+e.message)}
}
async function conn41Sources(){
 try{
  const r=await api('/api/admin/connectors41/keycrm/sources');
  const options=r.sources.map(s=>'<option value="'+Number(s.id)+'">'+esc(s.name)+' · ID '+Number(s.id)+'</option>').join('');
  conn41('conn41Source').innerHTML=options||'<option value="">Джерел не знайдено</option>';
  conn41('conn41Report').textContent='KeyCRM повернув '+r.sources.length+' джерел. Оберіть окреме джерело BeerShop.';
  msg('Джерела KeyCRM отримано без змін у CRM.');
 }catch(e){msg('KeyCRM: '+e.message)}
}
async function conn41CheckDispatch(){
 const id=Number(conn41('conn41Order').value);
 if(!Number.isSafeInteger(id)||id<=0)return msg('Вкажіть правильний ID замовлення BeerShop.');
 try{
  const result=await api('/api/admin/connectors41/keycrm/dispatch/'+id);
  conn41('conn41Report').textContent=JSON.stringify(result,null,2);
 }catch(e){msg('Перевірка відправлення: '+e.message)}
}
async function conn41Send(){
 const id=Number(conn41('conn41Order').value);
 const sid=Number(conn41('conn41Source').value);
 if(!Number.isSafeInteger(id)||id<=0||!Number.isSafeInteger(sid)||sid<=0)
  return msg('Вкажіть ID замовлення BeerShop і джерело KeyCRM.');
 if(!conn41Ready?.keycrm?.outbound_enabled)return msg('Реальний експорт у KeyCRM вимкнено.');
 if(!conn41('conn41Consent').checked)
  return msg('Підтвердіть згоду передати персональні дані покупця до KeyCRM.');
 if(!confirm('Дійсно надіслати замовлення №'+id+' у KeyCRM? Це реальний запис у CRM. Повторна відправка буде заблокована навіть після помилки.'))return;
 const btn=conn41('conn41Send');btn.disabled=true;
 try{
  const data=await api('/api/admin/connectors41/keycrm/sync/'+id,{
   method:'POST',body:JSON.stringify({source_id:sid,attestation:'SEND_PERSONAL_DATA_TO_KEYCRM'})});
  conn41('conn41Report').textContent=JSON.stringify(data,null,2);
  msg('KeyCRM: відповідь оброблено. Перевірте підтверджений зовнішній ID.');
 }catch(e){msg('KeyCRM: '+e.message+' · Перевірте source_uuid у CRM перед будь-якою повторною дією.')}
 finally{conn41('conn41Consent').checked=false;await conn41Load()}
}
async function conn41Track(){
 const id=Number(conn41('conn41TrackingOrder').value);
 if(!Number.isSafeInteger(id)||id<=0)return msg('Вкажіть ID замовлення з зареєстрованою ТТН.');
 const btn=conn41('conn41Track');btn.disabled=true;
 try{
  const data=await api('/api/admin/connectors41/nova-poshta/tracking/'+id);
  conn41('conn41TrackingReport').textContent=JSON.stringify(data,null,2);
  msg('Отримано актуальну відповідь Нової пошти. Локальний статус доставки не змінено.');
 }catch(e){msg('Нова пошта: '+e.message)}
 finally{btn.disabled=false}
}

async function conn41Refresh(){
 const id=Number(conn41('conn41Order').value);
 const source=Number(conn41('conn41Source').value);
 const external=conn41('conn41ExternalOrder').value.trim();
 if(!Number.isSafeInteger(id)||id<=0||!Number.isSafeInteger(source)||source<=0||!/^[1-9][0-9]{0,17}$/.test(external))
  return msg('Вкажіть замовлення BeerShop, джерело та числовий ID KeyCRM.');
 const btn=conn41('conn41Refresh');btn.disabled=true;
 try{
  const data=await api('/api/admin/connectors41/keycrm/refresh/'+id,{method:'POST',body:JSON.stringify({
   source_id:source,external_order_id:external,attestation:'CHECK_ORDER_IN_KEYCRM'})});
  conn41('conn41Report').textContent=JSON.stringify(data,null,2);
  msg('Замовлення звірено. Статус KeyCRM збережено.');
 }catch(e){msg('Звірка KeyCRM: '+e.message)}
 finally{await conn41Load()}
}
async function conn41Snapshot(){
 const id=Number(conn41('conn41Order').value);
 if(!Number.isSafeInteger(id)||id<=0)return msg('Вкажіть ID замовлення BeerShop.');
 try{conn41('conn41Report').textContent=JSON.stringify(await api('/api/admin/connectors41/snapshot/'+id),null,2)}
 catch(e){msg(e.message)}
}
async function conn41SyncTracking(){
 const id=Number(conn41('conn41TrackingOrder').value);
 if(!Number.isSafeInteger(id)||id<=0)return msg('Вкажіть ID замовлення з ТТН.');
 const btn=conn41('conn41SyncTracking');btn.disabled=true;
 try{
  const data=await api('/api/admin/connectors41/nova-poshta/sync/'+id,{method:'POST',body:JSON.stringify({
   attestation:'SYNC_EXISTING_WAYBILL_STATUS'})});
  conn41('conn41TrackingReport').textContent=JSON.stringify(data,null,2);
  msg(data.review_required?'Статус потребує ручної перевірки.':'Статус доставки синхронізовано.');
 }catch(e){msg('Нова пошта: '+e.message)}
 finally{await conn41Load()}
}
async function conn41BankCheck(){
 const btn=conn41('conn41BankCheck');btn.disabled=true;
 try{conn41('conn41BankReport').textContent=JSON.stringify(await api('/api/admin/connectors41/privatbank/availability'),null,2)}
 catch(e){msg('ПриватБанк: '+e.message)}
 finally{await conn41Load()}
}

