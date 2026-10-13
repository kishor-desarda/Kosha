// SPDX-License-Identifier: AGPL-3.0-or-later
// Deviations: temporary, approved departures from the released design ("42CrMo4 instead of C45 for batch 12").
// The register (with Excel export), the deviation page, the banners on item, file, BOM and change pages, and the
// Home panel. mountRecordPanels() adds the deviation banner and the discussion panel to a record page.
import { S, h, mount, icon, api, get, post, upload, toast, fail, dialog, confirmDlg, ago, fmtDate, fmtSize, can, debounce, typeChip } from './core.js';
import { discussionPanel, activeUsers } from './discussions.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
export const DEV_LABEL = { draft: 'Draft', submitted: 'Waiting for approval', approved: 'Approved, starts later', rejected: 'Rejected',
  active: 'Active', closed: 'Closed', cancelled: 'Cancelled' };
const CHIP = { draft: 'st-draft', submitted: 'st-submitted', approved: 'st-approved', rejected: 'st-rejected', active: 'st-dev-active',
  closed: 'st-dev-closed', cancelled: 'st-cancelled' };
const FILTERS = [['', 'All'], ['open', 'Open'], ['submitted', 'Waiting'], ['active', 'Active'], ['closed', 'Closed'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled']];
export const devChip = s => h('span', { class: `chip ${CHIP[s] || ''}` }, DEV_LABEL[s] || s);
const qty = q => (q === null || q === undefined || q === '' ? '' : String(+Number(q).toFixed(3)));
const day = iso => (iso ? fmtDate(`${iso}T12:00:00`, false) : '—');
const today = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };

// ------------------------------------------------------------------ banners (item, file, BOM, change pages)
export function deviationBanner(entityType, entityId) {
  const el = h('div', { class: 'dev-banners col', hidden: true });
  let loadedAt = 0;
  const load = async () => {
    try {
      const r = await get(`/api/deviations/active?entity_type=${entityType}&entity_id=${entityId}`);
      loadedAt = Date.now();
      el.data = r;
      mount(el, r.deviations.map(dv => (entityType === 'change_order'
        ? h('div', { class: 'banner info small' }, icon('warn'), h('div', null,
          dv.relation === 'made_permanent' ? 'This change order makes deviation ' : 'Linked deviation ',
          h('a', { href: `#/deviation/${dv.id}` }, dv.number), dv.relation === 'made_permanent' ? ` permanent (${dv.title}).` : `: ${dv.title} (${DEV_LABEL[dv.state]}).`))
        : h('div', { class: 'banner dev', role: 'status' }, icon('warn'),
          h('div', { style: { flex: 1, minWidth: 0 } }, h('b', null, dv.banner), dv.affected_text ? h('div', { class: 'small' }, `Applies to ${dv.affected_text}.`) : null),
          h('a', { class: 'btn sm', href: `#/deviation/${dv.id}` }, 'Open deviation')))));
      el.hidden = !r.deviations.length;
    } catch (e) { el.hidden = true; }
  };
  el.refresh = (force = false) => { if (force || Date.now() - loadedAt > 30000) load(); };
  load();
  return el;
}

// The same banner and discussion panel elements are reused while the user stays on one record, so redrawing the
// page (after Check out, Approve ...) keeps unsent comments.
let recordCache = null;
export function mountRecordPanels(page, entityType, entityId, opts = {}) {
  if (!page) return;
  const key = `${entityType}:${entityId}`;
  if (!recordCache || recordCache.key !== key) {
    recordCache = { key, revision: opts.revision, banner: deviationBanner(entityType, entityId), panel: discussionPanel(entityType, entityId, opts) };
  } else {
    const force = opts.revision !== recordCache.revision;
    recordCache.revision = opts.revision;
    recordCache.banner.refresh(force);
    recordCache.panel.refresh(force);
  }
  const head = page.querySelector(':scope > .page-head');
  if (head) head.after(recordCache.banner); else page.prepend(recordCache.banner);
  page.appendChild(h('div', { class: 'disc-slot' }, recordCache.panel));
}

export function bomDeviations(bomId) {
  return get(`/api/deviations/active?entity_type=bom&entity_id=${bomId}`).catch(() => ({ deviations: [], by_item: {} }));
}

// Small marker for a BOM line whose part has an active deviation.
export function lineDeviationChips(devs, itemId) {
  const list = (devs && devs.by_item && devs.by_item[String(itemId)]) || [];
  return list.map(x => h('a', { class: 'chip st-dev-active', href: `#/deviation/${x.id}`, title: x.banner, style: { marginLeft: '6px' } }, icon('warn', 12), x.number));
}

// ------------------------------------------------------------------ Home
export function deviationsHomePanel() {
  const el = h('section', { class: 'panel', hidden: true });
  (async () => {
    let r;
    try { r = await get('/api/deviations/home'); } catch (e) { return; }
    const seen = new Set();
    const row = (dv, sub) => { seen.add(dv.id); return h('a', { class: 'list-row', href: `#/deviation/${dv.id}` }, icon('warn', 20),
      h('div', { style: { flex: 1, minWidth: 0 } }, h('div', { class: 'ellipsis', style: { fontWeight: 600 } }, `${dv.number} · ${dv.title}`),
        h('div', { class: 'small muted ellipsis' }, sub)), devChip(dv.state)); };
    const waiting = r.waiting_for_me.map(dv => row(dv, `From ${dv.requested_by_name} · submitted ${ago(dv.submitted_at)} · ${dv.affected_text}`));
    const active = r.is_approver ? r.active.filter(dv => !seen.has(dv.id)).map(dv => row(dv, `${dv.affected_text}${dv.scope ? ` · ${dv.scope}` : ''}`)) : [];
    const mine = r.mine.filter(dv => !seen.has(dv.id)).map(dv => row(dv, dv.scope || dv.affected_text || 'Draft'));
    if (!r.is_approver && !mine.length) return;
    const sec = (title, rows) => (rows.length ? [h('div', { class: 'small muted', style: { padding: '8px 16px 0', fontWeight: 600 } }, title), rows] : null);
    mount(el, h('div', { class: 'panel-h' }, h('h2', null, 'Deviations'), waiting.length ? h('span', { class: 'badge' }, waiting.length) : null,
      h('span', { class: 'spacer' }), h('a', { href: '#/deviations' }, 'All deviations')),
    h('div', null, sec('Waiting for your approval', waiting), sec('Active now', active), sec('Your deviations', mine),
      !waiting.length && !active.length && !mine.length ? h('div', { class: 'empty' }, 'No deviations are waiting or active.') : null));
    el.hidden = false;
  })();
  return el;
}

// ------------------------------------------------------------------ pickers
function recordPicker(onPick) {
  const inp = h('input', { class: 'input', placeholder: 'Search part number, file name or description', 'aria-label': 'Find a part or file', autocomplete: 'off' });
  const list = h('div', { class: 'pick-list' });
  const run = debounce(async () => {
    const q = inp.value.trim();
    if (!q) { mount(list, h('div', { class: 'empty small' }, 'Type to search.')); return; }
    try {
      const r = await get(`/api/search?q=${encodeURIComponent(q)}&limit=20`);
      const rows = [
        ...r.items.map(it => h('button', { class: 'list-row', type: 'button', onclick: () => onPick({ item_id: it.id, label: `${it.part_number} rev ${it.revision}`, sub: it.description }) },
          h('span', { class: 'chip type' }, 'PART'), h('span', { class: 'mono', style: { minWidth: '130px' } }, it.part_number), h('span', { class: 'ellipsis', style: { flex: 1 } }, it.description), h('span', { class: 'muted small' }, `rev ${it.revision}`))),
        ...r.documents.map(d => h('button', { class: 'list-row', type: 'button', onclick: () => onPick({ document_id: d.id, label: `${d.filename} rev ${d.revision}`, sub: d.description }) },
          typeChip(d.ext), h('span', { class: 'ellipsis', style: { flex: 1 } }, d.filename), h('span', { class: 'muted small' }, `rev ${d.revision}`))),
      ];
      mount(list, rows.length ? rows : h('div', { class: 'empty small' }, 'Nothing found.'));
    } catch (e) { fail(e); }
  }, 200);
  inp.addEventListener('input', run);
  run();
  return h('div', null, inp, list);
}

const field = (label, el, hint) => { el.id = el.id || `f-${Math.random().toString(36).slice(2, 8)}`; return h('div', { class: 'field' }, h('label', { for: el.id }, label), el, hint ? h('span', { class: 'hint' }, hint) : null); };

function scopeFields(d = {}) {
  const f = {
    serials: h('input', { class: 'input', value: d.serials || '', placeholder: 'e.g. batch 12, or SN 1041 to 1050' }),
    qty_limit: h('input', { class: 'input', type: 'number', min: '0', step: 'any', value: d.qty_limit ?? '', placeholder: 'No limit' }),
    valid_from: h('input', { class: 'input', type: 'date', value: d.valid_from || '' }),
    valid_until: h('input', { class: 'input', type: 'date', value: d.valid_until || '', min: today() }),
  };
  const el = h('div', { class: 'form-grid' }, field('Serial or batch numbers', f.serials), field('Quantity allowed', f.qty_limit),
    field('Valid from', f.valid_from, 'Empty: from approval'), field('Valid until', f.valid_until, 'It closes by itself after this day'));
  return { f, el, values: () => ({ serials: f.serials.value, qty_limit: f.qty_limit.value, valid_from: f.valid_from.value, valid_until: f.valid_until.value }) };
}

// ------------------------------------------------------------------ new deviation
export async function newDeviationDialog(prefill = {}) {
  const approvers = (await activeUsers()).filter(u => ['approver', 'admin'].includes(u.role) && u.id !== S.user.id);
  const picked = new Map();
  const keyOf = p => (p.item_id ? `i${p.item_id}` : `d${p.document_id}`);
  try {
    if (prefill.item_id) { const r = await get(`/api/items/${prefill.item_id}`); picked.set(`i${r.item.id}`, { item_id: r.item.id, label: `${r.item.part_number} rev ${r.item.revision}`, sub: r.item.description }); }
    if (prefill.document_id) { const d = await get(`/api/documents/${prefill.document_id}`); picked.set(`d${d.id}`, { document_id: d.id, label: `${d.filename} rev ${d.revision}`, sub: d.description }); }
  } catch (e) { /* start empty */ }
  const title = h('input', { class: 'input', placeholder: 'e.g. 42CrMo4 instead of C45 for batch 12', maxlength: 200 });
  const departure = h('textarea', { class: 'input', placeholder: 'What may be different from the released design? e.g. material 42CrMo4 allowed instead of C45' });
  const reason = h('textarea', { class: 'input', placeholder: 'Why? e.g. C45 bar is out of stock until week 48' });
  const scope = scopeFields();
  const chosen = h('div', { class: 'col', style: { gap: '4px' } });
  const drawChosen = () => mount(chosen, picked.size ? [...picked.values()].map(p => h('div', { class: 'row small' }, icon(p.item_id ? 'cube' : 'file', 14),
    h('b', null, p.label), h('span', { class: 'muted ellipsis', style: { flex: 1 } }, p.sub || ''),
    h('button', { class: 'btn ghost sm', type: 'button', 'aria-label': `Remove ${p.label}`, onclick: () => { picked.delete(keyOf(p)); drawChosen(); } }, icon('x', 14))))
    : h('span', { class: 'muted small' }, 'Nothing chosen yet.'));
  drawChosen();
  const appr = approvers.map(u => ({ u, cb: h('input', { type: 'checkbox', checked: u.role === 'approver' && approvers.filter(x => x.role === 'approver').length === 1 }) }));
  const body = () => ({ title: title.value, departure: departure.value, reason: reason.value, ...scope.values(),
    affected: [...picked.values()].map(p => ({ item_id: p.item_id, document_id: p.document_id })), approver_ids: appr.filter(a => a.cb.checked).map(a => a.u.id) });
  const save = async submit => {
    if (!title.value.trim()) { toast('Give the deviation a title', 'err'); title.focus(); return false; }
    const d = await post('/api/deviations', body());
    changed();
    if (submit) {
      try { await post(`/api/deviations/${d.id}/submit`); toast(`${d.number} submitted for approval`, 'ok'); } catch (e) { toast(`${d.number} was saved as a draft. ${e.message}`, 'err'); }
    } else toast(`${d.number} saved as a draft`, 'ok');
    location.hash = `#/deviation/${d.id}`;
    return true;
  };
  dialog({
    title: 'New deviation', wide: true,
    body: [
      S.mode === 'guided' ? h('div', { class: 'banner info small' }, icon('info'), h('div', null,
        'A deviation allows a temporary, approved departure from the released design, for example another material for one batch. ',
        'Limit it by quantity, serial or batch numbers, or dates. Someone other than you approves it.')) : null,
      field('Title', title), field('What is different', departure), field('Reason', reason),
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Affected parts and files'), chosen, recordPicker(p => { picked.set(keyOf(p), p); drawChosen(); })),
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Limits'), scope.el),
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Approvers'),
        approvers.length ? h('div', { class: 'row wrap' }, appr.map(a => h('label', { class: 'check' }, a.cb, `${a.u.full_name} (${a.u.role})`)))
          : h('span', { class: 'muted small' }, 'No other approvers are set up. Ask your administrator.'),
        h('span', { class: 'hint' }, 'You are not listed: nobody approves their own deviation (four-eyes rule).'))],
    actions: [{ label: 'Cancel' }, { label: 'Save as draft', onClick: () => save(false) }, { label: 'Save and submit for approval', primary: true, onClick: () => save(true) }],
  });
}

// ------------------------------------------------------------------ list (the register)
export async function renderDeviations(container, _m, params, { setTitle }) {
  setTitle('Deviations');
  const f = { state: params.state || '', item: params.item_q || '', date_from: '', date_to: '' };
  const qs = () => Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  const tbody = h('tbody');
  const exportLink = h('a', { class: 'btn', title: 'Download the deviations shown here as an Excel workbook' }, icon('download', 16), 'Export to Excel');
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Status' });
  const load = async () => {
    const q = qs();
    exportLink.href = `/api/deviations/export?${q}${q ? '&' : ''}access_token=${encodeURIComponent(S.token)}`;
    try {
      const r = await get(`/api/deviations${q ? `?${q}` : ''}`);
      mount(tbody, r.deviations.length ? r.deviations.map(dv => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/deviation/${dv.id}`; } },
        h('td', { class: 'mono nowrap' }, dv.number),
        h('td', null, h('div', { style: { fontWeight: 600 } }, dv.title), dv.departure ? h('div', { class: 'small muted' }, dv.departure) : null),
        h('td', { class: 'small' }, dv.affected_text || h('span', { class: 'muted' }, '—')),
        h('td', { class: 'small' }, dv.scope || h('span', { class: 'muted' }, '—')),
        h('td', null, dv.requested_by_name), h('td', { class: 'muted nowrap' }, ago(dv.created_at)), h('td', null, devChip(dv.state))))
        : h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, f.state || f.item || f.date_from || f.date_to ? 'No deviations match these filters.' : 'No deviations yet.'))));
    } catch (e) { fail(e); }
  };
  const drawSeg = () => mount(seg, FILTERS.map(([k, l]) => h('button', { type: 'button', 'aria-pressed': String(f.state === k), onclick: () => { f.state = k; drawSeg(); load(); } }, l)));
  const itemInp = h('input', { class: 'input', type: 'search', value: f.item, placeholder: 'Part number or file name', 'aria-label': 'Filter by part or file', style: { width: '220px' } });
  itemInp.addEventListener('input', debounce(() => { f.item = itemInp.value.trim(); load(); }, 250));
  const dateInp = (key, label) => {
    const el = h('input', { class: 'input', type: 'date', 'aria-label': label, style: { width: 'auto' } });
    el.addEventListener('change', () => { f[key] = el.value; load(); });
    return h('label', { class: 'row small nowrap' }, label, el);
  };
  const newBtn = can('engineer') ? h('button', { class: 'btn primary', onclick: () => newDeviationDialog() }, icon('plus', 16), 'New deviation')
    : (S.mode === 'guided' ? h('button', { class: 'btn primary', disabled: true, title: 'Raising a deviation needs the engineer role.' }, icon('plus', 16), 'New deviation') : null);
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Deviations'),
      h('div', { class: 'sub' }, 'Temporary, approved departures from the released design, limited to a quantity, batch or dates.')), newBtn),
    h('div', { class: 'row wrap', style: { marginBottom: '12px', gap: '12px' } }, seg, itemInp, dateInp('date_from', 'Raised from'), dateInp('date_to', 'to'),
      h('span', { class: 'spacer' }), exportLink),
    h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Number', 'Title', 'Affects', 'Limits', 'Requested by', 'Raised', 'Status'].map(t => h('th', null, t)))), tbody))));
  drawSeg();
  await load();
  if (params.new && can('engineer')) newDeviationDialog({ item_id: params.item, document_id: params.document });
}

// ------------------------------------------------------------------ deviation page
const routeOf = () => location.hash.split('?')[0];

export async function renderDeviation(container, [id], _p, { setTitle }) {
  const disc = discussionPanel('deviation', id);
  const route = routeOf();
  let d = null;
  const load = async () => {
    d = await get(`/api/deviations/${id}`);
    if (routeOf() !== route) return;         // the user moved on while this was loading
    setTitle(d.number); draw();
  };
  const send = async (method, path, body, msg) => {
    try {
      const r = await api(method, `/api/deviations/${id}${path}`, body);
      d = r; if (msg) toast(typeof msg === 'function' ? msg(r) : msg, 'ok'); changed(); draw(); return r;
    } catch (e) { fail(e); return null; }
  };

  const decideDlg = decision => {
    const cm = h('textarea', { class: 'input', id: 'dv-dc', placeholder: decision === 'rejected' ? 'What needs to change? (required)' : 'Optional comment' });
    dialog({ title: decision === 'approved' ? `Approve ${d.number}` : `Reject ${d.number}`, body: [
      decision === 'approved' ? h('p', { style: { margin: 0 } }, `Allow this departure: "${d.departure || d.title}"${d.scope ? ` (${d.scope})` : ''}?`) : null,
      h('div', { class: 'field' }, h('label', { for: 'dv-dc' }, 'Comment'), cm)],
    actions: [{ label: 'Cancel' }, { label: decision === 'approved' ? 'Approve' : 'Reject', primary: decision === 'approved', danger: decision === 'rejected', onClick: async () => {
      if (decision === 'rejected' && !cm.value.trim()) { toast('Say why you reject it, so the requester knows what to change.', 'err'); return false; }
      return (await send('POST', '/decide', { decision, comment: cm.value }, r => (decision === 'rejected' ? 'Rejected' : r.state === 'active' ? `Approved: ${r.number} is active now` : r.state === 'approved' ? `Approved: starts ${day(r.valid_from)}` : 'Approved; waiting for the other approvers'))) !== null;
    } }] });
  };
  const usageDlg = () => {
    const q = h('input', { class: 'input', type: 'number', min: '0', step: 'any', value: '' });
    const ref = h('input', { class: 'input', placeholder: 'e.g. batch 12, work order 4711' });
    const note = h('input', { class: 'input' });
    const left = d.qty_limit !== null ? d.qty_limit - d.qty_used : null;
    dialog({ title: 'Record usage', body: [left !== null ? h('p', { class: 'muted', style: { margin: 0 } }, `${qty(left)} of ${qty(d.qty_limit)} are left. When all are used the deviation closes by itself.`) : null,
      h('div', { class: 'form-grid' }, field('Quantity made or fitted', q), field('Batch, serial or order', ref)), field('Note', note)],
    actions: [{ label: 'Cancel' }, { label: 'Record', primary: true, onClick: async () => (await send('POST', '/usage', { qty: q.value, reference: ref.value, note: note.value }, r => (r.state === 'closed' ? `Recorded. ${r.number} is used up and closed.` : 'Usage recorded'))) !== null }] });
  };
  const closeDlg = () => {
    const q = h('input', { class: 'input', type: 'number', min: '0', step: 'any', value: qty(d.qty_used) });
    const note = h('textarea', { class: 'input', placeholder: 'e.g. Batch 12 finished; C45 back in stock' });
    dialog({ title: `Close ${d.number}`, body: [h('p', { style: { margin: 0 } }, 'After closing, the departure is no longer allowed and the banners disappear. The record stays in the register.'),
      field('Quantity actually used in total', q, 'Kosha records the difference to what was booked so far.'), field('Note', note)],
    actions: [{ label: 'Cancel' }, { label: 'Close deviation', primary: true, onClick: async () => (await send('POST', '/close', { qty_used: q.value, note: note.value }, 'Deviation closed')) !== null }] });
  };
  const editDlg = () => {
    const title = h('input', { class: 'input', value: d.title, maxlength: 200 });
    const departure = h('textarea', { class: 'input' }); departure.value = d.departure;
    const reason = h('textarea', { class: 'input' }); reason.value = d.reason;
    const scope = scopeFields(d);
    dialog({ title: `Edit ${d.number}`, wide: true, body: [field('Title', title), field('What is different', departure), field('Reason', reason), h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Limits'), scope.el)],
      actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => (await send('PATCH', '', { title: title.value, departure: departure.value, reason: reason.value, ...scope.values() }, 'Saved')) !== null }] });
  };
  const addAffectedDlg = () => dialog({ title: 'Add a part or file', body: [h('p', { class: 'muted small', style: { margin: 0 } }, 'Kosha records the revision the part or file has now.'),
    recordPicker(p => send('POST', '/affected', { item_id: p.item_id, document_id: p.document_id }, `Added ${p.label}`))], actions: [{ label: 'Done' }] });
  const linkChangeDlg = async () => {
    const r = await get('/api/changes');
    const opts = r.changes.filter(c => !d.changes.some(x => x.id === c.id));
    if (!opts.length) { toast('There are no other change orders to link.', ''); return; }
    const sel = h('select', { class: 'input' }, opts.map(c => h('option', { value: c.id }, `${c.number} · ${c.title} (${c.state})`)));
    dialog({ title: 'Link a change order', body: field('Change order', sel), actions: [{ label: 'Cancel' }, { label: 'Link', primary: true, onClick: async () => (await send('POST', '/changes', { change_id: Number(sel.value) }, 'Linked')) !== null }] });
  };
  const makePermanent = async () => {
    if (!await confirmDlg('Make this permanent', `Create a draft change order from ${d.number}, with its parts, files and approvers? You then revise the files and submit the change as usual.`, 'Create change order')) return;
    const r = await send('POST', '/make-permanent', {}, x => `${x.change_number} created`);
    if (r) {
      if (r.skipped && r.skipped.length) toast(`Not added: ${r.skipped.join(' ')}`, 'err');
      location.hash = `#/change/${r.change_id}`;
    }
  };
  const attach = () => {
    const inp = h('input', { type: 'file', multiple: true, hidden: true });
    inp.addEventListener('change', async () => {
      if (!inp.files.length) return;
      const fd = new FormData();
      for (const f of inp.files) fd.append('file', f);
      try { d = await upload(`/api/deviations/${id}/attachments`, fd); toast(`${inp.files.length} file${inp.files.length > 1 ? 's' : ''} attached`, 'ok'); draw(); } catch (e) { fail(e); }
    });
    document.body.appendChild(inp);
    inp.click();
    setTimeout(() => inp.remove(), 60000);
  };

  const draw = () => {
    if (routeOf() !== route) return;
    const A = d.actions;
    const btn = (label, ic, key, run, cls = '') => (A[key].ok ? h('button', { class: `btn ${cls}`, onclick: run }, icon(ic, 16), label)
      : (S.mode === 'guided' ? h('button', { class: `btn ${cls}`, disabled: true, title: A[key].why }, icon(ic, 16), label) : null));
    const st = d.state;
    const buttons = [];
    if (['draft', 'rejected'].includes(st)) buttons.push(btn('Edit', 'edit', 'edit', editDlg), btn('Submit for approval', 'review', 'submit', () => send('POST', '/submit', {}, 'Submitted for approval'), 'primary'));
    if (st === 'submitted') buttons.push(btn('Approve', 'check', 'decide', () => decideDlg('approved'), 'primary'), btn('Reject', 'x', 'decide', () => decideDlg('rejected'), 'danger'));
    if (st === 'active') buttons.push(btn('Record usage', 'plus', 'record_usage', usageDlg, 'primary'));
    if (['approved', 'active'].includes(st)) buttons.push(btn('Close deviation', 'release', 'close', closeDlg));
    if (['approved', 'active', 'closed'].includes(st)) buttons.push(btn('Make this permanent', 'change', 'make_permanent', makePermanent));
    if (['draft', 'submitted', 'rejected', 'approved'].includes(st)) buttons.push(btn('Cancel deviation', 'x', 'cancel', async () => {
      if (await confirmDlg('Cancel deviation', `Cancel ${d.number}? It stays in the register as cancelled; its number is not used again.`, 'Cancel deviation', true)) send('POST', '/cancel', {}, 'Cancelled');
    }, 'ghost'));

    const steps = [['draft', 'Draft'], ['submitted', 'Approval'], ['active', 'Active'], ['closed', 'Closed']];
    const idx = { draft: 0, rejected: 0, submitted: 1, approved: 2, active: 2, closed: 3, cancelled: -1 }[st];
    const stepper = h('div', { class: 'stepper' });
    steps.forEach(([, l], i) => {
      if (i) stepper.appendChild(h('span', { class: 'line' }));
      const cls = st === 'closed' || i < idx ? 'done' : i === idx ? 'cur' : '';
      stepper.appendChild(h('span', { class: `s ${cls}` }, h('span', { class: 'dot' }, cls === 'done' ? '✓' : i + 1), l));
    });
    const guide = {
      draft: d.readiness.length ? `Before submitting: ${d.readiness.join('; ')}.` : 'Ready. Submit it for approval; the approvers below are told.',
      rejected: `Rejected. Read the approvers' comments, change what is needed and submit again.${d.readiness.length ? ` Still missing: ${d.readiness.join('; ')}.` : ''}`,
      submitted: 'Waiting for the approvers below. The requester never approves their own deviation (four-eyes rule).',
      approved: `Approved. It becomes active on ${day(d.valid_from)}.`,
      active: d.banner,
      closed: `Closed ${ago(d.closed_at)}: ${d.close_reason_label || 'closed'}${d.closed_by_name ? ` by ${d.closed_by_name}` : ''}. ${qty(d.qty_used) || 0} used.${d.close_note ? ` ${d.close_note}` : ''}`,
      cancelled: `Cancelled ${ago(d.closed_at)}. It stays in the register; its number is not used again.`,
    }[st];
    const bannerCls = { rejected: 'err', active: 'dev', closed: 'ok', cancelled: '' }[st] ?? 'info';
    const editable = A.edit.ok;
    const usersForAdd = activeUsers();

    const props = [['Serial or batch numbers', d.serials || '—'], ['Quantity allowed', d.qty_limit !== null ? qty(d.qty_limit) : 'No limit'],
      ['Quantity used', qty(d.qty_used) || '0'], ['Valid from', d.valid_from ? day(d.valid_from) : 'From approval'], ['Valid until', d.valid_until ? day(d.valid_until) : '—'],
      ['Requested by', d.requested_by_name], ['Raised', fmtDate(d.created_at)], ['Submitted', d.submitted_at ? fmtDate(d.submitted_at) : '—'],
      ['Decided', d.decided_at ? fmtDate(d.decided_at) : '—'], ['Active since', d.activated_at ? fmtDate(d.activated_at) : '—'], ['Ended', d.closed_at ? fmtDate(d.closed_at) : '—']];

    const approverAdder = h('span');
    if (editable) {
      usersForAdd.then(us => {
        const opts = us.filter(u => ['approver', 'admin'].includes(u.role) && u.id !== d.requested_by && !d.approvers.some(a => a.user_id === u.id));
        if (!opts.length) return;
        const sel = h('select', { class: 'input', style: { width: 'auto', minHeight: '28px' }, 'aria-label': 'Add approver' }, h('option', { value: '' }, 'Add approver…'), opts.map(u => h('option', { value: u.id }, u.full_name)));
        sel.addEventListener('change', () => { if (sel.value) send('POST', '/approvers', { user_id: Number(sel.value) }); });
        mount(approverAdder, sel);
      });
    }
    const pct = d.qty_limit ? Math.min(100, Math.round((d.qty_used / d.qty_limit) * 100)) : 0;
    const showUsage = d.qty_limit !== null || d.usage.length || st === 'active';
    const relLabel = { made_permanent: 'Makes it permanent', related: 'Related' };

    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } },
        h('div', { class: 'small muted' }, `${d.number} · requested by ${d.requested_by_name} ${ago(d.created_at)}`),
        h('h1', { style: { margin: '4px 0 10px' } }, d.title),
        h('div', { class: 'row wrap' }, stepper, devChip(st))),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } }, buttons)),
      h('div', { class: `banner ${bannerCls}`, style: { marginBottom: '16px' } }, icon(st === 'active' ? 'warn' : 'info'), h('div', null, guide)),
      d.over_limit ? h('div', { class: 'banner err', style: { marginBottom: '16px' } }, icon('warn'), h('div', null, `More was used (${qty(d.qty_used)}) than the ${qty(d.qty_limit)} allowed. Raise a non-conformance or a new deviation for the difference.`)) : null,
      h('div', { class: 'grid-cards' },
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Details')), h('div', { class: 'panel-b col', style: { gap: '12px' } },
          h('div', null, h('b', null, 'What is different'), h('p', { class: 'pre', style: { margin: '4px 0 0' } }, d.departure || h('span', { class: 'muted' }, 'Not described yet.'))),
          h('div', null, h('b', null, 'Reason'), h('p', { class: 'pre', style: { margin: '4px 0 0' } }, d.reason || h('span', { class: 'muted' }, '—'))),
          h('dl', { class: 'props' }, props.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])))),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Affected (${d.affected.length})`), h('span', { class: 'spacer' }),
          editable ? h('button', { class: 'btn sm', onclick: addAffectedDlg }, icon('plus', 14), 'Add part or file') : null),
        d.affected.length ? h('table', { class: 'tbl' }, h('tbody', null, d.affected.map(a => h('tr', null,
          h('td', { style: { width: '64px' } }, a.item_id ? h('span', { class: 'chip type' }, 'PART') : typeChip(a.ext)),
          h('td', null, h('a', { href: a.item_id ? `#/item/${a.item_id}` : `#/doc/${a.document_id}` }, a.item_id ? a.part_number : a.filename), h('span', { class: 'muted' }, ` rev ${a.revision}`)),
          h('td', { class: 'small muted' }, a.description),
          h('td', { style: { width: '40px' } }, editable ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${a.label}`, onclick: () => send('DELETE', `/affected/${a.id}`) }, icon('x', 14)) : null)))))
          : h('div', { class: 'empty' }, editable ? 'Add the parts or files this deviation applies to.' : 'Nothing listed.')),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Approvers (${d.approvers.length})`), h('span', { class: 'spacer' }), approverAdder),
          h('div', null, d.approvers.length ? d.approvers.map(a => h('div', { class: 'list-row' }, icon('users', 18), h('div', { style: { flex: 1 } }, h('b', null, a.name),
            a.comment ? h('div', { class: 'small muted' }, `"${a.comment}"`) : null, a.decided_at ? h('div', { class: 'small muted' }, ago(a.decided_at)) : null),
          h('span', { class: `chip st-${a.decision}` }, a.decision),
          editable ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${a.name}`, onclick: () => send('DELETE', `/approvers/${a.user_id}`) }, icon('x', 14)) : null))
            : h('div', { class: 'empty' }, editable ? 'Add at least one approver. You can\'t approve your own deviation.' : 'No approvers.'))),
        showUsage ? h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Usage'), h('span', { class: 'spacer' }),
          A.record_usage.ok ? h('button', { class: 'btn sm', onclick: usageDlg }, icon('plus', 14), 'Record usage') : null),
        h('div', { class: 'panel-b col' },
          d.qty_limit !== null ? [h('div', { class: 'progress', role: 'progressbar', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-label': 'Quantity used' }, h('div', { style: { width: `${pct}%` } })),
            h('div', { class: 'small' }, `${qty(d.qty_used) || 0} of ${qty(d.qty_limit)} used`)] : h('div', { class: 'small' }, `${qty(d.qty_used) || 0} used (no quantity limit)`),
          d.usage.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['When', 'Quantity', 'Batch / reference', 'By'].map(t => h('th', null, t)))),
            h('tbody', null, d.usage.map(u => h('tr', null, h('td', { class: 'nowrap' }, fmtDate(u.at)), h('td', { class: 'num' }, qty(u.qty)),
              h('td', null, u.reference, u.note ? h('div', { class: 'small muted' }, u.note) : null), h('td', null, u.by)))))
            : h('div', { class: 'muted small' }, 'Nothing recorded yet.'))) : null,
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Attachments (${d.attachments.length})`), h('span', { class: 'spacer' }),
          A.attach.ok ? h('button', { class: 'btn sm', onclick: attach }, icon('upload', 14), 'Attach files')
            : (S.mode === 'guided' ? h('button', { class: 'btn sm', disabled: true, title: A.attach.why }, icon('upload', 14), 'Attach files') : null)),
        d.attachments.length ? h('div', null, d.attachments.map(a => h('div', { class: 'list-row' }, icon('file', 18),
          h('div', { style: { flex: 1, minWidth: 0 } }, h('a', { class: 'ellipsis', href: `/api/deviations/${id}/attachments/${a.id}?access_token=${encodeURIComponent(S.token)}`, download: a.filename }, a.filename),
            h('div', { class: 'small muted' }, `${fmtSize(a.size)} · ${a.by} · ${ago(a.uploaded_at)}`)),
          a.can_remove.ok ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${a.filename}`, onclick: async () => {
            if (await confirmDlg('Remove attachment', `Remove ${a.filename} from ${d.number}? The file stays in Kosha's store.`, 'Remove', true)) send('DELETE', `/attachments/${a.id}`, undefined, 'Attachment removed');
          } }, icon('x', 14)) : null)))
          : h('div', { class: 'empty' }, 'Photos, inspection reports or supplier certificates.')),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Change orders (${d.changes.length})`), h('span', { class: 'spacer' }),
          A.link_change.ok ? h('button', { class: 'btn sm', onclick: linkChangeDlg }, icon('link', 14), 'Link') : null),
        d.changes.length ? h('div', null, d.changes.map(c => h('div', { class: 'list-row' }, icon('change', 18),
          h('div', { style: { flex: 1, minWidth: 0 } }, h('a', { href: `#/change/${c.id}` }, `${c.number} · ${c.title}`), h('div', { class: 'small muted' }, relLabel[c.relation] || c.relation)),
          h('span', { class: `chip st-${c.state}` }, c.state),
          A.link_change.ok && c.relation !== 'made_permanent' ? h('button', { class: 'btn ghost sm', 'aria-label': `Unlink ${c.number}`, onclick: () => send('DELETE', `/changes/${c.id}`, undefined, 'Unlinked') }, icon('x', 14)) : null)))
          : h('div', { class: 'empty' }, 'If the departure works well, "Make this permanent" starts a change order to update the released design.'))),
      h('div', { class: 'disc-slot' }, disc)));
  };
  await load();
}
