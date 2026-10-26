/* Same-group robot choice, distinct group counts and scoped event binding. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8'),source=read('interface.js')+'\n'+read('app.js'),nodes={};
class Fields{constructor(value){this.fields=value?.fields||value||{};}get(key){return this.fields[key]??null;}getAll(key){const v=this.fields[key];return Array.isArray(v)?v:v==null?[]:[v];}}
const node=selector=>nodes[selector]||(nodes[selector]={value:'',innerHTML:'',textContent:'',addEventListener:()=>{},querySelector:()=>({}),querySelectorAll:()=>[],reportValidity:()=>true});
const ctx=vm.createContext({URL,URLSearchParams,Intl,Date,Map,Set,Number,String,Array,JSON,AbortController,FormData:Fields,location:{origin:'http://localhost'},sessionStorage:{getItem:()=>null},document:{querySelector:node,querySelectorAll:()=>[]}});
vm.runInContext(['workspace.js','event-workspace.js','scope-workspace.js','casual-workspace.js','membership-workspace.js'].map(read).join('\n')+'\n'+read('availability-workspace.js').split("document.addEventListener('click'")[0]+'\n'+source.slice(0,source.indexOf("document.addEventListener('click'")),ctx);
const run=code=>vm.runInContext(code,ctx),plain=value=>JSON.parse(JSON.stringify(value));
ctx.rows=[{id:'a',bot_id:'robot-a',bot_name:'活动助手',name:'活动群',chat_id:'same',venue_ids:[],active:true,configured:true},
 {id:'b',bot_id:'robot-b',bot_name:'赛事助手',name:'同一个群',chat_id:'same',venue_ids:[1],active:true,configured:true},
 {id:'c',bot_id:'robot-a',bot_name:'活动助手',name:'<script>群',chat_id:'other',venue_ids:[2],active:true,configured:true}];
run("state.epoch=3;state.authed=true;state.bots=[{id:'robot-a',name:'活动助手',venue_ids:null},{id:'robot-b',name:'赛事助手',venue_ids:[1]}];");
assert.equal(run("selectedChannelGroups(rows,['a','b','c','b','unknown']).length"),2);
assert.equal(run("selectedChannelGroups(rows,[]).length"),0);
assert.equal(run("selectedChannelGroups(rows,['b'])[0].channels[0].id"),'b');
assert.match(run("channelGroupSummary(rows,['a','b'])"),/活动助手、赛事助手/);
assert.equal(run('channelServesVenue(rows[0],1)'),true);
assert.equal(run('channelServesVenue(rows[1],2)'),false);
assert.equal(run('channelServesVenue(rows[2],2)'),true);
run("state.bots[0].venue_ids=[]");assert.equal(run('channelServesVenue(rows[0],1)'),false);
run("state.bots[0].venue_ids=null");
const options=run("channelOptions(rows,['a','b'])");
assert.equal((options.match(/ checked/g)||[]).length,2);
assert.match(options,/同一群可选择多个机器人/);assert.ok(!options.includes('<script>'));
assert.match(read('scope-workspace.js'),/把这个地址填到机器人平台/);
assert.match(read('event-workspace.js'),/同群通知只发一次/);

(async()=>{
  run("globalThis.forms={};globalThis.sent=[];globalThis.toasts=[];openModal=(title,html)=>{globalThis.modal=html};bindForm=(id,callback)=>{forms[id]=callback};toast=text=>toasts.push(text);closeModal=()=>{};loadChannels=async()=>rows;state.page.tournament={id:7,venue_id:1,status:'registration'};api=async(path,options={})=>{sent.push({path,...options});return {channel_ids:['a','b']}};");
  node('#notify-form').fields={type:'registration',channel_ids:['a','b']};
  await run('tournamentNotify()');
  assert.match(node('#event-destinations').textContent,/发送至 1 个群/);
  assert.match(node('#event-destinations').textContent,/本次通知只发一次/);
  assert.match(ctx.modal,/value="a" checked/);assert.match(ctx.modal,/value="b" checked/);
  assert.ok(!ctx.modal.includes('value="c"'));
  ctx.fields={type:'registration',channel_ids:['a','b']};
  await run("forms['notify-form'](new FormData(fields),{},3)");
  assert.deepEqual(plain(ctx.sent.find(x=>x.method==='POST').body),{type:'registration',channel_ids:['a','b']});
  assert.match(ctx.toasts.at(-1),/1 个群/);
  run("state.route='guide';state.viewEpoch=3;bindAINavigator=()=>{}");
  await run('renderGuide(3)');
  const guide=node('#main').innerHTML;
  assert.match(guide,/普通用户版/);assert.match(guide,/参赛/);assert.match(guide,/管理员版/);
  assert.match(guide,/href="\/guide\/member"/);assert.match(guide,/href="\/guide\/admin"/);
  assert.ok(!guide.includes('最多 8 步'));assert.ok(!guide.includes('12位确认码'));
  console.log('v2.7 UI checks passed: group counts, same-group choice, scope filtering, safe labels, bound notification payload and concise public guide entries.');
})().catch(error=>{console.error(error);process.exitCode=1;});
