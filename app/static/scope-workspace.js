'use strict';
async function scopeSlots(){const lists=await Promise.all(state.venues.map(async v=>{const [weekly,special,holiday]=await Promise.all([api(query('/api/slots',{venue_id:v.id})),api(query('/api/special-dates',{venue_id:v.id})),api(query('/api/holiday-schedule',{venue_id:v.id}))]);return {venue:v,slots:[...asList(weekly,'slots'),...asList(holiday.available_slots||holiday.slots).map(s=>({...s,holiday:true})),...asList(special).filter(day=>day.date>=today()).flatMap(day=>day.slots.filter(slot=>!asList(weekly,'slots').some(s=>s.slot_key===slot.slot_key)).map(slot=>({...slot,special_date:day.date})))]};}));return lists.flatMap(({venue,slots})=>slots.map(s=>({...s,venue_id:venue.id,venue_name:venue.name})));}
function scopeControls(value,slots,{bot=false}={}){
  const venueIds=value.venue_ids,keys=value.slot_keys;
  return `${bot?`<fieldset class="field"><legend>负责的场地</legend>${selectfield('场地范围','venue_mode',[['all','全部场地'],['selected','仅所选场地']],venueIds===null||venueIds===undefined?'all':'selected')}<div class="checks">${state.venues.map(v=>`<label class="check-label"><input type="checkbox" name="scope_venue_ids" value="${attr(v.id)}"${venueIds?.map(String).includes(String(v.id))?' checked':''}>${e(v.name)}</label>`).join('')}</div></fieldset>`:''}<fieldset class="field"><legend>负责的活动时段</legend>${selectfield('场次范围','slot_mode',[['all',bot?'全部时段':'机器人允许的全部时段'],['selected','仅所选时段']],keys===null||keys===undefined?'all':'selected')}<div class="scope-slot-options">${slots.map(s=>`<label class="check-label"><input type="checkbox" name="slot_keys" value="${attr(s.slot_key)}"${keys?.includes(s.slot_key)?' checked':''}><span>${e(s.venue_name)} · ${e(s.name)} ${e(s.start)}–${e(s.end)}<small>${s.holiday?'法定节假日':s.special_date?e(s.special_date):'周'+(s.weekdays||[]).map(d=>'一二三四五六日'[d]).join('、')}</small></span></label>`).join('')||'<p class="caption">暂无活动时段，请先在活动运营中配置。</p>'}</div><small>选择“仅所选”但不勾选时，停止负责任何场次。时段调整后自动跟随；大型赛事单独安排赛程，不受日常活动时段限制。</small></fieldset>`;
}
function bindScopeControls(form){const refresh=()=>{const slotsAll=form.elements.slot_mode.value==='all';$$('[name=slot_keys]',form).forEach(el=>el.disabled=slotsAll);if(form.elements.venue_mode){const all=form.elements.venue_mode.value==='all';$$('[name=scope_venue_ids]',form).forEach(el=>el.disabled=all);}};form.elements.slot_mode.onchange=refresh;if(form.elements.venue_mode)form.elements.venue_mode.onchange=refresh;refresh();}
function scopeBody(data,bot=false){return {...(bot?{venue_ids:data.get('venue_mode')==='all'?null:data.getAll('scope_venue_ids').map(Number)}:{}),slot_keys:data.get('slot_mode')==='all'?null:data.getAll('slot_keys')};}

function botCapabilityCatalog(){return state.page.botCapabilities||[];}
function botCapabilities(bot){return bot.capabilities===undefined?botCapabilityCatalog().filter(c=>c.default_enabled).map(c=>c.id):(bot.capabilities||[]);}
function botCapabilityBadges(bot){const enabled=new Set(botCapabilities(bot));return botCapabilityCatalog().filter(c=>enabled.has(c.id)).map(c=>`<span class="badge outline">${e(c.name)}</span>`).join('')||'<span class="caption">未开启业务</span>';}
function slotScopeLabel(s){
  const days=(s.weekdays||[]).join(','),period=s.holiday?'节假日':s.special_date||({'0,1,2,3,4':'工作日','5,6':'周末','0,1,2,3,4,5,6':'每天'}[days]||'周'+(s.weekdays||[]).map(d=>'一二三四五六日'[d]).join('、'));
  const name=s.name.replace(/^(?:工作日|周末|法定节假日|节假日)\s*/, '');
  return `${period} ${name} ${s.start}–${s.end}`;
}
function scopeSummary(bot){
  const venues=bot.venue_ids==null?'全部场地':bot.venue_ids.length?bot.venue_ids.map(id=>state.venues.find(v=>String(v.id)===String(id))?.name||`场地 ${id}`).join('、'):'未选择场地';
  if(bot.slot_keys?.length===0)return `${venues} · 未选择场次`;
  const slots=(state.page.botScopeSlots||[]).filter(s=>(bot.venue_ids==null||bot.venue_ids.includes(s.venue_id))&&(bot.slot_keys==null||bot.slot_keys.includes(s.slot_key)));
  const labels=[...new Set(slots.map(slotScopeLabel))];
  return `${venues} · ${labels.join('；')||(bot.slot_keys==null?'全部场次':`指定 ${bot.slot_keys.length} 个场次`)}`;
}
function channelScopeSummary(c){return scopeSummary({venue_ids:c.bot_venue_ids,slot_keys:c.bot_slot_keys});}
function groupedBots(bots,channels,known=state.page.botGroups||[]){
  const groups=new Map(known.map(g=>[g.id,{...g,entries:[]}])),unbound=[];
  for(const bot of bots){
    const mine=channels.filter(c=>!c.archived&&String(c.bot_id)===String(bot.id));
    if(!mine.length){unbound.push({bot,channel:null});continue;}
    for(const channel of mine){
      const id=channel.group_id||channel.chat_id;
      if(!groups.has(id))groups.set(id,{id,chat_id:channel.chat_id,name:channel.name||'待识别的群',entries:[]});
      groups.get(id).entries.push({bot,channel,legacy:mine.length>1});
    }
  }
  const result=[...groups.values()];
  if(unbound.length)result.push({id:'unbound',chat_id:'',name:'未绑定群',unbound:true,entries:unbound});
  return result;
}
function disclosureOpen(key,defaultOpen=false){return (state.botDisclosure?.get(key)??defaultOpen)?' open':'';}
function botStatus(bot,channel){
  if(!isActive(bot.active))return ['已停用',''];
  if(!channel)return ['待连接','amber'];
  if(!isActive(channel.active))return ['已暂停',''];
  if(channel.connection_status==='needs_test')return ['待测试发送','amber'];
  if(channel.connection_status==='waiting_callback')return ['待配置回调','amber'];
  if(channel.connection_status==='waiting_message'||channel.awaiting_callback)return ['等待群内验证','amber'];
  if(!channel.configured)return ['待配置','amber'];
  if(!channel.receive_commands)return ['仅发送','blue'];
  return ['已连接','green'];
}
function compactBotCard({bot,channel,legacy}){
  const key=`bot:${bot.id}:${channel?.id||'unbound'}`,[status,tone]=botStatus(bot,channel),data=`data-bot-id="${attr(bot.id)}" data-id="${attr(channel?.id||'')}"`;
  return `<details class="robot-card" data-bot-id="${attr(bot.id)}" data-disclosure-key="${attr(key)}"${disclosureOpen(key)}>
    <summary class="robot-card-summary"><span class="robot-summary-main"><span class="robot-summary-title"><strong>${e(bot.name)}</strong><span class="badge ${tone}">${status}</span></span><span class="robot-summary-scope">${e(scopeSummary(bot))}${bot.user_policy?.enabled?` · ${bot.user_policy.rules.length} 项用户条件`:''}${bot.capability_rules?.length?` · ${bot.capability_rules.length} 项时间规则`:''}</span></span><span class="robot-summary-business" aria-label="业务组合">${botCapabilityBadges(bot)}</span><span class="robot-disclosure-label"><span class="when-closed">展开</span><span class="when-open">收起</span></span></summary>
    <div class="robot-card-content">${legacy?'<p class="notice warning">请保留一个当前群连接，移除多余连接。</p>':''}
      <div class="robot-setup-grid"><section class="robot-setup-panel"><div><h3>机器人设置</h3><p>业务、场地、场次与用户条件</p></div>${btn('编辑设置','bot-edit','small',`data-id="${attr(bot.id)}"`)}</section><section class="robot-setup-panel"><div><h3>群连接</h3><p>${channel?.connection_status==='connected'?'消息收发已验证':'配置收发地址，并在群内验证'}</p></div>${btn(channel?'编辑连接':'连接群',channel?'channel-edit':'channel-new','small primary',data)}</section></div>
      <div class="robot-footer"><div class="actions">${btn('帮助预览','bot-help','small quiet',`data-id="${attr(bot.id)}"`)}${channel?['receive_commands','daily_notifications'].map((key,i)=>btn(`${i?'活动提醒':'接收指令'} · ${channel[key]?'开':'关'}`,'channel-option','small quiet',`${data} data-key="${key}" data-active="${!channel[key]}" aria-pressed="${!!channel[key]}"`)).join(''):''}</div><div class="actions">${channel?`${btn(isActive(channel.active)?'暂停连接':'启用连接','channel-toggle','small quiet',`${data} data-active="${!isActive(channel.active)}"`)}${btn('解除连接','channel-remove','small danger-quiet',`${data} data-name="${attr(channel.name||'当前群')}"`)}`:''}${btn('删除机器人','bot-delete','small danger-quiet',`data-id="${attr(bot.id)}"`)}</div></div>
    </div></details>`;
}
async function renderBotWorkspace(epoch){
  const [result,channels,catalog,known,slots]=await Promise.all([api('/api/bots'),loadChannels(),api('/api/bot-capabilities'),api('/api/groups'),scopeSlots()]);if(!isCurrent(epoch))return;
  state.bots=asList(result,'bots');state.page.botCapabilities=asList(catalog,'capabilities');state.page.botGroups=asList(known,'groups');state.page.botScopeSlots=slots;
  const groups=groupedBots(state.bots,channels),count=state.page.botGroups.length;
  $('#main').innerHTML=head('机器人与群','先建群，再连接机器人。每个机器人服务一个群。',btn('新建群','group-new','')+btn(`${icon('plus')}新建机器人`,'bot-new','primary'),'BOTS & GROUPS')+`<div class="robot-overview"><span><strong>${count}</strong> 个群</span><span><strong>${state.bots.length}</strong> 个机器人</span><a href="/guide/admin#robots" target="_blank" rel="noopener">接入说明 ↗</a></div><div class="stack robot-clusters">${groups.map(g=>{
    const key=`group:${g.id}`;
    return `<section class="panel robot-cluster" data-group-id="${attr(g.id)}" data-chat-id="${attr(g.chat_id)}"><details data-disclosure-key="${attr(key)}"${disclosureOpen(key,true)}><summary class="robot-cluster-summary"><span><h2>${e(g.name)}</h2><span class="robot-group-id">${g.chat_id?'群 ID · '+e(g.chat_id):g.unbound?'选择已有群并配置连接':'等待机器人自动识别群 ID'}</span></span><span class="badge outline">${g.entries.length} 个机器人</span></summary><div class="robot-cluster-body">${!g.unbound?`<div class="robot-cluster-tools">${btn('重命名','group-edit','small quiet',`data-id="${attr(g.id)}"`)}${btn('添加机器人','bot-new','small',`data-group-id="${attr(g.id)}"`)}${btn('删除群','group-delete','small danger-quiet',`data-id="${attr(g.id)}"`)}</div>`:''}${g.entries.map(compactBotCard).join('')||'<p class="robot-empty">尚未添加机器人</p>'}</div></details></section>`;
  }).join('')}</div>${!groups.length?empty('先新建一个群','填写群名称即可，群 ID 会自动识别。'):''}`;
  state.botDisclosure??=new Map();
  $$('[data-disclosure-key]',$('#main')).forEach(detail=>detail.addEventListener('toggle',()=>{if(detail.isConnected)state.botDisclosure.set(detail.dataset.disclosureKey,detail.open);}));
  if(typeof clearTimeout==='function')clearTimeout(state.botRefreshTimer);
  if(channels.some(c=>c.connection_status!=='connected')&&typeof setTimeout==='function')state.botRefreshTimer=setTimeout(()=>{if(isCurrent(epoch))renderBotWorkspace(epoch).catch(()=>{});},5000);
}
function removeBotGroup(id){
  const group=(state.page.botGroups||[]).find(g=>g.id===id);if(!group)return;
  const names=state.channels.filter(c=>c.group_id===id&&!c.archived).map(c=>state.bots.find(b=>b.id===c.bot_id)?.name||c.bot_name);
  confirmAction('删除群',`删除“${group.name}”${names.length?`，同时解除 ${names.length} 个机器人的连接：${names.join('、')}`:''}。机器人、会员、报名及赛事历史保留。此操作只删除后台群配置。`,'删除群',async epoch=>{await api(`/api/groups/${encodeURIComponent(id)}`,{method:'DELETE'});if(epoch===state.epoch){closeModal();toast('群已删除，机器人可重新连接其他群。');await renderRoute();}});
}
async function editGroup(id,returnBot){
  const group=(state.page.botGroups||[]).find(g=>g.id===id)||{};
  openModal(id?'重命名群':'新建群',`<form id="group-form">${field('群名称','name',group.name||'','text','required maxlength="80"')}${!id?'<p class="caption">群 ID 将在首次 @机器人时自动识别。</p>':''}${formFooter('保存')}</form>`);
  bindForm('group-form',async(data,_form,epoch)=>{const result=await api(id?`/api/groups/${encodeURIComponent(id)}`:'/api/groups',{method:id?'PATCH':'POST',body:{name:data.get('name').trim()}});if(epoch!==state.epoch)return;closeModal();toast('群已保存。');await renderRoute();if(returnBot)await editChannelScope(returnBot,null,{group_id:result.id});});
}
async function editBotScope(id,preset={}){
  const epoch=state.epoch,bot=state.bots.find(b=>String(b.id)===String(id))||{},[slots,catalog,attributes]=await Promise.all([scopeSlots(),api('/api/bot-capabilities'),api('/api/bot-user-attributes')]);if(epoch!==state.epoch)return;
  state.page.botCapabilities=asList(catalog,'capabilities');const enabled=new Set(botCapabilities(bot));
  openModal(id?'机器人设置':'新建机器人',`<form id="bot-form">${field('机器人名称','name',bot.name||'','text','required maxlength="80"')}<fieldset class="robot-capability-field"><legend>业务组合</legend><div class="robot-capabilities">${botCapabilityCatalog().map(c=>`<label class="robot-capability"><input type="checkbox" name="capabilities" value="${attr(c.id)}"${enabled.has(c.id)?' checked':''}><span><strong>${e(c.name)}</strong><small>${e(c.description||'')}</small></span></label>`).join('')}</div></fieldset><details class="robot-scope-settings"><summary>场地与场次范围</summary>${scopeControls(bot,slots,{bot:true})}</details>${botPolicyForm(bot.user_policy)}<details class="robot-scope-settings"><summary>功能开放时间 · ${(bot.capability_rules||[]).length} 条规则</summary><p class="caption">仅开放规则限制可用时间；关闭规则优先。</p><div id="bot-time-rules"></div></details>${id?`<label class="check-label robot-active"><input type="checkbox" name="active"${isActive(bot.active)?' checked':''}>启用机器人</label>`:''}${formFooter(id?'保存设置':'创建并连接群')}</form>`,true);bindScopeControls($('#bot-form'));
  const readPolicy=bindBotPolicy($('#bot-form'),bot.user_policy,attributes);
  const readTimeRules=bindTimeRules($('#bot-time-rules'),bot.capability_rules,{mode:'capabilities',capabilities:()=>[...$('#bot-form').querySelectorAll('[name="capabilities"]:checked')].map(el=>el.value)});
  $('#bot-form').querySelectorAll('[name="capabilities"]').forEach(el=>el.addEventListener('change',()=>readTimeRules.refresh()));
  bindForm('bot-form',async(data,_form,context)=>{const result=await api(id?`/api/bots/${encodeURIComponent(id)}`:'/api/bots',{method:id?'PATCH':'POST',body:{name:data.get('name').trim(),active:id?data.has('active'):true,capabilities:data.getAll('capabilities'),...scopeBody(data,true),user_policy:readPolicy(),capability_rules:readTimeRules().filter(r=>r.capabilities.length)}});if(context!==state.epoch)return;closeModal();toast('机器人设置已保存。');await renderRoute();if(!id)await editChannelScope(result.id,null,preset);});
}

function removeBot(id){
  const bot=state.bots.find(b=>b.id===id);if(!bot)return;
  confirmAction('删除机器人',`删除“${bot.name}”后停止收发并解除群连接，会员、报名、赛事和历史消息保留。同群其他机器人不受影响。此操作只移除后台机器人，群内机器人可在金山协作中移除。`,'删除机器人',async epoch=>{await api(`/api/bots/${encodeURIComponent(id)}`,{method:'DELETE'});if(epoch===state.epoch){closeModal();toast('机器人已删除。');await renderRoute();}});
}

function botPolicyForm(policy){
  return `<details class="robot-user-settings"><summary>用户使用条件 · ${policy?.enabled?`${policy.rules.length} 项`:'不限'}</summary><div class="robot-user-body"><label class="check-label"><input type="checkbox" name="policy_enabled"${policy?.enabled?' checked':''}>启用用户条件</label><p class="caption">限制使用本机器人的账号；注册、本人资料与帮助仍可使用。原有会员资格和管理权限要求继续生效。</p>${selectfield('条件组合','policy_match',[['all','全部满足'],['any','任一满足']],policy?.match||'all')}<div id="bot-policy-rules" class="stack"></div><button type="button" class="btn small" data-rule-add>添加条件</button></div></details>`;
}
function bindBotPolicy(form,policy,attributes){
  let rules=JSON.parse(JSON.stringify(policy?.rules||[]));const fields=attributes.fields,root=$('#bot-policy-rules',form);
  function collect(){return $$('[data-rule-index]',root).map(row=>{
    const fieldId=$('[data-rule-field]',row).value,f=fields.find(f=>f.id===fieldId),op=$('[data-rule-op]',row).value;
    let value=null;
    if(!['empty','present'].includes(op)){
      if(['enum','set'].includes(f.type))value=$$('input[data-rule-value]:checked',row).map(n=>f.options.find(o=>String(o.id)===n.value)?.id);
      else{value=$('[data-rule-value]',row)?.value??'';if(f.type==='number')value=value===''?null:Number(value);else if(f.type==='bool')value=value==='true';else if(['in','not_in'].includes(op))value=value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean);}
    }
    return {field:fieldId,op,value};
  });}
  function paint(){
    root.innerHTML=rules.map((r,i)=>{
      const f=fields.find(f=>f.id===r.field)||fields[0],op=f.operators.includes(r.op)?r.op:f.operators[0],value=r.value;
      const choose=(label,attribute,options,current)=>`<label>${label}<select class="control" ${attribute} aria-label="${attr(label)}">${options.map(([id,name])=>`<option value="${attr(id)}"${String(current)===String(id)?' selected':''}>${e(name)}</option>`).join('')}</select></label>`;
      let input='';
      if(!['empty','present'].includes(op)){
        if(['enum','set'].includes(f.type))input=`<fieldset class="rule-choices"><legend>取值</legend>${f.options.map(o=>`<label class="check-label"><input type="checkbox" data-rule-value value="${attr(o.id)}"${Array.isArray(value)&&value.includes(o.id)?' checked':''}>${e(o.name)}</label>`).join('')||'<p class="caption">暂无可选值，请先在会员管理中添加组别。</p>'}</fieldset>`;
        else if(f.type==='bool')input=choose('取值','data-rule-value',[['true','是'],['false','否']],value===false?'false':'true');
        else input=`<label>取值<input class="control" data-rule-value type="${f.type==='number'?'number':f.type==='date'?'date':f.private?'password':'text'}" value="${attr(Array.isArray(value)?value.join('，'):value??'')}" ${f.type==='number'?`min="${f.min}" max="${f.max}" step="1"`:''} autocomplete="off" maxlength="500" placeholder="${['in','not_in'].includes(op)?'多个值用逗号分隔':''}"></label>`;
      }
      return `<section class="robot-rule" data-rule-index="${i}"><div class="robot-rule-head"><strong>条件 ${i+1}</strong><button type="button" class="btn small quiet" data-rule-remove="${i}" aria-label="删除条件 ${i+1}">删除</button></div><div class="robot-rule-controls">${choose('用户属性','data-rule-field',fields.map(f=>[f.id,f.name]),f.id)}${choose('匹配方式','data-rule-op',f.operators.map(o=>[o,attributes.operators[o]]),op)}${input}</div></section>`;
    }).join('');
    $$('[data-rule-field]',root).forEach((node,i)=>node.onchange=()=>{const next=node.value;rules=collect();rules[i]={field:next,op:fields.find(f=>f.id===next).operators[0],value:null};paint();});
    $$('[data-rule-op]',root).forEach(node=>node.onchange=()=>{rules=collect();paint();});
    $$('[data-rule-remove]',root).forEach(node=>node.onclick=()=>{rules=collect();rules.splice(Number(node.dataset.ruleRemove),1);paint();});
  }
  $('[data-rule-add]',form).onclick=()=>{rules=collect();if(rules.length>=30){toast('最多添加30条用户条件。');return;}const f=fields[0];rules.push({field:f.id,op:f.operators[0],value:null});paint();};
  paint();return ()=>({enabled:$('[name=policy_enabled]',form).checked,match:$('[name=policy_match]',form).value,rules:collect()});
}
async function editChannelScope(botId,id,preset={}){
  const epoch=state.epoch,[rows,groups,addresses]=await Promise.all([api(`/api/bots/${encodeURIComponent(botId)}/channels`),api('/api/groups'),api(query(`/api/bots/${encodeURIComponent(botId)}/connection`,{channel_id:id||undefined}))]);if(epoch!==state.epoch)return;
  const current=asList(rows,'channels').filter(c=>!c.archived),known=asList(groups,'groups');
  if(!id&&current.length){toast('机器人已有连接，请编辑现有连接。');return;}
  if(!known.length){openModal('先新建群',`<p>填写群名称后，即可继续连接机器人。</p><div class="actions">${btn('新建群','group-new','primary',`data-bot-id="${attr(botId)}"`)}${btn('稍后连接','modal-close')}</div>`);return;}
  const c=current.find(c=>String(c.id)===String(id))||preset;
  openModal(id?'编辑连接':'连接群',`<form id="channel-form">${selectfield('所属群','group_id',[['','请选择群'],...known.map(g=>[g.id,g.name])],c.group_id||'')}<div class="connection-step"><span class="connection-step-number">发</span><div><h3>发送到群</h3>${field('发送 Webhook','webhook_url',addresses.webhook_url||'','url','autocomplete="off" spellcheck="false"')}<p class="caption">粘贴群里这个机器人提供的 Webhook。</p></div></div><div class="connection-step"><span class="connection-step-number">收</span><div><h3>接收群消息</h3>${textfield('机器人回调地址','callback_url',addresses.callback_url,'readonly rows="3"')}<div class="actions">${btn('复制回调地址','copy-callback','small')}</div><p class="caption">填到同一个机器人的“消息接收地址 / 回调地址”。保存后在群内 @机器人发送“连接”。</p></div></div><div class="inline-error" role="alert"></div><div class="form-footer">${btn('取消','modal-close','ghost')}${btn('保存并测试发送','connection-save-test','')}<button class="btn primary" type="submit">保存连接</button></div></form>${id?`<div class="connection-resume"><span>${e(botStatus(state.bots.find(b=>b.id===botId)||{active:true},c)[0])}</span>${btn('查看连接状态','connection-progress','small',`data-bot-id="${attr(botId)}" data-id="${attr(id)}"`)}</div>`:''}`,true);
  const form=$('#channel-form');form.elements.group_id.required=true;
  form.elements.group_id.onchange=()=>{form.elements.webhook_url.value=form.elements.group_id.value===c.group_id?(addresses.webhook_url||''):'';};
  let currentId=id,attemptId=null,wantTest=false;
  $('[data-action=connection-save-test]',form).onclick=()=>{wantTest=true;form.requestSubmit();wantTest=false;};
  bindForm('channel-form',async(data,_form,context)=>{
    const test=wantTest,url=data.get('webhook_url').trim();
    if(test&&!url)throw new Error('请先填写群里提供的发送 Webhook。');
    const result=await api(`/api/bots/${encodeURIComponent(botId)}/channels${currentId?`/${encodeURIComponent(currentId)}`:''}`,{method:currentId?'PATCH':'POST',body:{group_id:data.get('group_id'),webhook_url:data.get('webhook_url').trim()}});
    currentId=result.id;
    if(!test){if(context===state.epoch){closeModal();toast(url?'连接已保存，请在群内 @机器人发送“连接”验证。':'连接已保存，可继续补充发送地址。');await renderRoute();}return;}
    attemptId??=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
    const tested=await api(`/api/bots/${encodeURIComponent(botId)}/connection/test`,{method:'POST',body:{channel_id:currentId,request_id:attemptId}});
    if(tested.status!=='sending')attemptId=null;
    if(tested.status!=='sent')throw new Error(tested.message);
    if(context===state.epoch){await renderBotWorkspace(context);await showConnectionProgress(botId,currentId);}
  });
}
async function showConnectionProgress(botId,id){
  const epoch=state.epoch,path=query(`/api/bots/${encodeURIComponent(botId)}/connection`,{channel_id:id}),data=await api(path);if(epoch!==state.epoch)return;
  openModal('群连接状态',`<div id="connection-progress"><p id="connection-send-status" class="connection-done"></p><div class="connection-step"><span class="connection-step-number">收</span><div><h3>把这个地址填到金山协作</h3>${textfield('机器人回调地址','callback_url',data.callback_url,'readonly rows="3"')}<div class="actions">${btn('复制回调地址','copy-callback','small primary')}</div><p class="caption">粘贴到同一个机器人的“消息接收地址 / 回调地址”，然后保存。</p></div></div><div class="connection-step"><span class="connection-step-number">验</span><div><h3>回到群里 @机器人发送“连接”</h3><p id="connection-status" role="status" aria-live="polite"></p></div></div><div class="form-footer">${btn('关闭','modal-close','ghost')}${btn('刷新状态','connection-refresh','')}</div></div>`,true);
  const area=$('#connection-progress'),status=$('#connection-status');
  const update=value=>{$('#connection-send-status').textContent=value.send_verified?'发送已验证':'发送尚未验证，可返回编辑连接测试发送。';status.textContent=value.connection_status==='connected'?'已连接 · 消息收发验证完成':value.receive_verified?'已收到群消息，等待发送验证。':value.callback_checked?'回调地址已验证，等待群内 @机器人发送“连接”。':'等待金山协作验证回调地址；保存后请在群内发送“连接”。';status.className=value.connection_status==='connected'?'connection-done':'caption';};update(data);
  const refresh=async()=>{if(!area.isConnected||epoch!==state.epoch)return;try{const next=await api(path);if(!area.isConnected)return;update(next);if(next.connection_status==='connected'){await renderBotWorkspace(epoch);return;}}catch(error){if(area.isConnected)status.textContent=error.message;}if(area.isConnected&&epoch===state.epoch)state.connectionTimer=setTimeout(refresh,3000);};
  $('[data-action=connection-refresh]',area).onclick=()=>{clearTimeout(state.connectionTimer);refresh();};
  if(data.connection_status!=='connected')state.connectionTimer=setTimeout(refresh,3000);
}
async function toggleBotChannel(botId,id,active){const epoch=state.epoch;await api(`/api/bots/${encodeURIComponent(botId)}/channels/${encodeURIComponent(id)}`,{method:'PATCH',body:{active}});if(epoch===state.epoch){toast(active?'连接已启用。':'连接已暂停。');await renderRoute();}}
async function toggleChannelOption(botId,id,key,value){if(!['receive_commands','daily_notifications'].includes(key))return;const epoch=state.epoch;await api(`/api/bots/${encodeURIComponent(botId)}/channels/${encodeURIComponent(id)}`,{method:'PATCH',body:{[key]:value}});if(epoch===state.epoch){toast('设置已保存。');await renderRoute();}}
function removeBotChannel(botId,id,name){const bot=state.bots.find(b=>String(b.id)===String(botId));confirmAction('解除群连接',`解除“${bot?.name||'此机器人'}”与“${name}”的连接。历史记录和同群其他机器人保留。`,'解除连接',async epoch=>{await api(`/api/bots/${encodeURIComponent(botId)}/channels/${encodeURIComponent(id)}`,{method:'DELETE'});if(epoch===state.epoch){closeModal();toast('连接已解除。');await renderRoute();}});}
function showBotCallback(url){openModal('机器人回调地址',`${textfield('回调地址','callback_url',url,'readonly')}<div class="actions">${btn('复制地址','copy-callback','primary')}${btn('完成','modal-close')}</div>`);}
async function viewBotCallback(id){const epoch=state.epoch,result=await api(`/api/bots/${encodeURIComponent(id)}/callback`);if(epoch===state.epoch)showBotCallback(result.callback_url);}

async function previewBotHelp(id){const epoch=state.epoch,result=await api(`/api/bots/${encodeURIComponent(id)}/help-preview?audience=member`);if(epoch!==state.epoch)return;const bot=state.bots.find(b=>String(b.id)===String(id));openModal(`${bot?.name||'机器人'} · 帮助预览`,`${selectfield('查看身份','help_audience',[['member','已注册会员'],['unregistered','未注册用户'],['admin','管理员']],'member')}<div id="bot-help-preview" class="preview">${markdown(result.reply||'暂无帮助内容')}</div>`,true);let request=0;$('#f-help_audience').onchange=async event=>{const turn=++request,area=$('#bot-help-preview');area.innerHTML=loading();try{const value=await api(`/api/bots/${encodeURIComponent(id)}/help-preview?audience=${encodeURIComponent(event.target.value)}`);if(turn===request&&epoch===state.epoch&&area.isConnected)area.innerHTML=markdown(value.reply||'暂无帮助内容');}catch(error){if(turn===request&&epoch===state.epoch&&area.isConnected)area.innerHTML=`<p role="alert">${e(error.message)}</p>`;}};}
