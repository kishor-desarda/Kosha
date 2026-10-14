// SPDX-License-Identifier: AGPL-3.0-or-later
// Quality (#/quality): nonconformance reports (#/ncr/<id>), corrective and preventive actions (#/capa/<id>) and
// FMEA (#/fmea/<id>). Who may do what is decided by the server; every disabled button says why.
import { S, h, mount, icon, api, get, post, put, upload, toast, fail, dialog, confirmDlg, ago, fmtDate, fmtSize, debounce, tabs } from './core.js';
import { discussionPanel, activeUsers } from './discussions.js';
import { itemPicker } from './bom.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const routeOf = () => location.hash.split('?')[0];
const day = iso => (iso ? fmtDate(`${iso}T12:00:00`, false) : '—');
const qty = q => (q === null || q === undefined || q === '' ? '' : String(+Number(q).toFixed(3)));
const NCR_CHIP = { open: 'st-open', dispositioned: 'st-submitted', closed: 'st-done', cancelled: 'st-cancelled' };
const CAPA_CHIP = { open: 'st-open', actions: 'st-in_work', verifying: 'st-submitted', closed: 'st-done', cancelled: 'st-cancelled' };
const chip = (map, state, label) => h('span', { class: `chip ${map[state] || ''}` }, label || state);
const field = (label, el, hint) => { el.id = el.id || `q-${Math.random().toString(36).slice(2, 8)}`; return h('div', { class: 'field' }, h('label', { for: el.id }, label), el, hint ? h('span', { class: 'hint' }, hint) : null); };
const text = (value = '', placeholder = '') => { const t = h('textarea', { class: 'input', placeholder }); t.value = value || ''; return t; };
const select = (choices, value) => h('select', { class: 'input' }, Object.entries(choices).map(([k, l]) => h('option', { value: k, selected: k === value }, l)));
let meta = null;
const quality = async () => { meta = await get('/api/quality'); return meta; };

function stepper(steps, idx, done) {
  const el = h('div', { class: 'stepper' });
  steps.forEach((l, i) => {
    if (i) el.appendChild(h('span', { class: 'line' }));
    const cls = done || i < idx ? 'done' : i === idx ? 'cur' : '';
    el.appendChild(h('span', { class: `s ${cls}` }, h('span', { class: 'dot' }, cls === 'done' ? '✓' : i + 1), l));
  });
  return el;
}

// a button that is either live or (in guided mode) disabled with the reason
function btn(label, ic, ok, why, run, cls = '') {
  if (ok) return h('button', { class: `btn ${cls}`, onclick: run }, icon(ic, 16), label);
  return S.mode === 'guided' ? h('button', { class: `btn ${cls}`, disabled: true, title: why }, icon(ic, 16), label) : null;
}

function partField(initial) {
  let picked = initial || null;
  const shown = h('div', { class: 'small' });
  const draw = () => mount(shown, picked ? h('div', { class: 'row' }, icon('cube', 14), h('b', null, picked.part_number), h('span', { class: 'muted ellipsis' }, picked.description || ''),
    h('button', { class: 'btn ghost sm', type: 'button', 'aria-label': 'Clear the part', onclick: () => { picked = null; draw(); } }, icon('x', 14)))
    : h('span', { class: 'muted' }, 'No part chosen.'));
  draw();
  return { el: h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Part or assembly'), shown, itemPicker(it => { picked = it; draw(); })), id: () => (picked ? picked.id : null) };
}

async function userSelect(value, emptyLabel) {
  const us = await activeUsers();
  return h('select', { class: 'input' }, emptyLabel ? h('option', { value: '' }, emptyLabel) : null,
    us.map(u => h('option', { value: u.id, selected: u.id === value }, u.full_name || u.username)));
}

function attachButton(path, after) {
  const inp = h('input', { type: 'file', multiple: true, hidden: true });
  inp.addEventListener('change', async () => {
    if (!inp.files.length) return;
    const fd = new FormData();
    for (const f of inp.files) fd.append('file', f);
    try { after(await upload(path, fd)); toast(`${inp.files.length} file${inp.files.length > 1 ? 's' : ''} added`, 'ok'); } catch (e) { fail(e); }
  });
  document.body.appendChild(inp);
  inp.click();
  setTimeout(() => inp.remove(), 60000);
}

function attachmentsPanel(kind, rec, canAttach, send, reload) {
  const base = `/api/${kind}s/${rec.id}/attachments`;
  return h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Photos and files (${rec.attachments.length})`), h('span', { class: 'spacer' }),
    btn('Add photos or files', 'upload', canAttach, rec.state === 'closed' || rec.state === 'cancelled' ? 'Closed and cancelled records are kept as they are.' : 'Adding files needs the engineer or quality role.',
      () => attachButton(base, reload), 'sm')),
  rec.attachments.length ? h('div', null, rec.attachments.map(a => h('div', { class: 'list-row' }, icon('file', 18),
    h('div', { style: { flex: 1, minWidth: 0 } }, h('a', { class: 'ellipsis', href: `${base}/${a.id}?access_token=${encodeURIComponent(S.token)}`, download: a.filename }, a.filename),
      h('div', { class: 'small muted' }, `${fmtSize(a.size)} · ${a.uploaded_by_name || ''} · ${ago(a.uploaded_at)}`)),
    canAttach && (a.uploaded_by === S.user.id || (meta && meta.may_decide)) ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${a.filename}`, onclick: async () => {
      if (await confirmDlg('Remove file', `Remove ${a.filename}? The file stays in Kosha's store and the record keeps a note of it.`, 'Remove', true)) send('DELETE', `/attachments/${a.id}`, undefined, 'Removed');
    } }, icon('x', 14)) : null)))
    : h('div', { class: 'empty' }, kind === 'ncr' ? 'Photos of the fault, measurement reports.' : 'Analysis, 8D report, evidence that it worked.'));
}

// ------------------------------------------------------------------ new records
export async function newNcrDialog(prefill = {}) {
  const m = meta || await quality();
  const title = h('input', { class: 'input', placeholder: 'e.g. Bore Ø40 measured 40.08, above H7', maxlength: 200 });
  const part = partField(prefill.item);
  const found = select(m.found_at, 'incoming');
  const batch = h('input', { class: 'input', placeholder: 'e.g. lot 12, SN 1041 to 1048' });
  const q = h('input', { class: 'input', type: 'number', min: '0', step: 'any' });
  const supplier = h('input', { class: 'input', placeholder: 'Who made it, if bought in' });
  const desc = text('', 'What was found, how it was measured, how many are affected');
  dialog({ title: 'Report a nonconformance', wide: true, body: [
    S.mode === 'guided' ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Something does not match its drawing. Report it here with photos; an approver or quality then decides what happens to the parts.')) : null,
    field('What is wrong', title), part.el,
    h('div', { class: 'form-grid' }, field('Found at', found), field('Batch or serial numbers', batch), field('Quantity affected', q), field('Supplier', supplier)),
    field('Description', desc)],
  actions: [{ label: 'Cancel' }, { label: 'Report', primary: true, onClick: async () => {
    if (!title.value.trim()) { toast('Say in a few words what is wrong', 'err'); title.focus(); return false; }
    const r = await post('/api/ncrs', { title: title.value, item_id: part.id(), found_at: found.value, batch: batch.value, qty: q.value, supplier: supplier.value, description: desc.value });
    changed(); toast(`${r.ncr.number} reported`, 'ok'); location.hash = `#/ncr/${r.ncr.id}`; return true;
  } }] });
}

async function capaFields(c = {}) {
  const m = meta || await quality();
  return {
    title: h('input', { class: 'input', value: c.title || '', placeholder: 'e.g. Hub seat fretting on agitator shafts', maxlength: 200 }),
    kind: select(m.capa_kinds, c.kind || 'corrective'),
    owner: await userSelect(c.owner_id || S.user.id),
    due: h('input', { class: 'input', type: 'date', value: c.due_date || '' }),
    problem: text(c.problem, 'The problem, how often, how big'),
    containment: text(c.containment, 'What was done at once to protect the customer (stock checked, parts held ...)'),
  };
}

export async function newCapaDialog(prefill = {}) {
  const f = await capaFields(prefill);
  const part = partField(prefill.item);
  dialog({ title: 'Start a corrective action', wide: true, body: [
    S.mode === 'guided' ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Find the root cause, carry out the actions that remove it, and let someone else check that it worked.')) : null,
    field('Problem to remove', f.title), part.el, h('div', { class: 'form-grid' }, field('Kind', f.kind), field('Owner', f.owner), field('Due date', f.due)),
    field('Problem', f.problem), field('Containment', f.containment)],
  actions: [{ label: 'Cancel' }, { label: 'Start', primary: true, onClick: async () => {
    if (!f.title.value.trim()) { toast('Say which problem this removes', 'err'); f.title.focus(); return false; }
    const r = await post('/api/capas', { title: f.title.value, kind: f.kind.value, item_id: part.id(), owner_id: Number(f.owner.value), due_date: f.due.value, problem: f.problem.value, containment: f.containment.value });
    changed(); toast(`${r.capa.number} started`, 'ok'); location.hash = `#/capa/${r.capa.id}`; return true;
  } }] });
}

export async function newFmeaDialog(prefill = {}) {
  const m = meta || await quality();
  const part = partField(prefill.item);
  const kind = select(m.fmea_kinds, 'design');
  const title = h('input', { class: 'input', placeholder: 'Empty: "Design FMEA <part number>"', maxlength: 200 });
  const team = h('input', { class: 'input', placeholder: 'Who took part' });
  dialog({ title: 'Start an FMEA', wide: true, body: [part.el, h('div', { class: 'form-grid' }, field('Kind', kind, 'Design: how the part can fail. Process: how making it can go wrong.'), field('Title', title)), field('Team', team)],
    actions: [{ label: 'Cancel' }, { label: 'Start', primary: true, onClick: async () => {
      if (!part.id()) { toast('Choose the part or assembly the FMEA is about', 'err'); return false; }
      const r = await post('/api/fmeas', { item_id: part.id(), kind: kind.value, title: title.value, team: team.value });
      toast('FMEA started', 'ok'); location.hash = `#/fmea/${r.fmea.id}`; return true;
    } }] });
}

// ------------------------------------------------------------------ the Quality screen
export async function renderQuality(container, _m, params, { setTitle }) {
  setTitle('Quality');
  let tab = ['ncrs', 'capas', 'fmeas'].includes(params.tab) ? params.tab : 'ncrs';
  let state = 'open';
  let q = '';
  const m = await quality();
  const body = h('div');
  const kpi = (n, label, go) => h('button', { class: 'kpi', style: { textAlign: 'left', cursor: 'pointer' }, onclick: go }, h('b', null, String(n)), h('span', null, label));
  const go = (t, s) => () => { tab = t; state = s; draw(); };
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Number, title, part, batch or supplier', 'aria-label': 'Search', style: { width: '260px' } });
  search.addEventListener('input', debounce(() => { q = search.value.trim(); load(); }, 250));
  const why = 'Needs the engineer or quality role.';
  const tbody = h('tbody');
  const load = async () => {
    const qs = [state && tab !== 'fmeas' ? `state=${state}` : '', q ? `q=${encodeURIComponent(q)}` : ''].filter(Boolean).join('&');
    try {
      if (tab === 'ncrs') {
        const r = await get(`/api/ncrs${qs ? `?${qs}` : ''}`);
        mount(tbody, r.ncrs.length ? r.ncrs.map(n => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/ncr/${n.id}`; } },
          h('td', { class: 'mono nowrap' }, n.number), h('td', null, h('div', { style: { fontWeight: 600 } }, n.title), n.batch ? h('div', { class: 'small muted' }, n.batch) : null),
          h('td', { class: 'mono small' }, n.part_number || '—'), h('td', { class: 'small' }, n.found_at_label), h('td', { class: 'num' }, qty(n.qty)),
          h('td', { class: 'small' }, n.disposition_label || '—'), h('td', { class: 'small mono' }, n.capa_number || ''), h('td', { class: 'muted nowrap' }, ago(n.created_at)), h('td', null, chip(NCR_CHIP, n.state, n.state_label))))
          : h('tr', null, h('td', { colspan: 9 }, h('div', { class: 'empty' }, state || q ? 'No reports match.' : 'No nonconformance reports yet.'))));
      } else if (tab === 'capas') {
        const r = await get(`/api/capas${qs ? `?${qs}` : ''}`);
        mount(tbody, r.capas.length ? r.capas.map(c => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/capa/${c.id}`; } },
          h('td', { class: 'mono nowrap' }, c.number), h('td', null, h('div', { style: { fontWeight: 600 } }, c.title), h('div', { class: 'small muted' }, c.kind_label)),
          h('td', { class: 'mono small' }, c.part_number || '—'), h('td', null, c.owner_name || ''), h('td', { class: 'nowrap' }, day(c.due_date), c.overdue ? h('span', { class: 'chip st-failed', style: { marginLeft: '6px' } }, 'Overdue') : null),
          h('td', { class: 'num' }, String(c.open_actions)), h('td', { class: 'num' }, String(c.ncrs)), h('td', null, chip(CAPA_CHIP, c.state, c.state_label))))
          : h('tr', null, h('td', { colspan: 8 }, h('div', { class: 'empty' }, state || q ? 'No corrective actions match.' : 'No corrective actions yet.'))));
      } else {
        const r = await get('/api/fmeas');
        const rows = r.fmeas.filter(f => !q || `${f.title} ${f.part_number} ${f.description}`.toLowerCase().includes(q.toLowerCase()));
        mount(tbody, rows.length ? rows.map(f => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/fmea/${f.id}`; } },
          h('td', null, h('b', null, f.title)), h('td', { class: 'small' }, f.kind_label), h('td', { class: 'mono small' }, f.part_number), h('td', { class: 'small muted' }, f.description),
          h('td', { class: 'num' }, String(f.rows)), h('td', { class: 'num' }, f.max_rpn === null ? '—' : h('span', { class: f.max_rpn > r.rpn_limit ? 'chip st-failed' : '' }, String(f.max_rpn))), h('td', { class: 'muted nowrap' }, ago(f.updated_at))))
          : h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, 'No FMEA yet.'))));
      }
    } catch (e) { fail(e); }
  };
  const draw = () => {
    const heads = { ncrs: ['Number', 'What is wrong', 'Part', 'Found at', 'Qty', 'Disposition', 'CAPA', 'Raised', 'Status'],
      capas: ['Number', 'Problem', 'Part', 'Owner', 'Due', 'Open actions', 'Reports', 'Status'],
      fmeas: ['Title', 'Kind', 'Part', 'Description', 'Rows', 'Highest RPN', 'Changed'] }[tab];
    const newBtn = { ncrs: btn('Report a nonconformance', 'plus', m.may_raise, why, () => newNcrDialog(), 'primary'),
      capas: btn('Start a corrective action', 'plus', m.may_raise, why, () => newCapaDialog(), 'primary'),
      fmeas: btn('Start an FMEA', 'plus', m.may_raise, why, () => newFmeaDialog(), 'primary') }[tab];
    const filters = tab === 'fmeas' ? null : h('div', { class: 'seg', role: 'group', 'aria-label': 'Status' },
      [['open', 'Open'], ['', 'All'], ['closed', 'Closed'], ['cancelled', 'Cancelled']].map(([k, l]) => h('button', { type: 'button', 'aria-pressed': String(state === k), onclick: () => { state = k; draw(); } }, l)));
    mount(body, tabs([['ncrs', 'Nonconformance reports'], ['capas', 'Corrective actions'], ['fmeas', 'FMEA']], tab, k => { tab = k; state = k === 'fmeas' ? '' : 'open'; draw(); }),
      h('div', { class: 'row wrap', style: { margin: '12px 0', gap: '12px' } }, filters, search, h('span', { class: 'spacer' }), newBtn),
      h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, heads.map(t => h('th', null, t)))), tbody)));
    load();
  };
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Quality'),
      h('div', { class: 'sub' }, 'Parts that do not match their drawing, corrective actions that remove the cause, and FMEA of how parts and processes can fail.'))),
    h('div', { class: 'kpis', style: { marginBottom: '16px' } },
      kpi(m.ncr_open, 'Reports waiting for a decision', go('ncrs', 'open')), kpi(m.ncr_dispositioned, 'Reports decided, not closed', go('ncrs', 'dispositioned')),
      kpi(m.capa_open, 'Corrective actions open', go('capas', 'open')), kpi(m.capa_overdue, 'Corrective actions overdue', go('capas', 'open')),
      kpi(m.my_actions, 'Actions for me', go('capas', 'open')), m.may_decide ? kpi(m.verify, 'Waiting for me to check', go('capas', 'verifying')) : null,
      kpi(m.fmeas, `FMEA (RPN limit ${m.rpn_limit})`, go('fmeas', ''))),
    body));
  draw();
}

// ------------------------------------------------------------------ nonconformance report
export async function renderNcr(container, [id], _p, { setTitle }) {
  const disc = discussionPanel('ncr', id);
  const route = routeOf();
  let n = null;
  await quality();
  const send = async (method, path, body, msg) => {
    try {
      const r = await api(method, `/api/ncrs/${id}${path}`, body);
      if (r.capa) { location.hash = `#/capa/${r.capa.id}`; return r; }
      n = r.ncr; if (msg) toast(msg, 'ok'); changed(); draw(); return r;
    } catch (e) { fail(e); return null; }
  };
  const editDlg = () => {
    const title = h('input', { class: 'input', value: n.title, maxlength: 200 });
    const found = select(n.found_at_choices, n.found_at);
    const batch = h('input', { class: 'input', value: n.batch });
    const q = h('input', { class: 'input', type: 'number', min: '0', step: 'any', value: n.qty ?? '' });
    const supplier = h('input', { class: 'input', value: n.supplier });
    const desc = text(n.description);
    dialog({ title: `Edit ${n.number}`, wide: true, body: [field('What is wrong', title), h('div', { class: 'form-grid' }, field('Found at', found), field('Batch or serial numbers', batch), field('Quantity affected', q), field('Supplier', supplier)), field('Description', desc)],
      actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => (await send('PATCH', '', { title: title.value, found_at: found.value, batch: batch.value, qty: q.value, supplier: supplier.value, description: desc.value }, 'Saved')) !== null }] });
  };
  const decideDlg = () => {
    const what = select(n.dispositions, 'rework');
    const note = text('', 'Why, and any condition (re-inspect, limit to this batch ...)');
    const hint = h('div', { class: 'banner info small' });
    const upd = () => mount(hint, icon('info'), h('div', null, ['use_as_is', 'repair'].includes(what.value)
      ? 'The part stays different from its drawing: Kosha raises a draft deviation for it, which is approved the usual way. Say why it can be used.'
      : what.value === 'return' ? 'Name the supplier if it is not filled in yet.' : 'The parts end up as drawn, or are not used.'));
    what.addEventListener('change', upd); upd();
    dialog({ title: `Decide what happens to ${n.number}`, body: [field('Disposition', what), hint, field('Reason', note)],
      actions: [{ label: 'Cancel' }, { label: 'Decide', primary: true, onClick: async () => (await send('POST', '/disposition', { disposition: what.value, note: note.value }, 'Decided')) !== null }] });
  };
  const closeDlg = () => {
    const note = text('', 'e.g. 8 bores reworked and re-measured, all within H7');
    dialog({ title: `Close ${n.number}`, body: field('What was done with the parts', note),
      actions: [{ label: 'Cancel' }, { label: 'Close report', primary: true, onClick: async () => (await send('POST', '/close', { note: note.value }, 'Closed')) !== null }] });
  };
  const cancelDlg = () => {
    const r = h('input', { class: 'input', placeholder: 'e.g. raised twice; the part was fine after all' });
    dialog({ title: `Cancel ${n.number}`, body: [h('p', { class: 'muted', style: { margin: 0 } }, 'It stays in the register as cancelled; its number is not used again.'), field('Reason', r)],
      actions: [{ label: 'Keep it' }, { label: 'Cancel report', danger: true, onClick: async () => (await send('POST', '/cancel', { reason: r.value }, 'Cancelled')) !== null }] });
  };
  const capaDlg = async () => {
    const open = (await get('/api/capas?state=open')).capas;
    const sel = h('select', { class: 'input' }, h('option', { value: '' }, 'Start a new corrective action'), open.map(c => h('option', { value: c.id }, `${c.number} · ${c.title}`)));
    dialog({ title: 'Corrective action', body: [h('p', { class: 'muted small', style: { margin: 0 } }, 'Remove the cause so it does not happen again. Several reports with the same cause can share one corrective action.'), field('Link to', sel)],
      actions: [{ label: 'Cancel' }, { label: 'Continue', primary: true, onClick: async () => (await send('POST', '/capa', sel.value ? { capa_id: Number(sel.value) } : {})) !== null }] });
  };
  const draw = () => {
    if (routeOf() !== route) return;
    const C = n.can;
    const decideWhy = 'Deciding needs the approver or quality role.';
    const buttons = [
      n.state === 'open' ? btn('Edit', 'edit', C.edit, 'Only the person who raised it, an approver or quality can change it.', editDlg) : null,
      n.state === 'open' ? btn('Decide disposition', 'check', C.disposition, decideWhy, decideDlg, 'primary') : null,
      n.state === 'dispositioned' ? btn('Close report', 'release', C.close, decideWhy, closeDlg, 'primary') : null,
      !n.capa && n.state !== 'cancelled' ? btn('Corrective action', 'change', C.capa, 'Needs the engineer or quality role.', capaDlg) : null,
      n.state === 'open' ? btn('Cancel report', 'x', C.cancel, 'Only the person who raised it, an approver or quality can cancel it.', cancelDlg, 'ghost') : null];
    const idx = { open: 0, dispositioned: 1, closed: 2, cancelled: -1 }[n.state];
    const guide = { open: 'Waiting for an approver or quality to decide what happens to the parts.',
      dispositioned: `Decided: ${n.disposition_label}. Close the report once that is done.`,
      closed: `Closed ${ago(n.closed_at)} by ${n.closed_by_name || ''}: ${n.close_note}`,
      cancelled: `Cancelled ${ago(n.closed_at)}: ${n.close_note}` }[n.state];
    const props = [['Part', n.item ? h('a', { href: `#/item/${n.item.id}` }, `${n.item.part_number} rev ${n.revision}`) : '—'], ['Found at', n.found_at_label],
      ['Batch or serials', n.batch || '—'], ['Quantity', qty(n.qty) || '—'], ['Supplier', n.supplier || '—'], ['Raised by', n.raised_by_name], ['Raised', fmtDate(n.created_at)]];
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } },
        h('div', { class: 'small muted' }, h('a', { href: '#/quality' }, 'Quality'), ` · ${n.number}`),
        h('h1', { style: { margin: '4px 0 10px' } }, n.title),
        h('div', { class: 'row wrap' }, stepper(['Reported', 'Decided', 'Closed'], idx, n.state === 'closed'), chip(NCR_CHIP, n.state, n.state_label))),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } }, buttons)),
      h('div', { class: `banner ${n.state === 'closed' ? 'ok' : 'info'}`, style: { marginBottom: '16px' } }, icon('info'), h('div', null, guide)),
      h('div', { class: 'grid-cards' },
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Details')), h('div', { class: 'panel-b col', style: { gap: '12px' } },
          h('p', { class: 'pre', style: { margin: 0 } }, n.description || h('span', { class: 'muted' }, 'No description.')),
          h('dl', { class: 'props' }, props.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])))),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Decision')), h('div', { class: 'panel-b col', style: { gap: '10px' } },
          n.disposition ? [h('div', null, h('b', null, n.disposition_label), n.dispositioned_by_name ? h('span', { class: 'muted small' }, ` · ${n.dispositioned_by_name}, ${ago(n.dispositioned_at)}`) : null),
            n.disposition_note ? h('p', { class: 'pre', style: { margin: 0 } }, n.disposition_note) : null]
            : h('div', { class: 'muted' }, 'Not decided yet.'),
          n.deviation ? h('div', { class: 'row' }, icon('warn', 16), h('a', { href: `#/deviation/${n.deviation.id}` }, `Deviation ${n.deviation.number}`), h('span', { class: 'chip' }, n.deviation.state)) : null,
          n.capa ? h('div', { class: 'row' }, icon('change', 16), h('a', { href: `#/capa/${n.capa.id}` }, `${n.capa.number} · ${n.capa.title}`), h('span', { class: `chip ${CAPA_CHIP[n.capa.state] || ''}` }, n.capa.state)) : null)),
        attachmentsPanel('ncr', n, C.attach, send, r => { n = r.ncr; draw(); })),
      h('div', { class: 'disc-slot' }, disc)));
  };
  n = (await get(`/api/ncrs/${id}`)).ncr;
  setTitle(n.number);
  draw();
}

// ------------------------------------------------------------------ corrective action
export async function renderCapa(container, [id], _p, { setTitle }) {
  const disc = discussionPanel('capa', id);
  const route = routeOf();
  let c = null;
  await quality();
  const send = async (method, path, body, msg) => {
    try {
      const r = await api(method, `/api/capas/${id}${path}`, body);
      c = r.capa; if (msg) toast(msg, 'ok'); changed(); draw(); return r;
    } catch (e) { fail(e); return null; }
  };
  const editDlg = async () => {
    const f = await capaFields(c);
    const root = text(c.root_cause, 'Why did it happen? Ask "why" until you reach something you can change.');
    dialog({ title: `Edit ${c.number}`, wide: true, body: [field('Problem to remove', f.title), h('div', { class: 'form-grid' }, field('Owner', f.owner), field('Due date', f.due)),
      field('Problem', f.problem), field('Containment', f.containment), field('Root cause', root)],
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => (await send('PATCH', '', { title: f.title.value, owner_id: Number(f.owner.value), due_date: f.due.value, problem: f.problem.value, containment: f.containment.value, root_cause: root.value }, 'Saved')) !== null }] });
  };
  const nextDlg = () => {
    if (c.state === 'open') {
      const root = text(c.root_cause, 'e.g. The reamer had no tool life limit, so it wore until bores came out oversize.');
      dialog({ title: 'Root cause found', body: [field('Root cause', root, 'Five whys: keep asking why until you reach something you can change.')],
        actions: [{ label: 'Cancel' }, { label: 'Plan the actions', primary: true, onClick: async () => (await send('POST', '/advance', { root_cause: root.value }, 'Root cause recorded')) !== null }] });
    } else if (c.state === 'actions') {
      send('POST', '/advance', {}, 'Ready to check that it worked');
    } else {
      const eff = text('', 'e.g. 3 months, 600 shafts made, none out of tolerance');
      dialog({ title: 'Did it work?', body: [field('How you checked', eff)],
        actions: [{ label: 'Cancel' }, { label: 'It did not work', danger: true, onClick: async () => (await send('POST', '/advance', { effectiveness: eff.value, effective: false }, 'Back to finding the cause')) !== null },
          { label: 'It worked: close', primary: true, onClick: async () => (await send('POST', '/advance', { effectiveness: eff.value }, 'Closed')) !== null }] });
    }
  };
  const addActionDlg = async () => {
    const t = h('input', { class: 'input', placeholder: 'e.g. Tool life limit of 400 parts on the reamer', maxlength: 500 });
    const owner = await userSelect(null, 'Choose who does it');
    const due = h('input', { class: 'input', type: 'date' });
    dialog({ title: 'Add an action', body: [field('What will be done', t), h('div', { class: 'form-grid' }, field('Who does it', owner), field('Done by', due))],
      actions: [{ label: 'Cancel' }, { label: 'Add', primary: true, onClick: async () => (await send('POST', '/actions', { text: t.value, owner_id: owner.value ? Number(owner.value) : null, due_date: due.value }, 'Action added')) !== null }] });
  };
  const ecrDlg = () => {
    const t = h('input', { class: 'input', value: c.title, maxlength: 200 });
    dialog({ title: 'Raise a change request', body: [h('p', { class: 'muted small', style: { margin: 0 } }, 'When the design has to change, a change request carries the problem and root cause to engineering.'), field('What should change', t)],
      actions: [{ label: 'Cancel' }, { label: 'Raise', primary: true, onClick: async () => (await send('POST', '/change-request', { title: t.value }, 'Change request raised')) !== null }] });
  };
  const cancelDlg = () => {
    const r = h('input', { class: 'input' });
    dialog({ title: `Cancel ${c.number}`, body: [h('p', { class: 'muted', style: { margin: 0 } }, 'It stays in the register as cancelled.'), field('Reason', r)],
      actions: [{ label: 'Keep it' }, { label: 'Cancel it', danger: true, onClick: async () => (await send('POST', '/cancel', { reason: r.value }, 'Cancelled')) !== null }] });
  };
  const draw = () => {
    if (routeOf() !== route) return;
    const C = c.can;
    const editWhy = 'Only the owner, the person who started it, an approver or quality can change it.';
    const buttons = [
      C.edit || S.mode === 'guided' ? btn('Edit', 'edit', C.edit, c.state === 'closed' || c.state === 'cancelled' ? 'Closed and cancelled corrective actions are kept as they are.' : editWhy, editDlg) : null,
      c.next.label ? btn(c.next.label, 'check', c.next.ok, c.next.why || editWhy, nextDlg, 'primary') : null,
      !c.ecr && c.state !== 'cancelled' ? btn('Raise a change request', 'change', C.ecr, 'Needs the engineer role.', ecrDlg) : null,
      ['open', 'actions'].includes(c.state) ? btn('Cancel corrective action', 'x', C.cancel, 'Only the person who started it, an approver or quality can cancel it.', cancelDlg, 'ghost') : null];
    const idx = { open: 0, actions: 1, verifying: 2, closed: 3, cancelled: -1 }[c.state];
    const props = [['Kind', c.kind_label], ['Part', c.item ? h('a', { href: `#/item/${c.item.id}` }, c.item.part_number) : '—'], ['Owner', c.owner_name],
      ['Due', h('span', null, day(c.due_date), c.overdue ? h('span', { class: 'chip st-failed', style: { marginLeft: '6px' } }, 'Overdue') : null)],
      ['Started by', c.raised_by_name], ['Started', fmtDate(c.created_at)], ['Checked by', c.verified_by_name || '—']];
    const para = (label, value, empty) => h('div', null, h('b', null, label), h('p', { class: 'pre', style: { margin: '4px 0 0' } }, value || h('span', { class: 'muted' }, empty)));
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } },
        h('div', { class: 'small muted' }, h('a', { href: '#/quality' }, 'Quality'), ` · ${c.number}`),
        h('h1', { style: { margin: '4px 0 10px' } }, c.title),
        h('div', { class: 'row wrap' }, stepper(['Find the cause', 'Actions', 'Check it worked', 'Closed'], idx, c.state === 'closed'), chip(CAPA_CHIP, c.state, c.state_label))),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } }, buttons)),
      c.next.why && c.state !== 'closed' ? h('div', { class: 'banner info', style: { marginBottom: '16px' } }, icon('info'), h('div', null, c.next.why)) : null,
      c.state === 'closed' ? h('div', { class: 'banner ok', style: { marginBottom: '16px' } }, icon('check'), h('div', null, `Closed ${ago(c.closed_at)}. Checked by ${c.verified_by_name || ''}: ${c.effectiveness}`)) : null,
      h('div', { class: 'grid-cards' },
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Problem and cause')), h('div', { class: 'panel-b col', style: { gap: '12px' } },
          para('Problem', c.problem, '—'), para('Containment', c.containment, 'Nothing recorded.'), para('Root cause', c.root_cause, 'Not found yet.'),
          c.effectiveness && c.state !== 'closed' ? para(c.state === 'cancelled' ? 'Why cancelled' : 'Last check (it did not work)', c.effectiveness, '') : null,
          h('dl', { class: 'props' }, props.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])))),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Actions (${c.actions.length})`), h('span', { class: 'spacer' }),
          ['open', 'actions'].includes(c.state) ? btn('Add action', 'plus', C.actions, editWhy, addActionDlg, 'sm') : null),
        c.actions.length ? h('div', null, c.actions.map(a => {
          const mayTick = ['open', 'actions'].includes(c.state) && (C.actions || a.owner_id === S.user.id);
          const cb = h('input', { type: 'checkbox', checked: !!a.done_at, disabled: !mayTick, 'aria-label': `Done: ${a.text}`, title: mayTick ? '' : 'Only the person who does it, the owner, an approver or quality can tick it off.' });
          cb.addEventListener('change', () => send('PATCH', `/actions/${a.id}`, { done: cb.checked }, cb.checked ? 'Done' : 'Reopened'));
          return h('div', { class: 'list-row' }, cb, h('div', { style: { flex: 1, minWidth: 0 } }, h('div', { style: a.done_at ? { textDecoration: 'line-through' } : {} }, a.text),
            h('div', { class: 'small muted' }, `${a.owner_name || ''} · by ${day(a.due_date)}`, a.done_at ? ` · done ${ago(a.done_at)}` : '')),
          a.overdue ? h('span', { class: 'chip st-failed' }, 'Overdue') : null,
          C.actions ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${a.text}`, onclick: () => send('DELETE', `/actions/${a.id}`, undefined, 'Removed') }, icon('x', 14)) : null);
        })) : h('div', { class: 'empty' }, 'Each action has someone who does it and a date.')),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Linked')), h('div', { class: 'panel-b col', style: { gap: '8px' } },
          c.ncrs.length ? c.ncrs.map(x => h('div', { class: 'row' }, icon('warn', 16), h('a', { href: `#/ncr/${x.id}` }, `${x.number} · ${x.title}`), h('span', { class: `chip ${NCR_CHIP[x.state] || ''}` }, x.state)))
            : h('div', { class: 'muted small' }, 'No nonconformance reports linked. Link one from the report.'),
          c.ecr ? h('div', { class: 'row' }, icon('change', 16), h('a', { href: `#/ecr/${c.ecr.id}` }, `${c.ecr.number} · ${c.ecr.title}`), h('span', { class: 'chip' }, c.ecr.state)) : null)),
        attachmentsPanel('capa', c, C.attach, send, r => { c = r.capa; draw(); })),
      h('div', { class: 'disc-slot' }, disc)));
  };
  c = (await get(`/api/capas/${id}`)).capa;
  setTitle(c.number);
  draw();
}

// ------------------------------------------------------------------ FMEA
const COLS = [['function', 'Function', 'text'], ['failure_mode', 'Failure mode', 'text'], ['effect', 'Effect', 'text'], ['severity', 'S', 'score'],
  ['cause', 'Cause', 'text'], ['occurrence', 'O', 'score'], ['prevention', 'Prevention', 'text'], ['detection_control', 'Detection control', 'text'],
  ['detection', 'D', 'score'], ['rpn', 'RPN', 'rpn'], ['action', 'Action', 'text'], ['due_date', 'Due', 'date'], ['action_taken', 'Action taken', 'text'],
  ['new_severity', 'S', 'score'], ['new_occurrence', 'O', 'score'], ['new_detection', 'D', 'score'], ['new_rpn', 'RPN after', 'rpn']];
const SCORE_HELP = { severity: 'Severity: how bad the effect is (10 = safety or the law)', occurrence: 'Occurrence: how often the cause happens (10 = almost always)',
  detection: 'Detection: how likely it slips through (10 = cannot be found)' };

export async function renderFmea(container, [id], _p, { setTitle }) {
  const disc = discussionPanel('fmea', id);
  const route = routeOf();
  const m = await quality();
  let f = null;
  let rows = [];
  let dirty = false;
  const plain = () => rows.map(r => Object.fromEntries([...COLS.filter(c => c[2] !== 'rpn').map(([k]) => [k, r[k] ?? '']), ['owner_id', r.owner_id ?? '']]));
  const rpn = (r, pre = '') => { const v = ['severity', 'occurrence', 'detection'].map(k => Number(r[pre + k])); return v.every(x => x >= 1) ? v[0] * v[1] * v[2] : null; };
  const save = async () => {
    try {
      f = (await put(`/api/fmeas/${id}`, { rows: plain() })).fmea;
      rows = f.rows.map(r => ({ ...r })); dirty = false; toast('Saved', 'ok'); draw();
    } catch (e) { fail(e); }
  };
  const cell = (r, i, [k, , kind]) => {
    if (kind === 'rpn') {
      const v = k === 'rpn' ? rpn(r) : rpn(r, 'new_');
      const high = v !== null && v > f.rpn_limit;
      return h('td', { class: 'num' }, v === null ? '—' : h('span', { class: high ? 'chip st-failed' : 'chip' }, String(v)));
    }
    if (!m.may_raise) return h('td', { class: kind === 'score' ? 'num' : 'small' }, kind === 'date' ? day(r[k]) : String(r[k] ?? ''));
    const inp = h(kind === 'text' ? 'textarea' : 'input', kind === 'score' ? { class: 'input', type: 'number', min: 1, max: 10, style: { width: '52px' }, title: SCORE_HELP[k.replace('new_', '')] || '' }
      : kind === 'date' ? { class: 'input', type: 'date', style: { width: '140px' } } : { class: 'input', rows: 2, style: { minWidth: '150px' } });
    inp.value = r[k] ?? '';
    inp.setAttribute('aria-label', `Row ${i + 1}: ${k.replace(/_/g, ' ')}`);
    inp.addEventListener('input', () => { r[k] = inp.value; dirty = true; if (kind === 'score') drawTable(); });
    return h('td', null, inp);
  };
  const table = h('div', { class: 'panel', style: { overflow: 'auto' } });
  const drawTable = () => {
    const active = document.activeElement && document.activeElement.getAttribute('aria-label');
    mount(table, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, h('th', null, '#'), COLS.map(([k, l]) => h('th', { title: SCORE_HELP[k.replace('new_', '')] || '' }, l)), h('th', null, 'Needs attention'), m.may_raise ? h('th') : null)),
      h('tbody', null, rows.length ? rows.map((r, i) => h('tr', null, h('td', { class: 'muted' }, String(i + 1)), COLS.map(c => cell(r, i, c)),
        h('td', { class: 'small', style: { minWidth: '180px' } }, (r.flags || []).map(x => h('div', { class: 'chip st-failed', style: { whiteSpace: 'normal', marginBottom: '2px' } }, x)), r.overdue ? h('div', { class: 'chip st-failed' }, 'Action overdue') : null),
        m.may_raise ? h('td', null, h('button', { class: 'btn ghost sm', 'aria-label': `Remove row ${i + 1}`, onclick: () => { rows.splice(i, 1); dirty = true; drawTable(); } }, icon('x', 14))) : null))
        : h('tr', null, h('td', { colspan: COLS.length + 3 }, h('div', { class: 'empty' }, 'No failure modes yet. Add a row for each way the function can fail.'))))));
    if (active) { const el = table.querySelector(`[aria-label="${CSS.escape(active)}"]`); if (el) { el.focus(); if (el.type === 'number') el.select(); } }
  };
  const draw = () => {
    if (routeOf() !== route) return;
    const s = f.summary;
    const exp = h('a', { class: 'btn', href: `/api/fmeas/${id}/export?access_token=${encodeURIComponent(S.token)}` }, icon('download', 16), 'Excel');
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } },
        h('div', { class: 'small muted' }, h('a', { href: '#/quality?tab=fmeas' }, 'Quality'), ' · ', f.kind_label, ' · ', h('a', { href: `#/item/${f.item.id}` }, `${f.item.part_number} rev ${f.item.revision}`)),
        h('h1', { style: { margin: '4px 0 6px' } }, f.title), f.team ? h('div', { class: 'small muted' }, `Team: ${f.team}`) : null),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } },
        m.may_raise ? h('button', { class: 'btn', onclick: () => { rows.push({ severity: '', occurrence: '', detection: '' }); dirty = true; drawTable(); } }, icon('plus', 16), 'Add row') : null,
        btn('Save', 'check', m.may_raise, 'Changing an FMEA needs the engineer or quality role.', save, 'primary'), exp)),
      h('div', { class: 'kpis', style: { marginBottom: '16px' } },
        h('div', { class: 'kpi' }, h('b', null, String(s.rows)), h('span', null, 'Failure modes')),
        h('div', { class: 'kpi' }, h('b', null, String(s.need_action)), h('span', null, 'Need attention')),
        h('div', { class: 'kpi' }, h('b', null, String(s.high_severity)), h('span', null, 'Severity 9 or 10')),
        h('div', { class: 'kpi' }, h('b', null, String(s.max_rpn)), h('span', null, `Highest RPN (limit ${f.rpn_limit})`))),
      S.mode === 'guided' ? h('div', { class: 'banner info small', style: { marginBottom: '12px' } }, icon('info'), h('div', null,
        'Score severity, occurrence and detection from 1 to 10. RPN = S × O × D. Rows above the limit, and every row with severity 9 or 10, need an action; score the row again after the action. Save keeps the scores and marks what still needs attention.')) : null,
      table, h('div', { class: 'disc-slot', style: { marginTop: '16px' } }, disc)));
    drawTable();
  };
  f = (await get(`/api/fmeas/${id}`)).fmea;
  rows = f.rows.map(r => ({ ...r }));
  setTitle(f.title);
  draw();
  // leaving with unsaved rows saves them, so nothing typed is lost; a row that can't be saved says so
  const leave = async () => {
    if (routeOf() === route) return;
    window.removeEventListener('hashchange', leave);
    if (!dirty) return;
    try { await put(`/api/fmeas/${id}`, { rows: plain() }); toast(`Your changes to ${f.title} were saved`, 'ok'); } catch (e) { toast(`Your changes to ${f.title} were not saved: ${e.message} Open it again to fix the row.`, 'err'); }
  };
  window.addEventListener('hashchange', leave);
}
