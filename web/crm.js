'use strict';
const $ = id => document.getElementById(id);
const state = {token:'', view:'orders', page:1, total:0, managers:[], detail:null, catalog:null, listSeq:0, detailSeq:0};
const labels = {pending:'Нове',confirmed:'Підтверджено',fulfilled:'Виконано',cancelled:'Скасовано',paid:'Оплачено',unpaid:'Не оплачено',not_created:'Без ТТН',created:'ТТН зареєстровано',handed_over:'У дорозі',delivered:'Доставлено',high:'Високий',normal:'Звичайний',low:'Низький'};
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => new Intl.NumberFormat('uk-UA',{style:'currency',currency:'UAH'}).format(Number(value||0));
const date = value => value ? new Intl.DateTimeFormat('uk-UA',{dateStyle:'short',timeStyle:'short'}).format(new Date(value)) : 'Дата не зафіксована';
const badge = value => `<span class="badge ${esc(value)}">${esc(labels[value]||value)}</span>`;
const icon = name => `<img src="/assets/crm-icons/${name}.svg" alt="">`;
function photo(item){try{const url=new URL(item.image_url,location.origin);if(!item.image_url||!['http:','https:'].includes(url.protocol))return '';return `<img class="product-photo" src="${esc(url.href)}" alt="${esc(item.name)}" loading="lazy" referrerpolicy="no-referrer">`}catch{return ''}}
document.addEventListener('error',event=>{if(event.target.classList?.contains('product-photo'))event.target.hidden=true},true);
let noticeTimer;
function notify(message, error=false){clearTimeout(noticeTimer);const notice=$('notice');($('editor').open?$('editor'):$('detail').open?$('detail'):document.body).append(notice);notice.textContent=message;notice.className=error?'error':'';notice.hidden=false;noticeTimer=setTimeout(()=>notice.hidden=true,6500)}
async function api(path, options={}){
 const token=state.token;
 const response=await fetch(path,{...options,cache:'no-store',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',...(options.headers||{})}});
 if(state.token!==token)throw Error('Сесію змінено');
 const data=await response.json().catch(()=>({}));
 if(!response.ok){
  if(response.status===401)logout();
  throw Error(typeof data.detail==='string'?data.detail:'Перевірте заповнення полів ('+response.status+')');
 }
 return data;
}
const crm = (path,options) => api('/api/admin/crm'+path,options);
const send = (method,body) => ({method,body:JSON.stringify(body)});
function logout(){state.token='';state.detail=null;state.catalog=null;state.managers=[];state.listSeq++;state.detailSeq++;$('workspace').hidden=true;$('login').hidden=false;$('logout').hidden=true;$('admin-token').value='';$('list').replaceChildren();$('detail-body').replaceChildren();$('editor-form').replaceChildren();$('detail').close();$('editor').close()}
async function busy(button, fn){button.disabled=true;try{await fn()}catch(e){notify(e.message,true)}finally{button.disabled=false}}
$('login-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{
 state.token=$('admin-token').value.trim();
 try{await crm('/index-orders',send('POST',{}));await loadManagers();$('admin-token').value='';$('login').hidden=true;$('workspace').hidden=false;$('logout').hidden=false;await loadList()}
 catch(e){logout();throw e}
})});
$('logout').onclick=logout;
$('refresh').onclick=()=>busy($('refresh'),async()=>{await crm('/index-orders',send('POST',{}));await loadManagers();await loadList()});
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{
 state.view=button.dataset.view;state.page=1;$('query').value='';
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('selected',b===button));
 loadList().catch(e=>notify(e.message,true));
});
$('filters').onsubmit=event=>{event.preventDefault();state.page=1;loadList().catch(e=>notify(e.message,true))};
['status-filter','payment-filter','manager-filter','priority-filter'].forEach(id=>$(id).onchange=()=>{state.page=1;loadList().catch(e=>notify(e.message,true))});
$('prev').onclick=()=>{state.page--;loadList().catch(e=>notify(e.message,true))};
$('next').onclick=()=>{state.page++;loadList().catch(e=>notify(e.message,true))};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('detail').addEventListener('close',()=>{state.detailSeq++});
async function loadManagers(){state.managers=await crm('/managers');const old=$('manager-filter').value;$('manager-filter').innerHTML='<option value="all">Усі відповідальні</option><option value="unassigned">Без відповідального</option>'+state.managers.map(m=>`<option value="${m.id}">${esc(m.name)}${m.active?'':' (неактивний)'}</option>`).join('');$('manager-filter').value=old||'all'}
async function loadList(){
 if(!state.token)return;
 const seq=++state.listSeq, view=state.view;
 const titles={orders:['Замовлення','Нове замовлення'],customers:['Клієнти','Новий клієнт'],managers:['Відповідальні','Додати відповідального']};
 $('page-title').textContent=titles[view][0];$('create').querySelector('span').textContent=titles[view][1];
 $('filters').hidden=view==='managers';$('pagination').hidden=view==='managers';
 ['status-filter','payment-filter','manager-filter','priority-filter'].forEach(id=>$(id).hidden=view!=='orders');
 $('query').placeholder=view==='orders'?'Номер, ім’я, телефон або email':'Ім’я, телефон або email';$('loading').hidden=false;
 try{
  const params=new URLSearchParams({q:$('query').value,page:state.page,limit:25});
  if(view==='orders'){params.set('status',$('status-filter').value);params.set('payment',$('payment-filter').value);params.set('manager',$('manager-filter').value);params.set('priority',$('priority-filter').value)}
  const [data,summary]=await Promise.all([crm('/'+view+(view==='managers'?'':'?'+params)),crm('/summary')]);
  if(seq!==state.listSeq)return;
  $('summary').innerHTML=[['Усі замовлення',summary.orders],['Нові',summary.statuses.pending||0],['Очікують оплати',summary.awaiting_payment],['Виконані',summary.statuses.fulfilled||0],['Клієнти',summary.customers]].map(([name,value])=>`<div class="metric"><span>${name}</span><strong>${value}</strong></div>`).join('');
  const rows=view==='managers'?data:data.items;state.total=view==='managers'?data.length:data.total;
  if(!rows.length&&state.page>1){state.page=Math.max(1,Math.ceil(state.total/25));return loadList()}
  $('result-count').textContent='Знайдено: '+state.total;$('page-number').textContent=`${state.page} / ${Math.max(1,Math.ceil(state.total/25))}`;
  $('prev').disabled=state.page<=1;$('next').disabled=state.page*25>=state.total;
  if(!rows.length){$('list').innerHTML=`<div class="empty">${icon(view==='orders'?'clipboard-list':'users')}<h2>${view==='orders'?'Замовлень не знайдено':view==='customers'?'Клієнтів не знайдено':'Відповідальних ще немає'}</h2><button class="primary" data-action="create">${esc(titles[view][1])}</button></div>`;return}
  if(view==='managers'){$('list').innerHTML=rows.map(m=>`<div class="manager-row"><div><strong>${esc(m.name)}</strong><span class="sub">${m.active?'Активний':'Неактивний'}</span></div><button class="icon" data-action="edit-manager" data-id="${m.id}" title="Редагувати" aria-label="Редагувати ${esc(m.name)}">${icon('pencil')}</button></div>`).join('');return}
  if(view==='customers'){$('list').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Клієнт</th><th class="hide-small">Контакти</th><th>Замовлення</th><th>Оплачено</th></tr></thead><tbody>${rows.map(c=>`<tr><td><button class="text-button cell-title" data-action="customer" data-id="${c.id}">${esc(c.name)}</button><span class="sub mobile-only">${esc(c.phone)}</span></td><td class="hide-small"><span>${esc(c.phone)}</span><span class="sub">${esc(c.email)}</span></td><td>${c.orders}</td><td class="money">${money(c.paid_total)}</td></tr>`).join('')}</tbody></table></div>`;return}
  $('list').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Замовлення</th><th>Клієнт</th><th>Сума / оплата</th><th class="hide-small">Статус</th><th class="hide-medium">Доставка</th><th class="hide-medium">Відповідальний</th></tr></thead><tbody>${rows.map(o=>`<tr><td><button class="text-button cell-title" data-action="order" data-id="${o.id}">${esc(o.invoice)}</button><span class="sub hide-small">${o.created_at?date(o.created_at):'Дата не зафіксована'}</span><span class="mobile-only">${badge(o.status)}</span>${o.priority==='high'?'<span class="sub"><span class="priority-dot"></span>Високий</span>':''}</td><td><button class="text-button cell-title" data-action="customer" data-id="${o.customer_id}">${esc(o.name)}</button><span class="sub">${esc(o.phone)}</span></td><td><span class="money cell-title">${money(o.total)}</span>${badge(o.payment_status)}</td><td class="hide-small">${badge(o.status)}</td><td class="hide-medium"><span>${esc(labels[o.delivery_status]||o.delivery_status)}</span><span class="sub">${esc(o.tracking_number||'')}</span></td><td class="hide-medium">${esc(o.manager||'Не призначено')}</td></tr>`).join('')}</tbody></table></div>`;
 }finally{if(seq===state.listSeq)$('loading').hidden=true}
}
function contacts(c){return `<dl class="contact"><div><dt>Телефон</dt><dd>${esc(c.phone)}</dd></div><div><dt>Email</dt><dd>${esc(c.email)}</dd></div></dl>`}
function notesHtml(notes){return notes.length?notes.map(n=>`<article class="note"><small>${date(n.created_at)}${n.order_id?' · BS-'+String(n.order_id).padStart(6,'0'):''}</small><p>${esc(n.body)}</p></article>`).join(''):'<p class="sub">Нотаток ще немає</p>'}
function noteForm(){return '<form id="note-form" class="note-form"><label for="note-text">Нова нотатка</label><textarea id="note-text" required maxlength="2000" rows="3"></textarea><div class="form-actions"><button type="submit" class="secondary">Додати нотатку</button></div></form>'}
async function openOrder(id){
 const seq=++state.detailSeq;
 const o=await crm('/orders/'+id);if(seq!==state.detailSeq)return;state.detail={type:'orders',id,data:o};
 $('detail-title').textContent=o.invoice;
 $('detail-body').innerHTML=`<section class="detail-section"><div class="detail-top"><div>${badge(o.status)} ${badge(o.payment_status)}</div><strong class="detail-total">${money(o.total)}</strong></div><div class="actions"><button class="secondary" data-action="status" data-status="confirmed" ${o.status!=='pending'?'disabled':''}>Підтвердити</button><button class="secondary" data-action="status" data-status="fulfilled" ${o.status!=='confirmed'||o.payment_status!=='paid'||o.delivery_status!=='delivered'?'disabled':''}>Завершити</button><button class="danger" data-action="status" data-status="cancelled" ${!['pending','confirmed'].includes(o.status)||o.payment_status==='paid'||o.tracking_number?'disabled':''}>Скасувати</button></div></section>
 <section class="detail-section"><h3>Клієнт</h3><button class="text-button" data-action="customer" data-id="${o.customer_id}">${esc(o.name)}</button>${contacts(o)}<dl class="contact"><div class="full"><dt>Доставка</dt><dd>${esc(o.delivery_address)}</dd></div></dl></section>
 <section class="detail-section"><h3>Товари</h3>${o.items.map(i=>`<div class="item">${photo(i)}<div class="item-main"><strong>${esc(i.name)}</strong><span class="sub">${esc(i.sku)} · ${i.quantity} × ${money(i.unit_price)}</span></div><span class="money">${money(Number(i.unit_price)*i.quantity)}</span></div>`).join('')}</section>
 <section class="detail-section"><form id="assignment-form"><div class="form-grid"><div><label for="assigned">Відповідальний</label><select id="assigned"><option value="">Не призначено</option>${state.managers.filter(m=>m.active||m.id===o.manager_id).map(m=>`<option value="${m.id}" ${m.id===o.manager_id?'selected':''}>${esc(m.name)}${m.active?'':' (неактивний)'}</option>`).join('')}</select></div><div><label for="priority">Пріоритет</label><select id="priority">${['normal','high','low'].map(p=>`<option value="${p}" ${p===o.priority?'selected':''}>${labels[p]}</option>`).join('')}</select></div></div><div class="form-actions"><button type="submit">Зберегти</button></div></form></section>
 <section class="detail-section"><h3>Оплата та доставка</h3><p>${badge(o.payment_status)} <span class="sub">${esc(labels[o.delivery_status]||o.delivery_status)}${o.tracking_number?' · '+esc(o.tracking_number):''}</span></p><div class="actions"><button data-action="bank" ${o.payment_status==='paid'||o.status==='cancelled'?'disabled':''}>Звірити оплату</button><button data-action="waybill" ${o.payment_status!=='paid'||o.status==='cancelled'||o.tracking_number?'disabled':''}>Додати ТТН</button><button data-action="tracking" ${!o.tracking_number?'disabled':''}>Перевірити доставку</button><button data-action="delivery-manual" ${!o.tracking_number||o.delivery_status==='delivered'||o.status==='cancelled'?'disabled':''}>Статус вручну</button></div></section>
 <section class="detail-section"><h3>Нотатки</h3>${notesHtml(o.notes)}${noteForm()}</section>
 <section class="detail-section"><details><summary>Журнал змін (${o.audit.length})</summary>${o.audit.map(a=>`<p class="audit">${date(a.at)} · ${esc(eventLabel(a.event))}<br>${esc(a.note)}</p>`).join('')||'<p class="sub">Змін ще немає</p>'}</details></section>`;
 if(!$('detail').open)$('detail').showModal();
 $('assignment-form').onsubmit=e=>{e.preventDefault();busy(e.submitter,async()=>{await crm('/orders/'+id,send('PATCH',{manager_id:$('assigned').value?Number($('assigned').value):null,priority:$('priority').value,version:o.version}));notify('Зміни збережено');await openOrder(id);await loadList()})};
 bindNote('orders',id);
}
function eventLabel(event){return ({crm_assignment_updated:'Призначення відповідального / пріоритет',order_status_changed:'Статус замовлення',bank_payment_manually_confirmed:'Оплату звірено вручну',nova_poshta_waybill_manually_registered:'ТТН зареєстровано',nova_poshta_status_manually_confirmed:'Статус доставки звірено вручну',nova_poshta_status_verified:'Статус доставки перевірено'})[event]||event}
async function openCustomer(id){
 const seq=++state.detailSeq;const c=await crm('/customers/'+id);if(seq!==state.detailSeq)return;state.detail={type:'customers',id,data:c};
 $('detail-title').textContent=c.name;$('detail-body').innerHTML=`<section class="detail-section">${contacts(c)}<div class="actions"><button data-action="edit-customer">${icon('pencil')}Редагувати контакти</button><button class="primary" data-action="new-customer-order">${icon('plus')}Нове замовлення</button></div></section><section class="detail-section"><h3>Останні замовлення (${c.orders.length})</h3>${c.orders.map(o=>`<div class="item"><div class="item-main"><button class="text-button" data-action="order" data-id="${o.id}">${esc(o.invoice)}</button><span class="sub">${esc(labels[o.status])}</span></div><span class="money">${money(o.total)}</span>${badge(o.payment_status)}</div>`).join('')||'<p class="sub">Замовлень ще немає</p>'}</section><section class="detail-section"><h3>Нотатки</h3>${notesHtml(c.notes)}${noteForm()}</section>`;
 if(!$('detail').open)$('detail').showModal();bindNote('customers',id);
}
function bindNote(type,id){let key=crypto.randomUUID();$('note-form').onsubmit=e=>{e.preventDefault();busy(e.submitter,async()=>{await crm('/'+type+'/'+id+'/notes',send('POST',{body:$('note-text').value,request_key:key}));key=crypto.randomUUID();notify('Нотатку збережено');await (type==='orders'?openOrder(id):openCustomer(id))})}}
let editorAction;
function editor(title,html,action){$('editor-title').textContent=title;$('editor-form').innerHTML=html+'<p id="editor-error" class="inline-error" role="alert" hidden></p><div class="form-actions"><button type="button" id="cancel-edit">Скасувати</button><button class="primary" type="submit" id="save-edit">Зберегти</button></div>';editorAction=action;$('cancel-edit').onclick=()=>$('editor').close();$('editor').showModal()}
$('editor-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;$('editor-error').hidden=true;try{await editorAction();$('editor').close();notify('Збережено');await loadList()}catch(e){$('editor-error').textContent=e.message;$('editor-error').hidden=false}finally{button.disabled=false}};
function fields(c={}){return `<div class="form-grid"><div class="full"><label for="contact-name">Ім’я</label><input id="contact-name" name="name" value="${esc(c.name)}" required minlength="2" maxlength="180"></div><div><label for="contact-phone">Телефон</label><input id="contact-phone" name="phone" type="tel" value="${esc(c.phone)}" required minlength="5" maxlength="50"></div><div><label for="contact-email">Email</label><input id="contact-email" name="email" type="email" value="${esc(c.email)}" required maxlength="254"></div></div>`}
function contactValues(){return {name:$('contact-name').value,phone:$('contact-phone').value,email:$('contact-email').value}}
function editCustomer(c){editor(c?'Контакти клієнта':'Новий клієнт',fields(c),async()=>{const data=await crm('/customers'+(c?'/'+c.id:''),send(c?'PATCH':'POST',{...contactValues(),...(c?{version:c.version}:{})}));await openCustomer(data.id)})}
function editManager(id){const m=state.managers.find(x=>x.id===id);editor(m?'Відповідальний':'Новий відповідальний',`<label for="manager-name">Ім’я</label><input id="manager-name" value="${esc(m?.name||'')}" required minlength="2" maxlength="100" style="width:100%"><label class="check"><input id="manager-active" type="checkbox" ${m?.active!==false?'checked':''}>Активний</label>`,async()=>{await crm('/managers'+(m?'/'+m.id:''),send(m?'PATCH':'POST',{name:$('manager-name').value,active:$('manager-active').checked}));await loadManagers()})}
async function newOrder(customer){
 const products=await api('/api/admin/products');
 state.catalog=products.filter(p=>p.active).flatMap(p=>p.variants.filter(v=>v.active&&v.stock>0).map(v=>({...v,name:p.name})));
 if(!state.catalog.length)throw Error('Немає активних товарів із залишком');
 const key=crypto.randomUUID();let quoteVersion=0,quoted=null;
 editor('Нове замовлення',fields(customer)+`<div style="margin-top:16px"><label for="delivery-address">Місто, відділення або адреса доставки</label><input id="delivery-address" style="width:100%" required minlength="5" maxlength="500"></div><h3 style="margin-top:24px">Товари</h3><div id="order-lines"></div><button id="add-line" type="button" class="secondary">${icon('plus')}Додати товар</button><div id="quote-total" class="quote-total">0,00 ₴</div>`,async()=>{
  const items=readLines();if(!quoted||quoted.items!==JSON.stringify(items))throw Error('Дочекайтеся розрахунку суми');
  let order;
  try{order=await api('/api/orders',send('POST',{...contactValues(),delivery_address:$('delivery-address').value,request_key:key,items,expected_total:quoted.total}))}
  catch(e){await refreshQuote();throw e}
  await openOrder(order.id);
 });
 const refreshQuote=async()=>{const current=++quoteVersion;quoted=null;try{const items=readLines(),quote=await api('/api/quote',send('POST',{items}));if(current===quoteVersion&&$('quote-total')){$('quote-total').textContent=money(quote.total);quoted={items:JSON.stringify(items),total:quote.total}}}catch(e){if(current===quoteVersion&&$('quote-total'))$('quote-total').textContent=e.message}};
 const addLine=()=>{const row=document.createElement('div');row.className='line-picker';row.dataset.line='';row.innerHTML=`<select aria-label="Товар">${state.catalog.map(v=>`<option value="${v.id}">${esc(v.name)} · ${esc(v.sku)} · ${v.stock} шт.</option>`).join('')}</select><input type="number" aria-label="Кількість" min="1" max="1000" value="1" required><button class="icon" type="button" title="Прибрати товар" aria-label="Прибрати товар">${icon('x')}</button>`;row.querySelector('button').onclick=()=>{row.remove();refreshQuote()};row.addEventListener('change',refreshQuote);$('order-lines').append(row);refreshQuote()};
 $('add-line').onclick=addLine;addLine();
}
function readLines(){const rows=[...document.querySelectorAll('[data-line]')];if(!rows.length)throw Error('Додайте товар');const items=rows.map(row=>({variant_id:Number(row.querySelector('select').value),quantity:Number(row.querySelector('input').value)}));if(items.some(x=>!Number.isInteger(x.quantity)||x.quantity<1||x.quantity>1000))throw Error('Перевірте кількість');if(new Set(items.map(x=>x.variant_id)).size!==items.length)throw Error('Товар повторюється: змініть його кількість в одному рядку');return items}
function bankDialog(o){editor('Звірка з банківською випискою',`<div class="form-grid"><div><label for="bank-tx">ID транзакції з виписки</label><input id="bank-tx" required minlength="6" maxlength="120"></div><div><label for="bank-amount">Сума надходження, UAH</label><input id="bank-amount" type="number" step="0.01" min="0.01" required></div><div class="full"><label for="bank-ref">Призначення платежу з виписки</label><textarea id="bank-ref" required minlength="6" maxlength="300"></textarea></div></div><p class="sub">Рахунок ${esc(o.invoice)} · ${money(o.total)}</p><label class="check"><input type="checkbox" required>Надходження перевірено за реальною банківською випискою</label>`,async()=>{await api('/api/admin/ops/orders/'+o.id+'/bank-confirm',send('POST',{transaction_id:$('bank-tx').value,amount:$('bank-amount').value,currency:'UAH',reference:$('bank-ref').value,attestation:'BANK_STATEMENT_CHECKED'}));await openOrder(o.id)})}
function waybillDialog(o){editor('Наявна ТТН Нової пошти',`<label for="waybill">Номер ТТН</label><input id="waybill" style="width:100%" inputmode="numeric" pattern="[0-9]{14}" required minlength="14" maxlength="14"><label class="check"><input type="checkbox" required>ТТН цього замовлення перевірено в кабінеті Нової пошти</label>`,async()=>{await api('/api/admin/ops/orders/'+o.id+'/nova-poshta/waybill',send('POST',{tracking_number:$('waybill').value,attestation:'WAYBILL_CHECKED_IN_NOVA_POSHTA'}));await openOrder(o.id)})}
function deliveryDialog(o){editor('Перевірений статус ТТН',`<p>${esc(o.tracking_number)}</p><label for="delivery-state">Статус у Новій пошті</label><select id="delivery-state"><option value="${o.delivery_status==='created'?'handed_over':'delivered'}">${o.delivery_status==='created'?'У дорозі':'Доставлено'}</option></select><label class="check"><input type="checkbox" required>Статус цієї ТТН перевірено в кабінеті Нової пошти</label>`,async()=>{await api('/api/admin/ops/orders/'+o.id+'/nova-poshta/status',send('PATCH',{status:$('delivery-state').value,attestation:'STATUS_CHECKED_IN_NOVA_POSHTA'}));await openOrder(o.id)})}
async function create(){if(state.view==='orders')await newOrder();else if(state.view==='customers')editCustomer();else editManager()}
$('create').onclick=()=>busy($('create'),create);
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
 busy(button,async()=>{
  const action=button.dataset.action,id=Number(button.dataset.id),o=state.detail?.data;
  if(action==='create')return create();
  if(action==='order')return openOrder(id);
  if(action==='customer')return openCustomer(id);
  if(action==='edit-manager')return editManager(id);
  if(action==='edit-customer')return editCustomer(o);
  if(action==='new-customer-order')return newOrder(o);
  if(action==='bank')return bankDialog(o);
  if(action==='waybill')return waybillDialog(o);
  if(action==='delivery-manual')return deliveryDialog(o);
  if(action==='status'){
   const status=button.dataset.status;
   if(status==='cancelled'&&!confirm('Скасувати '+o.invoice+' та повернути товари на склад?'))return;
   await api('/api/admin/orders/'+o.id+'/status',send('PATCH',{status}));await openOrder(o.id);await loadList();notify('Статус оновлено');
  }
  if(action==='tracking'){
   const result=await api('/api/admin/connectors41/nova-poshta/sync/'+o.id,send('POST',{attestation:'SYNC_EXISTING_WAYBILL_STATUS'}));
   await openOrder(o.id);await loadList();notify(result.review_required?'Доставка потребує ручної перевірки':'Статус доставки оновлено');
  }
 });
});
