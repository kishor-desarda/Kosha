// SPDX-License-Identifier: AGPL-3.0-or-later
// Engineering BOMs: list, indented editor, validation, import, compare, ERP push.
import { S, h, mount, clear, icon, get, post, put, toast, fail, dialog, confirmDlg, stateChip, ago, upload, can, debounce } from './core.js';
import { bomDeviations, lineDeviationChips, mountRecordPanels } from './deviations.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

export async function renderBoms(container, _m, _p, { setTitle }) {
  setTitle('Engineering BOMs');
  const r = await get('/api/boms');
  const q = h('input', { class: 'input', type: 'search', placeholder: 'Filter by part number or description', 'aria-label': 'Filter BOMs', style: { maxWidth: '360px' } });
  const tbody = h('tbody');
  const draw = () => {
    clear(tbody);
    const f = q.value.toLowerCase();
    for (const b of r.boms.filter(b => !f || `${b.part_number} ${b.description}`.toLowerCase().includes(f))) {
      tbody.appendChild(h('tr', { class: 'clickable', onclick: () => { location.hash = `#/bom/${b.id}`; } },
        h('td', { class: 'mono' }, b.part_number), h('td', null, b.description), h('td', null, b.revision),
        h('td', null, h('span', { class: `chip st-${b.state === 'released' ? 'released' : 'draft'}` }, b.state === 'released' ? 'Released' : 'Draft')),
        h('td', { class: 'num' }, b.lines), h('td', null, sourceLabel(b.source)), h('td', { class: 'muted' }, ago(b.updated_at))));
    }
    if (!tbody.children.length) tbody.appendChild(h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, 'No BOMs yet. Open an assembly in the Explorer and choose "BOM", or create one here.'))));
  };
  q.addEventListener('input', draw);
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Engineering BOMs'),
      h('div', { class: 'sub' }, `Built from SolidWorks assemblies or imported from a SolidWorks BOM table, then released and sent to ${S.erp}.`)),
    can('engineer') ? h('button', { class: 'btn primary', onclick: newBomDialog }, icon('plus', 16), 'New BOM') : null),
    h('div', { style: { marginBottom: '12px' } }, q),
    h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Assembly', 'Description', 'Rev', 'State', 'Lines', 'Source', 'Updated'].map(t => h('th', null, t)))), tbody))));
  draw();
}

const sourceLabel = s => ({ solidworks: 'SolidWorks add-in', file_references: 'Assembly file', import: 'Imported table', manual: 'Manual' })[s] || s;

function itemPicker(onPick, placeholder = 'Type a part number or description') {
  const inp = h('input', { class: 'input', placeholder, 'aria-label': 'Find part', autocomplete: 'off' });
  const list = h('div', { style: { maxHeight: '220px', overflow: 'auto', border: '1px solid var(--line)', borderRadius: '6px', marginTop: '6px' } });
  const search = debounce(async () => {
    const r = await get(`/api/items?q=${encodeURIComponent(inp.value.trim())}&limit=30`);
    mount(list, r.items.length ? r.items.map(it => h('button', { class: 'list-row', style: { width: '100%', border: 0, background: 'transparent', textAlign: 'left', cursor: 'pointer' }, onclick: () => onPick(it) },
      h('span', { class: 'mono', style: { minWidth: '140px' } }, it.part_number), h('span', { class: 'ellipsis', style: { flex: 1 } }, it.description), stateChip(it.state)))
      : h('div', { class: 'empty small' }, 'No matching part.'));
  }, 200);
  inp.addEventListener('input', search);
  search();
  return h('div', null, inp, list);
}

function newBomDialog() {
  let picked = null;
  const label = h('div', { class: 'small' }, 'Pick the assembly this BOM belongs to.');
  const pn = h('input', { class: 'input mono', placeholder: 'e.g. MX-200-000', id: 'nb-pn' });
  const ds = h('input', { class: 'input', placeholder: 'e.g. ROTOR ASSEMBLY', id: 'nb-ds' });
  dialog({ title: 'New BOM', body: [label, itemPicker(it => { picked = it; label.textContent = `Selected: ${it.part_number} ${it.description}`; }),
    h('div', { class: 'muted small' }, 'Or make a new assembly part number:'),
    h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'nb-pn' }, 'Part number'), pn), h('div', { class: 'field' }, h('label', { for: 'nb-ds' }, 'Description'), ds))],
  actions: [{ label: 'Cancel' }, { label: 'Create BOM', primary: true, onClick: async () => {
    const body = picked ? { item_id: picked.id } : { part_number: pn.value.trim(), description: ds.value.trim() };
    if (!body.item_id && !body.part_number) { toast('Pick an assembly or type a part number', 'err'); return false; }
    const r = await post('/api/boms', body);
    location.hash = `#/bom/${r.bom_id}`;
  } }] });
}

export async function renderBom(container, [id], _p, { setTitle }) {
  let data, edits = null, view = 'indented';
  const collapsed = new Set();
  const load = async () => { data = await get(`/api/boms/${id}`); data.devs = await bomDeviations(id); edits = null; setTitle(`BOM ${data.bom.part_number}`); draw(); };

  const draw = () => {
    const b = data.bom;
    const draft = b.state !== 'released';
    const eng = can('engineer') && draft;
    const errors = data.issues.filter(i => i.severity === 'error').length;
    const warns = data.issues.filter(i => i.severity === 'warning').length;
    const revSel = h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'BOM revision', onchange: e => { location.hash = `#/bom/${e.target.value}`; } },
      data.revisions.map(r => h('option', { value: r.id, selected: r.id === b.id }, `Revision ${r.revision} (${r.state})`)));
    const head = h('div', { class: 'page-head' },
      h('div', { style: { flex: 1, minWidth: '300px' } }, h('div', { class: 'small muted' }, 'Engineering BOM'),
        h('h1', null, h('a', { href: `#/item/${b.item_id}`, class: 'mono', style: { color: 'inherit', textDecoration: 'none' } }, b.part_number), ' ', h('span', { style: { fontWeight: 400 } }, b.description)),
        h('div', { class: 'row wrap', style: { marginTop: '8px' } }, revSel,
          h('span', { class: `chip st-${draft ? 'draft' : 'released'}` }, draft ? 'Draft — can be edited' : 'Released — locked'),
          h('span', { class: 'muted small' }, `Source: ${sourceLabel(b.source)} · updated ${ago(b.updated_at)}`),
          data.assembly_document ? h('a', { class: 'small', href: `#/doc/${data.assembly_document.id}` }, data.assembly_document.filename) : null)),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } },
        eng && data.assembly_document ? h('button', { class: 'btn', title: 'Rebuild from the SolidWorks assembly', onclick: regenerate }, icon('refresh', 16), 'Rebuild from model') : null,
        eng ? h('button', { class: 'btn', onclick: importDlg }, icon('upload', 16), 'Import SolidWorks BOM') : null,
        h('button', { class: 'btn', onclick: () => compareDlg() }, icon('layers', 16), 'Compare'),
        h('a', { class: 'btn', href: `/api/boms/${b.id}/export?format=xlsx&access_token=${encodeURIComponent(S.token)}` }, icon('download', 16), 'Excel'),
        draft && can('approver') ? h('button', { class: 'btn primary', disabled: errors > 0, title: errors ? 'Fix the errors first' : `Lock this BOM and send it to ${S.erp}`, onclick: release }, icon('release', 16), 'Release BOM') : null,
        !draft && can('approver') ? h('button', { class: 'btn primary', onclick: pushErp }, icon('erp', 16), `Send to ${S.erp}`) : null));

    const lines = edits || data.lines.map(l => ({ ...l }));
    const tableWrap = h('div', { class: 'panel', style: { overflow: 'auto', flex: '1 1 640px', minWidth: 0 } });
    const viewSeg = h('div', { class: 'seg' }, [['indented', 'Indented'], ['flat', 'This level']].map(([k, l]) =>
      h('button', { 'aria-pressed': String(view === k), onclick: () => { view = k; draw(); } }, l)));
    const toolbar = h('div', { class: 'panel-h' }, viewSeg, h('span', { class: 'muted small' }, `${data.lines.length} lines on this level · ${data.tree.length} rows in total`), h('span', { class: 'spacer' }),
      eng ? h('button', { class: 'btn sm', onclick: () => addLineDlg(lines) }, icon('plus', 14), 'Add part') : null,
      edits ? h('button', { class: 'btn sm', onclick: () => { edits = null; draw(); } }, 'Discard changes') : null,
      edits ? h('button', { class: 'btn sm primary', onclick: () => save(lines) }, icon('check', 14), 'Save changes') : null);
    tableWrap.appendChild(toolbar);

    const issuesBy = {};
    for (const i of data.issues) (issuesBy[i.part_number] = issuesBy[i.part_number] || []).push(i);
    const cols = ['', 'Find', 'Part number', 'Description', 'Qty', view === 'indented' ? 'Total' : null, 'Unit', 'Material', 'Make/Buy', 'Rev', 'State', 'ERP', ''].filter(c => c !== null);
    const tbody = h('tbody');
    if (view === 'flat' || edits) {
      lines.forEach((l, i) => tbody.appendChild(lineRow(l, 1, i, lines, eng, issuesBy, false)));
    } else {
      let hideBelow = null;
      for (const r of data.tree) {
        if (hideBelow !== null && r.level > hideBelow) continue;
        hideBelow = null;
        const key = r.path;
        if (collapsed.has(key)) hideBelow = r.level;
        tbody.appendChild(lineRow(r, r.level, data.lines.findIndex(l => l.id === r.id), lines, eng && r.level === 1, issuesBy, true, key));
      }
    }
    if (!tbody.children.length) tbody.appendChild(h('tr', null, h('td', { colspan: cols.length }, h('div', { class: 'empty' },
      'This BOM is empty. ', eng ? 'Rebuild it from the model, import the BOM table exported from SolidWorks, or add parts one by one.' : ''))));
    tableWrap.appendChild(h('table', { class: 'tbl bom-grid' }, h('thead', null, h('tr', null, cols.map(c => h('th', null, c)))), tbody));

    const side = h('aside', { class: 'panel', style: { flex: '0 1 340px', minWidth: '280px', alignSelf: 'flex-start' } },
      h('div', { class: 'panel-h' }, h('h2', null, 'Checks'), h('span', { class: 'spacer' }),
        errors ? h('span', { class: 'chip st-failed' }, `${errors} error${errors > 1 ? 's' : ''}`) : null,
        warns ? h('span', { class: 'chip', style: { background: 'var(--warn-bg)', color: 'var(--warn)' } }, `${warns} warning${warns > 1 ? 's' : ''}`) : null),
      h('div', { class: 'panel-b' }, data.issues.length ? data.issues.map(i => h('div', { class: 'issue' },
        h('span', { class: `sev-${i.severity}` }, icon(i.severity === 'info' ? 'info' : 'warn', 16)),
        h('div', null, i.part_number ? h('b', { class: 'mono' }, i.part_number, ' ') : null, i.message)))
        : h('div', { class: 'banner ok small' }, icon('check'), h('div', null, 'No problems found. This BOM is ready to release.'))),
      data.erp_jobs.length ? h('div', { class: 'panel-b', style: { borderTop: '1px solid var(--line-2)' } }, h('h3', { style: { marginBottom: '6px' } }, `${S.erp}`),
        data.erp_jobs.map(j => h('div', { class: 'small row wrap', style: { marginBottom: '4px' } }, h('span', { class: `chip st-${j.status}` }, j.status), ago(j.updated_at), j.error ? h('span', { class: 'sev-error' }, j.error) : null))) : null);

    mount(container, h('div', { class: 'page-pad' }, head,
      S.mode === 'guided' && draft ? h('div', { class: 'banner info', style: { marginBottom: '14px' } }, icon('info'), h('div', null,
        'Check the quantities, fix anything listed under ', h('b', null, 'Checks'), ', then an approver presses ', h('b', null, 'Release BOM'),
        `. Released BOMs are sent to ${S.erp} automatically. Sub-assemblies show their own parts indented below them.`)) : null,
      h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '16px' } }, tableWrap, side)));
    mountRecordPanels(container.firstChild, 'bom', id);
  };

  const lineRow = (r, level, idx, lines, editable, issuesBy, indented, key) => {
    const tw = indented && r.has_children ? h('button', { class: 'tw', 'aria-label': 'Expand or collapse', onclick: () => { collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key); draw(); } },
      icon(collapsed.has(key) ? 'chevR' : 'chevD', 14)) : h('span', { style: { display: 'inline-block', width: '18px' } });
    const qty = editable && idx >= 0
      ? h('input', { class: 'qty', type: 'number', min: '0', step: 'any', value: lines[idx].qty, 'aria-label': `Quantity of ${r.part_number}`,
        oninput: e => { if (!edits) edits = lines; edits[idx].qty = parseFloat(e.target.value) || 0; markDirty(); } })
      : h('span', null, r.qty);
    const iss = issuesBy[r.part_number] || [];
    return h('tr', null,
      h('td', { class: 'lvl', style: { paddingLeft: `${8 + (level - 1) * 18}px`, whiteSpace: 'nowrap' } }, tw, level > 1 ? `L${level}` : ''),
      h('td', { class: 'mono' }, r.find_number),
      h('td', { class: 'mono nowrap' }, h('a', { href: `#/item/${r.item_id}` }, r.part_number), r.cycle ? ' ⟲' : '', lineDeviationChips(data.devs, r.item_id)),
      h('td', null, r.description, r.standard_item_id ? h('span', { class: 'chip', style: { marginLeft: '6px', background: '#EEF3FB', color: '#1E3A8A' } }, 'has standard') : null),
      h('td', { class: 'num' }, qty),
      indented && !edits ? h('td', { class: 'num muted' }, r.ext_qty) : null,
      h('td', null, r.uom || r.item_uom),
      h('td', null, r.material || h('span', { class: 'muted' }, '—')),
      h('td', null, r.make_buy),
      h('td', null, r.revision),
      h('td', null, stateChip(r.state)),
      h('td', null, r.erp_synced_at ? h('span', { title: `In ${S.erp}`, style: { color: 'var(--ok)' } }, icon('check', 16)) : h('span', { class: 'muted', title: `Not in ${S.erp} yet` }, '—')),
      h('td', { class: 'nowrap' }, iss.length ? h('span', { class: `sev-${iss[0].severity}`, title: iss.map(i => i.message).join('\n') }, icon('warn', 15)) : null,
        editable && idx >= 0 ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${r.part_number}`, onclick: () => { if (!edits) edits = lines; edits.splice(idx, 1); draw(); } }, icon('trash', 14)) : null));
  };

  const markDirty = () => {
    const bar = container.querySelector('.panel-h');
    if (bar && !bar.querySelector('.dirty')) {
      bar.append(h('button', { class: 'btn sm dirty', onclick: () => { edits = null; draw(); } }, 'Discard'),
        h('button', { class: 'btn sm primary dirty', onclick: () => save(edits) }, icon('check', 14), 'Save changes'));
    }
  };

  const save = async lines => {
    try {
      data = await put(`/api/boms/${id}/lines`, { lines: lines.map(l => ({ item_id: l.item_id, part_number: l.part_number, description: l.description, qty: l.qty, uom: l.uom, find_number: l.find_number })) });
      edits = null; toast('BOM saved', 'ok'); draw();
    } catch (e) { fail(e); }
  };

  const addLineDlg = lines => {
    const qty = h('input', { class: 'input', type: 'number', value: 1, min: 0, step: 'any', id: 'al-q' });
    let picked = null;
    const info = h('div', { class: 'small' }, 'Choose a part, or type a new part number below.');
    const pn = h('input', { class: 'input mono', id: 'al-pn', placeholder: 'New part number' });
    const ds = h('input', { class: 'input', id: 'al-ds', placeholder: 'Description' });
    dialog({ title: 'Add a part to the BOM', body: [info, itemPicker(it => { picked = it; info.textContent = `Selected: ${it.part_number} ${it.description}`; }),
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'al-pn' }, 'New part number'), pn), h('div', { class: 'field' }, h('label', { for: 'al-ds' }, 'Description'), ds),
        h('div', { class: 'field' }, h('label', { for: 'al-q' }, 'Quantity'), qty))],
    actions: [{ label: 'Cancel' }, { label: 'Add', primary: true, onClick: async () => {
      const next = (edits || lines).slice();
      if (picked) next.push({ item_id: picked.id, part_number: picked.part_number, description: picked.description, qty: parseFloat(qty.value) || 1 });
      else if (pn.value.trim()) next.push({ part_number: pn.value.trim().toUpperCase(), description: ds.value.trim(), qty: parseFloat(qty.value) || 1 });
      else { toast('Choose or type a part', 'err'); return false; }
      await save(next);
    } }] });
  };

  const regenerate = async () => {
    if (!await confirmDlg('Rebuild from model', 'Replace the lines of this draft BOM with the components of the SolidWorks assembly?', 'Rebuild')) return;
    try {
      const r = await post('/api/boms/generate', { document_id: data.assembly_document.id });
      toast(`${r.lines} lines built from the ${r.source === 'solidworks' ? 'SolidWorks add-in data' : 'assembly file references (check quantities)'}.`, 'ok');
      if (r.unresolved.length) toast(`Could not identify: ${r.unresolved.join(', ')}`, 'err');
      if (r.bom_id !== Number(id)) location.hash = `#/bom/${r.bom_id}`; else load();
    } catch (e) { fail(e); }
  };

  const importDlg = () => {
    const file = h('input', { class: 'input', type: 'file', accept: '.xlsx,.csv', id: 'imp-f' });
    dialog({ title: 'Import a SolidWorks BOM', body: [
      h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'In SolidWorks, right-click the BOM table › Save As › Excel (.xlsx) or CSV. Include the ITEM NO., PART NUMBER, DESCRIPTION and QTY columns. Indented item numbers (1, 1.1, 1.2…) create sub-assembly BOMs automatically.')),
      h('div', { class: 'field' }, h('label', { for: 'imp-f' }, 'BOM file'), file)],
    actions: [{ label: 'Cancel' }, { label: 'Import', primary: true, onClick: async () => {
      if (!file.files[0]) { toast('Choose a file', 'err'); return false; }
      const fd = new FormData(); fd.append('file', file.files[0]);
      const r = await upload(`/api/boms/${id}/import`, fd);
      toast(`Imported ${r.rows} rows: ${r.lines} lines on this level${r.sub_assemblies.length ? `, ${r.sub_assemblies.length} sub-assembly BOMs` : ''}.`, 'ok');
      if (r.skipped_released.length) toast(`Released sub-assembly BOMs were not changed: ${r.skipped_released.join(', ')}`, 'err');
      load();
    } }] });
  };

  const compareDlg = () => {
    const others = data.revisions.filter(r => r.id !== data.bom.id);
    if (!others.length) { toast('There is only one revision of this BOM.', ''); return; }
    const sel = h('select', { class: 'input', id: 'cmp' }, others.map(r => h('option', { value: r.id }, `Revision ${r.revision}`)));
    const out = h('div');
    const run = async () => {
      const c = await get(`/api/boms/${data.bom.id}/compare?with=${sel.value}`);
      const sec = (title, rows, fn) => rows.length ? h('div', null, h('h3', { style: { margin: '8px 0 4px' } }, `${title} (${rows.length})`), rows.map(fn)) : null;
      mount(out, (c.added.length || c.removed.length || c.changed.length) ? [
        sec('Added', c.added, r => h('div', { class: 'small', style: { color: 'var(--ok)' } }, `+ ${r.part_number} ${r.description} × ${r.qty}`)),
        sec('Removed', c.removed, r => h('div', { class: 'small', style: { color: 'var(--err)' } }, `− ${r.part_number} ${r.description} × ${r.qty}`)),
        sec('Quantity changed', c.changed, r => h('div', { class: 'small' }, `${r.part_number} ${r.description}: ${r.from_qty} → ${r.to_qty}`))]
        : h('p', { class: 'muted' }, 'No differences on this level.'));
    };
    sel.addEventListener('change', run);
    dialog({ title: `Compare with another revision`, body: [h('div', { class: 'field' }, h('label', { for: 'cmp' }, 'Compare this BOM with'), sel), out], actions: [{ label: 'Close' }] });
    run();
  };

  const release = async () => {
    if (!await confirmDlg('Release BOM', `Release ${data.bom.part_number} revision ${data.bom.revision}? It will be locked and sent to ${S.erp}.`, 'Release')) return;
    try { await post(`/api/boms/${id}/release`); toast(`BOM released and queued for ${S.erp}`, 'ok'); changed(); load(); } catch (e) { fail(e); }
  };
  const pushErp = async () => {
    try {
      const r = await post(`/api/boms/${id}/push`);
      const j = r.job;
      toast(j.status === 'failed' ? `${S.erp}: ${j.error}` : `${S.erp}: ${j.response_text}`, j.status === 'failed' ? 'err' : 'ok');
      changed(); load();
    } catch (e) { fail(e); }
  };
  await load();
}
