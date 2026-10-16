/* Member validity and renewal ledger regression checks, with no external services. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8');
const source=read('app.js'),nodes={};
class Fields{constructor(value){this.fields=value?.fields||value||{};}get(key){return this.fields[key]??null;}getAll(key){const v=this.fields[key];return Array.isArray(v)?v:v==null?[]:[v];}}
const node=selector=>nodes[selector]||(nodes[selector]={value:'',innerHTML:'',textContent:'',querySelector:()=>({}),querySelectorAll:()=>[],reportValidity:()=>true});
const ctx=vm.createContext({URL,URLSearchParams,Intl,Date,Map,Set,Number,String,Array,JSON,AbortController,FormData:Fields,location:{origin:'http://localhost'},sessionStorage:{getItem:()=>null},document:{querySelector:node,querySelectorAll:()=>[]}});
vm.runInContext(['workspace.js','event-workspace.js','scope-workspace.js','casual-workspace.js','membership-workspace.js'].map(read).join('\n')+'\n'+read('availability-workspace.js').split("document.addEventListener('click'")[0]+'\n'+source.slice(0,source.indexOf("document.addEventListener('click'")),ctx);
const run=code=>vm.runInContext(code,ctx),plain=value=>JSON.parse(JSON.stringify(value));
run("state.epoch=3;state.viewEpoch=4;state.authed=true;state.route='members';");
for(const [status,label] of Object.entries({not_configured:'有效期待设置',active:'有效期内',upcoming:'尚未生效',gap:'续费间隔中',expired:'已到期',inactive:'无有效期'}))assert.match(run(`membershipStatus({status:'${status}'})`),new RegExp(label));
assert.equal(run("membershipDay('2026-02-30')"),'');
assert.equal(run("membershipYearEnd('2024-02-29')"),'2025-02-28');
assert.equal(run("membershipYearEnd('2026-10-13')"),'2027-10-12');
assert.equal(run("membershipDay('2026-12-31',1)"),'2027-01-01');
ctx.periods=[{id:1,version:1,status:'valid',start_date:'2025-01-01',end_date:'2025-12-31',amount:'365.00',note:'第一年',created_at:'2025-01-01T00:00:00Z'},
 {id:2,version:3,status:'void',start_date:'2026-01-01',end_date:'2026-03-31',amount:null,note:'<script>alert(1)</script>',void_note:'错录 <img src=x>',voided_at:'2026-01-01T00:00:00Z'},
 {id:3,version:1,status:'valid',start_date:'2026-04-01',end_date:'2027-03-31',amount:'0.00',note:'减免'}];
assert.deepEqual(plain(run('membershipGaps(periods)')),[{start_date:'2026-01-01',end_date:'2026-03-31'}]);
assert.deepEqual(plain(run("membershipGaps([{status:'valid',start_date:'2026-01-01',end_date:'2026-12-31'},{status:'valid',start_date:'2027-01-01',end_date:'2027-12-31'}])")),[]);
assert.deepEqual(plain(run("membershipTermStatus(periods[2],'2026-04-01')")),['生效中','green']);
assert.deepEqual(plain(run("membershipTermStatus(periods[2],'2027-03-31')")),['生效中','green']);
assert.deepEqual(plain(run("membershipTermStatus(periods[2],'2027-04-01')")),['已结束','']);
const table=run("membershipTable(periods,'2026-10-13')");assert.match(table,/<table>/);assert.match(table,/已作废/);assert.match(table,/0.00/);assert.match(table,/未记录/);assert.match(table,/2025-01-01 08:00/);assert.equal((table.match(/data-membership-void=/g)||[]).length,2);assert.ok(!table.includes('<script>'));assert.ok(!table.includes('<img'));
const profile=run("membershipProfile({user_id:'uid',membership:{summary:{status:'not_configured',period_count:0,registration:{created_at:'2026-10-12T18:00:00Z',reviewed_at:null}}}})");assert.match(profile,/首次建档/);assert.match(profile,/2026-10-13 02:00/);assert.match(profile,/有效期待设置/);assert.match(profile,/最近资格审核/);assert.match(profile,/—/);
run("state.page.members=[{user_id:'u1',name:'张三',status:'approved',membership:{summary:{status:'expired',last_end_date:'2026-09-30'}}},{user_id:'u2',name:'李四',status:'pending',membership:{summary:{status:'not_configured'}}}]");
node('#member-validity-filter').value='expired';run('renderMemberTable()');assert.match(node('#member-list').innerHTML,/张三/);assert.ok(!node('#member-list').innerHTML.includes('李四'));assert.match(node('#member-list').innerHTML,/data-action="member-membership"/);
node('#member-validity-filter').value='';run('renderMemberTable()');assert.match(node('#member-list').innerHTML,/李四/);
assert.ok(read('index.html').includes('/static/membership-workspace.js'));
const applications=run("membershipApplicationHistory([{name:'张三',status:'approved',submitted_at:'2026-10-12T18:00:00Z',reviewed_at:'2026-10-13T01:00:00Z',note:'<img src=x>'}])");assert.match(applications,/注册与审核记录/);assert.match(applications,/2026-10-13 02:00/);assert.match(applications,/2026-10-13 09:00/);assert.ok(!applications.includes('<img'));

(async()=>{
  ctx.reply={summary:{status:'active',as_of:'2026-10-13',last_end_date:'2025-12-31',current_term:ctx.periods[2],period_count:2,registration:{created_at:'2025-01-01T00:00:00Z',status:'approved'}},periods:ctx.periods};
  run("globalThis.forms={};globalThis.sent=[];openModal=(title,html)=>{globalThis.modal=html;return {querySelectorAll:()=>[]}};bindForm=(id,callback)=>{forms[id]=callback};api=async(path,options={})=>{sent.push({path,...options});return path.endsWith('/membership')?reply:{user_id:'u1',name:'张三'}};");
  await run("memberMembershipDialog('u1')");
  assert.match(ctx.modal,/间隔记录 · 1 段/);assert.match(ctx.modal,/name="start_date"[^>]*value="2027-04-01"/);assert.match(ctx.modal,/type="date"/);assert.match(ctx.modal,/登记金额（元，选填）/);
  node('#f-start_date').value='2028-02-29';node('#membership-one-year').onclick();assert.equal(node('#f-end_date').value,'2029-02-28');
  ctx.fields={start_date:'2027-04-01',end_date:'2028-03-31',amount:'99.50',note:' 新年度 '};await run("forms['membership-period-form'](new FormData(fields),{},-1)");
  assert.deepEqual(plain(ctx.sent.find(row=>row.method==='POST')),{path:'/api/members/u1/membership/periods',method:'POST',body:{start_date:'2027-04-01',end_date:'2028-03-31',amount:'99.50',note:'新年度'}});
  ctx.fields.end_date='2027-03-31';await assert.rejects(run("forms['membership-period-form'](new FormData(fields),{},-1)"),/结束日期不能早于/);
  ctx.fields.end_date='2027-05-01';ctx.fields.amount='';ctx.sent.length=0;await run("forms['membership-period-form'](new FormData(fields),{},-1)");assert.equal('amount' in ctx.sent[0].body,false);
  run("membershipVoidDialog('u1',periods[0],'张三')");assert.match(ctx.modal,/原记录仍保留/);assert.match(ctx.modal,/name="note" required/);ctx.fields={note:' 日期登记错误 '};ctx.sent.length=0;await run("forms['membership-void-form'](new FormData(fields),{},-1)");assert.deepEqual(plain(ctx.sent[0]),{path:'/api/members/u1/membership/periods/1/void',method:'POST',body:{expected_version:1,note:'日期登记错误'}});
  const previous=ctx.modal;run("membershipVoidDialog('u1',periods[1],'张三')");assert.equal(ctx.modal,previous);
  run("api=async()=>{state.viewEpoch++;return {}};globalThis.modal='unchanged'");await run("memberMembershipDialog('u1')");assert.equal(ctx.modal,'unchanged');
  console.log('v2.6 UI checks passed: validity filters, registration dates, renewal gaps, inclusive annual dates, escaped archival ledger, amount and versioned void payloads, stale navigation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
