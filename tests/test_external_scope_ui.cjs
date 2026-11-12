'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const ctx=vm.createContext({Map,Set,JSON,Number,String,Array,Error,e:escape,attr:escape,weekdaySummary:()=> '工作日',field:(label,name,value,type,attrs)=>`<label>${escape(label)}<input name="${escape(name)}" value="${escape(value)}" type="${type}" ${attrs}></label>`});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/scope-workspace.js'),'utf8'),ctx);
const run=code=>vm.runInContext(code,ctx);
ctx.settings={allow_external_substitute:true,external_slot_keys:['slot:2']};
ctx.slots=[{slot_key:'slot:2',venue_id:1,venue_name:'主场地',name:'第二场',start:'20:30',end:'22:00'},
  {slot_key:'slot:3',venue_id:2,venue_name:'<script>',name:'周末场',start:'18:00',end:'20:00'}];
const html=run('externalScopeControls(settings,slots)');
assert.match(html,/范围外替补报名/);assert.match(html,/必选场次/);assert.match(html,/value="slot:2" data-venue="1" checked/);
assert.ok(!html.includes('<script>'));
ctx.data={has:()=>false,get:key=>({allow_external_substitute:'on',venue_mode:'selected',slot_mode:'selected'})[key]??null,
  getAll:key=>({external_slot_keys:['slot:2'],scope_venue_ids:['1'],slot_keys:['slot:1']})[key]||[]};
assert.deepEqual(JSON.parse(JSON.stringify(run('scopeBody(data,true)'))),{venue_ids:[1],slot_keys:['slot:1'],allow_external_substitute:true,external_auto_promote:false,external_slot_keys:['slot:2']});
ctx.data={get:key=>key==='allow_external_substitute'?'on':null,getAll:()=>[]};
assert.throws(()=>run('scopeBody(data,true)'),/至少选择/);
const label=()=>({hidden:false});
const first={value:'slot:1',dataset:{venue:'1'},checked:true,label:label(),closest(){return this.label;}},
  second={value:'slot:2',dataset:{venue:'1'},checked:true,label:label(),closest(){return this.label;}};
const toggle={checked:true,setCustomValidity(value){this.validation=value;}};
const panel={},empty={};
ctx.form={elements:{allow_external_substitute:toggle,venue_mode:{value:'selected'},slot_mode:{value:'selected'}}};
ctx.$$=selector=>({'[name=scope_venue_ids]:checked':[{value:'1'}],'[name=slot_keys]:checked':[{value:'slot:1'}],'[name=external_slot_keys]':[first,second]})[selector]||[];
ctx.$=selector=>selector==='[data-external-scope]'?panel:empty;
run('refreshExternalScope(form)');assert.equal(first.disabled,true);assert.equal(first.label.hidden,true);assert.equal(second.disabled,false);assert.equal(toggle.validation,'');
second.checked=false;run('refreshExternalScope(form)');assert.match(toggle.validation,/至少选择/);
toggle.checked=false;run('refreshExternalScope(form)');assert.equal(toggle.validation,'');assert.equal(panel.hidden,true);assert.equal(second.disabled,true);
console.log('External scope UI: explicit selection, validation, exclusion, escaping and toggle state passed.');

ctx.settings.external_auto_promote=false;assert.doesNotMatch(run('externalScopeControls(settings,slots)'),/name="external_auto_promote" checked/);
ctx.settings.external_auto_promote=true;assert.match(run('externalScopeControls(settings,slots)'),/name="external_auto_promote" checked/);
