/* Venue reservations and holiday scheduling. Business validation is on the server. */
'use strict';
let availabilityRowSerial=0;
function setRegistrationSessions(rows,selected){
  const field=$('#f-session_id');if(!field)return;
  field.innerHTML=rows.map(s=>`<option value="${attr(s.id)}"${(s.occupancies?.length||s.status==='ended'||s.can_enroll===false)?' disabled':''}>${e(s.name)} ${e(s.start)}–${e(s.end)}${s.status==='ended'?' · 已结束':s.occupancies?.length?' · 已占用，不可报名':''}</option>`).join('');
  const available=rows.filter(s=>!s.occupancies?.length&&s.status!=='ended'&&s.can_enroll!==false);
  field.value=String(available.some(s=>String(s.id)===String(selected))?selected:available[0]?.id||'');
  const button=$('#registration-form [type=submit]');if(button)button.disabled=!available.length;
}
function bookingBadge(s){return s.occupancies?.length?`<span class="badge red">已占用 · 不可报名</span><span class="occupancy-reason">${e(s.booking_notice)}</span>`:'';}
function intervalRow(row={}){
  const serial=++availabilityRowSerial;
  return `<div class="form-grid reservation-row">${field('占用日期','reserve_date',row.date||datePlus(7),'date','required')}${field('开始时间','reserve_start',row.start||'17:30','time','required')}${field('结束时间','reserve_end',row.end||'20:30','time','required')}<div class="actions">${btn('移除此时段','reservation-row-remove','small danger')}</div></div>`.replace(/(id|for)="f-/g,`$1="reserve-${serial}-`);
}
function readIntervals(root){return $$('.reservation-row',root).map(row=>({date:$('[name=reserve_date]',row).value,start:$('[name=reserve_start]',row).value,end:$('[name=reserve_end]',row).value}));}
async function renderOccupancies(epoch){
  const first=today(),last=datePlus(30);
  $('#main').innerHTML=head('场地占用','为比赛、训练、维修等预留场地。重叠日常场次暂停新报名，已有名单保留。',btn('登记占用','occupancy-new','primary'))+panel('占用安排',`<form id="occupancy-filter" class="toolbar">${field('开始日期','start_date',first,'date','required')}${field('截止日期','end_date',last,'date','required')}<label class="check-label"><input type="checkbox" name="history">包含已释放</label><button class="btn" type="submit">查询</button><div class="inline-error" role="alert"></div></form><div id="occupancy-list">${loading()}</div>`);
  async function load(data){const rows=await api(venueQuery('/api/occupancies',{start_date:data.get('start_date'),end_date:data.get('end_date'),history:data.has('history')}));if(!isCurrent(epoch)||!$('#occupancy-list'))return;state.page.occupancies=rows;$('#occupancy-list').innerHTML=rows.length?`<div class="table-wrap"><table><thead><tr><th>用途</th><th>日期</th><th>时间</th><th>来源 / 状态</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="name">${e(r.title)}</td><td>${e(r.date)}</td><td class="nowrap">${e(r.start)}–${e(r.end)}</td><td>${r.kind==='event'?'大型赛事':'其他活动'} · ${r.status==='active'?'已占用':'已释放'}</td><td>${r.kind==='event'?btn('打开赛事','occupancy-event','small',`data-id="${attr(r.event_id)}"`):r.status==='active'?btn('编辑','occupancy-edit','small',`data-id="${attr(r.id)}"`)+btn('释放','occupancy-release','small danger',`data-id="${attr(r.id)}"`):'—'}</td></tr>`).join('')}</tbody></table></div>`:empty('所选日期没有占用','登记后，日历和相关机器人会同步显示。');if(typeof enhanceMobileTables==='function')enhanceMobileTables();}
  bindForm('occupancy-filter',load);await load(new FormData($('#occupancy-filter')));
}
async function occupancyDialog(id){
  const row=state.page.occupancies?.find(r=>r.id===Number(id))||{};
  openModal(id?'编辑场地占用':'登记场地占用',`<form id="occupancy-form"><div class="form-grid">${field('用途名称','title',row.title||'','text','required maxlength="80" placeholder="例如：校队训练"')}${field('日期','date',row.date||today(),'date','required')}${field('开始时间','start',row.start||'17:30','time','required')}${field('结束时间','end',row.end||'20:30','time','required')}</div><p class="caption">大型赛事请在赛事页面预留；这里登记其他活动。</p>${formFooter('保存占用')}</form>`);
  bindForm('occupancy-form',async(data,_form,context)=>{const result=await api(id?`/api/occupancies/${id}`:'/api/occupancies',{method:id?'PATCH':'POST',body:{venue_id:num(state.venueId),title:data.get('title').trim(),date:data.get('date'),start:data.get('start'),end:data.get('end'),...(id?{expected_version:row.version}:{})}});if(context!==state.epoch)return;closeModal();activityChanged();toast(`占用已保存，影响 ${result.affected_sessions.length} 个日常场次。`);await renderRoute();});
}
function holidaySlotRow(row={},index=0){
  const serial=++availabilityRowSerial;
  return `<div class="holiday-slot form-grid" data-slot-key="${attr(row.slot_key||'')}">${field('场次名称','name',row.name||`节假日第${index+1}场`,'text','required maxlength="60"')}${field('开始','start',row.start||'18:00','time','required')}${field('结束','end',row.end||'20:00','time','required')}${field('正式名额','capacity',row.capacity??8,'number','required min="1" max="2000"')}${field('替补名额','substitute_capacity',row.substitute_capacity??4,'number','required min="0" max="2000"')}<div class="actions">${btn('移除此场','holiday-row-remove','small danger')}</div></div>`.replace(/(id|for)="f-/g,`$1="holiday-${serial}-`);
}
function holidayOverrideRow(row={}){
  const serial=++availabilityRowSerial;
  return `<div class="holiday-override form-grid">${field('日期','override_date',row.date||datePlus(1),'date','required')}${selectfield('采用场次','override_kind',[['holiday','节假日场次'],['workday','工作日场次'],['weekend','周末场次']],row.kind||'holiday')}${field('备注','override_name',row.name||'','text','maxlength="50"')}<div class="actions">${btn('移除日期','holiday-override-remove','small danger')}</div></div>`.replace(/(id|for)="f-/g,`$1="override-${serial}-`);
}
async function renderHolidays(epoch){return renderHolidayPlans(epoch);}
async function eventReservationsDialog(){
  const event=state.page.tournament;if(!event||event.kind!=='large')return;
  openModal('赛事场地与占用时段',`<form id="event-reservations-form"><p><strong>${e(state.venues.find(v=>v.id===event.venue_id)?.name||`场地${event.venue_id}`)}</strong> · ${e(event.title)}</p><div id="reservation-rows">${(event.options?.reservations||[]).map(intervalRow).join('')}</div><div class="actions">${btn('增加占用时段','reservation-row-add')}</div><p class="caption">可以分多天预留。保存后重叠的日常场次暂停新报名；关闭或归档赛事后释放。</p>${formFooter('保存赛事占用')}</form>`,true);
  bindForm('event-reservations-form',async(_data,form,context)=>{await api(`/api/tournaments/${event.id}`,{method:'PATCH',body:{expected_version:event.version,options:{reservations:readIntervals(form)}}});if(context===state.epoch){closeModal();toast('赛事占用已保存。');await openTournament(event.id,'organization');}});
}
function eventReservationSummary(event){if(event.kind!=='large')return '';const rows=event.options?.reservations||[];return `<section class="config-section"><h3>场地与占用时间</h3><p>${e(state.venues.find(v=>v.id===event.venue_id)?.name||`场地${event.venue_id}`)}</p>${rows.length?`<ul>${rows.map(r=>`<li>${e(r.date)} ${e(r.start)}–${e(r.end)}</li>`).join('')}</ul>`:'<p class="notice warning">尚未预留场地，请配置比赛占用时段。</p>'}${!['archived','cancelled'].includes(event.status)?btn('配置赛事占用','event-reservations','small primary'):''}</section>`;}
document.addEventListener('click',async event=>{
  const target=event.target.closest('[data-action]');if(!target)return;const action=target.dataset.action;
  if(!['occupancy-new','occupancy-edit','occupancy-release','occupancy-event','holiday-row-add','holiday-row-remove','holiday-override-add','holiday-override-remove','event-reservations','reservation-row-add','reservation-row-remove'].includes(action))return;
  event.preventDefault();
  try{
    if(action==='occupancy-new'||action==='occupancy-edit')await occupancyDialog(target.dataset.id);
    else if(action==='occupancy-event')await openTournament(target.dataset.id,'organization');
    else if(action==='occupancy-release'){
      const row=state.page.occupancies.find(r=>r.id===Number(target.dataset.id));
      openModal('释放场地占用',`<form id="release-occupancy-form"><p>${e(row.title)} · ${e(row.date)} ${e(row.start)}–${e(row.end)}</p><p>释放后，未被其他安排占用的场次恢复报名。</p>${formFooter('确认释放')}</form>`);
      bindForm('release-occupancy-form',async(_data,_form,context)=>{await api(`/api/occupancies/${row.id}/release`,{method:'POST',body:{expected_version:row.version}});if(context===state.epoch){closeModal();activityChanged();toast('占用已释放。');await renderRoute();}});
    }else if(action==='holiday-row-add')$('#holiday-rows').insertAdjacentHTML('beforeend',holidaySlotRow({},$$('.holiday-slot').length));
    else if(action==='holiday-row-remove')target.closest('.holiday-slot').remove();
    else if(action==='holiday-override-add')$('#holiday-overrides').insertAdjacentHTML('beforeend',holidayOverrideRow());
    else if(action==='holiday-override-remove')target.closest('.holiday-override').remove();
    else if(action==='event-reservations')await eventReservationsDialog();
    else if(action==='reservation-row-add')$('#reservation-rows').insertAdjacentHTML('beforeend',intervalRow({date:state.page.tournament?.date}));
    else if(action==='reservation-row-remove')target.closest('.reservation-row').remove();
    if(typeof enhanceMobileTables==='function')enhanceMobileTables();
  }catch(error){showError(error);}
});
