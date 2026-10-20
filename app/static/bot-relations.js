'use strict';
function robotWorkspaceTabs(){return `<nav class="section-tabs" aria-label="机器人与群"><a href="#bots" data-route="bots" class="${state.route==='bots'?'active':''}" ${state.route==='bots'?'aria-current="page"':''}>机器人管理</a><a href="#bot-links" data-route="bot-links" class="${state.route==='bot-links'?'active':''}" ${state.route==='bot-links'?'aria-current="page"':''}>群关联</a></nav>`;}
function robotManagerMarkup(){
  return head('机器人管理','配置名称、业务与服务范围。',btn(`${icon('plus')}新建机器人`,'bot-new','primary'))+
    `<div class="robot-overview"><span><strong>${state.bots.length}</strong> 个机器人</span><a href="/guide/admin#robots" target="_blank" rel="noopener">使用说明 ↗</a></div><div class="stack robot-manager">${state.bots.map(bot=>{
      const channel=state.channels.find(c=>c.bot_id===bot.id&&!c.archived),[status,tone]=botStatus(bot,channel),key=`bot:${bot.id}:settings`;
      return `<details class="robot-card" data-bot-id="${attr(bot.id)}" data-disclosure-key="${attr(key)}"${disclosureOpen(key)}>
        <summary class="robot-card-summary"><span class="robot-summary-main"><span class="robot-summary-title"><strong>${e(bot.name)}</strong><span class="badge ${tone}">${status}</span></span><span class="robot-summary-scope">${e(scopeSummary(bot))}</span></span><span class="robot-summary-business" aria-label="业务组合">${botCapabilityBadges(bot)}</span><span class="robot-disclosure-label">设置</span></summary>
        <div class="robot-manager-details"><div><span class="caption">使用条件</span><p>${bot.user_policy?.enabled?`${nf(bot.user_policy.rules.length)} 项用户条件`:'不限用户标签'} · ${nf((bot.capability_rules||[]).length)} 条开放时间规则</p><span class="caption">关联群：${e(channel?.name||'尚未关联')}</span></div><div class="actions">${btn('编辑名称与功能','bot-edit','primary small',`data-id="${attr(bot.id)}"`)}${btn('帮助预览','bot-help','small',`data-id="${attr(bot.id)}"`)}${btn('群关联','go','small','data-route="bot-links"')}${btn('删除机器人','bot-delete','small danger-quiet',`data-id="${attr(bot.id)}"`)}</div></div></details>`;
    }).join('')||empty('还没有机器人','创建机器人后，在群关联中连接到群。',btn('新建机器人','bot-new','primary'))}</div>`;
}
function relationLayout(bots,channels,groups){
  const connected=channels.filter(c=>!c.archived),index=new Map(groups.map((g,i)=>[g.id,i]));
  const ordered=[...bots].sort((a,b)=>(index.get(connected.find(c=>c.bot_id===a.id)?.group_id)??groups.length)-(index.get(connected.find(c=>c.bot_id===b.id)?.group_id)??groups.length));
  const height=Math.max(360,ordered.length*118+90,groups.length*134+90);
  return {width:900,height,bots:ordered.map((bot,i)=>({bot,x:32,y:58+i*118})),
    groups:groups.map((group,i)=>({group,x:636,y:58+(i+.5)*(height-100)/Math.max(1,groups.length)-40})),
    channels:connected};
}
function graphConnectionLabel(c){return c.send_configured?(c.receive_commands?'收 / 发':'仅发送'):(c.receive_commands?'仅接收':'未配置');}
function relationGraphMarkup(bots,channels,groups){
  const layout=relationLayout(bots,channels,groups),lines=[],nodes=[];
  for(const {bot,x,y} of layout.bots){
    const channel=layout.channels.find(c=>c.bot_id===bot.id),[status,tone]=botStatus(bot,channel);
    const data=`data-bot-id="${attr(bot.id)}" data-id="${attr(channel?.id||'')}"`;
    nodes.push(`<button class="relation-node relation-bot" style="left:${x}px;top:${y}px" data-action="${channel?'channel-edit':'channel-new'}" ${data}><span class="relation-node-type">机器人</span><strong>${e(bot.name)}</strong><span class="relation-node-meta"><span class="badge ${tone}">${status}</span>${channel?'':'选择群 →'}</span></button>`);
    const end=layout.groups.find(g=>g.group.id===channel?.group_id||(!channel?.group_id&&g.group.chat_id===channel?.chat_id));
    if(end){
      const from=y+45,to=end.y+45,d=`M 264 ${from} C 420 ${from} 482 ${to} 636 ${to}`;
      lines.push(`<g class="relation-edge ${channel.active?'':'is-paused'}" role="button" tabindex="0" aria-label="${attr(bot.name+'连接到'+end.group.name+'，配置收发地址')}" data-action="channel-edit" ${data}><path class="relation-edge-hit" d="${d}"/><path class="relation-edge-line" d="${d}"/></g>`);
      nodes.push(`<button class="relation-edge-label" style="left:340px;top:${from-17}px" data-action="channel-edit" ${data} aria-label="${attr(bot.name+'收发配置')}">${graphConnectionLabel(channel)} · 配置</button>`);
    }
  }
  for(const {group,x,y} of layout.groups){
    const count=layout.channels.filter(c=>c.group_id===group.id).length;
    nodes.push(`<button class="relation-node relation-group" style="left:${x}px;top:${y}px" data-action="group-edit" data-id="${attr(group.id)}"><span class="relation-node-type">群</span><strong>${e(group.name)}</strong><span class="relation-node-meta">${count} 个机器人 · ${group.chat_id?'已识别':'待识别'}</span></button>`);
  }
  return `<div class="relation-viewport" tabindex="0" aria-label="机器人与群连接图，可滚动查看"><div class="relation-world" data-width="${layout.width}" data-height="${layout.height}" style="width:${layout.width}px;height:${layout.height}px"><div class="relation-canvas" style="width:${layout.width}px;height:${layout.height}px"><svg class="relation-edges" width="${layout.width}" height="${layout.height}" aria-label="机器人到群的连接">${lines.join('')}</svg>${nodes.join('')}</div></div></div>`;
}
function robotRelationsMarkup(){
  const groups=state.page.botGroups||[],selected=state.botGroupFilter||'';
  const shownGroups=selected?groups.filter(g=>g.id===selected):groups,channels=state.channels.filter(c=>!c.archived&&(!selected||c.group_id===selected));
  const bots=selected?state.bots.filter(b=>channels.some(c=>c.bot_id===b.id)):state.bots;
  return head('群关联','点击机器人或连线，配置收发地址。',btn('新建群','group-new','primary'))+
    `<div class="relation-workspace"><aside class="relation-sidebar panel" aria-label="群管理"><div class="panel-head"><h2>群管理</h2><span class="badge outline">${groups.length}</span></div><div class="relation-group-list"><button class="relation-filter ${selected?'':'selected'}" data-graph-group="">全部群<span>${state.channels.filter(c=>!c.archived).length} 条连接</span></button>${groups.map(g=>`<div class="relation-group-item"><button class="relation-filter ${selected===g.id?'selected':''}" data-graph-group="${attr(g.id)}"><strong>${e(g.name)}</strong><span>${g.chat_id?'群 ID '+e(g.chat_id):'群 ID 自动识别'}</span></button><div class="actions">${btn('编辑','group-edit','small quiet',`data-id="${attr(g.id)}"`)}${btn('删除','group-delete','small danger-quiet',`data-id="${attr(g.id)}"`)}</div></div>`).join('')}</div></aside><section class="panel relation-board" aria-label="群连接"><div class="relation-toolbar"><div><strong>连接图</strong><span class="caption">${bots.length} 个机器人 · ${shownGroups.length} 个群</span></div><div class="actions"><button class="btn small" data-graph-zoom="-0.15" aria-label="缩小连接图">−</button><button class="btn small" data-graph-fit>适应宽度</button><button class="btn small" data-graph-zoom="0.15" aria-label="放大连接图">＋</button></div></div>${relationGraphMarkup(bots,channels,shownGroups)}${!bots.length?'<p class="relation-empty">暂无关联机器人。请先在机器人管理中新建，或切换“全部群”连接已有机器人。</p>':''}</section></div>`;
}
function bindRelationGraph(){
  const area=$('.relation-viewport'),world=$('.relation-world'),canvas=$('.relation-canvas');
  if(!area)return;
  let zoom=1;
  const scale=value=>{zoom=Math.min(1.5,Math.max(.4,value));canvas.style.transform=`scale(${zoom})`;world.style.width=`${Number(world.dataset.width)*zoom}px`;world.style.height=`${Number(world.dataset.height)*zoom}px`;};
  $$('[data-graph-zoom]').forEach(button=>button.onclick=()=>scale(zoom+Number(button.dataset.graphZoom)));
  $('[data-graph-fit]').onclick=()=>scale(Math.min(1,area.clientWidth/Number(world.dataset.width)));
  if(area.clientWidth>=600)scale(Math.min(1,area.clientWidth/Number(world.dataset.width)));
  $$('[data-graph-group]').forEach(button=>button.onclick=()=>{state.botGroupFilter=button.dataset.graphGroup;renderRoute();});
  area.addEventListener('keydown',event=>{const edge=event.target.closest('.relation-edge');if(edge&&(event.key==='Enter'||event.key===' ')){event.preventDefault();editChannelScope(edge.dataset.botId,edge.dataset.id);}});
  let drag=null;
  area.addEventListener('pointerdown',event=>{if(event.target.closest('button,[role=button]')||event.pointerType==='touch')return;drag={x:event.clientX,y:event.clientY,left:area.scrollLeft,top:area.scrollTop};area.setPointerCapture(event.pointerId);});
  area.addEventListener('pointermove',event=>{if(drag){area.scrollLeft=drag.left-(event.clientX-drag.x);area.scrollTop=drag.top-(event.clientY-drag.y);}});
  area.addEventListener('pointerup',()=>drag=null);area.addEventListener('pointercancel',()=>drag=null);
}
