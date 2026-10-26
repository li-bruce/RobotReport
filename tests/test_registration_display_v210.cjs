/* User-facing rosters and exact-entry admin cancellation, without browser dependencies. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/interface.js'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8');
const content={innerHTML:'',setAttribute(){}},main={innerHTML:''},cards={innerHTML:''};
const handlers={},calls=[];
const context=vm.createContext({URL,URLSearchParams,Intl,Date,Map,Set,Symbol,Number,String,Array,JSON,AbortController,FormData,
  document:{querySelector:selector=>({'#tournament-content':content,'#main':main,'#tournament-list':cards}[selector]||null),querySelectorAll:()=>[],addEventListener:(name,handler)=>{handlers[name]=handler;}},
  fetch:async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>({})};},
  location:{origin:'http://localhost'},history:{replaceState(){}},sessionStorage:{getItem:()=>null}});
const availability=fs.readFileSync(path.join(__dirname,'../app/static/availability-workspace.js'),'utf8').split("document.addEventListener('click'")[0];
vm.runInContext(availability+'\n'+source.slice(0,source.indexOf("window.addEventListener('hashchange'")),context);
const run=code=>vm.runInContext(code,context);
run('globalThis.openTournamentForTest=openTournament;hydrateImages=async()=>{};closeModal=()=>{};toast=()=>{};openTournament=async()=>{};showError=error=>{throw error;};');
context.rows=[
  {id:'internal:0',name:'张三',user_id:'internal',gender:'male',level:3,category:'正式',status:'active',rank:11,groups:[]},
  {id:'internal:1',name:'小王',user_id:'internal',gender:'male',level:9,guest_index:1,sponsor_name:'张三',category:'substitute',status:'active',rank:12,groups:[{name:'不可继承组'}]},
  {id:'other:0',name:'李四',user_id:'other',gender:'female',category:'waitlist',status:'active',rank:13,groups:[]},
  {id:'cancelled:0',name:'赵六',user_id:'cancelled',gender:'unknown',category:'formal',status:'cancelled',groups:[]},
];
const html=run('registrationTable(rows)');
assert.match(html,/<th>性别<\/th><th>名额<\/th>/);
assert.match(html,/小王（张三代报名）/);
assert.match(html,/未填写/);
assert.match(html,/访客/);
assert.ok(!html.includes('不可继承组'));assert.ok(!html.includes('9 级'));
assert.ok(!html.includes('状态 / 顺序'));assert.ok(!html.includes('#11'));
assert.ok(!html.includes('>有效<'));
assert.match(html,/正式/);assert.match(html,/替补/);assert.match(html,/候补/);
assert.match(html,/>—<\/span><span class="sub">已取消/);
assert.equal(run('registrationName({name:"private-id+2",user_id:"private-id",guest_index:2,sponsor_name:"private-id"})'),'待补姓名（待补姓名代报名）');
assert.match(run('registrationTable([{name:"<张|三>",gender:"unknown",category:"formal",groups:[]}])'),/&lt;张\|三&gt;/);
assert.equal(run('registrationGenderName({name:"张三",gender:"female"})'),'女');
assert.equal(run('registrationGenderName({name:"同行",is_proxy:true,gender:"female"})'),'未填写');

(async()=>{
  run('state.platformAdmin=true;state.page.tournament={id:42,kind:"large",status:"registration",registration_open:"2000-01-01T00:00:00Z",registration_close:"2099-01-01T00:00:00Z",participants:rows.map(r=>({...r,source_category:r.category}))};');
  await run("tournamentTab('participants')");
  assert.match(content.innerHTML,/当前共 3 位参赛者/);
  assert.match(content.innerHTML,/小王（张三代报名）/);
  assert.match(content.innerHTML,/data-action="tournament-entry-cancel" data-id="internal:1"/);
  assert.ok(!content.innerHTML.includes('data-id="cancelled:0"'));
  assert.ok(!content.innerHTML.includes('不可继承组'));
  run('state.page.tournament.participants[0].status="cancelled"');
  await run("tournamentTab('participants')");
  assert.match(content.innerHTML,/小王（张三代报名）/,'a cancelled sponsor still supplies a real name');
  run('state.platformAdmin=false');await run("tournamentTab('participants')");
  assert.ok(!content.innerHTML.includes('tournament-entry-cancel'));
  run('state.platformAdmin=true;state.page.tournament.status="closed"');await run("tournamentTab('participants')");
  assert.ok(!content.innerHTML.includes('tournament-entry-cancel'));assert.ok(!content.innerHTML.includes('tournament-enroll'));
  run('state.page.tournament.status="registration";state.page.tournament.registration_close="2000-01-01T00:00:00Z"');
  assert.equal(run('tournamentCanEditRegistrations(state.page.tournament)'),false);
  run('state.page.tournament.registration_close="2099-01-01T00:00:00Z";confirmAction=(title,description,label,callback)=>{globalThis.pendingConfirmation=callback;}');
  const button={disabled:false,dataset:{action:'tournament-entry-cancel',id:'internal:1',name:'小王（张三代报名）'}};
  await handlers.click({target:{closest:selector=>selector==='[data-action]'?button:null},preventDefault(){}});
  assert.equal(calls.length,0,'nothing deleted before the confirmation');
  await run('pendingConfirmation(state.epoch)');
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/tournaments/42/entries/internal%3A1');
  assert.equal(calls[0].options.method,'DELETE');
  run('api=async(path,options)=>{if(path==="/api/members?status=approved")return {members:[{user_id:"internal",name:"张三",level:3}]};globalThis.enrollRequest={path,options};return {};};openModal=(title,body)=>{globalThis.enrollMarkup=body;};bindForm=(id,callback)=>{globalThis.enrollCallback=callback;};');
  await run('tournamentEnroll()');
  assert.match(run('enrollMarkup'),/name="guest_names"/);
  assert.match(run('enrollMarkup'),/已报名同行保留/);
  context.enrollData=new FormData();context.enrollData.set('user_id','internal');context.enrollData.set('guests','0');context.enrollData.set('guest_names','小王，李四');
  await run('enrollCallback(enrollData,null,state.epoch)');
  assert.equal(run('enrollRequest.path'),'/api/tournaments/42/participants');
  assert.deepEqual(JSON.parse(run('JSON.stringify(enrollRequest.options.body)')),{user_id:'internal',name:'张三',guests:2,guest_names:['小王','李四']});
  run('globalThis.eventFixture={id:42,title:"周末友谊杯",kind:"large",format:"round_robin",date:"2026-10-14",status:"registration",participants:rows,options:{},groups:[],matches:[]};api=async()=>eventFixture;state.authed=true;state.formats=[{id:"round_robin",name:"积分赛"}];');
  await run('openTournamentForTest(42,"participants")');
  assert.match(main.innerHTML,/参赛者 <strong>3<\/strong>/,'the summary excludes a cancelled entry');
  assert.match(content.innerHTML,/当前共 3 位参赛者/,'the summary and participant table agree');
  run('state.page.tournaments=[eventFixture];renderTournamentCards()');
  assert.match(cards.innerHTML,/3 位参赛者/,'a card with participant rows but no aggregate counts also excludes cancellation');
  run('eventFixture.participants=rows.map(row=>({...row,status:"cancelled"}))');
  await run('openTournamentForTest(42,"participants")');
  assert.match(main.innerHTML,/参赛者 <strong>0<\/strong>/);
  assert.match(content.innerHTML,/当前共 0 位参赛者/);
  run('state.venueId=1;download=async(url,name)=>{globalThis.exported={url,name};}');
  const exportButton={disabled:false,dataset:{action:'registration-export',venue:'2',session:'19',date:'2026-10-16'}};
  await handlers.click({target:{closest:selector=>selector==='[data-action]'?exportButton:null},preventDefault(){}});
  assert.equal(run('exported.name'),'报名记录-场地2-2026-10-16.csv');
  assert.match(run('exported.url'),/venue_id=2/);assert.match(run('exported.url'),/session_id=19/);
  const guide=fs.readFileSync(path.join(__dirname,'../app/static/guide.html'),'utf8');
  for(const command of ['取消张三，李四','春季联赛，报名，张三，李四','春季联赛，取消张三，李四'])assert.ok(guide.includes(command));
  assert.ok(!guide.includes('更新本人整组'));assert.ok(!guide.includes('取消本人及同行整组'));
  console.log('v2.10 registration display passed: people/gender/quota, proxy identity, selective exact-entry cancellation, stage/permission guards and instructions.');
})().catch(error=>{console.error(error);process.exitCode=1;});
