'use strict';
const drawerSections=[['basics','名称与功能'],['scope','服务范围'],['policy','用户条件'],['hours','开放时间'],['broadcast','定时播报'],['dialogue','回复文案']];
function feedbackControls(bot){
  const legacy={registration_result:'registration',registration_invite:'registration',member_profile:'member_notifications',role_change:'management',activity_open:'enrollment',activity_closing:'enrollment',activity_roster:'enrollment',occupancy:'enrollment',daily_event:'daily_events',large_event:'large_events'};
  const caps=new Set(botCapabilities(bot)),selected=new Set(bot.notification_types??Object.entries(legacy).filter(([,cap])=>caps.has(cap)).map(([key])=>key)),items=state.page.botNotifications||[];
  return `<fieldset class="feedback-permissions"><legend>反馈通知</legend>${[...new Set(items.map(c=>c.group))].map(group=>`<section><h4>${e(group)}</h4><div class="feedback-options">${items.filter(c=>c.group===group).map(c=>`<label class="check-label"><input type="checkbox" name="notification_types" value="${attr(c.id)}"${c.required||selected.has(c.id)?' checked':''}${c.required?' disabled':''}><span>${e(c.name)}${c.required?'<small>固定开启 · 同群只发送一份</small>':''}</span></label>`).join('')}</div></section>`).join('')}</fieldset>`;
}
function capabilitiesToSave(bot,data){return [...new Set([...data.getAll('capabilities'),'messages',...(bot.capabilities?.includes('member_notifications')?['member_notifications']:[])])];}
function botDrawerMarkup(bot){return `<section class="bot-drawer" hidden aria-labelledby="drawer-title-${attr(bot.id)}"><div class="bot-drawer-shell"><header class="bot-drawer-heading"><div><span>机器人管理 / 设置</span><h2 id="drawer-title-${attr(bot.id)}">${e(bot.name)}</h2></div><button type="button" class="btn quiet drawer-close" aria-label="关闭机器人设置">收起 ↑</button></header><div class="bot-drawer-layout"><nav class="bot-drawer-nav" aria-label="设置分类">${drawerSections.map(([id,label])=>`<button type="button" data-drawer-section="${id}">${label}<span aria-hidden="true">›</span></button>`).join('')}</nav><label class="bot-drawer-mobile-nav">设置分类<select data-drawer-mobile>${drawerSections.map(([id,label])=>`<option value="${id}">${label}</option>`).join('')}</select></label><div class="bot-drawer-content" data-inline-settings></div></div><footer class="bot-drawer-footer"><div class="drawer-save-state"><span data-drawer-status role="status"></span><span data-drawer-error role="alert"></span></div><div class="drawer-footer-actions"><button type="button" class="btn quiet" data-action="bot-help" data-id="${attr(bot.id)}">帮助预览</button><button type="button" class="btn danger-quiet" data-action="bot-delete" data-id="${attr(bot.id)}">删除机器人</button><button type="button" class="btn primary" data-drawer-save disabled>保存配置</button></div></footer></div></section>`;}
async function openBotDrawer(card,section){
  const dialog=card.querySelector('.bot-drawer');card.dataset.drawerSection=section||card.dataset.drawerSection||'basics';
  dialog.hidden=false;card.classList.add('is-expanded');card.querySelector('.robot-card-summary').setAttribute('aria-expanded','true');
  dialog.querySelector('.drawer-close').onclick=()=>closeBotDrawer(card);
  dialog.querySelector('[data-drawer-mobile]').onchange=event=>selectDrawerSection(card,event.target.value);
  dialog.querySelectorAll('[data-drawer-section]').forEach(button=>button.onclick=()=>selectDrawerSection(card,button.dataset.drawerSection));
  await loadInlineBot(card);if(card.isConnected)selectDrawerSection(card,card.dataset.drawerSection);
}
function arrangeBotDrawer(card){
  const body=card.querySelector('[data-inline-settings]'),panels=body.querySelectorAll('.bot-inline-form>.bot-settings-grid>.bot-setting-panel');
  ['basics','scope','policy','hours'].forEach((id,index)=>{if(panels[index])panels[index].dataset.drawerPane=id;});
  const broadcast=body.querySelector('.bot-broadcast-panel'),dialogue=body.querySelector('.bot-dialogue');if(broadcast)broadcast.dataset.drawerPane='broadcast';if(dialogue)dialogue.dataset.drawerPane='dialogue';
  body.querySelector('.robot-inline-utilities')?.remove();
  if(!card.dataset.drawerBound){
    card.dataset.drawerBound='true';
    body.addEventListener('invalid',event=>{const pane=event.target.closest('[data-drawer-pane]');if(pane)selectDrawerSection(card,pane.dataset.drawerPane);},true);
    body.addEventListener('input',()=>syncDrawerFooter(card));body.addEventListener('change',()=>syncDrawerFooter(card));
    const observer=new MutationObserver(()=>syncDrawerFooter(card));observer.observe(body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','data-dirty'],characterData:true});
    card.addEventListener('drawer-forms-ready',()=>syncDrawerFooter(card));
    card.querySelector('[data-drawer-save]').onclick=()=>{const form=activeDrawerForm(card),submit=form?.querySelector('[type=submit]');if(submit&&!submit.disabled)form.requestSubmit(submit);};
  }
  selectDrawerSection(card,card.dataset.drawerSection||'basics');
}
function activeDrawerForm(card){const section=card.dataset.drawerSection;return section==='broadcast'?card.querySelector('[id^="robot-broadcast-"]'):section==='dialogue'?card.querySelector('[data-dialogue-editor] form'):card.querySelector('.bot-inline-form');}
function selectDrawerSection(card,id){
  if(!drawerSections.some(([key])=>key===id))id='basics';card.dataset.drawerSection=id;
  card.querySelectorAll('[data-drawer-section]').forEach(button=>{const active=button.dataset.drawerSection===id;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
  card.querySelector('[data-drawer-mobile]').value=id;
  const form=card.querySelector('.bot-inline-form');if(form)form.hidden=['broadcast','dialogue'].includes(id);
  card.querySelectorAll('[data-drawer-pane]').forEach(panel=>panel.hidden=panel.dataset.drawerPane!==id);
  const dialogue=card.querySelector('.bot-dialogue');if(dialogue&&id==='dialogue'){dialogue.open=true;if(!dialogue.dataset.loaded&&!dialogue.dataset.loading)loadBotDialogue(dialogue);}
  card.querySelector('.bot-drawer-content').scrollTop=0;syncDrawerFooter(card);
}
function syncDrawerFooter(card){
  if(!card.isConnected)return;const form=activeDrawerForm(card),button=card.querySelector('[data-drawer-save]'),submit=form?.querySelector('[type=submit]');
  button.disabled=!submit||submit.disabled;button.textContent=submit?.disabled?'正在保存…':card.dataset.drawerSection==='broadcast'?'保存播报':card.dataset.drawerSection==='dialogue'?'保存文案':'保存配置';
  card.querySelector('[data-drawer-status]').textContent=form?.querySelector('[data-save-status]')?.textContent||'';
  card.querySelector('[data-drawer-error]').textContent=form?.querySelector('.inline-error')?.textContent||'';
  const name=state.bots.find(b=>b.id===card.dataset.botId)?.name;if(name)card.querySelector('.bot-drawer-heading h2').textContent=name;
}

function closeBotDrawer(card){
  card.querySelector('.bot-drawer').hidden=true;card.classList.remove('is-expanded');
  const trigger=card.querySelector('.robot-card-summary');trigger.setAttribute('aria-expanded','false');trigger.focus();
}
function feedbackFeature(bot){
  const selected=bot.notification_types,enabled=selected==null||selected.length>0;
  return `<details class="robot-feedback-feature"><summary><span><strong>反馈通知</strong><small>系统与活动通知</small></span><span class="feedback-summary">${enabled?'已配置':'仅公告'}<span aria-hidden="true">⌄</span></span></summary><div class="feedback-subpermissions">${feedbackControls(bot)}</div></details>`;
}
