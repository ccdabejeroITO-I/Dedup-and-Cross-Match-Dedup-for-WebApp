"use strict";
document.getElementById("auth-form").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,button=form.querySelector("button"),message=document.getElementById("message");
  button.disabled=true;message.classList.add("hidden");
  try{
    const payload=Object.fromEntries(new FormData(form));
    const response=await fetch("/api/auth/"+document.body.dataset.page,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await response.json();if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:"Please check your account details.");
    location.href="/";
  }catch(error){message.textContent=error.message;message.className="notice error";button.disabled=false;}
});
