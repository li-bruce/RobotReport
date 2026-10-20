'use strict';
const profileRequestStates={pending:'待审核',approved:'已通过',rejected:'已退回',cancelled:'已撤回',superseded:'已更新'};
function profileDiff(row){return `<div class="table-wrap"><table><thead><tr><th>项目</th><th>原资料</th><th>申请调整为</th></tr></thead><tbody>${row.items.map(item=>`<tr><td>${e(item.label)}</td><td>${e(item.before)}</td><td><strong>${e(item.after)}</strong></td></tr>`).join('')}</tbody></table></div>`;}
async function renderProfileRequests(epoch){return renderMembers(epoch);}
function profileHistoryMarkup(userId){return (state.page.profileHistory||[]).filter(row=>row.user_id===userId).map(row=>`<details><summary>资料调整 · ${e(profileRequestStates[row.status])} · ${e(displayTime(row.submitted_at))}</summary>${profileDiff(row)}${row.note?`<p>${e(row.note)}</p>`:''}${row.status==='pending'?btn('审核资料','profile-review','small primary',`data-id="${attr(row.id)}"`):''}</details>`).join('');}
function profileReviewDialog(id){
  const row=[...(state.page.profileRequests||[]),...(state.page.profileHistory||[])].find(item=>item.id===id);if(!row)return;
  openModal('审核资料申请',`<p class="member-review-name">${e(row.member_name)} · 资料申请${nf(row.id)}</p>${profileDiff(row)}<form id="profile-review-form">${selectfield('审核结果','approved',[['true','通过'],['false','退回']],'true')}${textfield('审核备注','note','','maxlength="500"')}${formFooter('保存审核结果')}</form>`);
  bindForm('profile-review-form',async(data,form,epoch)=>{
    await api(`/api/profile-requests/${id}/review`,{method:'POST',body:{approved:data.get('approved')==='true',note:data.get('note').trim()}});
    if(epoch===state.epoch){closeModal();toast('审核已保存，结果将通知本人。');await renderRoute();}
  });
}
