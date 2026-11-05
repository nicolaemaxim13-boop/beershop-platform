/* BeerShop 3.3: private export requires in-memory admin Bearer token. */
async function beerReport(){
 if(!token){msg('Спочатку підключи токен адміністратора');return}
 try{
  const data=await api('/api/admin/maintenance/report');
  $('beer-report').textContent=JSON.stringify(data,null,2);
  msg('Перевірка бази успішна');
 }catch(e){msg('Помилка перевірки бази: '+e.message)}
}
async function beerDownloadBackup(){
 if(!token){msg('Спочатку підключи токен адміністратора');return}
 if(!confirm('Резервна копія може містити персональні дані покупців. Збережіть її приватно та захистіть. Завантажити?'))return;
 try{
  const response=await fetch('/api/admin/maintenance/export',{
    headers:{Authorization:'Bearer '+token},cache:'no-store'});
  if(!response.ok)throw Error('HTTP '+response.status);
  const blob=await response.blob(),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download='beershop-private-backup.json';
  document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
  msg('Копію збережено на пристрій. Не публікуйте цей файл.');
 }catch(e){msg('Помилка завантаження: '+e.message)}
}
