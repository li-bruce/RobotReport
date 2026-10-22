/* Shared display numbering for admin data tables, including async modal content. */
(() => {
  'use strict';
  const selector = '.table-wrap > table';
  const columnClass = 'admin-row-number';

  function numberTable(table) {
    const head = table.tHead;
    if (!head?.rows.length) return;
    const first = head.rows[0];
    let heading = first.querySelector(`:scope > .${columnClass}`);
    if (!heading) {
      heading = document.createElement('th');
      heading.className = columnClass;
      heading.scope = 'col';
      heading.textContent = '序号';
      first.prepend(heading);
    }
    if (heading.rowSpan !== head.rows.length) heading.rowSpan = head.rows.length;
    const columns = [...first.cells].reduce((sum, cell) => sum + cell.colSpan, 0);
    let number = 0;
    for (const body of table.tBodies) {
      for (const row of body.rows) {
        let cell = row.querySelector(`:scope > .${columnClass}`);
        const dataCells = [...row.cells].filter(item => item !== cell);
        // A full-width empty/loading row is a message, not a numbered record.
        if (dataCells.length === 1 && dataCells[0].colSpan > 1) {
          cell?.remove();
          if (dataCells[0].colSpan !== columns) dataCells[0].colSpan = columns;
          continue;
        }
        if (!dataCells.length) continue;
        if (!cell) {
          cell = document.createElement('td');
          cell.className = columnClass;
          row.prepend(cell);
        }
        const style = getComputedStyle(row);
        const visible = !row.hidden && style.display !== 'none' && style.visibility !== 'collapse' && style.visibility !== 'hidden';
        const label = visible ? String(++number) : '';
        if (cell.textContent !== label) cell.textContent = label;
      }
    }
    // Keep summary/footer spans aligned without assigning a record number.
    for (const row of table.tFoot?.rows || []) {
      if (row.cells.length === 1 && row.cells[0].colSpan !== columns) row.cells[0].colSpan = columns;
    }
  }

  function collect(node, tables) {
    if (!(node instanceof Element)) return;
    const table = node.closest('table');
    if (table?.matches(selector)) tables.add(table);
    node.querySelectorAll(selector).forEach(item => tables.add(item));
  }

  const observer = new MutationObserver(records => {
    const tables = new Set();
    for (const record of records) {
      if (record.target instanceof Element && record.target.closest(`.${columnClass}`)) continue;
      collect(record.target, tables);
    }
    tables.forEach(table => { if (table.isConnected) numberTable(table); });
  });
  // Admin-only roots: message previews and public guide tables keep their format.
  for (const root of document.querySelectorAll('#app, #modal')) {
    root.querySelectorAll(selector).forEach(numberTable);
    observer.observe(root, {childList: true, subtree: true, attributes: true,
      attributeFilter: ['hidden', 'class', 'style']});
  }
})();
