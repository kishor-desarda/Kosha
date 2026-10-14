// SPDX-License-Identifier: AGPL-3.0-or-later
// Manufacturing BOMs and process plans (#/mboms, #/mbom/<id>): how the workshop builds an assembly, part by part and
// operation by operation, checked against the engineering BOM.
import { S, h, mount, clear, icon, get, post, put, toast, fail, dialog, confirmDlg, ago, can } from './core.js';
import { itemPicker } from './bom.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const chip = state => h('span', { class: `chip st-${state === 'released' ? 'released' : 'draft'}` }, state === 'released' ? 'Released' : 'Draft');
const num = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };

export async function renderMboms(container, _m, _p, { setTitle }) {
  setTitle('Manufacturing BOMs');
  const r = await get('/api/mboms');
  const q = h('input', { class: 'input', type: 'search', placeholder: 'Filter by part number or description', 'aria-label': 'Filter manufacturing BOMs', style: { maxWidth: '360px' } });
  const tbody = h('tbody');
  const draw = () => {
    clear(tbody);
    const f = q.value.toLowerCase();
    for (const m of r.mboms.filter(m => !f || `${m.part_number} ${m.description}`.toLowerCase().includes(f))) {
      tbody.appendChild(h('tr', { class: 'clickable', onclick: () => { location.hash = `#/mbom/${m.id}`; } },
        h('td', { class: 'mono' }, m.part_number), h('td', null, m.description), h('td', null, m.revision), h('td', null, chip(m.state)),
        h('td', { class: 'num' }, m.ops), h('td', { class: 'num' }, m.lines), h('td', { class: 'muted' }, ago(m.updated_at))));
    }
    if (!tbody.children.length) tbody.appendChild(h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' },
      'No manufacturing BOMs yet. Open an engineering BOM and press "Manufacturing BOM" to make one from it.'))));
  };
  q.addEventListener('input', draw);
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Manufacturing BOMs'),
      h('div', { class: 'sub' }, 'How the workshop builds each assembly: the parts issued at each operation, and the process plan. Made from the engineering BOM and checked against it.')),
    h('a', { class: 'btn', href: '#/boms' }, 'Engineering BOMs'),
    can('engineer') ? h('button', { class: 'btn primary', onclick: newDialog }, icon('plus', 16), 'New manufacturing BOM') : null),
    h('div', { style: { marginBottom: '12px' } }, q),
    h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Assembly', 'Description', 'Rev', 'State', 'Operations', 'Parts', 'Updated'].map(t => h('th', null, t)))), tbody))));
  draw();
}

function newDialog() {
  let picked = null;
  const label = h('div', { class: 'small' }, 'Pick the assembly. Its manufacturing BOM is made from its newest engineering BOM.');
  dialog({ title: 'New manufacturing BOM', body: [label, itemPicker(it => { picked = it; label.textContent = `Selected: ${it.part_number} ${it.description}`; }, 'Type the assembly part number')],
    actions: [{ label: 'Cancel' }, { label: 'Make it', primary: true, onClick: async () => {
      if (!picked) { toast('Pick the assembly first', 'err'); return false; }
      const r = await post('/api/mboms', { item_id: picked.id });
      location.hash = `#/mbom/${r.mbom_id}`;
    } }] });
}

// From an engineering BOM: open the manufacturing BOM of the same revision, or offer to make it.
export async function openMbom(itemId, revision) {
  try {
    const list = (await get(`/api/mboms?item_id=${itemId}`)).mboms;
    const same = list.find(m => m.revision === revision);
    if (same) { location.hash = `#/mbom/${same.id}`; return; }
    if (!can('engineer')) {
      if (list.length) location.hash = `#/mbom/${list[0].id}`;
      else toast('This assembly has no manufacturing BOM yet. An engineer can make one from this BOM.', '');
      return;
    }
    const msg = list.length
      ? `Make the manufacturing BOM for revision ${revision}? The process plan, the parts' operations and the parts added for manufacturing come over from revision ${list[0].revision}.`
      : `Make the manufacturing BOM for revision ${revision} from this engineering BOM? You then add the operations and say which part is used where.`;
    if (!await confirmDlg('Manufacturing BOM', msg, 'Make it')) {
      if (list.length) location.hash = `#/mbom/${list[0].id}`;
      return;
    }
    const r = await post('/api/mboms', { item_id: itemId, revision });
    location.hash = `#/mbom/${r.mbom_id}`;
  } catch (e) { fail(e); }
}

export async function renderMbom(container, [id], _p, { setTitle }) {
  let d;
  let ops = null;
  let lines = null;
  const dirty = () => ops !== null || lines !== null;
  const load = async (data) => {
    d = data || await get(`/api/mboms/${id}`);
    ops = null; lines = null;
    setTitle(`Manufacturing BOM ${d.mbom.part_number}`);
    draw();
  };
  const curOps = () => ops || d.ops.map(o => ({ ...o }));
  const curLines = () => lines || d.lines.map(l => ({ ...l }));
  const touchOps = () => { if (!ops) ops = curOps(); return ops; };
  const touchLines = () => { if (!lines) lines = curLines(); return lines; };

  const save = async () => {
    const body = {};
    if (ops) body.ops = ops.map(o => ({ op_no: o.op_no, work_centre: o.work_centre, description: o.description, setup_min: o.setup_min, run_min: o.run_min, instructions: o.instructions }));
    if (lines) body.lines = lines.map(l => ({ item_id: l.item_id, part_number: l.part_number, description: l.description, qty: l.qty, op_no: l.op_no, note: l.note }));
    try { await load(await put(`/api/mboms/${id}/plan`, body)); toast('Manufacturing BOM saved', 'ok'); } catch (e) { fail(e); }
  };
  const act = async (path, body, msg) => {
    if (dirty() && !await confirmDlg('Unsaved changes', 'Your changes are not saved yet and will be lost. Go on?', 'Go on', true)) return;
    try { await load(await post(path, body)); if (msg) toast(msg, 'ok'); changed(); } catch (e) { fail(e); }
  };

  const draw = () => {
    const m = d.mbom;
    const editable = m.state !== 'released' && can('engineer');
    const errors = d.issues.filter(i => i.severity === 'error').length;
    const warns = d.issues.filter(i => i.severity === 'warning').length;
    const revSel = h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Manufacturing BOM revision', onchange: e => { location.hash = `#/mbom/${e.target.value}`; } },
      d.revisions.map(r => h('option', { value: r.id, selected: r.id === m.id }, `Revision ${r.revision} (${r.state === 'released' ? 'released' : 'draft'})`)));
    const releaseBtn = can('approver') && m.state !== 'released'
      ? h('button', { class: 'btn primary', disabled: !d.can_release.ok || dirty(), title: dirty() ? 'Save your changes first' : d.can_release.why || 'Lock this manufacturing BOM' + (S.erp ? ` and send it to ${S.erp} when it takes manufacturing BOMs` : ''),
        onclick: async () => {
          if (!await confirmDlg('Release manufacturing BOM', `Release the manufacturing BOM of ${m.part_number} revision ${m.revision}? It is then locked.`, 'Release')) return;
          act(`/api/mboms/${id}/release`, {}, 'Manufacturing BOM released');
        } }, icon('release', 16), 'Release')
      : null;
    const head = h('div', { class: 'page-head' },
      h('div', { style: { flex: 1, minWidth: '300px' } }, h('div', { class: 'small muted' }, 'Manufacturing BOM and process plan'),
        h('h1', null, h('a', { href: `#/item/${m.item_id}`, class: 'mono', style: { color: 'inherit', textDecoration: 'none' } }, m.part_number), ' ', h('span', { style: { fontWeight: 400 } }, m.description)),
        h('div', { class: 'row wrap', style: { marginTop: '8px' } }, revSel,
          h('span', { class: `chip st-${m.state === 'released' ? 'released' : 'draft'}` }, m.state === 'released' ? 'Released — locked' : 'Draft — can be edited'),
          d.ebom ? h('a', { class: 'small', href: `#/bom/${d.ebom.id}` }, `Follows engineering BOM rev ${d.ebom.revision} (${d.ebom.state === 'released' ? 'released' : 'draft'})`) : null,
          h('span', { class: 'muted small' }, `updated ${ago(m.updated_at)}`))),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } },
        editable ? h('button', { class: 'btn', title: 'Take the parts from the engineering BOM again; operations, notes and added parts are kept', onclick: () => act(`/api/mboms/${id}/rebuild`, {}, 'Parts taken from the engineering BOM again') }, icon('refresh', 16), 'Rebuild from engineering BOM') : null,
        m.state === 'released' && d.newer_ebom && can('engineer') ? h('button', { class: 'btn', onclick: () => openMbom(m.item_id, d.newer_ebom) }, icon('plus', 16), `Make for revision ${d.newer_ebom}`) : null,
        h('a', { class: 'btn', href: `/api/mboms/${m.id}/export?access_token=${encodeURIComponent(S.token)}` }, icon('download', 16), 'Excel'),
        releaseBtn));

    // --- process plan
    const plan = curOps();
    const wcList = h('datalist', { id: 'mb-wc' }, d.work_centres.map(w => h('option', { value: w })));
    const opRows = plan.map((o, i) => {
      const parts = curLines().filter(l => l.op_no === o.op_no).length;
      const cell = (key, attrs = {}) => editable
        ? h('input', { class: `input ${attrs.cls || ''}`, value: o[key] ?? '', 'aria-label': `${attrs.label} of operation ${o.op_no}`, type: attrs.type || 'text', min: attrs.type === 'number' ? 0 : null, step: attrs.type === 'number' ? 'any' : null, list: attrs.list || null, style: attrs.style || null,
          oninput: e => { touchOps()[i][key] = attrs.type === 'number' ? (key === 'op_no' ? parseInt(e.target.value, 10) || 0 : num(e.target.value)) : e.target.value; markDirty(); } })
        : h('span', null, o[key] === '' || o[key] == null ? h('span', { class: 'muted' }, '—') : String(o[key]));
      return h('tr', null,
        h('td', { class: 'num' }, cell('op_no', { type: 'number', label: 'Number', style: { width: '70px' } })),
        h('td', null, cell('work_centre', { label: 'Work centre', list: 'mb-wc' })),
        h('td', null, cell('description', { label: 'What happens', style: { minWidth: '260px' } })),
        h('td', { class: 'num' }, cell('setup_min', { type: 'number', label: 'Set-up minutes', style: { width: '80px' } })),
        h('td', { class: 'num' }, cell('run_min', { type: 'number', label: 'Run minutes', style: { width: '80px' } })),
        h('td', null, editable ? h('textarea', { class: 'input', rows: 1, style: { minHeight: '38px', height: '38px', minWidth: '200px' }, value: o.instructions || '', 'aria-label': `Instructions for operation ${o.op_no}`, oninput: e => { touchOps()[i].instructions = e.target.value; markDirty(); } }) : h('span', { class: 'small' }, o.instructions || '')),
        h('td', { class: 'num muted' }, parts),
        editable ? h('td', null, h('button', { class: 'btn ghost sm', 'aria-label': `Remove operation ${o.op_no}`, disabled: parts > 0, title: parts ? 'Move its parts to another operation first' : '',
          onclick: () => { touchOps().splice(i, 1); draw(); } }, icon('trash', 14))) : null);
    });
    const opsPanel = h('section', { class: 'panel', style: { overflow: 'auto', marginBottom: '16px' } },
      h('div', { class: 'panel-h' }, h('h2', null, 'Process plan'), h('span', { class: 'muted small' }, `${plan.length} operation${plan.length === 1 ? '' : 's'} · set-up ${d.totals.setup_min} min · run ${d.totals.run_min} min`), h('span', { class: 'spacer' }),
        editable ? h('button', { class: 'btn sm', onclick: () => { const o = touchOps(); const next = (o.reduce((a, x) => Math.max(a, x.op_no), 0) || 0) + 10; o.push({ op_no: next, work_centre: '', description: '', setup_min: 0, run_min: 0, instructions: '' }); draw(); } }, icon('plus', 14), 'Add operation') : null),
      wcList,
      plan.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Op', 'Work centre', 'What happens', 'Set-up (min)', 'Run (min)', 'Instructions', 'Parts', editable ? '' : null].filter(c => c !== null).map(t => h('th', null, t)))), h('tbody', null, opRows))
        : h('div', { class: 'empty' }, editable ? 'No operations yet. Add the steps the workshop follows, for example 10 Build the drive unit, 20 Fit the tank, 30 Paint.' : 'No process plan yet.'));

    // --- parts
    const ls = curLines();
    const opChoices = plan.map(o => o.op_no);
    const subIds = new Set(d.sub_assemblies.map(s => s.item_id));
    const partRows = ls.map((l, i) => h('tr', null,
      h('td', { class: 'mono nowrap' }, h('a', { href: `#/item/${l.item_id}` }, l.part_number)),
      h('td', null, l.description, l.origin === 'added' ? h('span', { class: 'chip', style: { marginLeft: '6px' }, title: 'Not in the engineering BOM' }, 'Added') : null),
      h('td', { class: 'num' }, editable ? h('input', { class: 'qty', type: 'number', min: 0, step: 'any', value: l.qty, 'aria-label': `Quantity of ${l.part_number}`, oninput: e => { touchLines()[i].qty = num(e.target.value); markDirty(); } }) : l.qty),
      h('td', null, l.uom || l.item_uom),
      h('td', null, editable ? h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': `Operation that uses ${l.part_number}`, onchange: e => { touchLines()[i].op_no = e.target.value ? parseInt(e.target.value, 10) : null; markDirty(); } },
        h('option', { value: '', selected: l.op_no == null }, '—'), opChoices.map(n => h('option', { value: n, selected: l.op_no === n }, String(n))))
        : (l.op_no ?? h('span', { class: 'muted' }, '—'))),
      h('td', null, editable ? h('input', { class: 'input', value: l.note || '', 'aria-label': `Note for ${l.part_number}`, oninput: e => { touchLines()[i].note = e.target.value; markDirty(); } }) : h('span', { class: 'small' }, l.note || '')),
      h('td', { class: 'nowrap' },
        editable && subIds.has(l.item_id) && !dirty() ? h('button', { class: 'btn sm', title: 'Build this sub-assembly in one go with its parent: its parts are issued here instead', onclick: () => act(`/api/mboms/${id}/flatten`, { item_id: l.item_id, on: true }, `${l.part_number} is now built with its parent`) }, 'Build with parent') : null,
        editable ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${l.part_number}`, onclick: () => { touchLines().splice(i, 1); draw(); } }, icon('trash', 14)) : null)));
    const partsPanel = h('section', { class: 'panel', style: { overflow: 'auto' } },
      h('div', { class: 'panel-h' }, h('h2', null, 'Parts issued'), h('span', { class: 'muted small' }, `${ls.length} line${ls.length === 1 ? '' : 's'}`), h('span', { class: 'spacer' }),
        editable ? h('button', { class: 'btn sm', onclick: addPartDlg }, icon('plus', 14), 'Add part') : null),
      d.flattened.length ? h('div', { class: 'panel-b small', style: { borderBottom: '1px solid var(--line-2)' } }, 'Built with their parent: ',
        d.flattened.map(f => h('span', { class: 'row', style: { display: 'inline-flex', gap: '4px', marginRight: '10px' } }, h('b', { class: 'mono' }, f.part_number),
          editable && !dirty() ? h('button', { class: 'btn ghost sm', title: 'Build it separately again, as one line', onclick: () => act(`/api/mboms/${id}/flatten`, { item_id: f.id, on: false }, `${f.part_number} is built separately again`) }, 'Build separately') : null))) : null,
      ls.length ? h('table', { class: 'tbl bom-grid' }, h('thead', null, h('tr', null, ['Part number', 'Description', 'Qty', 'Unit', 'Operation', 'Note', ''].map(t => h('th', null, t)))), h('tbody', null, partRows))
        : h('div', { class: 'empty' }, 'No parts. Rebuild from the engineering BOM, or add parts one by one.'));

    const bar = h('div', { class: 'row', id: 'mb-dirty', style: { justifyContent: 'flex-end', margin: '0 0 12px', gap: '8px' }, hidden: !dirty() },
      h('span', { class: 'muted small' }, 'Changes not saved yet.'),
      h('button', { class: 'btn sm', onclick: () => { ops = null; lines = null; draw(); } }, 'Discard'),
      h('button', { class: 'btn sm primary', onclick: save }, icon('check', 14), 'Save changes'));

    const side = h('aside', { class: 'panel', style: { flex: '0 1 340px', minWidth: '280px', alignSelf: 'flex-start' } },
      h('div', { class: 'panel-h' }, h('h2', null, 'Checks'), h('span', { class: 'spacer' }),
        errors ? h('span', { class: 'chip st-failed' }, `${errors} error${errors > 1 ? 's' : ''}`) : null,
        warns ? h('span', { class: 'chip', style: { background: 'var(--warn-bg)', color: 'var(--warn)' } }, `${warns} warning${warns > 1 ? 's' : ''}`) : null),
      h('div', { class: 'panel-b' }, d.issues.length ? d.issues.map(i => h('div', { class: 'issue' },
        h('span', { class: `sev-${i.severity}` }, icon(i.severity === 'info' ? 'info' : 'warn', 16)),
        h('div', null, i.part_number ? h('b', { class: 'mono' }, i.part_number, ' ') : null, i.message)))
        : h('div', { class: 'banner ok small' }, icon('check'), h('div', null, 'Every part of the engineering BOM is issued at an operation.'))),
      !d.can_release.ok && m.state !== 'released' ? h('div', { class: 'panel-b small muted', style: { borderTop: '1px solid var(--line-2)' } }, `Release: ${d.can_release.why}`) : null);

    mount(container, h('div', { class: 'page-pad' }, head,
      S.mode === 'guided' && editable ? h('div', { class: 'banner info', style: { marginBottom: '14px' } }, icon('info'), h('div', null,
        'Add the operations the workshop follows, then choose for each part the operation that uses it. A sub-assembly the workshop builds together with its parent can be built ', h('b', null, 'with parent'),
        '. Parts the design does not list (paint, grease) can be added. ', h('b', null, 'Checks'), ' make sure every part of the design is issued.')) : null,
      bar,
      h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '16px' } },
        h('div', { style: { flex: '1 1 640px', minWidth: 0 } }, opsPanel, partsPanel), side)));
  };

  const markDirty = () => { const bar = container.querySelector('#mb-dirty'); if (bar) bar.hidden = false; };

  const addPartDlg = () => {
    let picked = null;
    const info = h('div', { class: 'small' }, 'Choose a part, or type a new part number below (for example paint or grease).');
    const pn = h('input', { class: 'input mono', id: 'mp-pn', placeholder: 'New part number' });
    const ds = h('input', { class: 'input', id: 'mp-ds', placeholder: 'Description' });
    const qty = h('input', { class: 'input', id: 'mp-q', type: 'number', value: 1, min: 0, step: 'any' });
    const op = h('select', { class: 'input', id: 'mp-op' }, h('option', { value: '' }, '—'), curOps().map(o => h('option', { value: o.op_no }, `${o.op_no} ${o.description}`)));
    dialog({ title: 'Add a part', body: [info, itemPicker(it => { picked = it; info.textContent = `Selected: ${it.part_number} ${it.description}`; }),
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'mp-pn' }, 'New part number'), pn), h('div', { class: 'field' }, h('label', { for: 'mp-ds' }, 'Description'), ds),
        h('div', { class: 'field' }, h('label', { for: 'mp-q' }, 'Quantity'), qty), h('div', { class: 'field' }, h('label', { for: 'mp-op' }, 'Operation'), op))],
    actions: [{ label: 'Cancel' }, { label: 'Add', primary: true, onClick: () => {
      const row = { qty: num(qty.value) || 1, op_no: op.value ? parseInt(op.value, 10) : null, note: '', origin: 'added' };
      if (picked) Object.assign(row, { item_id: picked.id, part_number: picked.part_number, description: picked.description });
      else if (pn.value.trim()) Object.assign(row, { part_number: pn.value.trim().toUpperCase(), description: ds.value.trim() });
      else { toast('Choose or type a part', 'err'); return false; }
      touchLines().push(row); draw();
    } }] });
  };

  await load();
}
