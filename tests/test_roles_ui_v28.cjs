/* Role allocation, group approvals, notification status, and escaping. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8'),source=read('app.js'),nodes={};
class Fields{constructor(value){this.fields=value?.fields||value||{};}get(key){return this.fields[key]??null;}getAll(key){const v=this.fields[key];return Array.isArray(v)?v:v==null?[]:[v];}}
const node=selector=>nodes[selector]||(nodes[selector]={value:'',checked:false,innerHTML:'',textContent:'',handlers:{},addEventListener(type,fn){this.handlers[type]=fn;},querySelector:selector=>node(selector),querySelectorAll:()=>[],reportValidity:()=>true});
const ctx=vm.createContext({URL,URLSearchParams,Intl,Date,Map,Set,Number,String,Array,JSON,AbortController,FormData:Fields,location:{origin:'http://localhost'},sessionStorage:{getItem:()=>null},document:{querySelector:node,querySelectorAll:()=>[]}});
vm.runInContext(['workspace.js','event-workspace.js','scope-workspace.js','casual-workspace.js','membership-workspace.js'].map(read).join('\n')+'\n'+read('availability-workspace.js').split("document.addEventListener('click'")[0]+'\n'+source.slice(0,source.indexOf("document.addEventListener('click'")),ctx);
const run=code=>vm.runInContext(code,ctx),plain=value=>JSON.parse(JSON.stringify(value));
ctx.members=[{user_id:'a',name:'张三',status:'approved',roles:['club_admin','member_manager']},{user_id:'b',name:'李四',status:'approved',roles:['event_manager']},{user_id:'c',name:'待审核人员',status:'pending',roles:[]},{user_id:'s',name:'暂停人员',status:'suspended',roles:['event_manager']}];
ctx.catalog={definitions:[{id:'club_admin',name:'后台管理员',description:'全部权限',permissions:['member.manage','event.manage','roles.manage']},{id:'member_manager',name:'会员管理员',description:'审核注册、会籍与分组',permissions:['member.manage']},{id:'event_manager',name:'赛事管理员',description:'管理比赛',permissions:['event.manage']}],permissions:[{id:'member.manage',name:'会员管理'},{id:'event.manage',name:'赛事管理'},{id:'roles.manage',name:'角色管理'}]};
run("state.epoch=3;state.viewEpoch=4;state.authed=true;state.platformAdmin=true;state.route='roles';globalThis.forms={};globalThis.sent=[];globalThis.toasts=[];openModal=(title,html)=>{globalThis.modal=html};bindForm=(id,callback)=>{forms[id]=callback};toast=text=>toasts.push(text);closeModal=()=>{};renderRoute=async()=>{};api=async(path,options={})=>{sent.push({path,...options});return path==='/api/roles'?catalog:{members};};");
(async()=>{
  await run('renderRoles(4)');
  const html=node('#main').innerHTML;
  assert.match(html,/角色与权限/);assert.match(html,/全部管理权限/);assert.match(html,/会员管理员/);assert.match(html,/赛事管理员/);assert.match(html,/配置人员角色/);assert.match(html,/李四/);assert.match(html,/暂停人员/);assert.ok(!html.includes('data-action="role-remove"'));
  assert.match(run("roleTags(members[1])"),/赛事管理员/);assert.ok(!run("roleTags(members[1])").includes('无管理角色'));
  run("grantRoleDialog('a')");
  assert.match(ctx.modal,/value="club_admin" checked/);assert.match(ctx.modal,/value="member_manager" checked/);assert.match(ctx.modal,/取消管理员后仍然生效/);
  ctx.fields={user_id:'a',roles:['member_manager']};
  await run("forms['role-form'](new FormData(fields),{},3)");
  assert.deepEqual(plain(ctx.sent.find(row=>row.method==='PUT').body),{roles:['member_manager']});
  run('sent.length=0;grantRoleDialog()');assert.ok(!ctx.modal.includes('待审核人员'));assert.ok(!ctx.modal.includes('暂停人员'));
  node('#f-user_id').value='b';node('#f-user_id').onchange();assert.match(node('#role-choice-fields').innerHTML,/value="event_manager" checked/);assert.ok(!node('#role-choice-fields').innerHTML.includes('value="club_admin" checked'));
  ctx.fields={user_id:'b',roles:['event_manager','member_manager']};await run("forms['role-form'](new FormData(fields),{},3)");assert.deepEqual(plain(ctx.sent.find(row=>row.method==='PUT').body.roles),['event_manager','member_manager']);
  run("grantRoleDialog('s')");assert.match(ctx.modal,/暂停人员/);
  ctx.fields={user_id:'s',roles:[]};await run("forms['role-form'](new FormData(fields),{},3)");assert.deepEqual(plain(ctx.sent.at(-1).body),{roles:[]});
  run('state.platformAdmin=false');assert.throws(()=>run("grantRoleDialog('a')"),/后台管理员/);await run('renderRoles(4)');assert.ok(!node('#main').innerHTML.includes('data-action="role-edit"'));assert.ok(!node('#main').innerHTML.includes('data-action="role-new"'));run('state.platformAdmin=true');
  const guide=run('applicationReviewGuide()');assert.match(guide,/待审核/);assert.match(guide,/审核通过 申请12/);assert.match(guide,/同名时请用申请编号/);assert.match(guide,/@本人/);
  ctx.notices=[{id:1,user_id:'secret-id',member_name:'张三',kind:'roles',status:'queued',created_at:'2026-10-13T01:00:00Z'},{id:2,user_id:'private-id',member_name:'<img src=x>',kind:'membership',status:'blocked',can_retry:true,last_error:'https://secret/?key=private'},{id:3,member_name:'李四',status:'sent',sent_at:'2026-10-13T02:00:00Z'},{id:4,member_name:'王五',status:'unknown'}];
  const table=run('identityNoticeTable(notices)');assert.match(table,/<table>/);assert.match(table,/管理角色变更/);assert.match(table,/待发送/);assert.match(table,/已送达/);assert.match(table,/等待群配置/);assert.match(table,/结果待核实/);assert.equal((table.match(/data-action="identity-notice-retry"/g)||[]).length,1);assert.ok(!table.includes('<img'));assert.ok(!table.includes('secret-id'));assert.ok(!table.includes('private'));assert.match(table,/2026-10-13 10:00/);
  run("api=async(path,options={})=>{sent.push({path,...options});return {notices}};sent.length=0");await run('identityNoticesDialog()');assert.match(ctx.modal,/待发送表示已入队/);assert.equal(ctx.sent[0].path,'/api/identity-notices?limit=50');await run('retryIdentityNotice(2)');assert.equal(ctx.sent.find(row=>row.method==='POST').path,'/api/identity-notices/2/retry');
  run("globalThis.modal='unchanged';api=async()=>{state.viewEpoch++;return {notices}}");await run('identityNoticesDialog()');assert.equal(ctx.modal,'unchanged');
  assert.match(read('index.html'),/workspace.js\?v=1\.0/);
  console.log('v2.8 UI checks passed: directory-based roles, inherited full access, preserving subroles, allocation gate, group review help, private-safe notices, retries and stale-response guard.');
})().catch(error=>{console.error(error);process.exitCode=1;});
