'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/import-registration.js'),'utf8');
const preview=(id='a',channel='channel-a')=>({text:'确认会员资料',signature:'s'.repeat(64),request_id:id.repeat(32),channel_id:channel});

function harness(respond){
  const calls=[],toasts=[],listeners={},area={innerHTML:'',replaceChildren(){this.innerHTML='';}};
  const button={dataset:{},textContent:'预览通知'};
  const selector={value:'channel-a',addEventListener:(name,callback)=>listeners[name]=callback};
  const form={isConnected:true,querySelector:selector=>selector==='#invite-preview'?area:button};
  const state={epoch:1,page:{selectedImportIds:new Set([7])}};
  let callback,closed=0,renderFailure=false;
  const context=vm.createContext({
    state,document:{addEventListener(){}},nf:String,selectfield:()=>'',formFooter:()=>'',openModal(){},
    $:key=>key==='#f-channel_id'?selector:key==='#invite-preview'?area:button,
    bindForm:(_id,handler)=>callback=handler,
    markdown:text=>{if(renderFailure){renderFailure=false;throw Error('render failed');}return text;},
    closeModal(){closed++;form.isConnected=false;},toast:text=>toasts.push(text),
    api:async(url,options)=>{
      if(url.endsWith('/invite-channels'))return [{id:'channel-a',bot_name:'会员机器人',name:'活动群'}];
      calls.push({url,body:JSON.parse(JSON.stringify(options.body))});
      return respond(url,options.body,calls);
    }
  });
  Object.defineProperty(context,'crypto',{get(){throw Error('Browser crypto must not be required');}});
  vm.runInContext(source,context);
  return {calls,area,button,selector,form,state,toasts,
    open:()=>context.inviteImportedMembers(),
    submit:()=>callback({get:()=>selector.value},form,state.epoch),
    change:value=>{selector.value=value;listeners.change();},
    failRender:()=>renderFailure=true,
    closed:()=>closed};
}

(async()=>{
  let sendAttempts=0;
  const retry=harness(async url=>{
    if(url.endsWith('/invite-preview'))return preview();
    if(sendAttempts++===0)throw Error('connection lost after server accepted');
    return {queued:1};
  });
  await retry.open();await retry.submit();
  assert.equal(retry.calls.length,1);
  assert.match(retry.area.innerHTML,/确认会员资料/);
  assert.equal(retry.button.dataset.idleLabel,'发送确认通知');
  await assert.rejects(retry.submit(),/connection lost/);
  assert.equal(retry.closed(),0);
  await retry.submit();
  assert.equal(retry.calls[1].body.request_id,'a'.repeat(32));
  assert.deepEqual(retry.calls[2].body,retry.calls[1].body);
  assert.equal(retry.closed(),1);

  let version=0;
  const invalidated=harness(async url=>{
    if(url.endsWith('/invite-preview'))return preview(version++?'b':'a');
    throw Object.assign(Error('名单已变化，请重新预览后发送'),{status:400});
  });
  await invalidated.open();await invalidated.submit();
  await assert.rejects(invalidated.submit(),/名单已变化/);
  assert.equal(invalidated.button.dataset.idleLabel,'预览通知');
  assert.equal(invalidated.area.innerHTML,'');
  await invalidated.submit();
  assert.ok(invalidated.calls[2].url.endsWith('/invite-preview'));
  assert.equal(version,2);

  let previews=0;
  const malformed=harness(async url=>{
    assert.ok(url.endsWith('/invite-preview'),'Incomplete preview must not be sent');
    return previews++===0?{...preview(),request_id:null}:preview('b');
  });
  await malformed.open();
  await assert.rejects(malformed.submit(),/预览未完成/);
  assert.equal(malformed.area.innerHTML,'');
  await malformed.submit();
  assert.equal(malformed.calls.length,2);
  assert.equal(malformed.button.dataset.idleLabel,'发送确认通知');

  const render=harness(async url=>{
    assert.ok(url.endsWith('/invite-preview'),'Failed rendering must remain in preview mode');
    return preview();
  });
  await render.open();render.failRender();
  await assert.rejects(render.submit(),/render failed/);
  await render.submit();
  assert.equal(render.calls.length,2);

  let resolve;
  const changing=harness(async(url,body)=>{
    if(body.channel_id==='channel-a')return new Promise(done=>resolve=done);
    return preview('c','channel-b');
  });
  await changing.open();
  const stale=changing.submit();
  changing.change('channel-b');
  resolve(preview());await stale;
  assert.equal(changing.area.innerHTML,'');
  assert.equal(changing.button.dataset.idleLabel,'预览通知');
  await changing.submit();
  assert.equal(changing.calls[1].body.channel_id,'channel-b');
  changing.change('channel-a');
  assert.equal(changing.area.innerHTML,'');

  let finish;
  const detached=harness(async()=>new Promise(done=>finish=done));
  await detached.open();
  const pending=detached.submit();detached.form.isConnected=false;
  finish(preview());await pending;
  assert.equal(detached.area.innerHTML,'');
  console.log('Invitation UI passed: no browser crypto, complete previews, render failure recovery, stable send retry, stale roster recovery, channel invalidation and detached response isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
