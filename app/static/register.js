'use strict';
const ref=location.hash.slice(1), form=document.querySelector('#registration'), status=document.querySelector('#status');
const endpoint='/public/registration/'+encodeURIComponent(ref);
const submitButton=form.querySelector('button');
const retryButton=document.querySelector('#retry');
let submitting=false, purpose='registration';
function profileForm(result){
  purpose='profile';
  document.title='资料调整申请';
  document.querySelector('.brand').textContent='会员服务 · 个人资料';
  document.querySelector('h1').textContent='申请调整资料';
  document.querySelector('h1 + p').textContent='填写需要调整的资料，审核通过后生效。分组按下方完整选择提交。';
  submitButton.textContent='提交资料申请';
  for(const name of ['name','gender','level'])form.elements[name].value=result.profile[name]??'';
  form.elements.phone.value='';form.elements.phone.placeholder='不修改请留空';
  document.querySelector('#phone-help').textContent='手机号不修改请留空；不需要在群里公开手机号。';
  let fieldset=document.querySelector('#profile-groups');
  if(!fieldset){fieldset=document.createElement('fieldset');fieldset.id='profile-groups';form.insertBefore(fieldset,submitButton);}
  fieldset.replaceChildren();
  const legend=document.createElement('legend');legend.textContent='申请分组';fieldset.append(legend);
  for(const group of result.groups){
    const label=document.createElement('label'),input=document.createElement('input');
    input.type='checkbox';input.name='group_ids';input.value=group.id;input.checked=result.profile.group_ids.includes(group.id);
    label.append(input,document.createTextNode(' '+group.name));fieldset.append(label);
  }
  if(!result.groups.length)fieldset.append(document.createTextNode('暂无可选分组'));
}
async function call(method,data){
  let response;
  try{response=await fetch(endpoint,{method,headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined,cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'});}
  catch{throw Error('连接失败，请检查网络后重试。');}
  let result;
  try{result=await response.json();}catch{throw Error('服务暂时无法响应，请稍后重试。');}
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'暂时无法提交，请稍后再试');
  return result;
}
function showResult(result,moveFocus=false){
  form.reset();form.hidden=true;
  if(result.purpose==='profile'){
    document.querySelector('#result').hidden=false;
    document.querySelector('#result-title').textContent='资料申请已提交';
    document.querySelector('#summary').textContent=result.name;
    document.querySelector('#result-message').textContent=result.message;
    document.querySelector('#next-step').textContent='回群发送“我的资料申请”查看审核进度。';
    status.textContent='';if(moveFocus)document.querySelector('#result-title').focus();return;
  }
  const approved=result.member_status==='approved', linked=result.registration_result==='linked';
  document.querySelector('#result').hidden=false;
  document.querySelector('#result-title').textContent=approved?(linked?'资料已关联，注册成功':'你已注册'):'申请已提交';
  document.querySelector('#summary').textContent=[result.name,({male:'男',female:'女'})[result.gender]||'',result.level?`${approved?'等级':'自评'} ${result.level} 级`:'暂未评级',result.phone_masked].filter(Boolean).join(' · ');
  document.querySelector('#result-message').textContent=result.message||(approved?'会员资格已生效，可以返回群聊使用报名等功能。':'请等待管理员审核。审核通过后即可使用会员功能。');
  document.querySelector('#next-step').textContent=approved?'现在可以返回群聊发送“报名”或“帮助”。':'可返回群聊发送“会员状态”查看进度，也可以稍后重新打开此链接查看提交结果。';
  status.textContent='';
  if(moveFocus)document.querySelector('#result-title').focus();
}
async function initialize(){
  retryButton.hidden=true;
  if(!/^[a-f0-9]{32}\.[A-Za-z0-9_-]{40,60}$/.test(ref)){
    form.hidden=true;status.textContent='请在群里 @机器人 发送“注册”，使用收到的本人注册链接填写。';return;
  }
  status.textContent='正在读取注册状态…';
  try{
    const result=await call('GET');
    if(result.status==='completed'){showResult(result);return;}
    if(result.purpose==='profile')profileForm(result);
    else if(result.profile)for(const key of ['name','gender','level'])form.elements[key].value=result.profile[key]??'';
    form.hidden=false;submitButton.disabled=false;status.textContent='';
  }catch(error){form.hidden=true;status.textContent=error.message;retryButton.hidden=false;}
}
retryButton.addEventListener('click',initialize);
form.addEventListener('submit',async event=>{
  event.preventDefault();if(submitting)return;submitting=true;form.setAttribute('aria-busy','true');submitButton.disabled=true;submitButton.textContent='正在提交…';status.textContent='';
  try{
    const data=new FormData(form),payload={name:data.get('name'),gender:data.get('gender'),level:data.get('level')?Number(data.get('level')):null,phone:data.get('phone')};
    if(!payload.phone.trim())delete payload.phone;
    if(purpose==='profile')payload.group_ids=data.getAll('group_ids').map(Number);
    const result=await call('POST',payload);
    showResult(result,true);
  }catch(error){status.textContent=error.message;status.focus();}
  finally{submitting=false;form.removeAttribute('aria-busy');submitButton.disabled=false;submitButton.textContent=purpose==='profile'?'提交资料申请':'提交注册申请';}
});
initialize();
