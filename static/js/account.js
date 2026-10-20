"use strict";
const $=id=>document.getElementById(id);
const localTime=epoch=>epoch?new Date(epoch*1000).toLocaleString():"Not recorded";
function message(text,error=true){const n=$("message");n.textContent=text;n.className="notice "+(error?"error":"success");n.scrollIntoView({block:"nearest",behavior:"smooth"});}
async function api(url,method="GET",body){
  const response=await fetch(url,{method,credentials:"same-origin",headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined});
  if(response.status===401){location.href="/login";throw Error("Please sign in again.");}
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:"The request could not be completed.");return data;
}
function textCell(tr,text){const td=document.createElement("td");td.textContent=String(text??"");tr.append(td);return td;}
function statusBadge(status,kind){const span=document.createElement("span");span.className="status-pill status-"+kind;span.textContent=status;return span;}
async function accountSummary(){
  const data=await api("/api/auth/account-summary"),u=data.user;
  $("account-name").textContent=u.display_name;$("nav-display-name").textContent=u.display_name;
  $("account-username").textContent=u.username;$("display-name").value=u.display_name;
  $("account-role").textContent=u.role==="admin"?"Administrator":"Standard User";
  $("created-at").textContent=localTime(u.created_at);$("last-login").textContent=localTime(data.last_login);
  $("account-avatar").textContent=(u.display_name||u.username).split(/\s+/).slice(0,2).map(s=>s.charAt(0).toUpperCase()).join("")||"CM";
  $("sessions-total").textContent=data.sessions.length+" active";
  $("logout-others").disabled=data.sessions.filter(s=>!s.current).length===0;
  const tbody=$("sessions-list");tbody.replaceChildren();
  for(const session of data.sessions){const tr=document.createElement("tr");
    textCell(tr,session.agent||"Unknown device");textCell(tr,session.ip||"Unknown");textCell(tr,localTime(session.expires_at));
    const td=textCell(tr,"");td.append(statusBadge(session.current?"This browser":"Other session",session.current?"green":"blue"));tbody.append(tr);
  }
  if(!data.sessions.length){const tr=document.createElement("tr"),td=textCell(tr,"No active sessions.");td.colSpan=4;tbody.append(tr);}
}
async function accountActivity(){
  const data=await api("/api/auth/activity?limit=12"),tbody=$("activity-list");tbody.replaceChildren();
  for(const record of data.activity){const tr=document.createElement("tr");textCell(tr,localTime(record.timestamp));
    textCell(tr,record.action.replaceAll("_"," "));const td=textCell(tr,"");td.append(statusBadge(record.success?"Success":"Failed",record.success?"green":"red"));tbody.append(tr);
  }
  if(!data.activity.length){const tr=document.createElement("tr"),td=textCell(tr,"No recent activity recorded.");td.colSpan=3;tbody.append(tr);}
}
$("profile-form").addEventListener("submit",async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector('button[type="submit"]');button.disabled=true;
  try{await api("/api/auth/profile","PATCH",{display_name:$("display-name").value});await accountSummary();await accountActivity();message("Display name successfully updated.",false);}
  catch(error){message(error.message);}finally{button.disabled=false;}
});
$("my-password-form").addEventListener("submit",async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector('button[type="submit"]');const values=Object.fromEntries(new FormData(form));
  if(values.new_password!==values.confirm_password){message("New password and confirmation do not match.");return;}
  if(values.new_password===values.current_password){message("Please choose a different new password.");return;}
  button.disabled=true;
  try{
    await api("/api/auth/change-password","POST",values);form.reset();message("Password changed. All sessions have been revoked. Sign in again.",false);
    button.textContent="Go to login";button.type="button";button.disabled=false;button.addEventListener("click",()=>location.assign("/login"),{once:true});
  }catch(error){message(error.message);button.disabled=false;}
});
$("logout-others").addEventListener("click",async()=>{
  if(!confirm("Sign out all OTHER login sessions? This browser will stay signed in."))return;
  const button=$("logout-others");button.disabled=true;
  try{const result=await api("/api/auth/logout-other-sessions","POST");await Promise.all([accountSummary(),accountActivity()]);message(`Signed out ${result.revoked} other session(s).`,false);}
  catch(error){message(error.message);}finally{button.disabled=false;}
});
$("refresh-sessions").addEventListener("click",()=>accountSummary().catch(e=>message(e.message)));
$("refresh-activity").addEventListener("click",()=>accountActivity().catch(e=>message(e.message)));
async function logout(){try{await api("/api/auth/logout","POST");location.href="/login";}catch(error){message(error.message);}}
$("logout").addEventListener("click",logout);$("logout-bottom").addEventListener("click",logout);
Promise.all([accountSummary(),accountActivity()]).catch(error=>message(error.message));
