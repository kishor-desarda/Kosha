// SPDX-License-Identifier: AGPL-3.0-or-later
// Standard-parts library: browse by category, filter by size, search by designation, "Use this part".
import { S, h, mount, icon, get, post, toast, fail, dialog, debounce, can } from './core.js';
import { fieldControl, newPartNumberDialog } from './numbering.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const NOT_CHECKED = 'Not yet checked against the standard. Check the dimensions in the standard before you rely on them.';

function verifiedChip(row) {
  return row.verified
    ? h('span', { class: 'chip st-released', title: `Checked against ${row.standard}${row.edition ? ':' + row.edition : ''}` }, 'Checked')
    : h('span', { class: 'chip', style: { background: 'var(--warn-bg)', color: 'var(--warn)' }, title: NOT_CHECKED }, 'Not checked');
}

const noRole = 'You need the engineer role to add parts to Kosha.';

/** "Use this part": choose the options, see the part number, then create or find the item. */
export function useDialog(table, row, preset = {}, onUsed) {
  const opts = { ...preset };
  const out = h('div', { class: 'panel', style: { background: 'var(--panel-2)' }, 'aria-live': 'polite' });
  const btn = h('button', { class: 'btn primary', type: 'button' }, icon('check', 16), 'Use this part');
  const why = h('span', { class: 'small muted', style: { flex: 1 } });
  let last = null;
  let seq = 0;
  const controls = {};
  const allowed = can('engineer');
  const setBtn = (ok, reason) => { btn.disabled = !ok; btn.title = ok ? '' : reason; why.textContent = ok ? '' : reason; };
  const refresh = debounce(async () => {
    const mine = ++seq;
    let r;
    try { r = await post('/api/library/preview', { table: table.id, row: row.key, options: opts }); } catch (e) { fail(e); return; }
    if (mine !== seq) return;
    last = r;
    for (const [n, c] of Object.entries(controls)) c.setError(opts[n] ? (r.errors || {})[n] : '');
    if (!r.ok) {
      mount(out, h('div', { class: 'panel-b small muted' }, 'Choose the options to see the part number.'));
      setBtn(false, Object.keys(r.errors || {}).some(k => opts[k]) ? 'Fix the option marked in red first.' : 'Choose the options first.');
      return;
    }
    const ex = r.existing;
    mount(out, h('div', { class: 'panel-b col', style: { gap: '6px' } },
      h('div', { class: 'small muted' }, 'Designation'), h('div', { style: { fontWeight: 600 } }, r.designation),
      h('div', { class: 'small muted' }, 'Part number'),
      h('div', { class: 'mono', style: { fontSize: '20px', fontWeight: 700, wordBreak: 'break-all' } }, r.part_number),
      h('div', null, r.description),
      ex ? h('div', { class: 'banner ok small' }, icon('check'), h('div', null, 'Already in Kosha as ', h('a', { href: `#/item/${ex.id}` }, ex.part_number), '. Using it again adds nothing new.'))
        : h('div', { class: 'small muted' }, 'Kosha will create this part, marked as bought out, ready for BOMs.')));
    if (!allowed) setBtn(false, noRole); else setBtn(true);
    btn.lastChild.textContent = ex ? 'Use the existing part' : 'Use this part';
  }, 150);
  const fields = table.options.map(f => {
    const c = fieldControl(f, opts[f.name], v => { opts[f.name] = v; refresh(); });
    controls[f.name] = c;
    return c.el;
  });
  const dims = h('dl', { class: 'props' }, table.columns.flatMap(c => [h('dt', null, c.label), h('dd', null, `${row.values[c.name]}${c.unit ? ' ' + c.unit : ''}`)]),
    h('dt', null, 'Standard'), h('dd', null, row.standard, row.edition ? `:${row.edition}` : ''));
  let dlg;
  btn.addEventListener('click', async () => {
    if (!last || !last.ok) return;
    btn.disabled = true;
    try {
      const r = await post('/api/library/use', { table: table.id, row: row.key, options: opts });
      toast(r.message, 'ok');
      changed();
      mount(dlg.el.querySelector('.dlg-b'), h('div', { class: 'col', style: { gap: '12px' } },
        h('div', { class: 'banner ok' }, icon('check'), h('div', null, r.message)),
        h('div', { class: 'mono', style: { fontSize: '22px', fontWeight: 700, userSelect: 'all' } }, r.part_number),
        h('div', null, r.item.description),
        h('div', { class: 'row wrap' },
          h('a', { class: 'btn primary', href: `#/item/${r.item.id}` }, icon('external', 16), 'Open the part'),
          h('button', { class: 'btn', onclick: async () => { try { await navigator.clipboard.writeText(r.part_number); toast('Copied', 'ok'); } catch (e) { toast('Copy did not work in this browser; select the number instead.', 'err'); } } }, icon('copy', 16), 'Copy part number'))));
      if (onUsed) onUsed(r);
    } catch (e) { fail(e); btn.disabled = false; }
  });
  dlg = dialog({ title: `${table.standard} ${row.key}`, body: [
    h('div', { class: 'small muted' }, table.label),
    row.verified ? null : h('div', { class: 'banner small' }, icon('warn'), h('div', null, NOT_CHECKED)),
    dims,
    fields.length ? h('div', { class: 'form-grid', style: { gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' } }, fields) : null,
    out, h('div', { class: 'row' }, why, btn)], actions: [{ label: 'Close' }] });
  refresh();
  return dlg;
}

export async function renderLibrary(container, _m, params, { setTitle }) {
  setTitle('Standard parts library');
  const ov = await get('/api/library');
  const tables = ov.categories.flatMap(c => c.tables);
  let tableId = tables.some(t => t.id === params.table) ? params.table : (tables[0] && tables[0].id);
  let size = params.size || '';
  const qInput = h('input', { class: 'input', type: 'search', id: 'lib-q', value: params.q || '', autocomplete: 'off',
    placeholder: 'Search by designation, e.g. M16x60 8.8, 6205-2RS, DIN 912 M8' });
  const side = h('nav', { class: 'panel', 'aria-label': 'Library categories', style: { flex: '0 0 270px', alignSelf: 'flex-start', padding: '6px 6px 10px' } });
  const main = h('section', { class: 'panel', style: { flex: '1 1 600px', minWidth: 0, overflow: 'auto' } });
  let current = null;
  let alive = true;

  const keepUrl = () => {
    const p = new URLSearchParams();
    if (qInput.value.trim()) p.set('q', qInput.value.trim());
    else { if (tableId) p.set('table', tableId); if (size) p.set('size', size); }
    history.replaceState(null, '', `#/library${p.toString() ? '?' + p : ''}`);
  };

  const drawSide = () => mount(side, ov.categories.map(c => [
    h('div', { class: 'tree-sec' }, `${c.name} (${c.code})`),
    c.tables.map(t => h('button', { class: `tree-node ${t.id === tableId && !qInput.value.trim() ? 'active' : ''}`,
      style: { border: 0, background: t.id === tableId && !qInput.value.trim() ? null : 'transparent', width: '100%', textAlign: 'left', height: 'auto', minHeight: '34px', whiteSpace: 'normal', padding: '5px 8px' },
      onclick: () => { tableId = t.id; size = ''; qInput.value = ''; keepUrl(); drawSide(); loadTable(); } },
    h('span', { style: { flex: 1 } }, h('b', null, t.standard), ' ', h('span', { class: 'small' }, t.label)), h('span', { class: 'cnt' }, t.rows)))]));

  const loadTable = async () => {
    if (!tableId) return;
    const r = await get(`/api/library/tables/${encodeURIComponent(tableId)}${size ? `?size=${encodeURIComponent(size)}` : ''}`);
    if (!alive) return;
    current = r;
    const t = r.table;
    const sizeCol = t.columns.find(c => c.name === t.size);
    const sizeSel = h('select', { class: 'input', id: 'lib-size', style: { width: 'auto', minWidth: '140px' }, onchange: e => { size = e.target.value; keepUrl(); loadTable(); } },
      h('option', { value: '' }, 'All sizes'), t.sizes.map(s => h('option', { value: s, selected: s === size }, `${s}${sizeCol.unit ? ' ' + sizeCol.unit : ''}`)));
    const cols = t.columns;
    const keyCol = (/^\{(\w+)\}$/.exec(t.key) || [])[1];      // the key is one column (6205): no separate Size column
    mount(main,
      h('div', { class: 'panel-h', style: { flexWrap: 'wrap' } },
        h('div', { style: { flex: 1, minWidth: '240px' } }, h('h2', null, `${t.standard} · ${t.label}`),
          h('div', { class: 'small muted' }, t.aliases.length ? `Replaces or matches ${t.aliases.join(', ')}. ` : '',
            `${t.rows} sizes, ${t.verified} checked against the standard.`)),
        h('label', { for: 'lib-size', class: 'small muted' }, `Filter by ${sizeCol.label.toLowerCase()}`), sizeSel),
      h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, keyCol ? null : h('th', null, 'Size'), cols.map(c => h('th', { class: c.type === 'text' ? null : 'num' }, c.label, c.unit ? ` (${c.unit})` : '')),
          h('th', null, 'Standard'), h('th', null, 'Checked'), h('th', null, ''))),
        h('tbody', null, r.rows.length ? r.rows.map(row => h('tr', null,
          keyCol ? null : h('td', { class: 'mono nowrap' }, h('b', null, row.key)),
          cols.map(c => h('td', { class: c.type === 'text' ? null : 'num' }, c.name === keyCol ? h('b', { class: 'mono' }, row.values[c.name]) : row.values[c.name])),
          h('td', { class: 'nowrap small' }, row.standard, row.edition ? `:${row.edition}` : ''),
          h('td', null, verifiedChip(row)),
          h('td', { class: 'nowrap' }, h('button', { class: 'btn sm', title: can('engineer') ? 'Choose the options and add the part to Kosha' : noRole,
            onclick: () => useDialog({ ...t, options: r.options }, row) }, 'Use this part'))))
          : h('tr', null, h('td', { colspan: cols.length + (keyCol ? 3 : 4) }, h('div', { class: 'empty' }, 'No sizes match this filter.'))))));
  };

  const runSearch = debounce(async () => {
    const q = qInput.value.trim();
    keepUrl();
    drawSide();
    if (!q) { loadTable(); return; }
    let r;
    try { r = await get(`/api/library/search?q=${encodeURIComponent(q)}&limit=60`); } catch (e) { fail(e); return; }
    if (!alive || q !== qInput.value.trim()) return;
    const tableDefs = {};
    mount(main,
      h('div', { class: 'panel-h' }, h('h2', null, `Parts matching "${q}"`), h('span', { class: 'spacer' }),
        h('span', { class: 'small muted' }, r.total > r.hits.length ? `First ${r.hits.length} of ${r.total}. Type more to narrow it down.` : `${r.total} found`)),
      r.hits.length ? h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Designation', 'Part number', 'Description', 'Checked', ''].map(x => h('th', null, x)))),
        h('tbody', null, r.hits.map(hit => h('tr', null,
          h('td', { class: 'nowrap' }, h('b', null, hit.designation)),
          h('td', { class: 'mono nowrap small' }, hit.existing ? h('a', { href: `#/item/${hit.existing.id}`, title: 'Already in Kosha' }, hit.part_number) : hit.part_number),
          h('td', { class: 'small' }, hit.description),
          h('td', null, verifiedChip(hit)),
          h('td', { class: 'nowrap' }, h('button', { class: 'btn sm', title: can('engineer') ? 'See the part and add it to Kosha' : noRole, onclick: async () => {
            try {
              const def = tableDefs[hit.table] = tableDefs[hit.table] || await get(`/api/library/tables/${encodeURIComponent(hit.table)}`);
              const row = def.rows.find(x => x.key === hit.row);
              useDialog({ ...def.table, options: def.options }, row, hit.options);
            } catch (e) { fail(e); }
          } }, hit.existing ? 'Open or use' : 'Use this part'))))))
        : h('div', { class: 'empty' }, 'Nothing matches. Try the size only (M16, 6205) or the standard (ISO 4017, DIN 912).'));
  }, 250);
  qInput.addEventListener('input', runSearch);

  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Standard parts library'),
      h('div', { class: 'sub' }, 'Market components described by their standards. "Use this part" adds it to Kosha with a fixed part number, so everyone uses the same one.')),
    h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => newPartNumberDialog() }, icon('plus', 16), 'New part number'))),
    !ov.installed ? h('div', { class: 'banner info', style: { marginBottom: '14px' } }, icon('info'), h('div', null,
      'No standard-parts data is installed yet. ',
      S.user.role === 'admin' ? [h('a', { href: '#/admin?tab=packs' }, 'Install the Standard parts pack'), ' under Admin › Packs.'] : 'Ask your admin to install the Standard parts pack under Admin › Packs.')) : null,
    S.mode === 'guided' && ov.installed ? h('div', { class: 'banner info small', style: { marginBottom: '14px' } }, icon('info'), h('div', null,
      'Pick a standard on the left or type a designation. Rows marked ', h('b', null, 'Not checked'), ' were typed from public sources and have not yet been checked against the standard.')) : null,
    h('div', { style: { marginBottom: '12px', maxWidth: '640px' } }, h('label', { for: 'lib-q', class: 'sr-only' }, 'Search the library'), qInput),
    ov.installed ? h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '16px' } }, side, main) : null));
  if (ov.installed) {
    drawSide();
    if (qInput.value.trim()) runSearch(); else await loadTable();
  }
  return () => { alive = false; };
}
