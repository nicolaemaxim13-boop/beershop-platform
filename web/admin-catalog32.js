/* BeerShop Admin 3.2: uses the in-memory admin token from manage.html. */
let c32Page=1,c32Limit=20,c32Categories=[],c32Selected=null;
const c32SafeImage=u=>u&&(/^https:\/\//i.test(u)||/^\/uploads\/[0-9a-f]{32}\.(jpg|png|webp)$/.test(u))?u:'';
function c32SetPhoto(url){
 const img=$('c32photo'),src=c32SafeImage(url);
 img.hidden=!src;$('c32imgmissing').hidden=!!src;
 if(src)img.src=src;else img.removeAttribute('src');
}
async function catalog32Categories(){
 c32Categories=await api('/api/admin/categories32');
 const old=$('c32catfilter').value;
 $('c32catfilter').innerHTML='<option value="">Усі категорії</option>'+c32Categories.map(c=>`<option value="${c.id}">${esc(c.name)} (${c.products})</option>`).join('');
 $('c32catfilter').value=old;
 $('c32categories').innerHTML=c32Categories.map(c=>`<div class="c32category"><span>${esc(c.name)} <small>(${c.products}) · ${esc(c.slug)}</small></span><button class="secondary" type="button" data-rename="${c.id}">Перейменувати</button></div>`).join('');
 $('c32categories').querySelectorAll('[data-rename]').forEach(b=>b.addEventListener('click',()=>catalog32RenameCategory(Number(b.dataset.rename))));
}
async function catalog32Load(page=1){
 if(!token){msg('Спочатку підключи токен адміністратора');return}
 try{
  await catalog32Categories();
  const params=new URLSearchParams({page:String(Math.max(1,page)),limit:String(c32Limit),q:$('c32q').value.trim()});
  if($('c32catfilter').value)params.set('category_id',$('c32catfilter').value);
  const data=await api('/api/admin/catalog32?'+params);
  c32Page=data.page;
  $('c32total').textContent=data.total+' товарів';
  $('c32page').textContent=data.page+' / '+Math.max(1,Math.ceil(data.total/data.limit));
  $('c32prev').disabled=c32Page===1;$('c32next').disabled=c32Page*c32Limit>=data.total;
  $('c32cards').innerHTML=data.items.map(p=>{
    const image=c32SafeImage(p.image_url);
    const picture=image?'<img src="'+esc(image)+'" loading="lazy" alt="">':'<span aria-hidden="true">📦</span>';
    return '<article class="c32card">'+picture+'<div class="c32card-body"><div class="c32card-name">'+esc(p.name)+'</div><div class="c32card-meta">'+(p.active?'Опубліковано':'Приховано')+' · '+p.variants+' SKU</div><button type="button" data-edit="'+p.id+'">Редагувати</button></div></article>';
  }).join('')||'<p class="muted">Товарів не знайдено.</p>';
  $('c32cards').querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>catalog32Open(Number(b.dataset.edit))));
 }catch(e){msg('Каталог: '+e.message)}
}
function catalog32Go(d){catalog32Load(c32Page+d)}
async function catalog32Open(id){
 try{
  c32Selected=await api('/api/admin/catalog32/'+id);
  const p=c32Selected;
  $('c32id').textContent='#'+p.id;$('c32name').value=p.name;
  $('c32description').value=p.description;$('c32image').value=p.image_url;
  $('c32active').checked=p.active;
  $('c32category').innerHTML=c32Categories.map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join('');
  $('c32category').value=String(p.category_id);c32SetPhoto(p.image_url);
  $('c32file').value='';$('c32editor').hidden=false;
  $('c32editor').scrollIntoView({behavior:'smooth',block:'start'});
 }catch(e){msg('Товар: '+e.message)}
}
function catalog32Close(){c32Selected=null;$('c32editor').hidden=true}
function catalog32PreviewImage(){c32SetPhoto($('c32image').value.trim())}
async function catalog32Save(e){
 e.preventDefault();if(!c32Selected)return;
 const image_url=$('c32image').value.trim();
 if(image_url&&!c32SafeImage(image_url))return msg('Фото має бути HTTPS-посиланням або /uploads/...');
 const data={name:$('c32name').value.trim(),description:$('c32description').value,
   category_id:Number($('c32category').value),image_url,active:$('c32active').checked};
 if(data.name.length<2)return msg('Вкажи назву товару');
 $('c32save').disabled=true;
 try{await api('/api/admin/products/'+c32Selected.id,{method:'PATCH',body:JSON.stringify(data)});
  msg('Товар збережено');catalog32Close();await catalog32Load(c32Page)}
 catch(e){msg('Помилка: '+e.message)}finally{$('c32save').disabled=false}
}
async function catalog32Upload(){
 if(!c32Selected)return msg('Спочатку відкрий товар');
 const file=$('c32file').files[0];if(!file)return msg('Обери фото');
 if(file.size>3000000)return msg('Максимальний розмір 3 МБ');
 if(!['image/png','image/jpeg','image/webp'].includes(file.type))return msg('Дозволено JPEG, PNG, WebP');
 if(!confirm('Це тимчасове фото: на Render Free воно може зникнути після перезапуску. Продовжити?'))return;
 const fd=new FormData();fd.append('file',file);
 try{const r=await api('/api/admin/products/'+c32Selected.id+'/photo',{method:'POST',body:fd});
  $('c32image').value=r.image_url;c32SetPhoto(r.image_url);await catalog32Load(c32Page);
  msg('Тимчасове фото завантажено. Для постійного зберігання потрібне зовнішнє сховище.')}
 catch(e){msg('Завантаження: '+e.message)}
}
async function catalog32NewCategory(){
 const name=$('c32newName').value.trim(),slug=$('c32newSlug').value.trim().toLowerCase();
 if(name.length<2||!/^[a-z0-9-]{2,90}$/.test(slug))return msg('Перевір назву й slug латиницею');
 try{await api('/api/admin/categories',{method:'POST',body:JSON.stringify({name,slug})});
 $('c32newName').value='';$('c32newSlug').value='';await catalog32Categories();msg('Категорію створено')}
 catch(e){msg('Категорія: '+e.message)}
}
async function catalog32RenameCategory(id){
 const c=c32Categories.find(x=>x.id===id);if(!c)return;
 const name=prompt('Нова назва категорії:',c.name);
 if(name===null||name.trim()===c.name)return;
 if(name.trim().length<2)return msg('Назва занадто коротка');
 try{await api('/api/admin/categories32/'+id,{method:'PATCH',body:JSON.stringify({name:name.trim()})});
 await catalog32Categories();msg('Назву категорії збережено')}
 catch(e){msg('Категорія: '+e.message)}
}
$('c32q').addEventListener('keydown',e=>{if(e.key==='Enter')catalog32Load(1)});
$('c32catfilter').addEventListener('change',()=>catalog32Load(1));
