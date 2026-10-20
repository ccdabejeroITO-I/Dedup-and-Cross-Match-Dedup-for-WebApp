"use strict";
const $=id=>document.getElementById(id);
let allUsers=[];
async function registrations(){
  const data=await api('/api/admin/registrations');
  const requests=data.requests||[];
  $('pending-count').textContent=requests.length.toLocaleString();
  $('pending-quick-count').textContent=requests.length.toLocaleString();
  $('pending-registrations').replaceChildren();
  for(const item of requests){
    const row=document.createElement('tr');
    textCell(row,fmtTime(item.requested_at));textCell(row,item.username);textCell(row,item.display_name);
    textCell(row,'').append(statusPill('Awaiting approval','blue'));
    const td=textCell(row,''),actions=document.createElement('div');actions.className='admin-action-buttons';
    for(const approve of [true,false]){
      const button=document.createElement('button');button.type='button';button.className=approve?'secondary':'secondary danger-outline';
      button.textContent=approve?'Approve':'Reject';
      button.addEventListener('click',async()=>{
        const action=approve?'Approve':'Reject';
        if(!confirm(`${action} account request for "${item.username}"?${approve?' This will allow them to sign in.':''}`))return;
        for(const b of actions.querySelectorAll('button'))b.disabled=true;
        try{
          await api(`/api/admin/registrations/${item.id}/review`,'POST',{approve});
          await Promise.all([registrations(),users(),overview(),logs()]);
          message(`${item.username}: request ${approve?'approved':'rejected'}.`,false);
        }catch(error){message(error.message);await registrations().catch(()=>{});}
      });actions.append(button);
    }
    td.append(actions);$('pending-registrations').append(row);
  }
  if(!requests.length){const row=document.createElement('tr'),cell=textCell(row,'No pending account requests.','admin-empty');cell.colSpan=5;$('pending-registrations').append(row);}
}
function message(text,error=true){$("message").textContent=text;$("message").className="notice "+(error?"error":"success");$("message").scrollIntoView({block:"nearest",behavior:"smooth"});}
async function api(url,method="GET",body){
  const response=await fetch(url,{credentials:"same-origin",method,headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined});
  if(response.status===401){location.href="/login";throw Error("Please sign in again.");}
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail));return data;
}
function textCell(row,value,cls=""){const td=document.createElement("td");td.textContent=value??"";if(cls)td.className=cls;row.append(td);return td;}
function statusPill(value,variant){const badge=document.createElement("span");badge.className="status-pill status-"+variant;badge.textContent=value;return badge;}
function fmtTime(epoch){return epoch?new Date(epoch*1000).toLocaleString():"—";}
async function overview(){
  const data=await api('/api/admin/overview');
  for(const [id,key] of [['accounts','accounts'],['active','active_accounts'],['logins','logins_24h'],['jobs','jobs_24h'],['failures','failures_24h']])$('stat-'+id).textContent=Number(data[key]||0).toLocaleString();
  $('db-status').textContent=`${data.backend} · ${data.audit_events.toLocaleString()} recorded audit events · Local browser time`;
  const actionSelect=$('log-action'),previous=actionSelect.value;
  actionSelect.replaceChildren(new Option('All actions',''));
  for(const action of data.actions)actionSelect.add(new Option(action.replaceAll('_',' '),action));
  actionSelect.value=previous;
}
function renderUsers(){
  const search=$('user-search').value.trim().toLowerCase(),role=$('user-role-filter').value,status=$('user-status-filter').value;
  const filtered=allUsers.filter(user=>(!search||user.username.toLowerCase().includes(search)||user.display_name.toLowerCase().includes(search))
    &&(role==='all'||user.role===role)
    &&(status==='all'||(status==='active'&&user.is_active)||(status==='disabled'&&!user.is_active)));
  $('user-filter-count').textContent=`Showing ${filtered.length.toLocaleString()} of ${allUsers.length.toLocaleString()} registered accounts.`;
  $('users').replaceChildren();
  for(const user of filtered){
    const tr=document.createElement('tr');
    for(const v of [user.username,user.display_name,user.role==='admin'?'Administrator':'Standard User'])textCell(tr,v);
    textCell(tr,'').append(statusPill(user.is_active?'Active':'Disabled',user.is_active?'green':'gray'));
    textCell(tr,fmtTime(user.created_at));
    const td=textCell(tr,''),actions=document.createElement('div');actions.className='admin-action-buttons';
    const self=user.username===document.body.dataset.username;
    const edit=document.createElement('button');edit.type='button';edit.textContent='Edit';edit.className='secondary';edit.addEventListener('click',()=>{
      $('edit-user-id').value=user.id;$('edit-username').textContent=user.username;
      $('edit-display-name').value=user.display_name;$('edit-role').value=user.role;$('edit-role').disabled=self;
      $('edit-role-help').textContent=self?'To change your own password or profile, use My Account. You cannot change your own admin role.':'Changing a user role revokes that user’s existing login sessions.';
      $('edit-user-dialog').showModal();
    });actions.append(edit);
    const active=document.createElement('button');active.type='button';active.textContent=user.is_active?'Disable':'Enable';active.className=user.is_active?'secondary danger-outline':'secondary';
    active.disabled=self&&user.is_active;active.title=active.disabled?'You cannot disable your own administrator account.':'';
    active.addEventListener('click',async()=>{
      if(!confirm(`${user.is_active?'Disable':'Enable'} account "${user.username}"?`))return;
      try{active.disabled=true;await api(`/api/admin/users/${user.id}/active`,'PATCH',{is_active:!user.is_active});await Promise.all([users(),logs(),overview()]);message('Account status updated.',false);}
      catch(error){active.disabled=false;message(error.message);}
    });actions.append(active);
    if(!self){const reset=document.createElement('button');reset.type='button';reset.textContent='Reset password';reset.className='secondary';
      reset.addEventListener('click',()=>{$('reset-user-id').value=user.id;$('reset-username').textContent=user.username;$('new-password').value='';$('password-dialog').showModal();});actions.append(reset);
    }
    const view=document.createElement('button');view.type='button';view.className='secondary';view.textContent='View logs';view.addEventListener('click',()=>{
      $('log-user').value=user.username;logs().then(()=>$('audit-panel').scrollIntoView({behavior:'smooth'})).catch(error=>message(error.message));
    });actions.append(view);
    td.append(actions);$('users').append(tr);
  }
  if(!filtered.length){const tr=document.createElement('tr'),td=textCell(tr,'No accounts match your filters.','admin-empty');td.colSpan=6;$('users').append(tr);}
}
async function users(){const data=await api('/api/admin/users');allUsers=data.users;renderUsers();}
function filters(){const q=new URLSearchParams({limit:$('log-limit').value});if($('log-user').value.trim())q.set('username',$('log-user').value.trim());if($('log-action').value)q.set('action',$('log-action').value);q.set('status',$('log-status').value);return q;}
async function logs(){
  const q=filters();$('export-logs').href='/api/admin/logs/export?'+q.toString();
  const data=await api('/api/admin/logs?'+q.toString());$('audit').replaceChildren();
  for(const item of data.logs){
    const tr=document.createElement('tr');textCell(tr,fmtTime(item.timestamp));textCell(tr,item.username||'(unknown)');textCell(tr,(item.action||'').replaceAll('_',' '));
    textCell(tr,item.detail,'detail');textCell(tr,item.app_session_id?item.app_session_id.slice(0,12)+'…':'—');textCell(tr,item.ip||'—');
    textCell(tr,'').append(statusPill(item.success?'Success':'Failed',item.success?'green':'red'));$('audit').append(tr);
  }
  if(!data.logs.length){const tr=document.createElement('tr'),td=textCell(tr,'No audit records match your filters.','admin-empty');td.colSpan=7;$('audit').append(tr);}
  $('log-summary').textContent=`Showing ${data.logs.length.toLocaleString()} most recent matching events (limit ${q.get('limit')}).`;
}
$('generate-password').addEventListener('click',()=>{
  const alphabet='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*?';
  const bytes=new Uint8Array(64);crypto.getRandomValues(bytes);
  const limit=256-(256%alphabet.length);let secret='';
  for(let i=0;i<bytes.length&&secret.length<20;i++)if(bytes[i]<limit)secret+=alphabet[bytes[i]%alphabet.length];
  if(secret.length<20){message('Could not generate a complete password. Try again.');return;}
  $('create-password').value=secret;$('create-password').type='text';$('show-create-password').textContent='Hide password';
});
$('show-create-password').addEventListener('click',()=>{
  const field=$('create-password'),visible=field.type==='text';field.type=visible?'password':'text';
  $('show-create-password').textContent=visible?'Show password':'Hide password';
});
$('copy-create-password').addEventListener('click',async()=>{
  const secret=$('create-password').value;
  if(!secret){message('Enter or generate a temporary password first.');return;}
  try{await navigator.clipboard.writeText(secret);message('Temporary password copied. Share it privately and clear the clipboard after use.',false);}
  catch(error){message('Clipboard access is unavailable. Use Show password and copy it manually.');}
});
$('create-user').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,button=form.querySelector('button');button.disabled=true;try{
  await api('/api/admin/users','POST',Object.fromEntries(new FormData(form)));form.reset();$('create-password').type='password';$('show-create-password').textContent='Show password';await Promise.all([users(),logs(),overview()]);message('Account created. Share the temporary password privately. Ask the new user to change it under My Account.',false);
}catch(error){message(error.message);}finally{button.disabled=false;}});
$('edit-user-form').addEventListener('submit',async e=>{
  e.preventDefault();const id=Number($('edit-user-id').value),target=allUsers.find(u=>u.id===id);
  if(!target){message('Account not found. Refresh the users list.');return;}
  const name=$('edit-display-name').value.trim(),role=$('edit-role').value;
  if(role!==target.role&&!confirm(`Change ${target.username}'s role to ${role==='admin'?'Administrator':'Standard User'}? Existing login sessions will be revoked.`))return;
  const submit=e.currentTarget.querySelector('[type="submit"]');submit.disabled=true;
  try{
    if(name!==target.display_name)await api(`/api/admin/users/${id}/profile`,'PATCH',{display_name:name});
    if(role!==target.role)await api(`/api/admin/users/${id}/role`,'PATCH',{role});
    $('edit-user-dialog').close();await Promise.all([users(),logs(),overview()]);message('Account details saved.',false);
  }catch(error){await users().catch(()=>{});message(error.message);}finally{submit.disabled=false;}
});
$('cancel-edit').addEventListener('click',()=>$('edit-user-dialog').close());
$('reset-password').addEventListener('submit',async e=>{e.preventDefault();const button=e.currentTarget.querySelector('[type="submit"]');button.disabled=true;try{
  await api(`/api/admin/users/${$('reset-user-id').value}/reset-password`,'POST',{password:$('new-password').value});$('password-dialog').close();message('Password reset; that account was signed out of all browsers.',false);await Promise.all([logs(),overview()]);
}catch(error){message(error.message);}finally{button.disabled=false;}});
$('cancel-reset').addEventListener('click',()=>$('password-dialog').close());
for(const id of ['user-search','user-role-filter','user-status-filter'])$(id).addEventListener(id==='user-search'?'input':'change',renderUsers);
$('clear-user-filters').addEventListener('click',()=>{$('user-search').value='';$('user-role-filter').value='all';$('user-status-filter').value='all';renderUsers();});
for(const button of document.querySelectorAll('[data-scroll-to]'))button.addEventListener('click',()=>$(button.dataset.scrollTo).scrollIntoView({behavior:'smooth',block:'start'}));
$('refresh-logs').addEventListener('click',()=>logs().catch(e=>message(e.message)));
$('refresh-users').addEventListener('click',()=>Promise.all([users(),overview()]).catch(e=>message(e.message)));
$('refresh-registrations').addEventListener('click',()=>Promise.all([registrations(),overview()]).catch(e=>message(e.message)));
$('export-logs').addEventListener('click',()=>{window.setTimeout(()=>overview().catch(()=>{}),1200);});
$('logout').addEventListener('click',async()=>{try{await api('/api/auth/logout','POST');location.href='/login';}catch(error){message(error.message);}});
Promise.all([overview(),users(),registrations(),logs()]).catch(e=>message(e.message));
