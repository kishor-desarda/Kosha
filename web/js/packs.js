// SPDX-License-Identifier: AGPL-3.0-or-later
// Admin › Packs: install, update and remove industry packs, see what each contains, and keep the company's own changes.
import { h, mount, icon, get, post, put, toast, fail, dialog, confirmDlg, tabs, ago } from './core.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const KEY_FIELD = { family: 'code', category: 'code', item_type: 'code', unit: 'code', field: 'key', dictionary: 'code', spec_template: 'id',
  numbering_scheme: 'id', title_block: 'id', library_table: 'id', library_row: 'key' };
const STATUS = {
  pack: ['From the pack', 'st-draft', 'As the pack installed it.'],
  changed: ['Changed here', 'st-submitted', 'Your company changed this. Pack updates keep your version.'],
  conflict: ['Pack changed it too', 'out', 'The pack has a newer version of this record. Your version is still used; compare and choose.'],
  hidden: ['Switched off', 'st-cancelled', 'Your company switched this off. Pack updates keep it off.'],
  other_pack: ['From another pack', 'st-draft', 'Another installed pack also has this record; the one installed first is used.'],
  not_installed: ['Not installed', 'st-cancelled', 'Install the pack to use this.'],
  local: ['Added here', 'st-submitted', 'Your company added this. Packs never change it.'],
};
const ONE = { family: 'product family', category: 'category', item_type: 'item type', unit: 'unit', field: 'item field', dictionary: 'dictionary entry',
  spec_template: 'spec-code template', numbering_scheme: 'number scheme', title_block: 'title-block map', library_table: 'library table',
  library_row: 'library row' };
const LABELS = { name: 'Name', label: 'Label', hint: 'Help text', code: 'Code template', pattern: 'Number pattern', description: 'Description',
  standard: 'Standard', edition: 'Edition of the standard', make_buy: 'New parts are', base: 'Kosha treats it as', uom: 'Unit',
  category: 'Category', file: 'File', key: 'Row key', designation: 'Designation', part_number: 'Part number', size: 'Size column',
  type: 'Type', unit: 'Unit', uqc: 'GST unit code (UQC)', item_type: 'Item type', creates_item: 'Creates a part',
  examples: 'Examples', erp_item_group: 'ERP item group', hsn: 'HSN code (GST)', erp: 'Sent to the ERP' };
const PACK_STATUS = { installed: ['Installed', 'st-released'], not_installed: ['Not installed', 'st-draft'], removed: ['Removed', 'st-cancelled'] };

function chip([label, cls, title]) {
  return h('span', { class: `chip ${cls}`, title: title || '' }, label);
}

function countChips(counts, kinds) {
  return Object.entries(counts || {}).map(([k, n]) => h('span', { class: 'chip st-draft', style: { marginRight: '4px', marginBottom: '3px' } },
    `${n} ${n === 1 ? ONE[k] || k : (kinds[k] || k).toLowerCase()}`));
}

export async function packsTab(body) {
  const draw = async () => {
    const [r, ch] = await Promise.all([get('/api/packs'), get('/api/pack-records/changes')]);
    const kinds = r.kinds;
    const rows = r.packs.map(p => {
      const st = PACK_STATUS[p.status] || [p.status, 'st-draft'];
      const canInstall = !p.problem && p.available_version;
      return h('tr', null,
        h('td', { style: { padding: '8px 10px', verticalAlign: 'top' } }, h('b', null, p.name),
          h('div', { class: 'small muted', style: { maxWidth: '520px', lineHeight: 1.4 } }, p.description),
          h('div', { style: { marginTop: '6px' } }, countChips(p.counts, kinds)),
          p.problem ? h('div', { class: 'small sev-error', style: { maxWidth: '520px' } }, p.problem) : null),
        h('td', { class: 'mono small', style: { verticalAlign: 'top', paddingTop: '10px' } },
          p.installed_version || '—', p.update_available ? h('div', { class: 'sev-info' }, `${p.available_version} available`) : null),
        h('td', { style: { verticalAlign: 'top', paddingTop: '8px' } }, chip([...st, p.installed_at ? `Since ${ago(p.installed_at)}` : '']),
          p.local_changes ? h('div', { class: 'small muted', style: { marginTop: '4px' } }, `${p.local_changes} changed here`) : null),
        h('td', { style: { verticalAlign: 'top', paddingTop: '6px' } }, h('div', { class: 'row', style: { gap: '6px', flexWrap: 'nowrap' } },
          p.status !== 'installed' ? h('button', { class: 'btn sm primary', disabled: !canInstall, title: canInstall ? `Install ${p.name}` : `Cannot install: ${p.problem}`, onclick: () => installDlg(p, draw) }, 'Install') : null,
          p.status === 'installed' && p.update_available ? h('button', { class: 'btn sm primary', onclick: () => installDlg(p, draw) }, `Update to ${p.available_version}`) : null,
          h('button', { class: 'btn sm', disabled: !p.available_version && p.status !== 'installed', title: 'What this pack contains', onclick: () => contentsDlg(p.id, draw) }, 'Contents'),
          p.status === 'installed' ? h('button', { class: 'btn sm ghost', onclick: () => removeDlg(p, draw) }, 'Remove') : null)));
    });
    mount(body,
      h('div', { class: 'banner info small', style: { marginBottom: '14px' } }, icon('info'), h('div', null,
        'Packs give Kosha ready-made categories, materials, spec codes, number schemes and standard parts. ',
        'Updating a pack keeps every change your company made; removing it switches its data off but never deletes parts already made from it.')),
      h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Pack', 'Version', 'Status', ''].map(t => h('th', null, t)))),
        h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: 4 }, h('div', { class: 'empty' }, `No packs found in ${r.folder}.`)))))),
      changesPanel(ch.changes, kinds, draw));
  };
  await draw();
}

function changesPanel(changes, kinds, draw) {
  const add = (kind, label) => h('button', { class: 'btn sm', onclick: () => addDlg(kind, draw) }, icon('plus', 14), label);
  return h('section', { style: { marginTop: '22px' } },
    h('div', { class: 'row wrap', style: { marginBottom: '8px' } }, h('h2', null, 'Your changes and additions'), h('span', { class: 'spacer' }),
      add('family', 'Product family'), add('category', 'Category'), add('dictionary', 'Dictionary entry'), add('unit', 'Unit'), add('item_type', 'Item type')),
    changes.length ? h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['What', 'Record', 'Status', 'Changed', ''].map(t => h('th', null, t)))),
      h('tbody', null, changes.map(c => {
        const status = c.hidden ? 'hidden' : c.conflict ? 'conflict' : c.origin === 'local' ? 'local' : 'changed';
        const v = c.value || c.pack_value || {};
        return h('tr', null, h('td', { class: 'small' }, kinds[c.kind] || c.kind),
          h('td', null, h('span', { class: 'mono' }, c.key), v.name || v.label ? h('span', { class: 'muted' }, ` ${v.name || v.label}`) : null),
          h('td', null, chip(STATUS[status])), h('td', { class: 'small muted' }, ago(c.updated_at)),
          h('td', { class: 'nowrap' }, recordActions({ kind: c.kind, key: c.key, status, value: c.value, pack_value: c.pack_value }, draw)));
      })))
      : h('div', { class: 'empty small' }, 'No changes yet. Everything is as the packs installed it.'));
}

function recordActions(rec, after) {
  const act = (label, path, msg, cls = 'btn sm ghost') => h('button', { class: cls, onclick: async () => {
    try { await post(`/api/pack-records/${path}`, { kind: rec.kind, key: rec.key }); toast(msg, 'ok'); changed(); after(); } catch (e) { fail(e); }
  } }, label);
  const out = [];
  if (['pack', 'changed', 'conflict', 'other_pack', 'local'].includes(rec.status)) {
    out.push(h('button', { class: 'btn sm', onclick: () => editDlg(rec, after) }, icon('edit', 14), 'Edit'));
  }
  if (rec.status === 'conflict') out.push(h('button', { class: 'btn sm ghost', onclick: () => compareDlg(rec, after) }, 'Compare'));
  if (rec.status === 'changed' || rec.status === 'conflict') out.push(act('Use pack version', 'revert', 'The pack version is used again'));
  if (rec.status === 'conflict') out.push(act('Keep mine', 'keep', 'Your version is kept'));
  if (rec.status === 'pack') out.push(act('Switch off', 'hide', 'Switched off'));
  if (rec.status === 'hidden') out.push(act('Switch on', 'revert', 'Switched on again'));
  if (rec.status === 'local') {
    out.push(h('button', { class: 'btn sm ghost', onclick: async () => {
      if (!await confirmDlg('Remove your record?', `Remove ${rec.key}? Parts that already use it keep their values.`, 'Remove')) return;
      try { await post('/api/pack-records/revert', { kind: rec.kind, key: rec.key }); toast('Removed', 'ok'); changed(); after(); } catch (e) { fail(e); }
    } }, 'Remove'));
  }
  return out;
}

async function installDlg(p, draw) {
  let plan;
  try { plan = await get(`/api/packs/${encodeURIComponent(p.id)}/plan`); } catch (e) { fail(e); return; }
  const c = plan.counts;
  const verb = plan.action === 'update' ? 'Update' : 'Install';
  const list = (title, rows) => rows.length ? h('details', null, h('summary', null, `${title} (${rows.length})`),
    h('ul', { class: 'small', style: { maxHeight: '180px', overflow: 'auto', margin: '6px 0' } }, rows.map(r => h('li', null, h('span', { class: 'muted' }, `${(plan.kinds || {})[r.kind] || r.kind.replace(/_/g, ' ')}: `), r.label)))) : null;
  dialog({
    title: `${verb} ${plan.pack.name} ${plan.to_version}?`, wide: true,
    body: h('div', { class: 'col', style: { gap: '10px' } },
      h('p', { style: { margin: 0, lineHeight: 1.5 } }, plan.pack.description),
      plan.action === 'none' ? h('div', { class: 'banner ok small' }, icon('check'), h('div', null, 'This version is already installed. Nothing would change.')) : null,
      h('div', { class: 'kpis' }, [['added', 'New'], ['changed', 'Changed by the pack'], ['removed', 'No longer in the pack'], ['kept_local', 'Your changes kept']]
        .map(([k, l]) => h('div', { class: 'kpi' }, h('b', null, c[k]), h('span', null, l)))),
      c.kept_local ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, `${c.kept_local} records you changed are also changed by this version. Your versions stay in use; you can compare them afterwards under "Your changes".`)) : null,
      list('New', plan.added), list('Changed by the pack', plan.changed), list('No longer in the pack (switched off unless you changed them)', plan.removed)),
    actions: [{ label: 'Cancel' }, { label: verb, primary: true, onClick: async () => {
      try { const r = await post(`/api/packs/${encodeURIComponent(p.id)}/install`); toast(r.message, 'ok'); changed(); draw(); } catch (e) { fail(e); return false; }
    } }],
  });
}

async function removeDlg(p, draw) {
  const ok = await confirmDlg(`Remove ${p.name}?`,
    `Its categories, templates, number schemes and library data are switched off. Parts already made from it stay, with their part numbers, and so do your own changes (${p.local_changes}). You can install it again at any time.`,
    'Remove pack', true);
  if (!ok) return;
  try { const r = await post(`/api/packs/${encodeURIComponent(p.id)}/remove`); toast(r.message, 'ok'); changed(); draw(); } catch (e) { fail(e); }
}

async function contentsDlg(id, draw) {
  let d;
  try { d = await get(`/api/packs/${encodeURIComponent(id)}`); } catch (e) { fail(e); return; }
  const kindsPresent = Object.keys(d.kinds).filter(k => d.records.some(r => r.kind === k));
  let tab = kindsPresent[0];
  const box = h('div');
  const reload = async () => { d = await get(`/api/packs/${encodeURIComponent(id)}`); show(); draw(); };
  const show = () => {
    const recs = d.records.filter(r => r.kind === tab);
    mount(box, tabs(kindsPresent.map(k => [k, `${d.kinds[k]} (${d.records.filter(r => r.kind === k).length})`]), tab, k => { tab = k; show(); }),
      h('div', { style: { maxHeight: '55vh', overflow: 'auto' } }, h('table', { class: 'tbl' }, h('tbody', null, recs.map(r => h('tr', null,
        h('td', { style: { padding: '6px 10px' } }, h('span', { class: 'mono' }, r.key), h('div', { class: 'small muted' }, summary(r.kind, r.effective || r.value))),
        h('td', { style: { padding: '6px 10px' } }, chip(STATUS[r.status] || [r.status, 'st-draft']), r.status === 'other_pack' && r.provided_by ? h('div', { class: 'small muted' }, r.provided_by) : null),
        h('td', { class: 'nowrap', style: { padding: '6px 10px' } }, recordActions({ kind: r.kind, key: r.key, status: r.status, value: r.effective || r.value, pack_value: r.value }, reload))))))));
  };
  dialog({ title: `${d.name} ${d.version}`, wide: true, body: [
    d.problem ? h('div', { class: 'banner err small' }, icon('warn'), h('div', null, d.problem)) : null,
    d.history && d.history.length ? h('div', { class: 'small muted' }, `Last ${d.history[0].action} ${ago(d.history[0].at)}.`) : null,
    kindsPresent.length ? box : h('div', { class: 'empty' }, 'This pack is empty.')], actions: [{ label: 'Close' }] });
  if (kindsPresent.length) show();
}

function summary(kind, v) {
  if (!v) return '';
  if (kind === 'spec_template') return `${v.label} · ${v.code}`;
  if (kind === 'numbering_scheme') return `${v.label} · ${v.pattern}`;
  if (kind === 'library_table') return `${v.standard} ${v.label}`;
  if (kind === 'library_row') return `${Object.entries(v.values || {}).map(([k, x]) => `${k} ${x}`).join(' · ')} · ${v.standard}${v.edition ? ':' + v.edition : ''} · ${v.verified ? 'checked' : 'not checked'}`;
  if (kind === 'title_block') return `${v.label}: ${Object.keys(v.map || {}).join(', ')}`;
  if (kind === 'field') return `${v.label} (${v.type}${v.unit ? ', ' + v.unit : ''})`;
  return [v.name, v.make_buy ? `${v.make_buy === 'buy' ? 'bought out' : 'made'}` : '', v.base ? `treated as ${v.base}` : '', v.uqc ? `UQC ${v.uqc}` : ''].filter(Boolean).join(' · ');
}

/** Edit a record's plain values (names, labels, templates, flags). Nested settings are shown but changed in the pack file. */
function editDlg(rec, after) {
  const v = JSON.parse(JSON.stringify(rec.value || rec.pack_value || {}));
  const keyField = KEY_FIELD[rec.kind];
  const setters = [];
  const control = (obj, k, label, idSuffix) => {
    const id = `pe-${idSuffix}`;
    const x = obj[k];
    if (k === 'make_buy' || k === 'base') {
      const opts = k === 'make_buy' ? [['', 'Not set'], ['make', 'Made in house'], ['buy', 'Bought out']] : [['part', 'Part'], ['assembly', 'Assembly (has a BOM)']];
      const sel = h('select', { class: 'input', id }, opts.map(([val, l]) => h('option', { value: val, selected: (x || '') === val }, l)));
      setters.push(() => { if (sel.value) obj[k] = sel.value; else delete obj[k]; });
      return h('div', { class: 'field' }, h('label', { for: id }, label), sel);
    }
    if (typeof x === 'boolean') {
      const cb = h('input', { type: 'checkbox', id, checked: x });
      setters.push(() => { obj[k] = cb.checked; });
      return h('label', { class: 'check', style: { gridColumn: '1 / -1' } }, cb, label);
    }
    const inp = h('input', { class: 'input', id, value: x ?? '', inputmode: typeof x === 'number' ? 'decimal' : null });
    setters.push(() => { obj[k] = typeof x === 'number' && inp.value.trim() !== '' && !isNaN(Number(inp.value)) ? Number(inp.value) : inp.value; });
    return h('div', { class: 'field' }, h('label', { for: id }, label), inp);
  };
  const plain = [];
  const nested = {};
  if (rec.kind === 'category' && !('make_buy' in v)) v.make_buy = '';
  for (const [k, x] of Object.entries(v)) {
    if (k === keyField) continue;
    if (rec.kind === 'library_row' && k === 'values') {
      for (const col of Object.keys(x)) plain.push(control(x, col, col, `v-${col}`));
    } else if (rec.kind === 'library_row' && k === 'verified') {
      plain.push(control(v, k, 'Checked against the standard (name the edition above)', k));
    } else if (x !== null && typeof x === 'object') {
      nested[k] = x;
    } else {
      plain.push(control(v, k, LABELS[k] || k.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()), k));
    }
  }
  if (rec.kind === 'category' && v.make_buy === '') delete v.make_buy;
  dialog({
    title: `Change ${rec.key}`, wide: true,
    body: [
      h('div', { class: 'small muted' }, `${keyField}: `, h('b', { class: 'mono' }, v[keyField] ?? rec.key), ' (cannot be changed; add a new record for a different code)'),
      h('div', { class: 'form-grid' }, plain),
      Object.keys(nested).length ? h('details', null, h('summary', { class: 'small' }, 'Other settings (change these in the pack file)'),
        h('pre', { class: 'small mono', style: { whiteSpace: 'pre-wrap', background: 'var(--panel-2)', padding: '8px', borderRadius: '6px' } }, JSON.stringify(nested, null, 2))) : null,
      h('div', { class: 'small muted' }, 'Your version is kept when the pack is updated. "Use pack version" undoes it.')],
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      setters.forEach(s => s());
      try { await put('/api/pack-records', { kind: rec.kind, key: rec.key, value: v }); toast('Saved', 'ok'); changed(); after(); } catch (e) { fail(e); return false; }
    } }],
  });
}

function compareDlg(rec, after) {
  const pre = x => h('pre', { class: 'small mono', style: { whiteSpace: 'pre-wrap', background: 'var(--panel-2)', padding: '8px', borderRadius: '6px', margin: 0 } }, JSON.stringify(x, null, 2));
  dialog({ title: `${rec.key}: yours and the pack's`, wide: true,
    body: h('div', { class: 'row wrap', style: { alignItems: 'flex-start' } },
      h('div', { style: { flex: '1 1 300px' } }, h('h3', null, 'Your version (in use)'), pre(rec.value)),
      h('div', { style: { flex: '1 1 300px' } }, h('h3', null, 'The pack\'s new version'), pre(rec.pack_value))),
    actions: [{ label: 'Close' },
      { label: 'Use pack version', onClick: async () => { await post('/api/pack-records/revert', { kind: rec.kind, key: rec.key }); toast('The pack version is used again', 'ok'); after(); } },
      { label: 'Keep mine', primary: true, onClick: async () => { await post('/api/pack-records/keep', { kind: rec.kind, key: rec.key }); toast('Your version is kept', 'ok'); after(); } }] });
}

async function addDlg(kind, draw) {
  const titles = { family: 'Add a product family', category: 'Add a category', dictionary: 'Add a dictionary entry', unit: 'Add a unit', item_type: 'Add an item type' };
  const inputs = {};
  const field = (name, label, el, hint) => { inputs[name] = el; el.id = `pa-${name}`; return h('div', { class: 'field' }, h('label', { for: el.id }, label), el, hint ? h('span', { class: 'hint' }, hint) : null); };
  const parts = [];
  if (kind === 'dictionary') {
    let names = [];
    try { names = [...new Set((await get('/api/pack-records?kind=dictionary')).records.map(r => r.scope))]; } catch (e) { fail(e); }
    const dl = h('datalist', { id: 'pa-dicts' }, names.map(n => h('option', { value: n })));
    parts.push(field('dictionary', 'Dictionary', h('input', { class: 'input mono', list: 'pa-dicts', value: names.includes('materials') ? 'materials' : '' }), 'For example materials. Small letters and _.'), dl);
  }
  parts.push(field('code', 'Code', h('input', { class: 'input mono' }), kind === 'family' ? 'Two capital letters, used in machine model and serial numbers, for example PU.' : kind === 'category' ? '1 to 6 capital letters or digits, for example GER.' : kind === 'item_type' ? 'Small letters and dashes, for example weldment.' : 'Used in part numbers and lists.'));
  parts.push(field('name', 'Name', h('input', { class: 'input' })));
  if (kind === 'category') parts.push(field('make_buy', 'New parts are', h('select', { class: 'input' }, [['', 'Decided per part'], ['make', 'Made in house'], ['buy', 'Bought out']].map(([v, l]) => h('option', { value: v }, l)))));
  if (kind === 'item_type') parts.push(field('base', 'Kosha treats it as', h('select', { class: 'input' }, [['part', 'A part'], ['assembly', 'An assembly with a BOM']].map(([v, l]) => h('option', { value: v }, l)))));
  dialog({ title: titles[kind], body: h('div', { class: 'col', style: { gap: '12px' } }, parts),
    actions: [{ label: 'Cancel' }, { label: 'Add', primary: true, onClick: async () => {
      const code = inputs.code.value.trim();
      const value = { code, name: inputs.name.value.trim() };
      if (kind === 'category' && inputs.make_buy.value) value.make_buy = inputs.make_buy.value;
      if (kind === 'item_type') value.base = inputs.base.value;
      const key = kind === 'dictionary' ? `${inputs.dictionary.value.trim()}/${code}` : code;
      try { await put('/api/pack-records', { kind, key, value }); toast('Added', 'ok'); changed(); draw(); } catch (e) { fail(e); return false; }
    } }] });
}
