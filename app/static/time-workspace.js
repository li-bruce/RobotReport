'use strict';
const broadcastKinds=[['open','当日活动提醒'],['closing','报名即将结束'],['roster','报名名单']];
const dayTypes=[['all','每天'],['workday','工作日（含调休）'],['weekend','周六、周日'],['restday','休息日（含节假日）']];
const holidayTypes=[['any','不限制'],['exclude','跳过节假日'],['only','仅节假日']];
const timeSelect=(label,key,options,value)=>`<label class="time-field"><span>${e(label)}</span><select data-rule-field="${attr(key)}">${options.map(([id,name])=>`<option value="${attr(id)}"${String(value)===String(id)?' selected':''}>${e(name)}</option>`).join('')}</select></label>`;
const timeInput=(label,key,value,type='text',extra='')=>`<label class="time-field"><span>${e(label)}</span><input data-rule-field="${attr(key)}" type="${attr(type)}" value="${attr(value||'')}" ${extra}></label>`;
const ruleChoices=(label,key,choices,values)=>`<fieldset class="time-choices"><legend>${e(label)}</legend><div>${choices.map(([id,name])=>`<label><input type="checkbox" data-rule-choice="${attr(key)}" value="${attr(id)}"${(values||[]).map(String).includes(String(id))?' checked':''}><span>${e(name)}</span></label>`).join('')}</div></fieldset>`;
function timeConditionFields(when={},timed=false){return `<div class="time-fields">${timeInput('开始日期','start_date',when.start_date,'date')}${timeInput('结束日期','end_date',when.end_date,'date')}${timeSelect('日期类型','day_type',dayTypes,when.day_type||'all')}${timeSelect('节假日','holidays',holidayTypes,when.holidays||'any')}${timed?timeInput('开始时间','start_time',when.start_time,'time')+timeInput('结束时间','end_time',when.end_time,'time'):''}</div>${ruleChoices('指定星期 · 不选则不限','weekdays',[...Array(7)].map((_,i)=>[i,'周'+'一二三四五六日'[i]]),when.weekdays)}`;}
function weekdaySummary(days=[]){const key=[...new Set(days)].sort().join(',');return {'':'每天','0,1,2,3,4':'周一至周五','5,6':'周末','0,1,2,3,4,5,6':'每天'}[key]||days.map(d=>'周'+'一二三四五六日'[d]).join('、');}
function timeRuleSummary(rule,mode,choices){
 const when=rule.when||{},labels=(mode==='capabilities'?rule.capabilities:rule.kinds)||[];
 const dates=when.start_date||when.end_date?(when.start_date===when.end_date?when.start_date:`${when.start_date||'即日起'} 至 ${when.end_date||'长期'}`):'';
 const kind=(dayTypes.find(x=>x[0]===when.day_type)||dayTypes[0])[1];
 const days=when.weekdays?.length?weekdaySummary(when.weekdays):'';
 const period=mode==='capabilities'?(rule.action==='open'?'开放':'关闭')+(when.start_time?` ${when.start_time}–${when.end_time}${when.end_time<when.start_time?'（次日）':''}`:' 全天'):rule.time;
 return [labels.map(id=>choices.find(c=>c[0]===id)?.[1]||id).join('、'),period,dates,kind==='每天'&&days?days:kind,kind!=='每天'?days:'',when.holidays&&when.holidays!=='any'?holidayTypes.find(x=>x[0]===when.holidays)?.[1]:''].filter(Boolean).join(' · ');
}
function bindTimeRules(root,initial,options){
  let rules=structuredClone(initial||[]);const mode=options.mode||'broadcast';
  const choices=()=>mode==='capabilities'?botCapabilityCatalog().filter(c=>options.capabilities().includes(c.id)).map(c=>[c.id,c.name]):broadcastKinds;
  const read=()=>[...root.querySelectorAll('[data-time-rule]')].map(card=>{
    const val=key=>card.querySelector(`[data-rule-field="${key}"]`)?.value||'',checked=key=>[...card.querySelectorAll(`[data-rule-choice="${key}"]:checked`)].map(el=>el.value);
    const when={start_date:val('start_date'),end_date:val('end_date'),day_type:val('day_type'),holidays:val('holidays'),weekdays:checked('weekdays').map(Number)};
    const base={id:card.dataset.timeRule,name:val('name').trim(),enabled:card.querySelector('[data-rule-field="enabled"]').checked,when};
    if(mode==='capabilities')return {...base,action:val('action'),capabilities:checked('capabilities').filter(c=>options.capabilities().includes(c)),when:{...when,start_time:val('start_time'),end_time:val('end_time')}};
    return {...base,time:val('time'),kinds:checked('kinds'),bot_ids:options.ownerId?[options.ownerId]:checked('bot_ids')};
  });
  function applyFilter(){
    if(mode!=='broadcast'||!options.botId)return;
    const selected=options.botId(),current=new Map(read().map(r=>[r.id,r]));
    let shown=0;
    root.querySelectorAll('[data-time-rule]').forEach(card=>{card.hidden=Boolean(selected&&!current.get(card.dataset.timeRule)?.bot_ids.includes(selected));if(!card.hidden)shown++;});
    const status=$('#broadcast-filter-status');if(status)status.textContent=`${shown} 条规则`;
    const empty=root.querySelector('[data-time-empty]');if(empty){empty.hidden=shown>0;empty.textContent=selected?'此机器人尚无定时播报。':'尚无定时播报。';}
  }
  function render(openId=''){
    root.innerHTML=`<div class="time-rule-list">${rules.map(rule=>`<details class="time-rule" data-time-rule="${attr(rule.id)}"${rule.id===openId?' open':''}><summary><span><strong>${e(rule.name||'新规则')}</strong><small>${e(timeRuleSummary(rule,mode,choices()))}</small></span><span class="badge ${rule.enabled?'green':''}">${rule.enabled?'启用':'停用'}</span></summary><div class="time-rule-body"><div class="time-fields">${timeInput('规则名称','name',rule.name,'text','required maxlength="80"')}${mode==='capabilities'?timeSelect('功能状态','action',[['open','仅这些时段开放'],['closed','这些时段关闭']],rule.action):timeInput('播报时间','time',rule.time,'time','required')}</div>${mode==='capabilities'?ruleChoices('功能','capabilities',choices(),rule.capabilities):ruleChoices('播报内容','kinds',broadcastKinds,rule.kinds)+(options.ownerId?'':ruleChoices('执行机器人','bot_ids',options.bots.map(b=>[b.id,b.name]),rule.bot_ids))}<details class="time-conditions"><summary>日期与${mode==='capabilities'?'时段':'星期'}条件</summary>${timeConditionFields(rule.when,mode==='capabilities')}${mode==='capabilities'?'<p class="caption">时间留空为全天；结束早于开始表示跨夜。</p>':''}</details><div class="time-rule-footer"><label class="check-label"><input type="checkbox" data-rule-field="enabled"${rule.enabled?' checked':''}>启用此规则</label><button class="btn small danger-quiet" type="button" data-remove-rule="${attr(rule.id)}">删除规则</button></div></div></details>`).join('')}</div><p class="caption" data-time-empty${rules.length?' hidden':''}>${mode==='capabilities'?'未设时间限制。':'尚无定时播报。'}</p><button type="button" class="btn small" data-add-rule>＋ 添加规则</button>`;
    applyFilter();
  }
  root.addEventListener('click',event=>{
    const add=event.target.closest('[data-add-rule]'),remove=event.target.closest('[data-remove-rule]');if(!add&&!remove)return;
    event.preventDefault();rules=read();
    if(remove){rules=rules.filter(r=>r.id!==remove.dataset.removeRule);render();return;}
    if(rules.length>=50){toast('最多50条规则。');return;}
    const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join(''),rule={id,name:'新规则',enabled:true,when:{},...(mode==='capabilities'?{action:'open',capabilities:choices().slice(0,1).map(x=>x[0])}:{time:'09:30',kinds:['open'],bot_ids:options.ownerId?[options.ownerId]:options.botId?.()?[options.botId()]:options.bots.slice(0,1).map(b=>b.id)})};
    rules.push(rule);render(id);root.querySelector(`[data-time-rule="${id}"] input`).focus();
  });
  root.addEventListener('input',event=>{const card=event.target.closest('[data-time-rule]');if(!card)return;const rule=read().find(r=>r.id===card.dataset.timeRule);const summary=card.querySelector('summary');summary.querySelector('strong').textContent=rule.name||'新规则';summary.querySelector('small').textContent=timeRuleSummary(rule,mode,choices());const badge=summary.querySelector('.badge');badge.textContent=rule.enabled?'启用':'停用';badge.classList.toggle('green',rule.enabled);});
  render();
  read.refresh=()=>{rules=read();render();};
  read.filter=applyFilter;read.replace=value=>{rules=structuredClone(value);render();};
  return read;
}
async function renderReminders(epoch){
 state.route='bots';history.replaceState(null,'','#bots');await renderBotWorkspace(epoch);
 if(isCurrent(epoch))await revealBotSettings(state.broadcastBotId,'broadcast');
}

function editImportedRecord(id){
  const row=(state.page.importRecords||[]).find(r=>String(r.id)===String(id));if(!row)return;
  openModal('补全预登记',`<form id="prereg-edit">${field('姓名','name',row.name||'','text','maxlength="80"')}${field('手机号','phone','','tel','inputmode="tel" autocomplete="tel" maxlength="32"',row.phone_masked?'已登记 '+row.phone_masked+'，不修改请留空':'补充手机号后，本人可通过机器人关联。')}${selectfield('性别','gender',[['unknown','待补齐'],['male','男'],['female','女']],row.gender||'unknown')}${selectfield('级别','level',[['','未自评'],...levelOptions],row.level??'')}${formFooter('保存')}</form>`);
  bindForm('prereg-edit',async(data,form,epoch)=>{const body={gender:data.get('gender'),level:nullableLevel(data.get('level'))};if(data.get('name').trim())body.name=data.get('name').trim();if(data.get('phone').trim())body.phone=data.get('phone').trim();const result=await api(`/api/member-imports/records/${encodeURIComponent(id)}`,{method:'PATCH',body});if(epoch===state.epoch){closeModal();toast(result.message||'预登记已保存。');await refreshImportRecords();}});
}

function importMappingMarkup(preview){const names={name:'姓名',phone:'手机号',gender:'性别',level:'级别',groups:'组别'};return `<details class="panel-body import-mappings" open><summary>已识别的列</summary>${(preview.mappings||[]).map(m=>`<p><strong>${e(m.sheet)} · 第 ${e(m.header_row)} 行表头</strong><br>${Object.entries(m.columns).map(([key,value])=>`${e(value)} → ${e(names[key])}`).join('；')}${Object.keys(m.identity_columns||{}).length?`<br>身份核对：${Object.values(m.identity_columns).map(e).join('、')}`:''}${m.ignored?.length?`<br>忽略：${m.ignored.map(e).join('、')}`:''}</p>`).join('')}${preview.skipped_sheets?.length?`<p>未使用工作表：${preview.skipped_sheets.map(e).join('、')}</p>`:''}</details>`;}
