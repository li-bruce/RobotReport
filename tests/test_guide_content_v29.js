/* Browser-independent tests for public guide search and HTTP clipboard fallback. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../app/static/guide.js'), 'utf8');

class Element {
  constructor(text = '') { this.textContent = text; this.hidden = false; this.value = ''; this.listeners = {}; this.attrs = {}; this.children = []; this.details = []; this.items = []; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  querySelectorAll(selector) { return selector === 'details' ? this.details : selector === '[data-guide-item]' ? this.items : []; }
  setAttribute(name, value) { this.attrs[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceWith(element) { this.wrapper = element; }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  remove() { this.removed = true; }
}

function fixture({secure = false, clipboard = null, legacy = true} = {}) {
  const nodes = Object.fromEntries(['search-input', 'guide-search', 'search-status', 'search-empty', 'clear-search', 'copy-status'].map(id => [id, new Element()]));
  nodes['guide-search'].hidden = true;
  const member = new Element('会员 预约 报名 第二场'), competition = new Element('比赛 混双 第二场'), management = new Element('管理 审核 会员');
  const detail = new Element(); detail.open = false; competition.details = [detail];
  const sections = [member, competition, management].map(item => { const section = new Element(); section.items = [item]; return section; });
  const anchor = new Element();
  const code = new Element('明天第二场报名');
  const created = [];
  const selection = {removeAllRanges() { this.cleared = true; }, addRange(range) { this.range = range; }};
  const doc = {
    body: new Element(),
    getElementById: id => nodes[id],
    querySelectorAll: selector => selector === '[data-guide-item]' ? [member, competition, management] : selector === '.chapter' ? sections : selector === '[data-command]' ? [code] : [anchor],
    createElement: tag => { const element = new Element(); element.tag = tag; created.push(element); return element; },
    execCommand: action => { assert.equal(action, 'copy'); return legacy; },
    createRange: () => ({selectNodeContents(target) { this.target = target; }})
  };
  const win = {
    isSecureContext: secure,
    clearTimeout() {}, setTimeout() { return 1; },
    getSelection: () => selection,
    listeners: {}, addEventListener(name, listener) { this.listeners[name] = listener; }
  };
  vm.runInNewContext(script, {document: doc, window: win, navigator: {clipboard}});
  return {nodes, member, competition, management, sections, detail, anchor, code, created, selection, win};
}

(async () => {
  const f = fixture();
  assert.equal(f.nodes['guide-search'].hidden, false);
  f.nodes['search-input'].value = '混双'; f.nodes['search-input'].listeners.input();
  assert.equal(f.member.hidden, true); assert.equal(f.competition.hidden, false); assert.equal(f.management.hidden, true);
  assert.equal(f.sections[0].hidden, true); assert.equal(f.sections[1].hidden, false); assert.equal(f.detail.open, true);
  assert.equal(f.nodes['search-status'].textContent, '找到 1 项用法');
  f.nodes['search-input'].value = '预约 第二场'; f.nodes['search-input'].listeners.input();
  assert.equal(f.member.hidden, false); assert.equal(f.competition.hidden, true);
  f.nodes['search-input'].value = '没有此业务'; f.nodes['search-input'].listeners.input();
  assert.equal(f.nodes['search-empty'].hidden, false);
  f.anchor.listeners.click();
  assert.equal(f.nodes['search-input'].value, ''); assert.equal(f.nodes['search-empty'].hidden, true);
  assert(f.sections.every(section => !section.hidden));
  f.nodes['search-input'].value = '会员'; f.nodes['search-input'].listeners.input();
  f.win.listeners.hashchange(); assert.equal(f.nodes['search-input'].value, '');
  f.nodes['clear-search'].listeners.click(); assert.equal(f.nodes['search-input'].focused, true);

  const http = fixture();
  const copy = http.code.wrapper.children[1];
  await copy.listeners.click();
  assert.equal(copy.attrs['aria-label'], '复制指令：明天第二场报名');
  assert.equal(copy.disabled, false); assert.equal(copy.focused, true);
  const area = http.created.find(item => item.tag === 'textarea');
  assert.equal(area.value, '明天第二场报名'); assert.equal(area.selected, true); assert.equal(area.removed, true);
  assert.match(http.nodes['copy-status'].textContent, /已复制/);

  let written;
  const https = fixture({secure: true, clipboard: {writeText: async value => { written = value; }}});
  await https.code.wrapper.children[1].listeners.click();
  assert.equal(written, '明天第二场报名'); assert.equal(https.created.some(item => item.tag === 'textarea'), false);

  const blocked = fixture({secure: true, clipboard: {writeText: async () => { throw new Error('Denied'); }}, legacy: false});
  await blocked.code.wrapper.children[1].listeners.click();
  assert.match(blocked.nodes['copy-status'].textContent, /长按/);
  assert.equal(blocked.selection.range.target, blocked.code);
  assert.equal(blocked.code.wrapper.children[1].disabled, false);
  console.log('Guide search, navigation, and secure/insecure clipboard checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
