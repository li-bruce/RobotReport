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
  const applySearch = () => {
    const words = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let count = 0;
    items.forEach(item => {
      item.hidden = !words.every(word => searchable.get(item).includes(word));
      if (!item.hidden) count += 1;
      if (words.length && !item.hidden) item.querySelectorAll('details').forEach(detail => { detail.open = true; });
    });
    sections.forEach(section => { section.hidden = !Array.from(section.querySelectorAll('[data-guide-item]')).some(item => !item.hidden); });
    empty.hidden = count > 0;
    status.textContent = words.length ? `找到 ${count} 项用法` : (document.body.dataset?.edition === 'admin' ? '搜索接入、审核、抽签或发布。' : '搜索注册、报名、比赛或个人记录。');
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
      if (copied) { announce('已复制。回到群里 @机器人后粘贴；示例中的日期和姓名请按实际修改。'); }
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
