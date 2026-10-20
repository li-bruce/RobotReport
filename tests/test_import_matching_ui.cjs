'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/import-matches.js'),'utf8').split("document.addEventListener('click'")[0];
const area={dataset:{},isConnected:true,innerHTML:'new-file'},form={reset(){this.resets=(this.resets||0)+1;}},state={epoch:1,viewEpoch:1,page:{importPreview:{token:'old'}}};
const escape=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
let finish,handler,refreshes=0;const calls=[],toasts=[];
const context=vm.createContext({state,e:escape,attr:escape,genderName:()=>'',levelName:()=>'',nf:String,icon:()=>'',btn:()=>'',document:{},
  $:selector=>selector==='#member-import-preview'?area:form,
  api:async(url,options)=>{calls.push({url,options});return new Promise(resolve=>finish=resolve);},
  importPreviewMarkup:r=>'render-'+r.token,importPreviewCanCommit:()=>true,
  bindForm:(_id,callback)=>handler=callback,refreshImportRecords:async()=>refreshes++,toast:text=>toasts.push(text)});
vm.runInContext(source,context);
(async()=>{
  const first=context.resolveImportMatches([{source_key:'row',target:'member:1'}]);
  state.page.importPreview={token:'new'};
  finish({token:'old',match_count:0});await first;
  assert.equal(state.page.importPreview.token,'new');
  assert.equal(area.innerHTML,'new-file');assert.equal(toasts.length,0);
  const second=context.resolveImportMatches([{source_key:'row',target:'new'}]);
  finish({token:'new',match_count:0});await second;
  assert.equal(area.innerHTML,'render-new');assert.equal(toasts.length,1);
  assert.equal(area.dataset.busy,'false');
  const commit=handler(null,{isConnected:true},1);
  state.page.importPreview={token:'newer'};finish({imported:1,skipped:0});await commit;
  assert.equal(state.page.importPreview.token,'newer');assert.equal(refreshes,0);assert.equal(form.resets,undefined);
  assert.equal(calls[2].options.body.token,'new');
  const markup=context.importMatchesMarkup({rows:[{status:'match',source_key:'x',name:'<script>',sheet:'会员',line:2,
    matches:[{target:'prereg:1',name:'<img src=x>',source:'档案',evidence:[{label:'姓名',value:'<x>'}]},
             {target:'prereg:2',name:'同名',source:'档案',evidence:[{label:'姓名',value:'同名'}]}]}]});
  assert.ok(!markup.includes('<script>')&&!markup.includes('<img src=x>'));
  assert.match(markup,/&lt;script>/);assert.match(markup,/data-import-batch disabled/);
  assert.ok(!markup.includes(' checked'));
  console.log('Import matching UI passed: explicit choices, safe labels, stale upload/confirmation isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
