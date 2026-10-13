// SPDX-License-Identifier: AGPL-3.0-or-later
// BOM views (latest released, in work, as of a date, as built), cost roll-up and what-if (#/bom-view/<item>),
// and the installed base of machine builds (#/machines).
import { S, h, mount, icon, get, post, put, patch, toast, fail, dialog, can, fmtDate } from './core.js';
import { itemPicker } from './bom.js';

const VIEWS = [['released', 'Latest released'], ['in_work', 'In work'], ['as_of', 'As of a date'], ['as_built', 'As built']];
const STATUS = { added: ['Added', 'st-released'], changed: ['Quantity changed', 'st-review'], removed: ['Removed', 'st-obsolete'] };
const SOURCE = { erp: 'ERP rate', estimate: 'Estimate', rolled_up: 'Rolled up', missing: 'No cost', in_bought: 'In bought assembly' };

const money = (n, cur) => (n == null ? '—' : `${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${cur ? ' ' + cur : ''}`);
const today = () => new Date().toISOString().slice(0, 10);
const query = o => Object.entries(o).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

export async function renderBomView(container, [id], params, { setTitle }) {
  const st = { view: params.view || 'released', date: params.date || today(), serial: params.serial || '', cost: params.cost === '1' };
  const machines = (await get(`/api/machines?item_id=${id}`)).machines;
  if (!st.serial && machines.length) st.serial = machines[0].serial;
  const body = h('div');
  const go = () => { location.hash = `#/bom-view/${id}?${query({ view: st.view, date: st.view === 'as_of' ? st.date : '', serial: st.view === 'as_built' ? st.serial : '', cost: st.cost ? '1' : '' })}`; };

  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'BOM view' }, VIEWS.map(([k, l]) =>
    h('button', { 'aria-pressed': String(st.view === k), disabled: k === 'as_built' && !machines.length, title: k === 'as_built' && !machines.length ? 'No machine of this part has been recorded yet' : '', onclick: () => { st.view = k; go(); } }, l)));
  const dateIn = h('input', { class: 'input', type: 'date', value: st.date, 'aria-label': 'Date', style: { width: 'auto' }, onchange: e => { st.date = e.target.value; go(); } });
  const serialSel = h('select', { class: 'input', 'aria-label': 'Machine serial number', style: { width: 'auto' }, onchange: e => { st.serial = e.target.value; go(); } },
    machines.map(m => h('option', { value: m.serial, selected: m.serial === st.serial }, `${m.serial}${m.customer ? ' · ' + m.customer : ''}`)));
  const costBox = h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: st.cost, onchange: e => { st.cost = e.target.checked; go(); } }), 'Show cost');

  let v;
  try {
    v = await get(`/api/items/${id}/bom/view?${query({ view: st.view, date: st.view === 'as_of' ? st.date : '', serial: st.view === 'as_built' ? st.serial : '', cost: st.cost ? '1' : '' })}`);
  } catch (e) {
    mount(container, h('div', { class: 'page-pad' }, h('div', { class: 'banner err' }, icon('warn'), h('div', null, e.message))));
    return;
  }
  setTitle(`BOM views ${v.part_number}`);
  const cost = st.cost;
  const cols = ['Level', 'Part number', 'Description', 'Qty', 'Total qty', 'Rev', st.view === 'in_work' ? 'Change' : null, st.view === 'as_built' ? 'Deviations' : null,
    cost ? 'Unit cost' : null, cost ? 'Total cost' : null, cost ? 'Source' : null].filter(Boolean);
  const table = v.rows.length ? h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl bom-grid' },
    h('thead', null, h('tr', null, cols.map(c => h('th', null, c)))),
    h('tbody', null, v.rows.map(r => h('tr', { class: r.status === 'removed' ? 'muted' : '' },
      h('td', { class: 'lvl', style: { paddingLeft: `${8 + (r.level - 1) * 18}px` } }, `L${r.level}`),
      h('td', { class: 'mono nowrap' }, h('a', { href: `#/item/${r.item_id}` }, r.part_number)),
      h('td', null, r.description),
      h('td', { class: 'num' }, r.status === 'changed' ? `${r.was_qty} → ${r.qty}` : r.qty),
      h('td', { class: 'num muted' }, r.ext_qty),
      h('td', null, r.revision),
      st.view === 'in_work' ? h('td', null, r.status ? h('span', { class: `chip ${STATUS[r.status][1]}` }, STATUS[r.status][0]) : null) : null,
      st.view === 'as_built' ? h('td', null, (r.deviations || []).map(d => h('a', { class: 'chip st-review', href: `#/deviation/${d.id}`, title: d.title, style: { marginRight: '4px' } }, d.number))) : null,
      cost ? h('td', { class: 'num' }, money(r.unit_cost)) : null,
      cost ? h('td', { class: 'num' }, money(r.ext_cost ?? r.ext_cost_below)) : null,
      cost ? h('td', { class: 'small', title: r.cost_how }, r.cost_source === 'estimate' ? h('span', { class: 'sev-warning' }, SOURCE[r.cost_source]) : r.cost_source === 'missing' ? h('span', { class: 'sev-error' }, SOURCE[r.cost_source]) : SOURCE[r.cost_source]) : null)))))
    : h('div', { class: 'empty' }, v.empty_why);

  const costPanel = cost ? h('section', { class: 'panel', style: { flex: '0 1 340px', minWidth: '280px', alignSelf: 'flex-start' } },
    h('div', { class: 'panel-h' }, h('h2', null, 'Cost'), h('span', { class: 'spacer' }),
      h('a', { class: 'btn sm', href: `/api/items/${id}/bom/cost/export?${query({ view: st.view, date: st.view === 'as_of' ? st.date : '', serial: st.view === 'as_built' ? st.serial : '', access_token: S.token })}` }, icon('download', 14), 'Excel')),
    h('div', { class: 'panel-b col', style: { gap: '10px' } },
      h('div', { style: { fontSize: '22px', fontWeight: 600 } }, money(v.total, v.currency)),
      v.estimated ? h('div', { class: 'small sev-warning' }, `${money(v.estimated, v.currency)} of it is estimated from weight and per-kg rates.`) : null,
      v.missing ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, `${v.missing} line(s) have no ERP rate and no estimate, so the total is too low: ${v.missing_parts.join(', ')}. Add the weight and material, or set a rate for the material.`)) : null,
      h('h3', { style: { margin: '6px 0 0' } }, 'Top cost drivers'),
      v.drivers.length ? h('table', { class: 'tbl' }, h('tbody', null, v.drivers.map(d => h('tr', null,
        h('td', { class: 'mono small' }, d.part_number), h('td', { class: 'num small' }, money(d.cost)), h('td', { class: 'num small muted' }, `${d.share}%`))))) : h('div', { class: 'muted small' }, 'No costs yet.'),
      h('div', { class: 'row wrap' },
        h('button', { class: 'btn sm', onclick: () => whatIfDialog(id, st, machines) }, 'What-if: compare'),
        can('admin') ? h('button', { class: 'btn sm', onclick: ratesDialog }, 'Rates') : null),
      h('p', { class: 'muted small', style: { margin: 0 } }, `Costs come from ${S.erp}; Kosha only reads and adds them up. Parts without a rate are estimated from their weight and the per-kg rates an admin sets, and marked as estimates.`)))
    : null;

  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } }, h('div', { class: 'small muted' }, 'BOM views'),
      h('h1', null, h('a', { href: `#/item/${id}`, class: 'mono', style: { color: 'inherit', textDecoration: 'none' } }, v.part_number), ' ', h('span', { style: { fontWeight: 400 } }, v.description)),
      h('div', { class: 'sub' }, v.revision ? `Revision ${v.revision}${v.serial ? ` · machine ${v.serial}` : ''}${v.day ? ` · as released on ${v.day}` : ''}${v.changes != null ? ` · ${v.changes} pending change(s)` : ''}` : '')),
    v.bom_id ? h('a', { class: 'btn', href: `#/bom/${v.bom_id}` }, icon('bom', 16), 'Open BOM') : null),
    h('div', { class: 'row wrap', style: { marginBottom: '12px' } }, seg, st.view === 'as_of' ? dateIn : null, st.view === 'as_built' ? serialSel : null, h('span', { class: 'spacer' }), costBox),
    v.build ? h('div', { class: 'banner info', style: { marginBottom: '12px' } }, icon('info'), h('div', null,
      `Machine ${v.build.serial}, built ${fmtDate(v.build.built_on, false)}${v.build.customer ? ' for ' + v.build.customer : ''}${v.build.site ? ', ' + v.build.site : ''}. This is the BOM it was built with; it does not change when the design moves on.`)) : null,
    body));
  mount(body, h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '16px' } }, h('div', { style: { flex: '1 1 640px', minWidth: 0 } }, table), costPanel));
}

function viewPicker(prefix, id, st, machines) {
  const sel = h('select', { class: 'input', id: `${prefix}-view` }, VIEWS.map(([k, l]) => h('option', { value: k, disabled: k === 'as_built' && !machines.length, selected: k === st.view }, l)));
  const day = h('input', { class: 'input', type: 'date', value: st.date, 'aria-label': 'Date' });
  const serial = h('select', { class: 'input', 'aria-label': 'Machine' }, machines.map(m => h('option', { value: m.serial }, m.serial)));
  const sync = () => { day.hidden = sel.value !== 'as_of'; serial.hidden = sel.value !== 'as_built'; };
  sel.addEventListener('change', sync); sync();
  return { el: h('div', { class: 'row wrap' }, sel, day, serial), value: () => ({ item_id: Number(id), view: sel.value, date: day.value, serial: serial.value }) };
}

function whatIfDialog(id, st, machines) {
  const a = viewPicker('wa', id, { ...st, view: machines.length ? 'as_built' : 'released' }, machines);
  const b = viewPicker('wb', id, { ...st, view: 'in_work' }, machines);
  const out = h('div');
  dialog({ title: 'What-if: where does the cost difference come from?', wide: true, body: [
    h('div', { class: 'field' }, h('label', { for: 'wa-view' }, 'Compare'), a.el),
    h('div', { class: 'field' }, h('label', { for: 'wb-view' }, 'With'), b.el), out],
  actions: [{ label: 'Close' }, { label: 'Compare', primary: true, onClick: async () => {
    try {
      const r = await post('/api/boms/what-if', { a: a.value(), b: b.value() });
      mount(out, h('p', null, h('b', null, `${r.difference >= 0 ? '+' : ''}${money(r.difference, r.currency)}`), ` from ${r.a.label} (${money(r.a.total)}) to ${r.b.label} (${money(r.b.total)}).`),
        r.lines.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Part', 'Change', 'Qty', 'Cost', 'Difference'].map(t => h('th', null, t)))),
          h('tbody', null, r.lines.slice(0, 30).map(x => h('tr', null, h('td', { class: 'mono small' }, x.part_number, h('div', { class: 'muted' }, x.description)),
            h('td', null, x.change), h('td', { class: 'num' }, `${x.from_qty} → ${x.to_qty}`), h('td', { class: 'num' }, `${money(x.from_cost)} → ${money(x.to_cost)}`),
            h('td', { class: 'num' }, x.missing_cost ? h('span', { class: 'sev-warning', title: 'A cost is missing on one side' }, money(x.difference)) : money(x.difference))))))
          : h('div', { class: 'empty' }, 'No difference.'));
    } catch (e) { fail(e); }
    return false;
  } }] });
}

async function ratesDialog() {
  const r = await get('/api/cost/rates');
  const cur = h('input', { class: 'input', id: 'cr-cur', value: r.currency, maxlength: 3, style: { maxWidth: '90px' } });
  const proc = h('input', { class: 'input', id: 'cr-proc', type: 'number', min: 0, step: 'any', value: r.process_rate_per_kg, style: { maxWidth: '140px' } });
  const mats = h('textarea', { class: 'input mono', id: 'cr-mat', rows: 6, value: Object.entries(r.material_rates).map(([k, v]) => `${k} = ${v}`).join('\n'), placeholder: 'S235 = 95\nSS304 = 320' });
  dialog({ title: 'Cost estimate rates', body: [
    h('p', { class: 'muted', style: { marginTop: 0 } }, 'Used only for parts without an ERP rate: weight × (material rate + process rate). Estimates are marked as such.'),
    h('div', { class: 'row wrap' }, h('div', { class: 'field' }, h('label', { for: 'cr-cur' }, 'Currency'), cur), h('div', { class: 'field' }, h('label', { for: 'cr-proc' }, 'Process rate per kg'), proc)),
    h('div', { class: 'field' }, h('label', { for: 'cr-mat' }, 'Material rates per kg (one per line: material = rate)'), mats)],
  actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
    const material_rates = {};
    for (const line of mats.value.split('\n')) {
      const m = line.split('=');
      if (m.length === 2 && m[0].trim()) material_rates[m[0].trim()] = Number(m[1]);
    }
    await put('/api/cost/rates', { currency: cur.value, process_rate_per_kg: Number(proc.value), material_rates });
    toast('Rates saved', 'ok');
    window.dispatchEvent(new Event('hashchange'));
  } }] });
}

// ------------------------------------------------------------------ installed base --

export async function renderMachines(container, _m, params, { setTitle }) {
  setTitle('Machines');
  const q = h('input', { class: 'input', type: 'search', value: params.q || '', placeholder: 'Serial number, customer, site, order or machine', 'aria-label': 'Find machines', style: { maxWidth: '380px' } });
  const tbody = h('tbody');
  const draw = async () => {
    const r = await get(`/api/machines?q=${encodeURIComponent(q.value)}`);
    mount(tbody, r.machines.length ? r.machines.map(m => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/bom-view/${m.item_id}?view=as_built&serial=${encodeURIComponent(m.serial)}`; } },
      h('td', { class: 'mono' }, m.serial), h('td', null, h('span', { class: 'mono' }, m.part_number), ' ', m.description), h('td', null, m.revision),
      h('td', null, m.customer || h('span', { class: 'muted' }, '—'), m.site ? h('div', { class: 'small muted' }, m.site) : null), h('td', null, m.order_ref),
      h('td', { class: 'nowrap' }, fmtDate(m.built_on, false)), h('td', { class: 'nowrap' }, m.commissioned_on ? fmtDate(m.commissioned_on, false) : h('span', { class: 'muted' }, 'Not yet')),
      h('td', null, m.deviations.map(d => h('span', { class: 'chip st-review', style: { marginRight: '4px' } }, d))),
      h('td', { class: 'nowrap', onclick: e => e.stopPropagation() }, can('engineer') ? h('button', { class: 'btn sm', onclick: () => editMachine(m, draw) }, 'Edit') : null,
        can('approver') ? h('button', { class: 'btn sm ghost', onclick: () => withdrawMachine(m, draw) }, 'Withdraw') : null)))
      : h('tr', null, h('td', { colspan: 9 }, h('div', { class: 'empty' }, q.value ? 'No machine matches.' : 'No machines recorded yet. Record each machine you deliver, so its as-built BOM is kept for spares.'))));
  };
  let t;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(draw, 250); });
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Machines'),
      h('div', { class: 'sub' }, 'The installed base: every machine delivered, its serial number, customer and the BOM it was built with.')),
    can('engineer') ? h('button', { class: 'btn primary', onclick: () => newMachine(draw) }, icon('plus', 16), 'Record a machine') : null),
    h('div', { style: { marginBottom: '12px' } }, q),
    h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Serial', 'Machine', 'Rev', 'Customer', 'Order', 'Built', 'Commissioned', 'Deviations', ''].map(x => h('th', null, x)))), tbody))));
  await draw();
}

function newMachine(done) {
  let picked = null;
  const info = h('div', { class: 'small' }, 'Choose the machine (its top assembly). Its latest released BOM becomes the as-built BOM.');
  const fields = { serial: ['Serial number', 'Leave empty for the next number of the family'], family: ['Product family', 'Two letters, for example MX'], customer: ['Customer', ''], site: ['Site', ''], order_ref: ['Sales order', ''], built_on: ['Built on', ''] };
  const inputs = Object.fromEntries(Object.keys(fields).map(k => [k, h('input', { class: 'input', id: `mb-${k}`, type: k === 'built_on' ? 'date' : 'text', value: k === 'built_on' ? today() : '' })]));
  dialog({ title: 'Record a machine', body: [info, itemPicker(it => { picked = it; info.textContent = `Machine: ${it.part_number} ${it.description}`; if (it.family && !inputs.family.value) inputs.family.value = it.family; }),
    h('div', { class: 'form-grid' }, Object.entries(fields).map(([k, [label, hint]]) => h('div', { class: 'field' }, h('label', { for: `mb-${k}` }, label), inputs[k], hint ? h('span', { class: 'hint' }, hint) : null)))],
  actions: [{ label: 'Cancel' }, { label: 'Record', primary: true, onClick: async () => {
    if (!picked) { toast('Choose the machine first', 'err'); return false; }
    const r = await post('/api/machines', { item_id: picked.id, ...Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])) });
    toast(`Machine ${r.machine.serial} recorded with ${r.machine.rows} BOM rows`, 'ok');
    done();
  } }] });
}

function editMachine(m, done) {
  const keys = { customer: 'Customer', site: 'Site', order_ref: 'Sales order', commissioned_on: 'Commissioned on', note: 'Note' };
  const inputs = Object.fromEntries(Object.keys(keys).map(k => [k, h('input', { class: 'input', id: `me-${k}`, type: k === 'commissioned_on' ? 'date' : 'text', value: m[k] || '' })]));
  dialog({ title: `Machine ${m.serial}`, body: h('div', { class: 'form-grid' }, Object.entries(keys).map(([k, l]) => h('div', { class: 'field' }, h('label', { for: `me-${k}` }, l), inputs[k]))),
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      await patch(`/api/machines/${m.id}`, Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])));
      toast('Saved', 'ok'); done();
    } }] });
}

function withdrawMachine(m, done) {
  const why = h('input', { class: 'input', id: 'mw-why', placeholder: 'Recorded twice, scrapped at the customer …' });
  dialog({ title: `Withdraw machine ${m.serial}`, body: [h('p', { style: { marginTop: 0 } }, 'It leaves the installed base; its record and as-built BOM are kept and can be listed again.'),
    h('div', { class: 'field' }, h('label', { for: 'mw-why' }, 'Why'), why)],
  actions: [{ label: 'Cancel' }, { label: 'Withdraw', danger: true, onClick: async () => {
    await post(`/api/machines/${m.id}/withdraw`, { reason: why.value }); toast('Withdrawn', 'ok'); done();
  } }] });
}
