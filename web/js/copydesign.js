// SPDX-License-Identifier: AGPL-3.0-or-later
// Copy design: start a new machine from an existing one. A step-by-step dialog:
// 1 where and how  ›  2 review every part  ›  3 copy  ›  4 result with links.
import { h, mount, icon, get, post, dialog, fail, toast, debounce } from './core.js';

const ACTIONS = [['copy', 'Copy with new number'], ['reuse', 'Reuse as is'], ['replace', 'Replace with another part']];

function newKey() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return 'copy-' + Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}

function folderOptions(folders) {
  const kids = new Map();
  for (const f of folders) { const k = f.parent_id || 0; if (!kids.has(k)) kids.set(k, []); kids.get(k).push(f); }
  const out = [];
  const walk = (pid, depth, path) => {
    for (const f of (kids.get(pid) || []).sort((a, b) => a.name.localeCompare(b.name))) {
      const p = path ? `${path} › ${f.name}` : f.name;
      out.push({ id: f.id, label: `${'\u00a0\u00a0\u00a0'.repeat(depth)}${f.name}`, path: p });
      walk(f.id, depth + 1, p);
    }
  };
  walk(0, 0, '');
  return out;
}

export async function copyDesignDialog(root) {
  let folders, start;
  try {
    folders = folderOptions((await get('/api/folders')).folders);
    if (root.document_id) {
      const d = await get(`/api/documents/${root.document_id}`);
      start = { title: `${d.part_number} ${d.description}`.trim() || d.filename, folder: d.folder_id };
    } else {
      const r = await get(`/api/items/${root.item_id}`);
      const doc = r.documents[0];
      start = { title: `${r.item.part_number} ${r.item.description}`, folder: doc ? doc.folder_id : folders[0]?.id };
    }
  } catch (e) { fail(e); return; }
  // default target: the folder above the one the design lives in (its product line)
  const parentOf = id => { const f = folders.find(x => x.id === id); return f && f.path.includes(' › ') ? folders.find(x => x.path === f.path.slice(0, f.path.lastIndexOf(' › ')))?.id : id; };
  const opts = {
    folder_id: parentOf(start.folder) || start.folder, new_folder: '', prefix: '', width: 3, desc_prefix: '', desc_suffix: '',
    replace_from: '', replace_to: '', include_drawings: true, include_subassemblies: true,
  };
  const choices = new Map();   // item_id -> { action, new_part_number, new_description, replace_item_id }
  let key = null;

  const body = h('div', { class: 'col', style: { gap: '14px' } });
  const foot = h('div', { class: 'dlg-f' });
  const dlg = dialog({ title: `Copy design: ${start.title}`, wide: true, body });
  dlg.el.appendChild(foot);
  const steps = ['Where and how', 'Review', 'Copy', 'Result'];
  const stepper = n => h('div', { class: 'stepper', 'aria-label': `Step ${n + 1} of 4` }, steps.flatMap((s, i) => [
    i ? h('span', { class: 'line' }) : null,
    h('span', { class: `s ${i < n ? 'done' : i === n ? 'cur' : ''}` }, h('span', { class: 'dot' }, i < n ? '✓' : i + 1), s)]));
  const button = (label, primary, run, why) => {
    const b = h('button', { class: `btn ${primary ? 'primary' : ''}`, disabled: !!why, title: why || '' }, label);
    b.addEventListener('click', run);
    return b;
  };
  const request = () => ({ ...opts, ...root, rows: [...choices.entries()].map(([item_id, c]) => ({ item_id, ...c })) });

  // ---------------------------------------------------------------- step 1
  function stepWhere() {
    const field = (label, el, hint) => h('div', { class: 'field' }, h('label', { for: el.id }, label), el, hint ? h('span', { class: 'hint' }, hint) : null);
    const inp = (id, k, extra = {}) => { const e = h('input', { class: 'input', id, value: opts[k], ...extra }); e.addEventListener('input', () => { opts[k] = e.type === 'number' ? Number(e.value) : e.value; check(); }); return e; };
    const folderSel = h('select', { class: 'input', id: 'cd-folder' }, folders.map(f => h('option', { value: f.id, selected: f.id === opts.folder_id }, f.label)));
    folderSel.addEventListener('change', () => { opts.folder_id = Number(folderSel.value); });
    const cb = (k, label) => { const c = h('input', { type: 'checkbox', checked: opts[k] }); c.addEventListener('change', () => { opts[k] = c.checked; }); return h('label', { class: 'check' }, c, label); };
    const prefix = inp('cd-prefix', 'prefix', { placeholder: 'e.g. MX-200-', class: 'input mono', autocomplete: 'off' });
    const next = button('Next: review the parts', true, () => stepReview(true));
    const check = () => { const why = !opts.prefix.trim() ? 'Type the prefix for the new part numbers first.' : !opts.folder_id ? 'Choose the target folder first.' : ''; next.disabled = !!why; next.title = why; };
    mount(body, stepper(0),
      h('p', { class: 'muted', style: { margin: 0 } }, 'Kosha copies the parts you choose under new part numbers, with their files and BOMs, in one step. Nothing changes on the original design.'),
      h('div', { class: 'form-grid' },
        field('Copy into folder', folderSel), field('New folder inside it (optional)', inp('cd-newfolder', 'new_folder', { placeholder: 'e.g. MX-VA-1500 Vertical Agitator' })),
        field('New part numbers start with', prefix, 'Running numbers follow the prefix, e.g. MX-200-001, MX-200-002 …'),
        field('Digits in the running number', inp('cd-width', 'width', { type: 'number', min: 1, max: 8 }))),
      h('div', { class: 'form-grid' },
        field('Description: add in front', inp('cd-dp', 'desc_prefix', { placeholder: 'optional' })), field('Description: add at the end', inp('cd-ds', 'desc_suffix', { placeholder: 'optional, e.g.  VA-1500' })),
        field('Description: replace this text', inp('cd-rf', 'replace_from', { placeholder: 'optional, e.g. 1200' })), field('… with', inp('cd-rt', 'replace_to', { placeholder: 'e.g. 1500' }))),
      h('div', { class: 'row wrap' }, cb('include_drawings', 'Copy drawings too'), cb('include_subassemblies', 'Copy sub-assemblies and their parts')));
    mount(foot, button('Cancel', false, () => dlg.close()), next);
    check();
    setTimeout(() => prefix.focus(), 30);
  }

  // ---------------------------------------------------------------- step 2
  async function stepReview(fresh) {
    if (fresh) mount(body, stepper(1), h('div', { class: 'muted' }, 'Working out what belongs to the design…'));
    let p;
    try { p = await post('/api/copy-design/preview', request()); } catch (e) { fail(e); stepWhere(); return; }
    const rerun = debounce(() => stepReview(false), 350);
    const set = (row, patch) => { choices.set(row.item_id, { ...(choices.get(row.item_id) || {}), ...patch }); rerun(); };
    const rows = p.rows.map(r => {
      const act = h('select', { class: 'input', style: { minWidth: '190px' }, 'aria-label': `Action for ${r.part_number}`, disabled: r.level === 0, title: r.level === 0 ? 'The assembly you chose is always copied.' : '' },
        ACTIONS.map(([v, l]) => h('option', { value: v, selected: r.action === v }, l)));
      act.addEventListener('change', () => set(r, { action: act.value }));
      let target = h('span', { class: 'muted' }, '—');
      if (r.action === 'copy') {
        const pn = h('input', { class: 'input mono', value: r.new_part_number, 'aria-label': `New part number for ${r.part_number}`, style: { minWidth: '130px' } });
        pn.addEventListener('change', () => set(r, { new_part_number: pn.value.trim() }));
        const ds = h('input', { class: 'input', value: r.new_description, 'aria-label': `New description for ${r.part_number}`, style: { minWidth: '180px' } });
        ds.addEventListener('change', () => set(r, { new_description: ds.value }));
        target = h('div', { class: 'col', style: { gap: '4px' } }, pn, ds);
      } else if (r.action === 'replace') {
        const rp = h('input', { class: 'input mono', value: r.replace_part_number || '', placeholder: 'Part number of the replacement', 'aria-label': `Replacement for ${r.part_number}` });
        rp.addEventListener('change', async () => {
          const q = rp.value.trim().toUpperCase();
          if (!q) return;
          try {
            const hit = (await get(`/api/items?q=${encodeURIComponent(q)}&limit=20`)).items.find(i => i.part_number.toUpperCase() === q);
            if (!hit) { toast(`${q} is not a part in Kosha.`, 'err'); return; }
            set(r, { action: 'replace', replace_item_id: hit.id });
          } catch (e) { fail(e); }
        });
        target = h('div', { class: 'col', style: { gap: '4px' } }, rp, r.replace_description ? h('span', { class: 'small muted' }, r.replace_description) : null);
      }
      const files = r.files.filter(f => f.copy);
      return h('tr', null,
        h('td', { style: { paddingLeft: `${10 + r.level * 16}px` } }, h('div', { class: 'mono' }, r.part_number), h('div', { class: 'small muted' }, r.description)),
        h('td', { class: 'num' }, r.level ? r.qty : ''),
        h('td', null, act, h('div', { class: 'small muted', style: { marginTop: '2px' } }, r.reason)),
        h('td', null, target),
        h('td', { class: 'small' }, files.length ? files.map(f => h('div', { class: 'mono' }, f.new_filename))
          : h('span', { class: 'muted' }, r.action === 'copy' ? 'No files' : r.files.length ? `${r.files.length} file(s) stay as they are` : '—')));
    });
    const why = p.problems.length ? 'Fix the problems listed above first.' : '';
    mount(body, stepper(1),
      p.problems.length ? h('div', { class: 'banner err small' }, icon('warn'), h('div', null, h('b', null, 'Before copying:'), h('ul', { style: { margin: '4px 0 0', paddingLeft: '18px' } }, p.problems.map(x => h('li', null, x))))) : null,
      p.notes.map(n => h('div', { class: 'banner info small' }, icon('info'), h('div', null, n))),
      h('div', { class: 'small muted' }, `${p.counts.copy} part(s) copied with ${p.counts.files} file(s), ${p.counts.reuse} reused, ${p.counts.replace} replaced. Change an action, number or description and the table updates.`),
      h('div', { style: { overflow: 'auto', border: '1px solid var(--line)', borderRadius: '6px' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Part', 'Qty', 'Action', 'New number and description', 'New files'].map(t => h('th', null, t)))),
        h('tbody', null, rows))));
    mount(foot, button('Back', false, stepWhere), h('span', { class: 'spacer' }),
      button(`Copy ${p.counts.copy} part${p.counts.copy === 1 ? '' : 's'}`, true, stepCopy, why));
  }

  // ---------------------------------------------------------------- step 3 + 4
  async function stepCopy() {
    key = key || newKey();   // the same key on "Try again", so a copy is never made twice
    mount(body, stepper(2), h('div', { class: 'banner info' }, icon('info'), h('div', null, 'Copying… Please keep this window open.')));
    mount(foot);
    let res;
    try { res = await post('/api/copy-design', { ...request(), key }); } catch (e) {
      const known = e.code === 'copy_problems' || e.code === 'copy_key_reused';
      mount(body, stepper(2), h('div', { class: 'banner err' }, icon('warn'), h('div', null, h('b', null, e.message),
        h('div', { class: 'small', style: { marginTop: '4px' } }, known ? 'Nothing was copied.'
          : 'If the copy did finish on the server, trying again shows its result; it is never made twice.'))));
      if (e.code === 'copy_key_reused') key = null;
      mount(foot, button('Back to the review', false, () => { key = null; stepReview(true); }), h('span', { class: 'spacer' }),
        known ? null : button('Try again', true, stepCopy));
      return;
    }
    window.dispatchEvent(new Event('kosha:changed'));
    const top = res.created[0];
    mount(body, stepper(3),
      h('div', { class: 'banner ok' }, icon('check'), h('div', null, `${res.created.length} part(s) copied into ${res.folder.path}. They start at revision A, In work.`)),
      res.notes.map(n => h('div', { class: 'banner info small' }, icon('info'), h('div', null, n))),
      h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['New part', 'Copied from', 'Files', 'BOM'].map(t => h('th', null, t)))),
        h('tbody', null, res.created.map(c => h('tr', null,
          h('td', null, h('a', { class: 'mono', href: `#/item/${c.item.id}`, onclick: () => dlg.close() }, c.item.part_number), h('div', { class: 'small muted' }, c.item.description)),
          h('td', { class: 'small' }, `${c.source.part_number} rev ${c.source.revision}`),
          h('td', { class: 'small' }, c.documents.length ? c.documents.map(d => h('div', null, h('a', { href: `#/doc/${d.id}`, onclick: () => dlg.close() }, d.filename))) : '—'),
          h('td', { class: 'small' }, c.bom_id ? h('a', { href: `#/bom/${c.bom_id}`, onclick: () => dlg.close() }, 'Open BOM') : '—'))))),
      res.reused.length ? h('div', { class: 'small muted' }, `Reused as they are: ${res.reused.map(x => x.part_number).join(', ')}.`) : null,
      res.replaced.length ? h('div', { class: 'small muted' }, `Replaced: ${res.replaced.map(x => `${x.part_number} by ${x.by_part_number}`).join(', ')}.`) : null);
    mount(foot, h('a', { class: 'btn', href: `#/files/${res.folder.id}`, onclick: () => dlg.close() }, icon('folderOpen', 16), 'Open the folder'),
      h('span', { class: 'spacer' }),
      top ? h('a', { class: 'btn primary', href: `#/item/${top.item.id}`, onclick: () => dlg.close() }, 'Open the new assembly') : null);
  }

  stepWhere();
}
