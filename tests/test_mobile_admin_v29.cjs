/* Phone navigation behavior and shared responsive admin contracts. No network. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static',name),'utf8');
const source=read('app.js'),css=read('style.css'),nodes=new Map(),handlers={},frames=[],scrolls=[];
let mobile=true,document;
class Element{
  constructor(name){this.name=name;this.dataset={};this.attributes={};this.handlers={};this.hidden=false;this.isConnected=true;this.inert=false;this.innerHTML='';this.style={values:{},setProperty(k,v){this.values[k]=v;},removeProperty(k){delete this.values[k];}};const classes=new Set();this.classList={contains:k=>classes.has(k),add:k=>classes.add(k),remove:k=>classes.delete(k),toggle(k,on){if(on===undefined)on=!classes.has(k);on?classes.add(k):classes.delete(k);return on;}};}
  setAttribute(k,v){this.attributes[k]=String(v);}
  removeAttribute(k){delete this.attributes[k];}
  focus(){document.activeElement=this;}
  addEventListener(k,f){this.handlers[k]=f;}
  querySelector(selector){return nodes.get(selector)||null;}
  querySelectorAll(selector){return this.name==='sidebar'&&selector.startsWith('a[href]')?[node('.nav-close'),node('.nav-first'),node('.nav-last')]:[];}
  showModal(){this.open=true;}
  close(){this.open=false;this.handlers.close?.({});}
}
const node=(selector,name=selector)=>{if(!nodes.has(selector))nodes.set(selector,new Element(name));return nodes.get(selector);};
const sidebar=node('#primary-navigation','sidebar'),shell=node('.shell'),toggle=node('.menu-toggle'),workspace=node('.workspace'),modal=node('#modal'),body=new Element('body');
node('.nav-close');node('.nav-first');node('.nav-last');node('.nav-backdrop');node('#app');node('#venue-select');node('#main');
const table=node('.wide-table'),calendar=node('.calendar-scroll');calendar.classList.add('calendar-scroll');table.clientWidth=300;table.scrollWidth=680;calendar.clientWidth=300;calendar.scrollWidth=308;
document={body,activeElement:toggle,querySelector:s=>nodes.get(s)||null,querySelectorAll:s=>s==='.table-wrap,.calendar-scroll'?[table,calendar]:[],addEventListener:(k,f)=>handlers[k]=f};
const window={scrollY:175,matchMedia:()=>({matches:mobile}),scrollTo:value=>scrolls.push(value),addEventListener:(k,f)=>handlers[`window:${k}`]=f};
const ctx=vm.createContext({console,URL,URLSearchParams,Intl,Date,Map,Set,Number,String,Array,JSON,AbortController,document,window,clearTimeout(){},requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},MutationObserver:class{constructor(fn){this.fn=fn;}observe(){}},location:{origin:'http://localhost',hash:''},history:{replaceState(){}},sessionStorage:{getItem:()=>null}});
vm.runInContext(read('workspace.js')+'\n'+read('calendar-visual.js')+'\n'+source.slice(0,source.indexOf("document.addEventListener('click'")),ctx);
const run=code=>vm.runInContext(code,ctx),key=(value,shift=false)=>{const event={key:value,shiftKey:shift,preventDefault(){this.prevented=true;}};ctx.event=event;run('handleNavigationKey(event)');return event;};
(async()=>{
  run('installResponsiveUI();renderShell()');frames.splice(0).forEach(fn=>fn());
  assert.match(node('#app').innerHTML,/aria-controls="primary-navigation"/);assert.match(node('#app').innerHTML,/data-action="menu-close"/);assert.match(node('#app').innerHTML,/href="\/guide\/admin"/);
  assert.equal(sidebar.inert,true);assert.equal(sidebar.attributes['aria-hidden'],'true');assert.equal(toggle.attributes['aria-expanded'],'false');
  run('setMobileNavigation(true)');
  assert.equal(shell.classList.contains('nav-open'),true);assert.equal(sidebar.inert,false);assert.equal(workspace.inert,true);assert.equal(sidebar.attributes['aria-modal'],'true');assert.equal(toggle.attributes['aria-expanded'],'true');assert.equal(node('.nav-backdrop').hidden,false);assert.equal(document.activeElement,node('.nav-close'));assert.equal(body.classList.contains('overlay-open'),true);assert.equal(body.style.values['--overlay-top'],'-175px');
  document.activeElement=node('.nav-last');assert.equal(key('Tab').prevented,true);assert.equal(document.activeElement,node('.nav-close'));
  assert.equal(key('Tab',true).prevented,true);assert.equal(document.activeElement,node('.nav-last'));
  assert.equal(key('Escape').prevented,true);assert.equal(document.activeElement,toggle);assert.equal(workspace.inert,false);assert.equal(body.classList.contains('overlay-open'),false);assert.equal(scrolls.at(-1).top,175);assert.equal(sidebar.inert,true);assert.equal(toggle.attributes['aria-expanded'],'false');
  run('setMobileNavigation(true)');mobile=false;handlers['window:resize']();assert.equal(shell.classList.contains('nav-open'),false);assert.equal(sidebar.inert,false);assert.equal(sidebar.attributes['aria-hidden'],'false');assert.equal(document.activeElement,node('#main'));assert.equal(workspace.inert,false);assert.equal(body.classList.contains('overlay-open'),false);
  run('setMobileNavigation(true)');assert.equal(shell.classList.contains('nav-open'),false);
  mobile=true;document.activeElement=toggle;run('setMobileNavigation(true);renderRoute=async()=>{}');await run("navigate('members')");assert.equal(toggle.attributes['aria-expanded'],'false');assert.equal(body.classList.contains('overlay-open'),false);assert.equal(document.activeElement,toggle);
  run("openModal('测试','<form>内容</form>')");assert.equal(body.classList.contains('overlay-open'),true);assert.equal(modal.open,true);const cancel={preventDefault(){this.prevented=true;}};modal.handlers.cancel(cancel);assert.equal(cancel.prevented,true);assert.equal(modal.open,false);assert.equal(body.classList.contains('overlay-open'),false);
  run('enhanceScrollRegions()');assert.equal(table.dataset.overflow,'true');assert.equal(table.attributes.tabindex,'0');assert.match(table.attributes['aria-label'],/表格/);assert.match(calendar.attributes['aria-label'],/日历/);
  table.scrollWidth=300;calendar.scrollWidth=300;run('enhanceScrollRegions()');assert.equal(table.dataset.overflow,'false');assert.equal(table.attributes.tabindex,undefined);assert.equal(table.attributes.role,undefined);
  assert.match(run("calendarGrid([], '2026-10')"),/calendar-scroll/);assert.match(run("calendarGrid([], '2026-10')"),/aria-label="2026-10-01[^\"]*0 人报名[^\"]*0 场[^\"]*0 个场地"/);
  run('state.authed=true;state.epoch=3;state.viewEpoch=3;globalThis.finderForms={};globalThis.finderCalls=[];bindForm=(id,callback)=>finderForms[id]=callback');await run('renderGuide(3)');const guide=node('#main').innerHTML;
  for(const path of ['/guide','/guide/admin'])assert.ok(guide.includes(`href="${path}"`));for(const title of ['普通用户版','管理员版'])assert.ok(guide.includes(title));assert.ok(!guide.includes('一句最多 8 步'));assert.ok(!guide.includes('12位确认码'));assert.ok(!guide.includes('浏览全部功能'));
  assert.match(guide,/id="ai-navigate-form"/);assert.match(guide,/查找功能/);assert.equal(typeof ctx.finderForms['ai-navigate-form'],'function');
  const finder=node('#ai-navigation-result');ctx.featureReply={feature:{name:'批量登记',description:'不显示长解释',steps:['不显示实施说明'],admin_route:'/admin#member-imports'}};
  run("api=async(path,options)=>{finderCalls.push({path,options});return featureReply}");
  await run("finderForms['ai-navigate-form']({get:()=> '  批量导入会员  '},{},3)");
  assert.equal(ctx.finderCalls[0].path,'/api/ai/navigate');assert.equal(ctx.finderCalls[0].options.method,'POST');assert.equal(ctx.finderCalls[0].options.body.content,'批量导入会员');assert.match(finder.innerHTML,/data-route="member-imports"/);assert.ok(!finder.innerHTML.includes('长解释'));assert.ok(!finder.innerHTML.includes('实施说明'));
  ctx.featureReply={feature:{name:'<img src=x>',command:'名单'}};await run("finderForms['ai-navigate-form']({get:()=> '查看名单'},{},3)");assert.match(finder.innerHTML,/群内发送/);assert.match(finder.innerHTML,/<code>名单<\/code>/);assert.ok(!finder.innerHTML.includes('<img'));assert.match(finder.innerHTML,/&lt;img/);
  ctx.featureReply={message:'请补充想查的内容'};await run("finderForms['ai-navigate-form']({get:()=> '查询'},{},3)");assert.match(finder.innerHTML,/请补充想查的内容/);
  finder.innerHTML='unchanged';run('state.viewEpoch=4');await run("finderForms['ai-navigate-form']({get:()=> '查询'},{},3)");assert.equal(finder.innerHTML,'unchanged');run('state.viewEpoch=3');await run("finderForms['ai-navigate-form']({get:()=> '查询'},{},2)");assert.equal(finder.innerHTML,'unchanged');
  assert.match(run("renderTimeline([{action:'member.roles',created_at:'2026-10-14T10:00:00+08:00'},{action:'registration.enroll'},{action:'future.action'}])"),/调整管理角色/);assert.match(run("activityActionName('registration.enroll')"),/活动报名/);assert.equal(run("activityActionName('future.action')"),'future.action');
  assert.match(read('index.html'),/viewport-fit=cover/);assert.match(read('index.html'),/app.js\?v=1\.0/);
  for(const width of [320,360,390,768]){assert.ok(width<=950);assert.match(css,/@media\(max-width:950px\)/);assert.match(css,/\.field input,\.field select,\.field textarea,input.control,select.control,textarea.control\{font-size:16px;min-height:44px\}/);}
  assert.match(css,/\.calendar-scroll \.calendar\{min-width:308px;grid-template-columns:repeat\(7,minmax\(44px,1fr\)\)\}/);
  assert.match(css,/\.scope-slot-options,\.role-directory\{grid-template-columns:minmax\(0,1fr\)\}/);assert.match(css,/max-height:calc\(100dvh - 16px/);assert.match(css,/env\(safe-area-inset-bottom\)/);assert.match(css,/左右滑动查看完整内容/);
  assert.match(css,/\.day-check\{position:relative\}/);assert.match(css,/\.day-check input\[type=checkbox\]\{inset:0;width:1px;min-width:1px;max-width:1px;height:1px;min-height:1px;max-height:1px;margin:0;padding:0;border:0;clip-path:inset\(50%\)\}/);
  assert.match(css,/\.day-check input:focus-visible\+span/);assert.ok(!run('slotRow()').includes('tabindex="-1"'));assert.ok(!run('slotRow()').includes('aria-hidden="true"'));
  assert.match(css,/\.table-wrap \.actions-cell\{white-space:nowrap;min-width:110px;max-width:none\}/);
  console.log('v2.9 mobile admin checks passed: drawer focus/Escape/resize/navigation, background lock and restoration, dialog lock, dynamic scroll regions, guide entries, touch/input/safe-area contracts.');
})().catch(error=>{console.error(error);process.exitCode=1;});
