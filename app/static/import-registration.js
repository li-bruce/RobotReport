'use strict';
async function reconcileImportedMembers(){
  const epoch=state.epoch;
  const result=await api('/api/member-imports/reconcile',{method:'POST',body:{}});
  if(epoch!==state.epoch)return;
  const eligible=result.items.filter(row=>row.can_link);
  openModal('匹配已有会员',`<p>可关联 <strong>${nf(eligible.length)}</strong> 人；其余 ${nf(result.waiting)} 人待本人确认或资料核对。</p><div class="table-wrap"><table><thead><tr><th>姓名</th><th>匹配结果</th></tr></thead><tbody>${result.items.map(row=>`<tr><td>${e(row.name)}</td><td>${e(row.reason)}</td></tr>`).join('')}</tbody></table></div>${eligible.length?`<form id="prereg-reconcile">${formFooter('关联匹配的 '+eligible.length+' 人')}</form>`:''}`);
  if(!eligible.length)return;
  bindForm('prereg-reconcile',async(data,form,serial)=>{
    const saved=await api('/api/member-imports/reconcile',{method:'POST',body:{record_ids:eligible.map(row=>row.id),commit:true}});
    if(serial===state.epoch){closeModal();toast(`已关联 ${saved.linked} 人。`);await refreshImportRecords();}
  });
}
async function inviteImportedMembers(){
  const selected=[...(state.page.selectedImportIds||[])];
  if(!selected.length)throw Error('请先勾选需要通知的待关联人员。');
  const epoch=state.epoch, channels=await api('/api/member-imports/invite-channels');
  if(epoch!==state.epoch)return;
  if(!channels.length)throw Error('请先配置可收发消息、已开启会员注册的机器人。');
  openModal('通知本人确认',`<form id="prereg-invite">${selectfield('发送机器人 / 群','channel_id',channels.map(row=>[row.id,`${row.bot_name} · ${row.name}`]),channels[0].id)}<p>已选 ${nf(selected.length)} 人。未取得账号 ID 或邮箱的人员以姓名列出。</p><div id="invite-preview"></div>${formFooter('预览通知')}</form>`);
  let preview=null, requestId=null;
  const selector=$('#f-channel_id');
  selector.addEventListener('change',()=>{preview=null;$('#invite-preview').replaceChildren();const submit=$('#prereg-invite button[type="submit"]');submit.dataset.idleLabel='预览通知';submit.textContent='预览通知';});
  bindForm('prereg-invite',async(data,form,serial)=>{
    const body={channel_id:data.get('channel_id'),record_ids:selected};
    if(!preview){
      const result=await api('/api/member-imports/invite-preview',{method:'POST',body});
      if(serial!==state.epoch||!form.isConnected||selector.value!==body.channel_id)return;
      preview=result;requestId=crypto.randomUUID();
      $('#invite-preview').innerHTML=`<div class="notice">${markdown(result.text)}</div>`;
      form.querySelector('button[type="submit"]').dataset.idleLabel='发送确认通知';
      return;
    }
    await api('/api/member-imports/invite',{method:'POST',body:{...body,signature:preview.signature,request_id:requestId}});
    if(serial===state.epoch){closeModal();toast('确认通知已加入发送队列。');}
  });
}
document.addEventListener('change',event=>{
  const target=event.target;
  if(target.matches('[data-import-check]')){
    state.page.selectedImportIds??=new Set();
    const id=Number(target.dataset.importCheck);
    target.checked?state.page.selectedImportIds.add(id):state.page.selectedImportIds.delete(id);
  }
  if(target.id==='import-select-all'){
    state.page.selectedImportIds??=new Set();
    document.querySelectorAll('[data-import-check]').forEach(input=>{
      input.checked=target.checked;
      target.checked?state.page.selectedImportIds.add(Number(input.dataset.importCheck)):state.page.selectedImportIds.delete(Number(input.dataset.importCheck));
    });
  }
});
