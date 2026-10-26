'use strict';
// Robot settings stay inside their owning row. Saves never rebuild other drafts.
function botBroadcastRules(bot){
  if(state.page.botBroadcastOverrides?.[bot.id])return state.page.botBroadcastOverrides[bot.id];
  const config=state.page.botBroadcastConfig||{};
  return (config.broadcast_rules??Object.entries(config.reminders||{}).map(([kind,time])=>({id:'legacy-'+kind,name:kind,enabled:true,kinds:[kind],time,when:{},bot_ids:state.bots.map(b=>b.id)})))
    .filter(r=>r.bot_ids.includes(bot.id)&&!(r.excluded_bot_ids||[]).includes(bot.id));
}
function robotScheduleSummary(bot){
  const slots=(state.page.botScopeSlots||[]).filter(s=>(bot.venue_ids==null||bot.venue_ids.includes(s.venue_id))&&(bot.slot_keys==null||bot.slot_keys.includes(s.slot_key)));
  const groups=new Map(),multipleVenues=new Set(slots.map(s=>s.venue_id)).size>1;
  for(const s of slots){
    const period=s.holiday?'节假日':s.special_date||weekdaySummary(s.weekdays)+(s.skip_holidays?'（跳过节假日）':''),key=(multipleVenues?s.venue_name+' · ':'')+period;
    const name=s.name.replace(/^(?:工作日|周末|法定节假日|节假日)\s*/,'');
    const items=groups.get(key)||new Set();items.add(`${name} ${s.start}–${s.end}`);groups.set(key,items);
  }
  const labels=[...groups].map(([period,items])=>`${period} · ${[...items].slice(0,2).join(' / ')}${items.size>2?` · 另 ${items.size-2} 场`:''}`),shown=labels.slice(0,3);
  const times=[...new Set(botBroadcastRules(bot).filter(r=>r.enabled).map(r=>r.time))].sort();
  return `<span class="robot-summary-times"><span class="robot-summary-key">场次</span><span class="robot-time-tags">${shown.map(s=>`<span>${e(s)}</span>`).join('')||`<span>${bot.slot_keys?.length===0?'未选择场次':bot.slot_keys==null?'全部场次':'暂无匹配场次'}</span>`}${labels.length>3?`<span class="robot-time-more">另 ${labels.length-3} 组时段</span>`:''}</span></span>${botCapabilities(bot).includes('enrollment')?`<span class="robot-summary-times"><span class="robot-summary-key">播报</span><span>${state.page.botBroadcastConfig?.daily_enabled===false?'全局暂停':!isActive(bot.active)?'机器人已停用':times.length?times.join(' / '):botBroadcastRules(bot).length?'规则已停用':'未设置'}</span></span>`:''}`;
}
function robotSettingsSummary(bot){
  const channel=state.channels.find(c=>c.bot_id===bot.id&&!c.archived),[status,tone]=botStatus(bot,channel);
  const venues=bot.venue_ids==null?'全部场地':bot.venue_ids.map(id=>state.venues.find(v=>v.id===id)?.name||`场地 ${id}`).join('、')||'未选择场地';
  return `<span class="robot-summary-main"><span class="robot-summary-title"><strong>${e(bot.name)}</strong><span class="badge ${tone}">${status}</span></span><span class="robot-summary-meta">${e(channel?.name||'未关联群')}<span>·</span>${e(venues)}</span><span class="robot-summary-business" aria-label="业务组合">${botCapabilityBadges(bot)}</span>${robotScheduleSummary(bot)}</span><span class="robot-disclosure-label"><span class="when-closed">设置</span><span class="when-open">收起</span></span>`;
}
function settingsPanel(title,description,body,extra=''){
  return `<section class="bot-setting-panel ${extra}"><header><h3>${e(title)}</h3>${description?`<p>${e(description)}</p>`:''}</header><div class="bot-setting-body">${body}</div></section>`;
}
function uniqueBotFields(html,id){return html.replace(/\b(id|for)="f-([^"]+)"/g,(_,key,name)=>`${key}="f-${attr(id)}-${name}"`);}
function inlineBotForm(bot,slots){
  const enabled=new Set(botCapabilities(bot));
  const basics=field('机器人名称','name',bot.name,'text','required maxlength="80"')+`<label class="check-label"><input type="checkbox" name="active"${isActive(bot.active)?' checked':''}>启用机器人</label><fieldset class="robot-capability-field"><legend>业务功能</legend><div class="robot-capabilities">${botCapabilityCatalog().map(c=>`<label class="robot-capability"><input type="checkbox" name="capabilities" value="${attr(c.id)}"${enabled.has(c.id)?' checked':''}><span><strong>${e(c.name)}</strong></span></label>`).join('')}</div></fieldset>`;
  let policy=botPolicyForm(bot.user_policy).replace(/<details[^>]*><summary>.*?<\/summary>/,'').replace(/<\/details>$/,'');
  return uniqueBotFields(`<form id="robot-form-${attr(bot.id)}" class="bot-inline-form"><div class="bot-settings-grid">${settingsPanel('名称与功能','',basics)}${settingsPanel('服务范围','',scopeControls(bot,slots,{bot:true}))}${settingsPanel('用户条件','',policy)}${settingsPanel('功能开放时间','设置日期与时间范围。','<div data-bot-time-rules></div>')}</div><div class="bot-inline-save"><span data-save-status role="status"></span><div class="inline-error" role="alert"></div><button type="submit" class="btn primary">保存机器人设置</button></div></form>`,bot.id);
}
function markBotDirty(form){
  form.dataset.dirty='true';const status=form.querySelector('[data-save-status]');if(status)status.textContent='有未保存的修改';
}
function trackBotDraft(form){
  form.addEventListener('invalid',event=>{let parent=event.target.parentElement;while(parent&&parent!==form){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}},true);
  form.addEventListener('input',()=>markBotDirty(form));
  form.addEventListener('change',()=>markBotDirty(form));
  form.addEventListener('click',event=>{if(event.target.closest('[data-add-rule],[data-remove-rule],[data-rule-add],[data-rule-remove]'))markBotDirty(form);});
}
function robotSaved(form){form.dataset.dirty='false';form.querySelector('[data-save-status]').textContent='已保存';}
async function botSaveRequest(form,path,options){
  const fields=[...form.querySelectorAll('input,select,textarea,button')].map(el=>[el,el.disabled]);
  fields.forEach(([el])=>el.disabled=true);
  try{return await api(path,options);}finally{fields.forEach(([el,disabled])=>el.disabled=disabled);}
}
function updateRobotSummary(card,bot){card.querySelector(':scope>summary').innerHTML=robotSettingsSummary(bot);}
async function loadInlineBot(card){
  if(card.dataset.loaded||card.dataset.loading)return;
  card.dataset.loading='true';const body=card.querySelector('[data-inline-settings]'),id=card.dataset.botId;
  body.innerHTML=loading();
  try{
    const [attributes,broadcasts]=await Promise.all([api('/api/bot-user-attributes'),api(`/api/bots/${encodeURIComponent(id)}/broadcasts`)]);
    if(!card.isConnected)return;
    let bot=state.bots.find(b=>b.id===id),revision=broadcasts.revision;
    body.innerHTML=inlineBotForm(bot,state.page.botScopeSlots)+settingsPanel('定时播报','按北京时间发送至该机器人关联的群。',`<form id="robot-broadcast-${attr(id)}"><div data-broadcast-notice></div><div data-broadcast-rules></div><div class="bot-inline-save"><span data-save-status role="status"></span><div class="inline-error" role="alert"></div><button type="submit" class="btn primary">保存定时播报</button></div></form>`,'bot-broadcast-panel')+`<div class="robot-inline-utilities"><button type="button" class="btn small quiet" data-action="bot-help" data-id="${attr(id)}">帮助预览</button><button type="button" class="btn small danger-quiet" data-action="bot-delete" data-id="${attr(id)}">删除机器人</button></div>`;
    const form=body.querySelector('.bot-inline-form'),broadcastForm=body.querySelector('[id^="robot-broadcast-"]');
    bindScopeControls(form);
    const readPolicy=bindBotPolicy(form,bot.user_policy,attributes);
    const readTime=bindTimeRules(form.querySelector('[data-bot-time-rules]'),bot.capability_rules,{mode:'capabilities',capabilities:()=>[...form.querySelectorAll('[name=capabilities]:checked')].map(el=>el.value)});
    form.querySelectorAll('[name=capabilities]').forEach(el=>el.addEventListener('change',()=>readTime.refresh()));
    const readBroadcast=bindTimeRules(broadcastForm.querySelector('[data-broadcast-rules]'),broadcasts.rules,{mode:'broadcast',ownerId:id,bots:[]});
    const refreshBroadcast=()=>{
      const enabled=botCapabilities(bot).includes('enrollment'),channel=state.channels.find(c=>c.bot_id===id&&!c.archived);
      broadcastForm.querySelector('[data-broadcast-notice]').innerHTML=!enabled?'<p class="notice">开启“活动报名”后可配置定时播报。</p>':!isActive(bot.active)?'<p class="notice">机器人已停用，定时播报暂停。</p>':!channel?'<p class="notice">关联群后开始播报。</p>':!isActive(channel.active)||!channel.daily_notifications?'<p class="notice">群连接的活动提醒已暂停。</p>':!broadcasts.daily_enabled?'<p class="notice">全局播报已暂停。</p>':'';
      broadcastForm.querySelector('[data-broadcast-rules]').hidden=!enabled;
      broadcastForm.querySelector('[type=submit]').disabled=!enabled;
    };
    refreshBroadcast();trackBotDraft(form);trackBotDraft(broadcastForm);
    bindForm(form.id,async(data,_form,epoch)=>{
      const saved=await botSaveRequest(form,`/api/bots/${encodeURIComponent(id)}`,{method:'PATCH',body:{name:data.get('name').trim(),active:data.has('active'),capabilities:data.getAll('capabilities'),...scopeBody(data,true),user_policy:readPolicy(),capability_rules:readTime().filter(r=>r.capabilities.length)}});
      if(epoch!==state.epoch||!card.isConnected)return;
      bot=saved;state.bots=state.bots.map(b=>b.id===id?saved:b);robotSaved(form);updateRobotSummary(card,bot);refreshBroadcast();toast('机器人设置已保存。');
    });
    bindForm(broadcastForm.id,async(_data,_form,epoch)=>{
      let result;
      try{result=await botSaveRequest(broadcastForm,`/api/bots/${encodeURIComponent(id)}/broadcasts`,{method:'PUT',body:{rules:readBroadcast(),revision}});}
      catch(error){
        if(broadcastForm.isConnected&&!broadcastForm.querySelector('[data-reload-broadcast]')){
          const button=document.createElement('button');button.type='button';button.className='btn quiet';button.dataset.reloadBroadcast='';button.textContent='重新载入播报';
          button.onclick=async()=>{
            if(!window.confirm('重新载入将放弃此处未保存的播报修改，继续吗？'))return;
            try{const latest=await api(`/api/bots/${encodeURIComponent(id)}/broadcasts`);if(!broadcastForm.isConnected)return;revision=latest.revision;readBroadcast.replace(latest.rules);robotSaved(broadcastForm);broadcastForm.querySelector('.inline-error').textContent='';button.remove();}
            catch(err){broadcastForm.querySelector('.inline-error').textContent=err.message;}
          };
          broadcastForm.querySelector('.bot-inline-save').append(button);
        }
        throw error;
      }
      if(epoch!==state.epoch||!card.isConnected)return;
      revision=result.revision;readBroadcast.replace(result.rules);robotSaved(broadcastForm);
      broadcastForm.querySelector('[data-reload-broadcast]')?.remove();
      state.page.botBroadcastOverrides??={};state.page.botBroadcastOverrides[id]=result.rules;
      updateRobotSummary(card,bot);
      toast('定时播报已保存。');
    });
    card.dataset.loaded='true';
  }catch(error){if(card.isConnected){body.innerHTML=`<p role="alert" class="notice">${e(error.message)}</p><button type="button" class="btn" data-retry-settings>重新载入</button>`;body.querySelector('button').onclick=()=>loadInlineBot(card);}}
  finally{delete card.dataset.loading;}
}
function bindInlineBotEditors(){
  document.querySelectorAll('.robot-manager>.robot-card').forEach(card=>{card.addEventListener('toggle',()=>{if(card.open)loadInlineBot(card);});if(card.open)loadInlineBot(card);});
}
async function revealBotSettings(id,section){
  if(state.route!=='bots')await navigate('bots');
  const card=[...document.querySelectorAll('.robot-manager>.robot-card')].find(el=>el.dataset.botId===id)||document.querySelector('.robot-manager>.robot-card');
  if(!card)return;card.open=true;await loadInlineBot(card);(section==='broadcast'?card.querySelector('.bot-broadcast-panel'):card)?.scrollIntoView({block:'start'});
}
window.addEventListener('beforeunload',event=>{if(document.querySelector('.robot-manager form[data-dirty=true]')){event.preventDefault();event.returnValue='';}});
