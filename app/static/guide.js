/* Progressive enhancements; the complete guide remains readable without JavaScript. */
'use strict';
(() => {
  const input = document.getElementById('search-input');
  const search = document.getElementById('guide-search');
  const status = document.getElementById('search-status');
  const empty = document.getElementById('search-empty');
  const items = Array.from(document.querySelectorAll('[data-guide-item]'));
  const sections = Array.from(document.querySelectorAll('.chapter'));
  // Cache business text before adding copy controls, so UI labels are not search terms.
  const searchable = new Map(items.map(item => [item, item.textContent.toLocaleLowerCase()]));
  const openedBeforeSearch = new Map();
  let searching = false;
  const applySearch = () => {
    const words = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    if (words.length && !searching) document.querySelectorAll('details').forEach(d => openedBeforeSearch.set(d, d.open));
    if (!words.length && searching) { openedBeforeSearch.forEach((open,d) => {d.open=open;}); openedBeforeSearch.clear(); }
    searching=Boolean(words.length);
    let count = 0;
    items.forEach(item => {
      item.hidden = !words.every(word => searchable.get(item).includes(word));
      if (!item.hidden) count += 1;
      if (words.length && !item.hidden) item.querySelectorAll('details').forEach(detail => { detail.open = true; });
    });
    sections.forEach(section => { section.hidden = !Array.from(section.querySelectorAll('[data-guide-item]')).some(item => !item.hidden); });
    empty.hidden = count > 0;
    status.textContent = words.length ? `找到 ${count} 项用法` : (document.body.dataset?.edition === 'admin' ? '接入、审核、抽签、发布' : '注册、报名、比赛、个人记录');
  };
  input.addEventListener('input', applySearch);
  document.getElementById('clear-search').addEventListener('click', () => { input.value = ''; applySearch(); input.focus(); });
  document.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener('click', () => {
    if (input.value) { input.value = ''; applySearch(); }
  }));
  // Also support externally supplied anchors and browser back/forward while searching.
  window.addEventListener('hashchange', () => {
    if (input.value) { input.value = ''; applySearch(); }
  });
  search.hidden = false;
  applySearch();

  const revealTarget = () => {
    let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}
    const target=document.getElementById(id);if(!target)return;
    if(input.value){input.value='';applySearch();}
    let node=target;
    while(node&&node!==document.body){if(node.tagName==='DETAILS')node.open=true;node=node.parentElement;}
    const task=target.querySelector(':scope > details.task-guide');if(task)task.open=true;
    requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));
  };
  document.querySelectorAll('a[href^="#"]').forEach(link=>link.addEventListener('click',()=>requestAnimationFrame(revealTarget)));
  window.addEventListener('hashchange',revealTarget);
  revealTarget();
  const markChapter = () => {
    const visible=sections.filter(section=>!section.hidden);
    const edge=document.querySelector('.site-header').getBoundingClientRect().bottom+80;
    const current=visible.filter(section=>section.getBoundingClientRect().top<=edge).at(-1)||visible[0];
    document.querySelectorAll('.contents nav a').forEach(link=>{
      if(current&&link.hash==='#'+current.id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
    });
  };
  let chapterFrame=false;
  const scheduleChapter = () => {
    if(chapterFrame)return;chapterFrame=true;
    requestAnimationFrame(()=>{chapterFrame=false;markChapter();});
  };
  window.addEventListener('scroll',scheduleChapter,{passive:true});
  window.addEventListener('resize',scheduleChapter);
  document.addEventListener('toggle',scheduleChapter,true);
  input.addEventListener('input',scheduleChapter);
  markChapter();

  let messageTimer;
  const announce = message => {
    const node = document.getElementById('copy-status');
    window.clearTimeout(messageTimer);
    node.textContent = message;
    messageTimer = window.setTimeout(() => { node.textContent = ''; }, 4500);
  };
  const legacyCopy = text => {
    const field = document.createElement('textarea');
    field.value = text;
    field.className = 'clipboard-fallback';
    field.setAttribute('readonly', '');
    field.setAttribute('aria-hidden', 'true');
    document.body.append(field);
    field.select();
    let copied = false;
    try { copied = Boolean(document.execCommand('copy')); } catch (_) { /* Select visible text below. */ }
    field.remove();
    return copied;
  };
  document.querySelectorAll('[data-command]').forEach(code => {
    const raw=code.textContent;
    // Only example values are colored. Text nodes keep copy output literal and safe.
    const pattern=/(张三|李四|王五|赵六|提高组|周二双打|春季联赛|校队训练|(?:明天|今天|后天)|\d{4}-\d{2}-\d{2}|(?:第[一二三四五六七八九十\d]+场)|(?:场地\d+)|(?:申请\d+)|(?:\d{1,2}[:：-]\d{1,2})|(?:\d级))/g;
    let start=0;
    for(const match of raw.matchAll(pattern)){
      if(start===0)code.replaceChildren();
      code.append(document.createTextNode(raw.slice(start,match.index)));
      const value=document.createElement('span');value.className='command-parameter';value.textContent=match[0];code.append(value);start=match.index+match[0].length;
    }
    if(start)code.append(document.createTextNode(raw.slice(start)));

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'copy-command';
    button.textContent = '复制';
    button.setAttribute('aria-label', `复制指令：${code.textContent}`);
    const wrapper = document.createElement('span');
    wrapper.className = 'command-unit';
    code.replaceWith(wrapper);
    wrapper.append(code, button);
    button.addEventListener('click', async () => {
      button.disabled = true;
      let copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          try { await navigator.clipboard.writeText(code.textContent); copied = true; } catch (_) { copied = legacyCopy(code.textContent); }
        } else { copied = legacyCopy(code.textContent); }
      } finally {
        button.disabled = false;
        button.focus({preventScroll: true});
      }
      if (copied) { announce('已复制，回群 @机器人后粘贴。'); }
      else {
        const selection = window.getSelection();
        if (selection) {
          const range = document.createRange(); range.selectNodeContents(code);
          selection.removeAllRanges(); selection.addRange(range);
        }
        announce('请长按指令文字复制，或使用系统复制操作。');
      }
    });
  });
})();
