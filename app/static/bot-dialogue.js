'use strict';
function botDialoguePanel(id){
  return `<details class="bot-setting-panel bot-dialogue" data-dialogue-bot="${attr(id)}"><summary><span><strong>回复文案</strong><small>称呼、引导与通知</small></span><span class="robot-disclosure-label">展开 / 收起</span></summary><div class="bot-setting-body" data-dialogue-editor></div></details>`;
}
function bindBotDialogue(card){
  const panel=card.querySelector('[data-dialogue-bot]');if(!panel)return;
  panel.addEventListener('toggle',()=>{if(panel.open&&!panel.dataset.loaded&&!panel.dataset.loading)loadBotDialogue(panel);});
}
async function loadBotDialogue(panel){
  panel.dataset.loading='true';const area=panel.querySelector('[data-dialogue-editor]'),id=panel.dataset.dialogueBot,path=`/api/bots/${encodeURIComponent(id)}/dialogue`;
  area.innerHTML=loading();
  try{
    let data=await api(path);if(!panel.isConnected)return;
    let drafts={...data.overrides},selected=data.templates[0].id,previewGeneration=0;
    const formId=`dialogue-${id}`;
    area.innerHTML=`<form id="${attr(formId)}"><div class="dialogue-toolbar">${field('系统显示名称','dialogue_system',data.system_name,'text','required maxlength="40"')}<label class="field"><span>查找文案</span><input type="search" data-dialogue-search placeholder="例如：注册、报名、审核"></label><label class="field"><span>类别</span><select data-dialogue-category><option value="">全部</option>${[...new Set(data.templates.map(t=>t.category))].map(c=>`<option>${e(c)}</option>`).join('')}</select></label></div><div class="dialogue-columns"><section><label class="field"><span>使用场景</span><select data-dialogue-select></select></label><p class="caption" data-dialogue-state></p><label class="field"><span>回复内容 <small data-dialogue-count></small></span><textarea rows="8" data-dialogue-text required></textarea></label><div class="dialogue-variables" aria-label="自动填入的内容"></div><div class="dialogue-actions"><button type="button" class="btn small quiet" data-dialogue-reset>恢复此项默认</button><button type="button" class="btn small" data-dialogue-preview>更新预览</button></div><p class="caption">变量会自动填入姓名、指令或业务结果；支持 **加粗**。</p></section><section class="dialogue-preview-area"><h4>消息预览</h4><div class="dialogue-message" data-dialogue-output aria-live="polite"></div><img class="dialogue-image" src="/static/dialogue-table.svg" alt="名单或对战表图片示意" hidden data-dialogue-image><p class="caption">示例资料，不会发送到群。</p></section></div><div class="bot-inline-save"><span data-save-status role="status"></span><div class="inline-error" role="alert"></div><button type="button" class="btn quiet" data-dialogue-reload>重新载入</button><button type="submit" class="btn primary">保存回复文案</button></div></form>`;
    area.innerHTML=uniqueBotFields(area.innerHTML,'dialogue-'+id);
    const form=area.querySelector('form'),select=form.querySelector('[data-dialogue-select]'),input=form.querySelector('[data-dialogue-text]'),system=form.querySelector('[name=dialogue_system]'),search=form.querySelector('[data-dialogue-search]'),category=form.querySelector('[data-dialogue-category]'),output=form.querySelector('[data-dialogue-output]');
    const current=()=>data.templates.find(t=>t.id===selected);
    function status(){
      form.querySelector('[data-dialogue-count]').textContent=`${input.value.length} / ${current().limit}`;
      form.querySelector('[data-dialogue-state]').textContent=Object.hasOwn(drafts,selected)?'已调整文案':'使用默认文案';
    }
    function storeDraft(){
      if(input.value===current().default)delete drafts[selected];else drafts[selected]=input.value;
      status();
    }
    async function showPreview(){
      const generation=++previewGeneration;
      output.textContent='正在预览…';
      try{
        const result=await api(`${path}/preview`,{method:'POST',body:{id:selected,text:input.value,system_name:system.value}});
        if(generation!==previewGeneration||!panel.isConnected)return;
        output.innerHTML=markdown(result.reply);form.querySelector('[data-dialogue-image]').hidden=!result.image;
      }catch(error){if(generation===previewGeneration&&panel.isConnected){output.textContent=error.message;form.querySelector('[data-dialogue-image]').hidden=true;}}
    }
    function choose(){
      const t=current();input.value=drafts[selected]??t.default;input.maxLength=t.limit;status();
      form.querySelector('.dialogue-variables').innerHTML=t.variables.map(v=>`<button type="button" class="dialogue-variable" data-variable="${attr(v)}" title="插入 {${attr(v)}}">${e(t.variable_labels?.[v]||v)}</button>`).join('');
      showPreview();
    }
    function options(){
      const query=search.value.trim().toLowerCase();
      const items=data.templates.filter(t=>(!category.value||t.category===category.value)&&(!query||(t.title+' '+t.category+' '+t.default).toLowerCase().includes(query)));
      form.querySelector('.dialogue-columns').hidden=!items.length;
      select.innerHTML=items.map(t=>`<option value="${attr(t.id)}">${e(t.category+' · '+t.title)}</option>`).join('');
      if(!items.length){select.innerHTML='<option value="">没有匹配的文案</option>';select.disabled=true;return;}
      select.disabled=false;
      if(!items.some(t=>t.id===selected)){selected=items[0].id;choose();}
      select.value=selected;
    }
    input.addEventListener('input',()=>{previewGeneration++;storeDraft();});
    system.addEventListener('input',()=>{previewGeneration++;});
    select.addEventListener('change',()=>{selected=select.value;choose();});
    search.addEventListener('input',options);category.addEventListener('change',options);
    form.querySelector('.dialogue-variables').onclick=event=>{
      const target=event.target.closest('[data-variable]');if(!target)return;
      input.setRangeText('{'+target.dataset.variable+'}',input.selectionStart,input.selectionEnd,'end');input.focus();storeDraft();markBotDirty(form);
    };
    form.querySelector('[data-dialogue-preview]').onclick=showPreview;
    form.querySelector('[data-dialogue-reset]').onclick=()=>{delete drafts[selected];choose();markBotDirty(form);};
    form.querySelector('[data-dialogue-reload]').onclick=async()=>{
      if(form.dataset.dirty==='true'&&!confirm('放弃未保存的文案修改并重新载入？'))return;
      try{data=await api(path);if(!panel.isConnected)return;drafts={...data.overrides};system.value=data.system_name;choose();robotSaved(form);form.querySelector('.inline-error').textContent='';}
      catch(error){form.querySelector('.inline-error').textContent=error.message;}
    };
    // Filtering and selecting templates are navigation, not edits.
    form.addEventListener('input',event=>{if(event.target===input||event.target===system)markBotDirty(form);});
    bindForm(formId,async()=>{
      storeDraft();const saved=await botSaveRequest(form,path,{method:'PUT',body:{revision:data.revision,system_name:system.value.trim(),overrides:drafts}});
      if(!panel.isConnected)return;data=saved;drafts={...data.overrides};robotSaved(form);status();showPreview();toast('回复文案已保存，新回复立即使用。');
    });
    options();choose();panel.dataset.loaded='true';
  }catch(error){if(panel.isConnected){area.innerHTML=`<p role="alert">${e(error.message)}</p><button type="button" class="btn">重试</button>`;area.querySelector('button').onclick=()=>loadBotDialogue(panel);}}
  finally{delete panel.dataset.loading;panel.closest('.robot-card')?.dispatchEvent(new Event('drawer-forms-ready'));}
}
