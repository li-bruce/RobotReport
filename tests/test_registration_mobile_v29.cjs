/* Registration on touch devices: submission recovery, accessible result focus,
   and keeping the personal registration credential out of the public guide. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8');
const source=read('register.js'),html=read('register.html');
const reference='a'.repeat(32)+'.'+'b'.repeat(43);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function page(responses,hash='#'+reference){
  const nodes={},requests=[];
  let activeElement=null;
  const node=selector=>nodes[selector]||(nodes[selector]={hidden:['#registration','#result','#retry'].includes(selector),textContent:'',disabled:selector==='button',attributes:{},fields:{name:'李明',gender:'male',level:'9',phone:''},
    querySelector:node,setAttribute(key,value){this.attributes[key]=value;},removeAttribute(key){delete this.attributes[key];},
    focus(){activeElement=selector;},reset(){this.didReset=true;},addEventListener(type,handler){this[type]=handler;}});
  const context=vm.createContext({encodeURIComponent,location:{hash},document:{querySelector:node},FormData:class{constructor(form){this.fields=form.fields;}get(key){return this.fields[key]??null;}},
    fetch:async(url,options)=>{requests.push({url,...options});const result=responses.shift();if(result instanceof Error)throw result;if(typeof result==='function')return result();return {ok:result.ok!==false,json:async()=>result.body};}});
  vm.runInContext(source,context);
  return {node,requests,context,get activeElement(){return activeElement;},submit:()=>node('#registration').submit({preventDefault(){}})};
}
const result=(extra={})=>({status:'completed',member_status:'pending',name:'李明',gender:'male',level:9,phone_masked:'',...extra});
(async()=>{
  // The guide destination is fixed; it never inherits the identity-bound hash.
  const anchor=html.match(/<a\b[^>]*href="\/guide#members"[^>]*>/)?.[0];
  assert.ok(anchor);assert.match(anchor,/rel="noreferrer"/);assert.match(anchor,/referrerpolicy="no-referrer"/);
  assert.match(html,/<meta name="referrer" content="no-referrer">/);
  assert.match(html,/name="name" required/);assert.match(html,/name="gender" required/);
  assert.doesNotMatch(html,/name="(?:phone|level)"[^>]*required/);
  assert.match(html,/name="phone" type="tel" inputmode="tel"/);
  assert.match(html,/<option value="9">9级<\/option>/);

  const invalid=page([], '#not-an-identity-token');await settle();
  assert.equal(invalid.requests.length,0);assert.equal(invalid.node('#registration').hidden,true);
  assert.match(invalid.node('#status').textContent,/本人注册链接/);
  assert.equal(invalid.node('#retry').hidden,true);

  const disconnected=page([new Error('network'),{body:{status:'draft'}}]);await settle();
  assert.equal(disconnected.node('#retry').hidden,false);await disconnected.node('#retry').click();
  assert.equal(disconnected.node('#retry').hidden,true);assert.equal(disconnected.node('#registration').hidden,false);

  const pending=page([{body:{status:'draft'}},{body:result()}]);await settle();
  assert.equal(pending.node('#registration').hidden,false);assert.equal(pending.node('button').disabled,false);
  await pending.submit();assert.equal(pending.requests.length,2);
  const sent=pending.requests[1];assert.equal(sent.url,'/public/registration/'+reference);
  assert.equal(sent.credentials,'omit');assert.equal(sent.referrerPolicy,'no-referrer');assert.equal(sent.cache,'no-store');
  assert.deepEqual(JSON.parse(sent.body),{name:'李明',gender:'male',level:9,phone:''});
  assert.equal(pending.node('#registration').hidden,true);assert.equal(pending.node('#result').hidden,false);
  assert.equal(pending.node('#result-title').textContent,'申请已提交');assert.equal(pending.activeElement,'#result-title');
  assert.match(pending.node('#next-step').textContent,/会员状态/);assert.equal(pending.node('#registration').attributes['aria-busy'],undefined);

  const linked=page([{body:result({member_status:'approved',registration_result:'linked'})}]);await settle();
  assert.equal(linked.node('#result-title').textContent,'资料已关联，注册成功');assert.match(linked.node('#next-step').textContent,/报名/);
  assert.equal(linked.activeElement,null);assert.equal(linked.requests.length,1);

  const fail=page([{body:{status:'draft'}},new Error('Failed to fetch'),{body:result()}]);await settle();
  await fail.submit();assert.equal(fail.node('#registration').hidden,false);assert.equal(fail.node('button').disabled,false);
  assert.equal(fail.node('#registration').didReset,undefined);assert.match(fail.node('#status').textContent,/连接失败/);
  assert.equal(fail.activeElement,'#status');await fail.submit();assert.equal(fail.node('#result').hidden,false);

  let release;const waiting=new Promise(resolve=>{release=resolve;});
  const repeated=page([{body:{status:'draft'}},()=>waiting]);await settle();
  const first=repeated.submit();assert.equal(repeated.node('#registration').attributes['aria-busy'],'true');
  assert.equal(repeated.node('button').disabled,true);await repeated.submit();assert.equal(repeated.requests.length,2);
  release({ok:true,json:async()=>result()});await first;assert.equal(repeated.node('#result').hidden,false);

  const expired=page([{ok:false,body:{detail:'链接已失效，请重新发送注册'}}]);await settle();
  assert.equal(expired.node('#registration').hidden,true);assert.match(expired.node('#status').textContent,/链接已失效/);
  assert.ok(!expired.node('#status').textContent.includes(reference));

  const plain=page([{body:{status:'draft'}},()=>({ok:false,json:async()=>{throw Error('not json');}})]);await settle();
  await plain.submit();assert.match(plain.node('#status').textContent,/服务暂时无法响应/);assert.equal(plain.node('button').disabled,false);
  console.log('v2.9 registration UI passed: token-free help, optional fields, draft/link/error states, keyboard focus, safe retry and duplicate-tap protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
