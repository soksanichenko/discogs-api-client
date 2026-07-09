// combo.js — lightweight searchable dropdown ("combobox"), used in place of a
// native <select> for filter dropdowns that can have long option lists (e.g.
// hundreds of distinct artist/label names). Native <select> popups cap their
// own visible height at a fixed browser constant regardless of available
// screen space — shrinking the font only buys a row or two — so long lists
// get stuck showing a handful of options with no way to see the rest without
// scrolling. This widget renders its own list under our own CSS control
// (explicit max-height + scroll, see .combo-list in theme.css) and adds a
// type-to-filter search box, which also helps for long lists.
//
// Expected markup:
//   <div class="combo">
//     <button type="button" class="combo-toggle"></button>
//     <div class="combo-menu">
//       <input type="text" class="combo-search">
//       <div class="combo-list"></div>
//     </div>
//   </div>
//
// initCombo(root, { onChange, allLabel }) wires up one such block and returns
// { setOptions(values), setValue(value), getValue() }. setOptions() takes the
// plain list of selectable string values — the "All ..." row is handled
// internally. setValue('') selects "All ...".
function initCombo(root, { onChange, allLabel = 'All' } = {}) {
  const toggle = root.querySelector('.combo-toggle');
  const menu = root.querySelector('.combo-menu');
  const search = root.querySelector('.combo-search');
  const list = root.querySelector('.combo-list');

  let options = [];
  let value = '';

  function escHtml(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function renderList() {
    const q = search.value.trim().toLowerCase();
    const visible = q ? options.filter(v => v.toLowerCase().includes(q)) : options;
    const rows = [`<div class="combo-option${value === '' ? ' active' : ''}" data-value="">${escHtml(allLabel)}</div>`];
    if (q && !visible.length) rows.push('<div class="combo-empty">No matches</div>');
    else rows.push(...visible.map(v => `<div class="combo-option${v === value ? ' active' : ''}" data-value="${escHtml(v)}">${escHtml(v)}</div>`));
    list.innerHTML = rows.join('');
  }

  function refreshToggleLabel() {
    toggle.textContent = value || allLabel;
    toggle.title = value || allLabel;
  }

  function isOpen() { return menu.classList.contains('open'); }
  function open() {
    menu.classList.add('open');
    search.value = '';
    renderList();
    search.focus();
  }
  function close() { menu.classList.remove('open'); }

  toggle.addEventListener('click', () => (isOpen() ? close() : open()));
  search.addEventListener('input', renderList);
  list.addEventListener('click', e => {
    const opt = e.target.closest('.combo-option[data-value]');
    if (!opt) return;
    value = opt.dataset.value;
    refreshToggleLabel();
    close();
    onChange?.(value);
  });
  document.addEventListener('click', e => { if (isOpen() && !root.contains(e.target)) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && isOpen()) close(); });

  refreshToggleLabel();

  return {
    setOptions(values) {
      options = values;
      if (isOpen()) renderList();
    },
    setValue(v) {
      value = v || '';
      refreshToggleLabel();
      if (isOpen()) renderList();
    },
    getValue() { return value; },
  };
}
