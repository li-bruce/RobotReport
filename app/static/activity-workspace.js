/* Date → venue → session. Rosters are fetched only when a session is opened. */
'use strict';
const localVenueRoutes=['dashboard','occupancies','holidays','daily','casual','stats','tournaments'];
const activitySelection={month:'',day:'',venue:'',status:'',search:'',page:0};
function localVenueOptions(selected,all=false){return (all?'<option value="">全部场地</option>':'')+state.venues.map(v=>`<option value="${attr(v.id)}"${String(v.id)===String(selected)?' selected':''}>${e(v.name)}${v.active?'':'（停用）'}</option>`).join('');}
function installLocalVenuePicker(){
  if(!localVenueRoutes.includes(state.route))return;
  $('.page-heading')?.insertAdjacentHTML('afterend',`<div class="local-venue-bar"><label for="page-venue">查看场地</label><select id="page-venue" class="control">${localVenueOptions(state.venueId)}</select></div>`);
  $('#page-venue')?.addEventListener('change',async ev=>{state.venueSelections[state.route]=ev.target.value;resetContext();await renderRoute();});
}
function activityChanged(){try{localStorage.setItem('robort-activity-changed',String(Date.now()));}catch{/* The current page still refreshes when browser storage is unavailable. */}}
async function refreshActivityCalendar(){
  if(!state.authed||state.route!=='calendar'||$('#modal')?.open)return;
  const epoch=state.viewEpoch,month=state.page.month;
  if(!month)return;
  const result=await api(query('/api/activity/calendar',{month}));
  if(!isCurrent(epoch)||state.page.month!==month)return;
  state.page.calendar=result;$('#activity-month-grid').innerHTML=calendarGrid(result.days,month);
  if(activitySelection.day?.startsWith(month))await activityOpenDay(activitySelection.day);
}
async function activityCalendar(epoch,month=activitySelection.month||today().slice(0,7)){
  const result=await api(query('/api/activity/calendar',{month}));if(!isCurrent(epoch))return;
  state.page={month,calendar:result};activitySelection.month=month;
  const selected=activitySelection.day?.startsWith(month)?activitySelection.day:(today().startsWith(month)?today():month+'-01');
  $('#main').innerHTML=head('报名日历','选日期，再查看各场地的场次与名单。',btn('刷新','activity-refresh')+btn('添加报名','registration-new','primary'))+
    `<section class="panel"><div class="panel-head"><div class="actions">${btn('‹','calendar-prev','small','aria-label="上个月"')}<h2>${e(month.replace('-',' 年 '))} 月</h2>${btn('›','calendar-next','small','aria-label="下个月"')}</div><div class="actions"><label class="sr-only" for="calendar-month">选择月份</label><input id="calendar-month" type="month" class="control" value="${attr(month)}">${btn('本月','calendar-today','small')}</div></div><div id="activity-month-grid">${calendarGrid(result.days,month)}</div></section><div id="calendar-detail" class="calendar-detail"></div>`;
  $('#calendar-month').onchange=ev=>{if(ev.target.value)activityCalendar(++state.viewEpoch,ev.target.value).then(()=>$('#main').insertAdjacentHTML('afterbegin',sectionTabs('calendar'))).catch(showError);};
  await activityOpenDay(selected);
}
async function activityOpenDay(date,session=''){
  activitySelection.day=date;
  if(!$('#calendar-detail'))await navigate('calendar');
  if(date.slice(0,7)!==state.page.month){
    activitySelection.day=date;const requestEpoch=++state.viewEpoch;
    await activityCalendar(requestEpoch,date.slice(0,7));
    if(isCurrent(requestEpoch))$('#main').insertAdjacentHTML('afterbegin',sectionTabs('calendar'));
    return;
  }
  const target=$('#calendar-detail'),epoch=state.viewEpoch,request=Symbol();if(!target)return;
  state.page.dayRequest=request;const previous=state.page.day;
  const expanded=$$('.activity-session[open]',target).map(el=>el.dataset.session);
  if(previous!==date){activitySelection.page=0;target.innerHTML=loading('正在读取场次');}
  const result=await api(query('/api/activity/day',{date}));
  if(!isCurrent(epoch)||!target.isConnected||state.page.dayRequest!==request)return;
  state.page.day=date;activitySelection.day=date;state.page.dayData=result;state.venues=result.venues.map(({sessions,...venue})=>venue);
  state.page.registrations={sessions:result.venues.flatMap(v=>v.sessions)};
  state.page.sessionId=session;state.page.expandedSessions=session?[String(session)]:expanded;
  if(session){activitySelection.venue='';activitySelection.status='';activitySelection.search='';const index=state.page.registrations.sessions.findIndex(s=>String(s.id)===String(session));activitySelection.page=Math.max(0,Math.floor(index/12));}
  $$('.calendar-day').forEach(el=>el.classList.toggle('selected',el.dataset.date===date));
  target.innerHTML=`<div class="activity-day-heading"><h2>${e(date)} <span class="muted">${result.session_count} 场</span></h2><label class="sr-only" for="activity-date">选择日期</label><input class="control" id="activity-date" type="date" value="${attr(date)}"></div><div class="activity-filters"><div class="field"><label for="day-venue">场地</label><select id="day-venue">${localVenueOptions(activitySelection.venue,true)}</select></div><div class="field"><label for="day-status">场次情况</label><select id="day-status">${[['','全部场次'],['available','可报名'],['occupied','已占用'],['ended','已结束']].map(([v,label])=>`<option value="${v}"${activitySelection.status===v?' selected':''}>${label}</option>`).join('')}</select></div><div class="field grow"><label for="day-search">查找场次</label><input id="day-search" value="${attr(activitySelection.search)}" placeholder="场次名称或开始时间"></div></div><div class="calendar-event-list">${(state.page.calendar?.days?.find(d=>d.date===date)?.events||[]).map(ev=>`<span class="badge blue">◆ ${e(ev.title)}</span>`).join('')}</div><div id="activity-day-list"></div>`;
  $('#activity-date').onchange=ev=>{if(ev.target.value)openDay(ev.target.value).catch(showError);};
  for(const [id,key] of [['day-venue','venue'],['day-status','status'],['day-search','search']])document.getElementById(id).addEventListener('input',ev=>{activitySelection[key]=ev.target.value;activitySelection.page=0;renderActivityDay();});
  renderActivityDay();
}
function renderActivityDay(){
  const data=state.page.dayData;if(!data)return;
  const q=activitySelection.search.trim().toLowerCase();
  const all=data.venues.flatMap(v=>v.sessions.map(s=>({...s,venue:v}))).filter(s=>(!activitySelection.venue||String(s.venue_id)===activitySelection.venue)&&(!q||(s.name+' '+s.start+' '+s.end+' '+s.venue.name).toLowerCase().includes(q))&&(!activitySelection.status||(activitySelection.status==='available'?s.can_enroll:activitySelection.status==='occupied'?s.occupancies.length:s.status==='ended')));
  const pages=Math.max(1,Math.ceil(all.length/12));activitySelection.page=Math.min(activitySelection.page,pages-1);
  const visible=all.slice(activitySelection.page*12,activitySelection.page*12+12);
  const grouped=data.venues.map(v=>({venue:v,rows:visible.filter(s=>s.venue_id===v.id)})).filter(v=>v.rows.length);
  $('#activity-day-list').innerHTML=grouped.map(({venue,rows})=>`<section class="activity-venue"><div class="activity-venue-heading"><div><h3>${e(venue.name)}</h3><span class="muted">${venue.active?'': '已停用 · '}${data.venues.find(v=>v.id===venue.id).sessions.length} 场</span></div>${btn('导出当日','registration-export','small',`data-date="${attr(data.date)}" data-venue="${attr(venue.id)}"`)}</div><div class="activity-session-list">${rows.map(s=>`<details class="activity-session" data-session="${s.id}" data-venue="${s.venue_id}"><summary><span class="activity-session-time">${e(s.start)}–${e(s.end)}</span><span class="activity-session-name"><strong>${e(s.name)}</strong><small>${e(eligibilityLabel(s))}</small></span><span class="activity-session-count">${s.count} 人<span>正式 ${Math.min(s.count,s.capacity)}/${s.capacity} · 替补 ${Math.min(Math.max(0,s.count-s.capacity),s.substitute_capacity)}/${s.substitute_capacity}${s.count>s.capacity+s.substitute_capacity?` · 候补 ${s.count-s.capacity-s.substitute_capacity}`:''}</span></span><span class="badge ${s.status==='ended'?'':s.occupancies.length?'red':s.status==='ongoing'?'blue':'green'}">${s.status==='ended'?'已结束':!s.venue.active?'已停用':s.occupancies.length?'已占用 · 不可报名':s.status==='ongoing'?'进行中':'可报名'}</span><span class="activity-chevron" aria-hidden="true">⌄</span></summary>${s.occupancies.length?`<div class="activity-blocked">${e(s.booking_notice)}</div>`:''}<div class="activity-roster"></div></details>`).join('')}</div></section>`).join('')||(empty('没有符合条件的场次','可切换日期或筛选条件。',btn('设置活动时段','go','small','data-route="sessions"')));
  if(all.length)$('#activity-day-list').insertAdjacentHTML('beforeend',`<div class="activity-pagination"><span>共 ${all.length} 场 · 第 ${activitySelection.page+1} / ${pages} 页</span><div class="actions">${btn('上一页','activity-page','small',`data-delta="-1"${!activitySelection.page?' disabled':''}`)}${btn('下一页','activity-page','small',`data-delta="1"${activitySelection.page===pages-1?' disabled':''}`)}</div></div>`);
  $$('.activity-session').forEach(el=>{el.addEventListener('toggle',()=>{if(el.open&&!el.dataset.loaded)loadActivityRoster(el).catch(showError);});if(state.page.expandedSessions?.includes(el.dataset.session))el.open=true;});
}
async function loadActivityRoster(el){
  if(el.dataset.loaded==='loading')return;
  el.dataset.loaded='loading';const target=$('.activity-roster',el),epoch=state.viewEpoch,day=state.page.day;
  target.innerHTML=loading('正在读取本场名单');
  try{
    const result=await api(query('/api/registrations',{date:day,venue_id:el.dataset.venue,session_id:el.dataset.session}));
    if(!isCurrent(epoch)||!el.isConnected||state.page.day!==day)return;
    const s=result.sessions[0];el.dataset.loaded='true';
    target.innerHTML=`<div class="activity-roster-actions">${s.can_enroll?btn('添加报名','registration-new','primary small',`data-date="${attr(day)}" data-session="${s.id}" data-venue="${s.venue_id}"`):''}${s.can_manage?btn('报名设置','session-constraints','small',`data-id="${s.id}"`):'<span class="muted">历史记录 · 只读</span>'}${btn('导出本场','registration-export','small',`data-date="${attr(day)}" data-session="${s.id}" data-venue="${s.venue_id}"`)}</div>${registrationTable(result.registrations)}`;
    if(typeof enhanceMobileTables==='function')enhanceMobileTables();
  }catch(error){delete el.dataset.loaded;if(el.isConnected)target.innerHTML=`<p class="notice warning">${e(error.message)}</p>${btn('重试','activity-roster-retry','small')}`;}
}
async function activityRegistration(date,session='',venue=''){
  const epoch=state.epoch;openModal('添加报名',loading());
  const [data,memberData]=await Promise.all([api(query('/api/activity/day',{date})),api('/api/members?status=approved')]);if(epoch!==state.epoch)return;
  let dayData=data;const members=asList(memberData,'members');
  const selected=venue||data.venues.find(v=>v.sessions.some(s=>String(s.id)===String(session)))?.id||activitySelection.venue||state.venues.find(v=>v.active)?.id;
  openModal('添加报名',`<form id="registration-form"><div class="form-grid">${field('活动日期','date',date,'date','required min="'+today()+'"')}${selectfield('场地','venue_id',state.venues.filter(v=>v.active).map(v=>[v.id,v.name]),selected,'required')}${selectfield('场次','session_id',[],session,'required')}${selectfield('报名会员','user_id',members.map(m=>[m.user_id,`${memberName(m)} · ${levelName(m.level)}`]),'','required')}${field('本次新增同行人数','guests',0,'number','min="0" max="20" required')}<div class="full">${textfield('同行姓名','guest_names','','maxlength="1600" placeholder="例如：张三，李四"','填写姓名则按姓名追加。')}</div></div>${!members.length?'<p class="notice warning">请先审核会员。</p>':''}<p id="registration-session-hint" class="caption"></p>${formFooter('添加报名')}</form>`);
  function update(selectedId){const rows=dayData.venues.find(v=>String(v.id)===$('#f-venue_id').value)?.sessions||[];setRegistrationSessions(rows,selectedId);$('#registration-session-hint').textContent=rows.some(s=>s.can_enroll)?'':'所选日期和场地没有可报名场次。';}
  let request=0;$('#f-date').onchange=async ev=>{const id=++request;$('#registration-form [type=submit]').disabled=true;try{const next=await api(query('/api/activity/day',{date:ev.target.value}));if(epoch!==state.epoch||!$('#registration-form')||id!==request)return;dayData=next;update();}catch(error){showError(error);}};
  $('#f-venue_id').onchange=()=>update();update(session);
  bindForm('registration-form',async(values,_form,context)=>{const member=members.find(m=>String(m.user_id)===values.get('user_id'));if(!member)throw new Error('请选择已审核会员。');const names=guestNames(values.get('guest_names'));await api('/api/registrations',{method:'POST',body:{date:values.get('date'),session_id:values.get('session_id'),venue_id:num(values.get('venue_id')),user_id:member.user_id,name:member.name,guests:names.length||num(values.get('guests')),...(names.length?{guest_names:names}:{})}});if(context!==state.epoch)return;closeModal();toast('报名已记录。');activityChanged();if(state.route==='calendar'){await refreshActivityCalendar();await openDay(values.get('date'),values.get('session_id'));}else await renderRoute();});
}

async function activitySessions(epoch){return scheduleWorkspace(epoch);}
function slotWeekdays(rule){return rule.weekdays.map(n=>'周'+['一','二','三','四','五','六','日'][n]).join('、');}
function renderActivitySlots(){
  const rows=state.page.slotPlan.rules.map((r,index)=>({...r,index})).filter(r=>!state.slotVenueFilter||r.venue_ids.map(String).includes(String(state.slotVenueFilter)));
  $('#activity-slot-list').innerHTML=rows.length?`<div class="activity-rules">${rows.map(r=>`<article class="activity-rule"><div><span class="activity-session-time">${e(r.start)}–${e(r.end)}</span><h3>${e(r.name)}</h3><p>${e(slotWeekdays(r))}${r.skip_holidays?' · 跳过节假日':' · 节假日照常'} · 正式 ${r.capacity} / 替补 ${r.substitute_capacity}</p><div class="activity-rule-venues">${r.venue_ids.map(id=>`<span class="badge outline">${e(state.venues.find(v=>v.id===id)?.name||'场地'+id)}</span>`).join('')}</div></div><div class="actions">${btn('编辑','activity-slot-edit','small',`data-index="${r.index}"`)}${btn('移除','activity-slot-delete','small danger',`data-index="${r.index}"`)}</div></article>`).join('')}</div>`:empty('尚未设置活动时段','新增时段后选择适用场地。',btn('新增时段','activity-slot-new','primary'));
}
function activitySlotDialog(index){
  const plan=state.page.slotPlan,rule=Number.isInteger(index)?plan.rules[index]:null;
  openModal(rule?'编辑活动时段':'新增活动时段',`<form id="activity-slot-form">${slotRow(rule||{},plan.rules.length).replace(/<button[^>]*data-action="slot-remove"[^>]*>.*?<\/button>/,'')}<label class="check-label skip-holiday"><input type="checkbox" name="skip_holidays"${(rule?.skip_holidays??true)?' checked':''}>跳过节假日</label><fieldset class="field activity-venue-checks"><legend>应用于场地</legend><div class="checks">${state.venues.map(v=>`<label class="check-label"><input type="checkbox" name="venue_ids" value="${v.id}"${(rule?.venue_ids||[Number(state.slotVenueFilter)||state.venues.find(v=>v.active)?.id]).includes(v.id)?' checked':''}${!v.active?' disabled':''}>${e(v.name)}${v.active?'':'（停用）'}</label>`).join('')}</div></fieldset>${formFooter('保存时段')}</form>`,true);
  bindForm('activity-slot-form',async(data,form,context)=>{const normalized={name:data.get('name').trim(),weekdays:data.getAll('weekday').map(Number),start:data.get('start'),end:data.get('end'),capacity:num(data.get('capacity')),substitute_capacity:num(data.get('substitute_capacity')),venue_ids:data.getAll('venue_ids').map(Number),skip_holidays:data.has('skip_holidays'),bindings:rule?.bindings||{}};for(const vid of rule?.venue_ids||[])if(!state.venues.find(v=>v.id===vid)?.active)normalized.venue_ids.push(vid);if(!normalized.venue_ids.length)throw new Error('请选择适用场地。');const rules=structuredClone(plan.rules);if(rule)rules[index]=normalized;else rules.push(normalized);await api('/api/activity/slot-plan',{method:'PUT',body:{revision:plan.revision,rules}});if(context!==state.epoch)return;activityChanged();closeModal();toast('活动时段已保存，报名日历已同步。');await renderRoute();});
}
function removeActivitySlot(index){
  const plan=state.page.slotPlan,rule=plan.rules[index];
  confirmAction('移除活动时段',`移除“${rule.name}”在所选场地的每周规则。历史记录保留；有预约或赛事锁定时会提示处理。`,'移除时段',async epoch=>{await api('/api/activity/slot-plan',{method:'PUT',body:{revision:plan.revision,rules:plan.rules.filter((_,i)=>i!==index)}});if(epoch!==state.epoch)return;closeModal();activityChanged();toast('时段规则已移除。');await renderRoute();});
}
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action]');if(!el||el.disabled)return;
  try{switch(el.dataset.action){
    case 'activity-page':activitySelection.page+=Number(el.dataset.delta);state.page.expandedSessions=[];renderActivityDay();$('#calendar-detail').scrollIntoView({block:'start'});break;
    case 'activity-refresh':await refreshActivityCalendar();toast('报名日历已更新。');break;
    case 'activity-roster-retry':await loadActivityRoster(el.closest('.activity-session'));break;
    case 'activity-slot-new':activitySlotDialog();break;
    case 'activity-slot-edit':activitySlotDialog(Number(el.dataset.index));break;
    case 'activity-slot-delete':removeActivitySlot(Number(el.dataset.index));break;
  }}catch(error){showError(error);}
});
window.addEventListener('storage',event=>{if(event.key==='robort-activity-changed')refreshActivityCalendar().catch(showError);});
window.addEventListener('focus',()=>refreshActivityCalendar().catch(showError));
