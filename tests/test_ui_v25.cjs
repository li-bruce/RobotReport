/* Behavioral regression checks for the v2.5 forms and archival displays. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8');
const app=read('app.js');
const nodes={};
class Fields {constructor(value){this.fields=value?.fields||value||{};}get(key){return this.fields[key]??null;}getAll(key){const value=this.fields[key];return Array.isArray(value)?value:value==null?[]:[value];}has(key){return this.fields[key]!=null;}}
const ctx=vm.createContext({URL,URLSearchParams,Intl,Date,Map,Set,Symbol,Number,String,Array,JSON,AbortController,FormData:Fields,
  location:{origin:'http://localhost'},sessionStorage:{getItem:()=>null},
  document:{querySelector:selector=>nodes[selector]||(nodes[selector]={value:'',innerHTML:'',textContent:'',querySelector:()=>({}),querySelectorAll:()=>[]}),querySelectorAll:()=>[]}});
vm.runInContext(['workspace.js','event-workspace.js','scope-workspace.js','casual-workspace.js'].map(read).join('\n')+'\n'+read('availability-workspace.js').split("document.addEventListener('click'")[0]+'\n'+app.slice(0,app.indexOf("document.addEventListener('click'")),ctx);
const run=code=>vm.runInContext(code,ctx),plain=value=>JSON.parse(JSON.stringify(value));
run("state.venues=[{id:1,name:'主场地'},{id:2,name:'<img src=x>'}];state.venueId=1;state.epoch=3;state.viewEpoch=3;state.authed=true;");
assert.equal(run("genderName('female')"),'女');assert.equal(run("genderName('unknown')"),'待补齐');
for(const [category,label] of [['mens','男双'],['womens','女双'],['mixed','混双'],['open','不限双打']])assert.equal(run(`disciplineName('doubles','${category}')`),label);
assert.equal(run("disciplineName('singles','mixed')"),'单打');
const scopes=run("scopeControls({venue_ids:[1],slot_keys:['slot:9']},[{slot_key:'slot:9',venue_name:'主场地',name:'晚场',start:'17:30',end:'20:30',weekdays:[0]}],{bot:true})");
assert.match(scopes,/value="1" checked/);assert.match(scopes,/value="slot:9" checked/);assert.match(scopes,/17:30/);assert.ok(!scopes.includes('<img src=x>'));
assert.deepEqual(plain(run("scopeBody(new FormData({venue_mode:'selected',slot_mode:'selected'}),true)")),{venue_ids:[],slot_keys:[]});
assert.deepEqual(plain(run("scopeBody(new FormData({venue_mode:'all',slot_mode:'all',scope_venue_ids:['1'],slot_keys:['slot:9']}),true)")),{venue_ids:null,slot_keys:null});
assert.deepEqual(plain(run("scopeBody(new FormData({slot_mode:'selected',slot_keys:['slot:9']}))")),{slot_keys:['slot:9']});
const preview=run("importPreviewTable({rows:[{line:2,name:'张三',gender:'female',phone_masked:'138****0001',status:'create',warning:'性别待补齐',level:9,groups:[]}]})");
assert.match(preview,/<th>性别<\/th>/);assert.match(preview,/>女<\/td>/);assert.match(preview,/性别待补齐/);
const pairs=run("casualPairTable('搭档',[{people:[{name:'张三'},{name:'<img src=x>',kind:'guest'}],played:4,wins:3,losses:1,win_rate:75}])");
assert.match(pairs,/<table>/);assert.match(pairs,/75.0%/);assert.match(pairs,/访客/);assert.ok(!pairs.includes('<img src=x>'));
assert.equal(run('casualScores({scores:[[21,19],[15,21],[21,17]]})'),'21 : 19 / 15 : 21 / 21 : 17');
const registration=read('register.html');assert.match(registration,/<select name="gender" required>/);
assert.match(registration,/<option value="male">男/);assert.match(registration,/<option value="female">女/);
assert.equal([...registration.matchAll(/<option value="[1-9]">/g)].length,9);

(async()=>{
  run("globalThis.forms={};globalThis.sent=[];openModal=(title,html)=>{globalThis.modal=html};bindForm=(id,callback)=>{forms[id]=callback};api=async(path,options={})=>{sent.push({path,...options});return []};");
  run("state.page.tournament={id:7,kind:'large',title:'混双杯',keyword:'混双杯',date:'2026-10-20',matches:[],options:{discipline:'doubles',doubles_category:'mixed'},registration_open:'2026-10-19T09:00:00+08:00',registration_close:'2026-10-20T17:00:00+08:00'};");
  await run('editTournamentDialog(7)');
  assert.match(ctx.modal,/name="doubles_category"/);assert.match(ctx.modal,/<option value="mixed" selected/);
  assert.match(ctx.modal,/name="session_id"/);
  ctx.editFields={title:'混双杯',keyword:'混双杯',notes:'',discipline:'doubles',doubles_category:'womens',session_id:'',assignment_strategy:'random',group_count:'2',capacity:'8',best_of:'1',points:'21',cap:'30',win_by:'2',advance_per_group:'1',min_level:'',max_level:'',registration_open:'2026-10-19T09:00',registration_close:'2026-10-20T17:00'};
  await run("forms['event-edit-form'](new FormData(editFields),{},-1)");
  const saved=plain(ctx.sent.find(r=>r.method==='PATCH'));
  assert.equal(saved.body.options.doubles_category,'womens');assert.equal(saved.body.options.session_id,null);
  run("state.page.tournament.matches=[{id:1}]");await run('editTournamentDialog(7)');
  assert.ok(!ctx.modal.includes('name="doubles_category"'));assert.ok(!ctx.modal.includes('name="session_id"'));
  nodes['#casual-filter']={fields:{user_id:'u1',date_from:'2026-10-01',date_to:'2026-10-20'}};
  ctx.matchRows=[{id:'c:9',source:'casual',source_id:9,date:'2026-10-20',discipline:'doubles',doubles_category:'mixed',session:{name:'旧晚场',start:'17:30',end:'20:30'},venue_name:'主场地',team_a:[{name:'张三'},{name:'李四'}],team_b:[{name:'王五'},{name:'赵六'}],scores:[[21,19]],status:'completed',can_edit:true,version:1},
    {id:'c:10',source:'casual',source_id:10,date:'2026-10-20',discipline:'singles',team_a:[{name:'<script>x</script>'}],team_b:[{name:'王五'}],scores:[[19,21]],status:'void',can_edit:true,version:2}];
  run("api=async(path)=>path.startsWith('/api/casual/history')?{matches:matchRows}:{played:1,wins:1,losses:0,win_rate:100,by_discipline:[],partners:[],opponents:[],opponent_teams:[]};");
  await run('loadCasualHistory(3)');
  const history=nodes['#casual-history'].innerHTML;
  assert.match(history,/混双/);assert.match(history,/旧晚场 17:30–20:30/);assert.match(history,/21 : 19/);assert.match(history,/已作废/);
  assert.equal((history.match(/data-casual-edit=/g)||[]).length,1);assert.ok(!history.includes('<script>'));
  assert.match(nodes['#casual-statistics'].innerHTML,/100.0%/);
  console.log('v2.5 UI checks passed: gender, doubles editing, scoped empty/all semantics, CSV warnings, immutable times and casual statistics.');
})().catch(error=>{console.error(error);process.exitCode=1;});
