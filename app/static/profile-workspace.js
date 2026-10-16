'use strict';
const profileRequestStates={pending:'待审核',approved:'已通过',rejected:'已退回',cancelled:'已撤回',superseded:'已更新'};
function profileDiff(row){return `<div class="table-wrap"><table><thead><tr><th>项目</th><th>原资料</th><th>申请调整为</th></tr></thead><tbody>${row.items.map(item=>`<tr><td>${e(item.label)}</td><td>${e(item.before)}</td><td><strong>${e(item.after)}</strong></td></tr>`).join('')}</tbody></table></div>`;}
async function renderProfileRequests(epoch){
  $('#main').innerHTML=head('资料调整申请','审核会员提交的姓名、性别、手机号、等级与分组。','','MEMBER PROFILE')+`<section class="panel"><div class="toolbar"><div class="field"><label for="profile-filter">申请状态</label><select id="profile-filter">${Object.entries(profileRequestStates).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></div></div><div class="panel-body stack" id="profile-list"></div></section>`;
  const filter=$('#profile-filter'),list=$('#profile-list');let request=0;
  async function refresh(){
    const serial=++request;list.innerHTML=loading();
    try{
      const rows=await api('/api/profile-requests?status='+encodeURIComponent(filter.value));
      if(!isCurrent(epoch)||serial!==request||!list.isConnected)return;
      state.page.profileRequests=rows;
      list.innerHTML=rows.length?rows.map(row=>`<article class="panel"><div class="panel-head"><div><h2>${e(row.member_name)} <span class="badge">资料申请${nf(row.id)}</span></h2><p>${e(displayTime(row.submitted_at))} · ${e(profileRequestStates[row.status])}</p></div>${row.status==='pending'?`<button class="btn primary small" data-profile-review="${row.id}">审核</button>`:''}</div>${profileDiff(row)}${row.note?`<div class="panel-body"><p>审核备注：${e(row.note)}</p></div>`:''}</article>`).join(''):empty('暂无申请','会员可以向负责注册的机器人发送“修改资料”。');
      list.querySelectorAll('[data-profile-review]').forEach(button=>button.addEventListener('click',()=>profileReviewDialog(Number(button.dataset.profileReview))));
    }catch(error){if(isCurrent(epoch)&&serial===request&&list.isConnected)list.innerHTML=`<p role="alert">${e(error.message)}</p>`;}
  }
  filter.addEventListener('change',refresh);await refresh();
}
function profileReviewDialog(id){
  const row=state.page.profileRequests.find(item=>item.id===id);if(!row)return;
  openModal('审核资料申请',`<p class="member-review-name">${e(row.member_name)} · 资料申请${nf(row.id)}</p>${profileDiff(row)}<form id="profile-review-form">${selectfield('审核结果','approved',[['true','通过'],['false','退回']],'true')}${textfield('审核备注','note','','maxlength="500"')}${formFooter('保存审核结果')}</form>`);
  bindForm('profile-review-form',async(data,form,epoch)=>{
    await api(`/api/profile-requests/${id}/review`,{method:'POST',body:{approved:data.get('approved')==='true',note:data.get('note').trim()}});
    if(epoch===state.epoch){closeModal();toast('审核已保存，结果将通知本人。');await renderRoute();}
  });
}
