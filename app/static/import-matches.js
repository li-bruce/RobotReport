'use strict';
function importMatchesMarkup(preview){
  const rows=(preview.rows||[]).filter(r=>r.status==='match');
  if(!rows.length)return '';
  const unique=rows.filter(r=>r.matches?.length===1).length;
  return `<div class="panel-body import-match-tools"><strong>${rows.length} 人需要核对</strong><button class="btn" data-import-batch${unique?'':' disabled'}>批量确认匹配（${unique}）</button><span class="caption">批量处理单一候选；确认后保留原档案。</span></div><div class="import-match-list">${rows.map(row=>`<section class="import-match-card" data-match-row="${attr(row.source_key)}"><div class="import-match-heading"><strong>${e(row.name||'待补姓名')}</strong><small>${e(row.sheet)} · 第 ${row.line} 行</small><span class="badge amber">待确认</span></div><p class="caption">${e(row.warning||'请选择对应档案，或作为新人员导入。')}</p>${(row.matches||[]).map((candidate,i)=>`<label class="import-match-candidate"><input type="radio" name="match-${attr(row.source_key)}" value="${attr(candidate.target)}"${row.matches.length===1?' checked':''}><span><strong>${e(candidate.name)} <small>${e(candidate.source)}</small></strong><small>${e([genderName(candidate.gender),levelName(candidate.level),candidate.phone_masked].filter(Boolean).join(' · '))}</small><small>匹配依据：${candidate.evidence.map(item=>`${e(item.label)}「${e(item.value)}」`).join('、')}</small></span></label>`).join('')}<div class="actions"><button class="btn" data-import-new="${attr(row.source_key)}">不匹配，新增</button><button class="btn primary" data-import-match="${attr(row.source_key)}"${row.matches?.length?'':' disabled'}>确认匹配</button></div></section>`).join('')}</div>`;
}
function bindImportCommit(form,view){
  bindForm('member-import-commit',async(_data,commitForm,context)=>{
    if(!importPreviewCanCommit(state.page.importPreview))throw new Error('请先完成重复人员核对。');
    const token=state.page.importPreview.token;
    const response=await api('/api/member-imports/commit',{method:'POST',body:{token}});
    if(context!==state.epoch||view!==state.viewEpoch||!commitForm.isConnected||state.page.importPreview?.token!==token)return;
    state.page.importPreview=null;$('#member-import-preview').innerHTML=`<div class="panel-body import-complete">${icon('check')}<div><h2>${response.already_committed?'这份名册已导入':'导入完成'}</h2><p>新增 ${nf(response.imported)} 人，保留 ${nf(response.skipped)} 条已有或重复记录。</p></div>${btn('继续导入','imports-reset','small')}</div>`;
    form.reset();await refreshImportRecords();
  });
}
async function resolveImportMatches(choices){
  const preview=state.page.importPreview,view=state.viewEpoch,epoch=state.epoch;
  if(!preview?.token||!choices.length)throw new Error('请选择匹配的人员档案。');
  const area=$('#member-import-preview');if(area.dataset.busy==='true')return;area.dataset.busy='true';
  try{
    const result=await api('/api/member-imports/matches',{method:'POST',body:{token:preview.token,choices}});
    if(view!==state.viewEpoch||epoch!==state.epoch||!area.isConnected||state.page.importPreview?.token!==preview.token)return;
    state.page.importPreview=result;area.innerHTML=importPreviewMarkup(result);bindImportCommit($('#member-import-upload'),view);
    toast(result.match_count?`已确认，剩余 ${result.match_count} 人待核对。`:'匹配已核对，请确认导入。');
  }finally{if(area.isConnected)area.dataset.busy='false';}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-import-match],[data-import-new],[data-import-batch]');if(!button)return;
  event.preventDefault();
  let choices=[];
  if(button.hasAttribute('data-import-batch')){
    choices=(state.page.importPreview?.rows||[]).filter(r=>r.status==='match'&&r.matches?.length===1).map(r=>({source_key:r.source_key,target:r.matches[0].target}));
  }else{
    const source_key=button.dataset.importMatch||button.dataset.importNew;
    const target=button.hasAttribute('data-import-new')?'new':button.closest('[data-match-row]').querySelector('input:checked')?.value;
    if(!target){toast('请选择对应档案。','error');return;}
    choices=[{source_key,target}];
  }
  resolveImportMatches(choices).catch(error=>toast(error.message,'error'));
});
