/* BeerShop Admin 3.1: preview-first bulk catalog editing, no automatic currency conversion. */
const bulkOriginals=new Map(),bulkDrafts=new Map();
let bulkPageNum=1,bulkPageSize=25,bulkPreviewPayload='';
function bulkInvalidate(){
  bulkPreviewPayload='';
  $('bulkSave').disabled=true;
  $('bulkPending').textContent='Незбережені зміни: '+bulkDrafts.size+' SKU';
}
function bulkHTML(row){
 const id=row.variant_id,draft=bulkDrafts.get(id)||{};
 const price=draft.price??row.price,stock=draft.stock??row.stock,currency=draft.currency??row.currency;
 const pub=draft.product_active??row.product_active,active=draft.variant_active??row.variant_active;
 return '<tr data-variant="'+id+'" class="'+(bulkDrafts.has(id)?'bulk-dirty':'')+'">'+
 '<td><div class="bulk-name">'+esc(row.name)+'</div><div class="bulk-sku">'+esc(row.category)+' · '+esc(row.sku)+'</div></td>'+
 '<td><input type="number" min="0" max="9999999999" step="0.01" data-field="price" value="'+esc(price)+'"></td>'+
 '<td><select data-field="currency"><option value="UAH" '+(currency==='UAH'?'selected':'')+'>UAH</option><option value="USD" '+(currency==='USD'?'selected':'')+'>USD</option></select></td>'+
 '<td><input type="number" min="0" max="1000000" step="1" data-field="stock" value="'+esc(stock)+'"></td>'+
 '<td><input type="checkbox" data-field="product_active" '+(pub?'checked':'')+'></td>'+
 '<td><input type="checkbox" data-field="variant_active" '+(active?'checked':'')+'></td></tr>';
}
function bulkCapture(){
 for(const tr of $('bulkRows').querySelectorAll('tr[data-variant]')){
  const id=Number(tr.dataset.variant),original=bulkOriginals.get(id);
  if(!original)continue;
  const get=f=>tr.querySelector('[data-field="'+f+'"]');
  const values={price:get('price').value,stock:get('stock').value,currency:get('currency').value,
      product_active:get('product_active').checked,variant_active:get('variant_active').checked};
  const edits={};
  for(const f of Object.keys(values)){
   const value=values[f],old=original[f];
   const different=f==='price'||f==='stock'?(value===''||Number(value)!==Number(old)):value!==old;
   if(different)edits[f]=value;
  }
  if(Object.keys(edits).length)bulkDrafts.set(id,edits);else bulkDrafts.delete(id);
  tr.classList.toggle('bulk-dirty',bulkDrafts.has(id));
 }
 bulkInvalidate();
}
async function bulkLoad(page=1){
 try{
  bulkCapture();
  if(!token)return msg('Спочатку підключіться як адміністратор');
  const params=new URLSearchParams({page:String(page),limit:String(bulkPageSize),q:$('bulkQuery').value.trim(),
      status:$('bulkStatus').value,currency:$('bulkCurrency').value});
  if($('bulkCategory').value)params.set('category_id',$('bulkCategory').value);
  const data=await api('/api/admin/bulk/catalog?'+params);
  bulkPageNum=data.page;
  for(const item of data.items)if(!bulkDrafts.has(item.variant_id))bulkOriginals.set(item.variant_id,item);
  $('bulkRows').innerHTML=data.items.length?data.items.map(bulkHTML).join(''):
      '<tr><td colspan="6">Товарів за цими фільтрами немає</td></tr>';
  $('bulkSummary').textContent='Знайдено '+data.total+' SKU · показано '+data.items.length;
  $('bulkPage').textContent='Сторінка '+data.page+' із '+Math.max(1,Math.ceil(data.total/data.limit));
  $('bulkPrev').disabled=page<=1;
  $('bulkNext').disabled=page*data.limit>=data.total;
  const cats=await fetch('/api/categories').then(r=>r.json());
  const value=$('bulkCategory').value;
  $('bulkCategory').innerHTML='<option value="">Усі</option>'+cats.map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join('');
  $('bulkCategory').value=value;
  bulkInvalidate();
 }catch(e){msg('Масовий редактор: '+e.message)}
}
function bulkGo(direction){bulkLoad(bulkPageNum+direction)}
function bulkRequest(){
 const changes=[];
 for(const [id,edit] of bulkDrafts){
  const original=bulkOriginals.get(id);
  if(!original)continue;
  changes.push({product_id:original.product_id,variant_id:id,
   expected_product_active:original.product_active,expected_variant_active:original.variant_active,
   expected_price:original.price,expected_currency:original.currency,expected_stock:original.stock,...edit});
 }
 return {changes};
}
async function bulkPreview(){
 bulkCapture();
 const payload=bulkRequest();
 if(!payload.changes.length)return msg('Немає змін для перевірки');
 if(payload.changes.length>100)return msg('Максимум 100 SKU на одну операцію');
 try{
  const result=await api('/api/admin/bulk/preview',{method:'POST',body:JSON.stringify(payload)});
  bulkPreviewPayload=JSON.stringify(payload);
  $('bulkReport').textContent='Перевірено '+result.count+' SKU. Дані ще НЕ змінено.\n\n'+JSON.stringify(result.changes,null,2);
  $('bulkSave').disabled=false;
  msg('Попередній перегляд готовий — перевірте суми та підтвердьте збереження.');
 }catch(e){bulkPreviewPayload='';$('bulkSave').disabled=true;msg('Помилка перевірки: '+e.message)}
}
async function bulkApply(){
 bulkCapture();
 const payload=JSON.stringify(bulkRequest());
 if(!bulkPreviewPayload||bulkPreviewPayload!==payload)return msg('Перед збереженням повторіть попередній перегляд');
 const count=bulkDrafts.size;
 if(!confirm('Змінити '+count+' SKU? Дані магазину оновляться відразу.'))return;
 $('bulkSave').disabled=true;
 try{
  const result=await api('/api/admin/bulk/apply',{method:'POST',body:payload});
  bulkDrafts.clear();bulkOriginals.clear();bulkPreviewPayload='';
  $('bulkReport').textContent='Збережено '+result.count+' SKU.';
  await bulkLoad(bulkPageNum);
  await loadProducts();
  msg('Збережено '+result.count+' SKU');
 }catch(e){bulkPreviewPayload='';msg('Зміни не застосовано: '+e.message)}
}
function bulkDiscard(){
 if(bulkDrafts.size&&!confirm('Скасувати '+bulkDrafts.size+' незбережених змін?'))return;
 bulkDrafts.clear();bulkOriginals.clear();bulkPreviewPayload='';
 $('bulkReport').textContent='';$('bulkRows').innerHTML='';bulkLoad(bulkPageNum);
}
$('bulkRows').addEventListener('change',bulkCapture);
$('bulkRows').addEventListener('input',bulkCapture);
