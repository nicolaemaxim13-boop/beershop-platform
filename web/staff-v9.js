/* v9 staging staff pilot. Session token is never stored in cookies/localStorage. */
(() => {
  'use strict';
  let token=null, role=null, username=null;
  const $=id=>document.getElementById(id);
  const status=$('staff-status'),out=$('staff-output');
  function clearEnrollment(){
    $('staff-enrollment-uri').value='';
    $('mfa-enrollment').hidden=true;
  }
  function reset(){
    token=null;role=null;username=null;
    clearEnrollment();$('session-panel').hidden=true;$('login-panel').hidden=false;
    $('staff-crm-access').hidden=true;
    $('staff-password').value='';$('staff-otp').value='';
    $('new-staff-password').value='';out.textContent='';
  }
  async function call(path,{method='GET',body=null,auth=true}={}){
    const headers={'Accept':'application/json'};
    if(body!==null)headers['Content-Type']='application/json';
    if(auth&&token)headers.Authorization='Bearer '+token;
    const result=await fetch('/api/admin/v9/staff/'+path,{
      method,headers,body:body===null?undefined:JSON.stringify(body),cache:'no-store'
    });
    const value=await result.json().catch(()=>({}));
    if(!result.ok)throw Error(result.status+' · '+(value.detail||'Не вдалось виконати запит'));
    return value;
  }
  function show(value){
    // Display summary only: no tokens, passwords, MFA seeds, order PII.
    out.textContent=JSON.stringify(value,null,2);
  }
  $('staff-login').addEventListener('submit',async e=>{
    e.preventDefault();
    const body={username:$('staff-username').value.trim(),
      password:$('staff-password').value,otp:$('staff-otp').value.trim()};
    try{
      const result=await call('login',{method:'POST',body,auth:false});
      token=result.access_token;role=result.role;username=body.username;
      $('login-panel').hidden=true;$('session-panel').hidden=false;
      $('staff-profile').textContent=username+' · '+role+' · сесія до 15 хв';
      $('owner-panel').hidden=role!=='owner';
      $('staff-crm-access').hidden=!['owner','operations'].includes(role);
      status.textContent='Вхід успішний. Доступ діє лише для дозволених тестових API.';
      out.textContent='';
    }catch(error){reset();status.textContent='Вхід відхилено: '+error.message;}
    finally{body.password='';body.otp='';$('staff-password').value='';$('staff-otp').value='';}
  });
  $('staff-revoke-all').addEventListener('click',async()=>{
    let confirmed=false;
    if(token){
      try{
        await call('sessions/revoke-all',{method:'POST'});
        confirmed=true;
      }catch(_){/* keep uncertain server state explicit */}
    }
    reset();
    status.textContent=confirmed
      ? 'Всі ваші активні сесії відкликано сервером.'
      : 'Локальну сесію закрито. Серверне відкликання не підтверджено.';
  });
  $('staff-logout').addEventListener('click',async()=>{
    if(token){try{await call('logout',{method:'POST'});}catch(_){/* revoke may fail if expired */}}
    reset();status.textContent='Сесію завершено в браузері.';
  });
  for(const [id,path] of [
    ['staff-readiness','readiness'],['staff-catalog','catalog-summary'],
    ['staff-orders','order-counts']
  ]) $(''+id).addEventListener('click',async()=>{
    try{const data=await call(path);show(data);status.textContent='Прочитано: '+path;}
    catch(err){out.textContent='';status.textContent='Доступ заборонено: '+err.message;}
  });
  $('staff-create').addEventListener('submit',async e=>{
    e.preventDefault();clearEnrollment();
    const body={username:$('new-staff-name').value.trim(),
      password:$('new-staff-password').value,role:$('new-staff-role').value};
    try{
      const created=await call('users',{method:'POST',body});
      $('staff-enrollment-uri').value=created.mfa_enrollment_uri;
      $('mfa-enrollment').hidden=false;
      show({username:created.username,role:created.role,needs_mfa_enrollment:true});
      status.textContent='Тестового працівника створено. Надайте секрет MFA через захищений канал.';
    }catch(err){status.textContent='Помилка створення: '+err.message;}
    finally{body.password='';$('new-staff-password').value='';}
  });
  $('staff-clear-enrollment').addEventListener('click',clearEnrollment);
  status.textContent='Увійдіть із персональним MFA. Якщо модуль вимкнено, API поверне 503.';
})();