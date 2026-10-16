/* Personal match archive; shared UI helpers are defined by app.js. */
'use strict';
function casualPeople(people){return (people||[]).map(p=>`${p.name||'未命名'}${p.kind==='guest'?'（访客）':''}`).join('、');}
function casualScores(row){return (row.scores?.length?row.scores:[[row.score_a,row.score_b]]).map(pair=>pair.join(' : ')).join(' / ');}
function casualRate(row){return `${num(row.win_rate).toFixed(1)}%`;}
function casualPairTable(title,rows){return panel(title,rows?.length?`<div class="table-wrap"><table><thead><tr><th>人员组合</th><th>对局</th><th>胜</th><th>负</th><th>胜率</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${e(casualPeople(r.people))}</td><td>${nf(r.played)}</td><td>${nf(r.wins)}</td><td>${nf(r.losses)}</td><td>${e(casualRate(r))}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无已完成对局','登记比赛结果后自动统计。'));}
async function renderCasual(epoch){
  const members=asList(await api('/api/members?status=approved'),'members');if(!isCurrent(epoch))return;
  state.page.casualMembers=members;
  $('#main').innerHTML=head('对局档案','零散对局与正式赛事统一查询；作废记录保留，统计只计算有效完赛。','','MATCH HISTORY')+
    `<section class="panel"><form id="casual-filter" class="toolbar">${selectfield('参赛会员','user_id',[['','全部人员'],...members.map(m=>[m.user_id,memberName(m)])],'')}${field('起始日期','date_from',today().slice(0,7)+'-01','date')}${field('结束日期','date_to',today(),'date')}<button class="btn primary" type="submit">查询</button><div class="inline-error" role="alert"></div></form></section><div id="casual-statistics"></div><div id="casual-history">${loading()}</div>`+
    `<details class="panel" style="margin-top:20px;padding:20px"><summary>群内怎样记录一局</summary><p class="caption">先 @机器人 发送“记录”查看示例。记录者必须是参赛者；姓名重名时按提示选择。</p><p><code>记录 单打 我 VS 张三 21:19</code></p><p><code>记录 混双 我、李四 VS 张三、王五 21-19</code></p><p class="caption">可发送“我的对战历史”“我的对战统计”。访客加“访客:”前缀，系统不会将其算作同名会员。</p></details>`;
  bindForm('casual-filter',async()=>loadCasualHistory(epoch));await loadCasualHistory(epoch);
}
async function loadCasualHistory(epoch=state.viewEpoch){
  const form=$('#casual-filter');if(!form)return;const fields=new FormData(form),user_id=fields.get('user_id')||undefined;
  const filters={user_id,date_from:fields.get('date_from')||undefined,date_to:fields.get('date_to')||undefined,venue_id:state.venueId};
  if(filters.date_from&&filters.date_to&&filters.date_from>filters.date_to)throw new Error('起始日期不能晚于结束日期。');
  const seq=state.page.casualRequest=(state.page.casualRequest||0)+1;
  const [history,stats]=await Promise.all([api(query('/api/casual/history',{...filters,include_void:true})),user_id?api(query('/api/casual/statistics',filters)):Promise.resolve(null)]);
  if(!isCurrent(epoch)||seq!==state.page.casualRequest||!$('#casual-history'))return;
  const rows=asList(history,'matches');state.page.casualMatches=rows;
  $('#casual-statistics').innerHTML=stats?metrics([['已完成对局',stats.played,'shuttle'],['获胜',stats.wins,'trophy'],['失利',stats.losses,'activity'],['胜率',casualRate(stats),'chart']])+panel('按项目统计',`<div class="table-wrap"><table><thead><tr><th>项目</th><th>对局</th><th>胜</th><th>负</th><th>胜率</th></tr></thead><tbody>${(stats.by_discipline||[]).map(r=>`<tr><td>${e(r.label||disciplineName(r.discipline))}</td><td>${nf(r.played)}</td><td>${nf(r.wins)}</td><td>${nf(r.losses)}</td><td>${casualRate(r)}</td></tr>`).join('')}</tbody></table></div>`)+`<div class="grid-2" style="margin-top:20px">${casualPairTable('固定搭档组合',stats.partners)}${casualPairTable('对各位对手的胜率',stats.opponents)}</div><div style="margin-top:20px">${casualPairTable('对对手组合的胜率',stats.opponent_teams)}</div>`:'';
  $('#casual-history').innerHTML=panel(`对局记录 · ${rows.length} 条`,rows.length?`<div class="table-wrap"><table><thead><tr><th>日期 / 场次</th><th>项目 / 来源</th><th>队伍 A</th><th>比分</th><th>队伍 B</th><th>状态</th><th></th></tr></thead><tbody>${rows.map((r,i)=>`<tr><td>${e(r.date)}<span class="sub">${e(r.venue_name||'')}${r.session?` · ${e(r.session.name||'')} ${e(r.session.start||'')}–${e(r.session.end||'')}`:''}</span></td><td>${e(disciplineName(r.discipline,r.doubles_category))}<span class="sub">${r.source==='tournament'?e(r.title||'正式赛事'):'零散对局'}</span></td><td>${e(casualPeople(r.team_a))}</td><td class="mono">${e(casualScores(r))}</td><td>${e(casualPeople(r.team_b))}</td><td><span class="badge ${r.status==='void'?'red':'green'}">${r.status==='void'?'已作废':'已完成'}</span></td><td>${r.source==='casual'&&r.can_edit&&r.status!=='void'?`<button class="btn small" data-casual-edit="${i}">修正比分</button> <button class="btn small danger" data-casual-void="${i}">作废</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无对局记录','可在群内发送“记录”登记当天单打或双打结果。'));
  $$('[data-casual-edit]').forEach(el=>el.onclick=()=>casualEdit(rows[Number(el.dataset.casualEdit)]));
  $$('[data-casual-void]').forEach(el=>el.onclick=()=>casualVoid(rows[Number(el.dataset.casualVoid)]));
}
function casualEdit(row){
  openModal('修正零散对局比分',`<form id="casual-edit-form"><p>${e(casualPeople(row.team_a))} VS ${e(casualPeople(row.team_b))}</p><div class="form-grid">${field('队伍 A 得分','score_a',row.score_a,'number','min="0" max="999" required')}${field('队伍 B 得分','score_b',row.score_b,'number','min="0" max="999" required')}</div>${formFooter('保存修正')}</form>`);
  bindForm('casual-edit-form',async(data,_form,epoch)=>{await api(`/api/casual/matches/${encodeURIComponent(row.source_id||row.id)}`,{method:'PATCH',body:{score_a:Number(data.get('score_a')),score_b:Number(data.get('score_b')),expected_version:row.version}});if(epoch===state.epoch){closeModal();toast('比分已修正，统计已更新。');await loadCasualHistory();}});
}
function casualVoid(row){confirmAction('作废零散对局',`${casualPeople(row.team_a)} VS ${casualPeople(row.team_b)}，${casualScores(row)}。作废后保留记录，并从统计中排除。`,'确认作废',async()=>{await api(`/api/casual/matches/${encodeURIComponent(row.source_id||row.id)}/void`,{method:'POST',body:{expected_version:row.version}});closeModal();toast('对局已作废。');await loadCasualHistory();});}
