// SPDX-License-Identifier: AGPL-3.0-or-later
// ERP integration: the transfer queue, orders from the ERP, the category mapping and the connection settings of the
// ERP connector add-in.
import { S, h, mount, icon, get, post, put, toast, fail, ago, fmtDate, can, tabs } from './core.js';
import { schemaForm } from './addins.js';
import { reconcileTab } from './erp_reconcile.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

export async function renderErp(container, _m, params, { setTitle }) {
  setTitle('ERP sync');
  let tab = params.tab || 'queue';
  let status = '';
  let stopTab = null;
  const draw = async () => {
    if (stopTab) { stopTab(); stopTab = null; }
    const [jobs, cfg] = await Promise.all([get(`/api/erp/jobs${status ? `?status=${status}` : ''}`), can('approver') ? get('/api/erp/settings') : Promise.resolve(null)]);
    if (!cfg && tab !== 'orders') tab = 'queue';
    const live = cfg && cfg.enabled && !cfg.dry_run;
    const head = h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'ERP sync'),
      h('div', { class: 'sub' }, `Released items and BOMs are sent to ${S.erp}. Stock and valuation come back.`)),
    h('div', { class: 'row' },
      cfg ? h('span', { class: `chip ${live ? 'st-sent' : 'st-dry_run'}` }, live ? `Live: ${cfg.base_url || cfg.connector_name || 'connected'}` : cfg.enabled ? 'Dry run (nothing is sent)' : 'Switched off — dry run') : null,
      can('approver') ? h('button', { class: 'btn', onclick: async e => { e.target.disabled = true; try { const r = await post('/api/erp/process'); toast(`Processed ${r.processed} waiting job(s)`, 'ok'); changed(); draw(); } catch (x) { fail(x); } } }, icon('play', 16), 'Send waiting now') : null));
    const kpis = h('div', { class: 'kpis', style: { marginBottom: '16px' } },
      [['pending', 'Waiting'], ['sent', 'Sent'], ['failed', 'Failed'], ['dry_run', 'Dry run']].map(([k, l]) =>
        h('button', { class: 'kpi', style: { textAlign: 'left', cursor: 'pointer', outline: status === k ? '2px solid var(--blue)' : '' }, onclick: () => { status = status === k ? '' : k; draw(); } },
          h('b', null, jobs.counts[k] || 0), h('span', null, l))));
    const body = h('div', { class: 'panel' });
    const t = tabs([['queue', 'Queue'], ['orders', 'Orders'], ...(can('approver') ? [['reconcile', 'Reconciliation'], ['mapping', 'Mapping'], ['settings', 'Connection settings']] : [])], tab, k => { tab = k; draw(); });
    body.appendChild(t);
    if (tab === 'orders' || (tab === 'mapping' && cfg)) {
      const box = h('div', { class: 'panel-b' }, h('div', { class: 'muted' }, 'Loading…'));
      body.appendChild(box);
      mount(container, h('div', { class: 'page-pad' }, head, kpis, body));
      if (tab === 'orders') await ordersTab(box, cfg); else await mappingTab(box, cfg, draw);
      return;
    }
    if (tab === 'reconcile' && cfg) {
      const box = h('div', { class: 'panel-b' });
      body.appendChild(box);
      mount(container, h('div', { class: 'page-pad' }, head, kpis, body));
      stopTab = await reconcileTab(box, cfg, changed);
      return;
    }
    if (tab === 'settings' && cfg) body.appendChild(settings(cfg, draw));
    else body.appendChild(queue(jobs.jobs, draw));
    mount(container, h('div', { class: 'page-pad' }, head, kpis, body));
  };
  await draw();
  return () => { if (stopTab) stopTab(); };
}

const KIND_LABEL = { item: 'Item', bom: 'BOM', mbom: 'Manufacturing BOM', refresh: 'Stock & cost', attach: 'Drawing PDF' };

function queue(jobs, draw) {
  const tbody = h('tbody');
  for (const j of jobs) {
    const detail = h('tr', { hidden: true }, h('td', { colspan: 7, style: { background: 'var(--panel-2)', padding: '10px 14px' } },
      h('div', { class: 'small' }, h('b', null, 'Response: '), j.response_text || '—'),
      j.error ? h('div', { class: 'small sev-error' }, h('b', null, 'Error: '), j.error) : null,
      j.payload_json ? h('pre', { class: 'mono small', style: { whiteSpace: 'pre-wrap', maxHeight: '220px', overflow: 'auto', background: 'var(--field)', border: '1px solid var(--line)', padding: '8px', borderRadius: '6px' } }, JSON.stringify(JSON.parse(j.payload_json), null, 2)) : null));
    const row = h('tr', { class: 'clickable', onclick: () => { detail.hidden = !detail.hidden; } },
      h('td', { class: 'mono' }, `#${j.id}`), h('td', null, KIND_LABEL[j.kind] || j.kind), h('td', null, j.label),
      h('td', null, h('span', { class: `chip st-${j.status}` }, j.status.replace('_', ' '))),
      h('td', { class: 'small' }, j.error ? h('span', { class: 'sev-error' }, j.error.slice(0, 120)) : (j.response_text || '').slice(0, 80),
        j.status === 'failed' ? h('div', { class: 'muted' }, j.next_try_at ? `Kosha tries again by itself ${fmtDate(j.next_try_at)}.` : 'Kosha stopped trying by itself. Fix the cause, then press Retry.') : null),
      h('td', { class: 'muted nowrap', title: fmtDate(j.updated_at) }, ago(j.updated_at), j.trigger_ref ? ` · ${j.trigger_ref}` : ''),
      h('td', null, j.status === 'failed' && can('approver') ? h('button', { class: 'btn sm', onclick: async e => {
        e.stopPropagation();
        try { const r = await post(`/api/erp/jobs/${j.id}/retry`); toast(r.job.status === 'failed' ? `Still failing: ${r.job.error}` : 'Sent', r.job.status === 'failed' ? 'err' : 'ok'); changed(); draw(); } catch (x) { fail(x); }
      } }, icon('refresh', 14), 'Retry') : null));
    tbody.append(row, detail);
  }
  if (!jobs.length) tbody.appendChild(h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, 'No transfers yet. They appear here when files or BOMs are released.'))));
  return h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['Job', 'What', 'Part', 'Status', 'Result', 'When', ''].map(x => h('th', null, x)))), tbody));
}

function settings(cfg, draw) {
  const admin = S.user.role === 'admin';
  const f = {};
  const box = (k, label) => { f[k] = h('input', { type: 'checkbox', checked: !!cfg[k], disabled: !admin }); return h('label', { class: 'check' }, f[k], label); };
  const defUom = h('input', { class: 'input', id: 'erp-default-uom', value: cfg.default_uom || '', disabled: !admin });
  const uom = h('textarea', { class: 'input mono', id: 'erp-uom', disabled: !admin, style: { minHeight: '110px' } });
  const bomSource = h('select', { class: 'input', id: 'erp-bom-source', disabled: !admin, title: admin ? '' : 'Only administrators can change this.' },
    [['ebom', 'Engineering BOMs'], ['mbom', 'Manufacturing BOMs (with operations)']].map(([v, l]) => h('option', { value: v, selected: (cfg.bom_source || 'ebom') === v }, l)));
  uom.value = Object.entries(cfg.uom_map || {}).map(([k, v]) => `${k}=${v}`).join('\n');
  const conn = schemaForm(cfg.connector_fields || [], cfg, admin);
  const result = h('div');
  const name = cfg.connector_name || 'the ERP';
  const choices = cfg.connectors || [];
  const chooser = choices.length > 1 ? h('div', { class: 'field', style: { maxWidth: '420px' } },
    h('label', { for: 'erp-connector' }, 'ERP system'),
    h('select', { class: 'input', id: 'erp-connector', disabled: !admin, title: admin ? '' : 'Only administrators can change this.',
      onchange: async e => {
        const pick = choices.find(c => c.id === e.target.value);
        try {
          await put('/api/erp/settings', { connector: e.target.value });
          toast(`Kosha now talks to ${pick ? pick.name.replace(' connector', '') : 'the chosen ERP'}. Check its connection settings below.`, 'ok');
          S.erp = pick ? pick.name.replace(' connector', '') : S.erp;
          draw();
        } catch (x) { fail(x); }
      } },
    choices.map(c => h('option', { value: c.id, selected: c.id === cfg.connector }, `${c.name.replace(' connector', '')}${c.status === 'enabled' ? '' : ' (switched off)'}`))),
    h('span', { class: 'hint' }, 'Each ERP has its own connector add-in and its own connection settings. Queued transfers go to the ERP chosen here.')) : null;
  return h('div', { class: 'panel-b col', style: { gap: '18px', maxWidth: '980px' } },
    !admin ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Only administrators can change these settings.')) : null,
    chooser,
    !cfg.connector ? h('div', { class: 'banner err small' }, icon('warn'), h('div', null, cfg.connector_help || 'No ERP connector add-in is installed.')) : null,
    cfg.connector && cfg.connector_status !== 'enabled' ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, `The ${name} add-in is switched off. Switch it on under Admin › Add-ins.`)) : null,
    h('div', { class: 'col' }, box('enabled', `Connect to ${name}`), box('dry_run', 'Dry run: prepare the data but do not send it (safe for testing)'),
      box('auto_push_on_release', 'Send automatically when an item or BOM is released')),
    h('div', { class: 'field', style: { maxWidth: '520px' } }, h('label', { for: 'erp-bom-source' }, `${name} builds from`), bomSource,
      h('span', { class: 'hint' }, `With manufacturing BOMs, ${name} gets each assembly's released manufacturing BOM with its operations instead of the engineering BOM.`)),
    cfg.connector ? h('div', null, h('h3', { style: { marginBottom: '8px' } }, `${name} connection`),
      cfg.connector_help ? h('div', { class: 'banner info small', style: { marginBottom: '12px' } }, icon('info'), h('div', null, cfg.connector_help)) : null,
      conn.el) : null,
    h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'erp-default-uom' }, 'Default unit'), defUom, h('span', { class: 'hint' }, 'Used when an item has no unit, e.g. Nos'))),
    h('div', { class: 'field' }, h('label', { for: 'erp-uom' }, `Unit mapping (Kosha unit = ${name} unit, one per line)`), uom),
    admin ? h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: async () => {
        const body = { ...conn.values(), default_uom: defUom.value.trim(), bom_source: bomSource.value };
        for (const [k, el] of Object.entries(f)) body[k] = el.checked;
        body.uom_map = Object.fromEntries(uom.value.split('\n').map(l => l.split('=').map(s => s.trim())).filter(p => p.length === 2 && p[0]));
        try { await put('/api/erp/settings', body); toast('ERP settings saved', 'ok'); draw(); } catch (e) { fail(e); }
      } }, 'Save settings'),
      h('button', { class: 'btn', onclick: async () => {
        try { const r = await post('/api/erp/test'); mount(result, h('div', { class: `banner ${r.ok ? 'ok' : 'err'} small` }, icon(r.ok ? 'check' : 'warn'), h('div', null, r.message))); } catch (e) { fail(e); }
      } }, 'Test connection')) : null,
    result);
}

// Purchase orders, RFQs and subcontracting orders the ERP announced: the revision of each part when the order came in,
// and the vendor package for it in one click.
async function ordersTab(box, cfg) {
  const r = await get('/api/erp/orders');
  const name = (cfg && cfg.connector_name) || S.erp || 'the ERP';
  if (!r.orders.length) {
    mount(box, h('div', { class: 'empty' }, `No orders yet. When a purchase order, RFQ or subcontracting order for your parts is submitted in ${name}, it appears here with the revision of each part.`));
    return;
  }
  const engineer = can('engineer');
  const rows = r.orders.map(o => {
    const sendable = o.lines.some(l => l.item_id && l.revision);
    const why = !engineer ? 'Only engineers and approvers send vendor packages.' : o.status !== 'open' ? `This order was cancelled in ${name}.` : !sendable ? 'None of its parts has a released drawing in Kosha.' : '';
    return h('tr', null,
      h('td', { class: 'mono' }, o.number), h('td', null, o.kind_label), h('td', null, o.party || '—'),
      h('td', null, h('div', { class: 'col', style: { gap: '2px' } }, o.lines.map(l => h('div', { class: 'small' },
        h('span', { class: 'mono' }, l.code), l.revision ? ` Rev ${l.revision}` : '', l.note ? h('span', { class: 'muted' }, ` · ${l.note}`) : null)))),
      h('td', null, h('span', { class: `chip st-${o.status === 'open' ? 'sent' : 'cancelled'}` }, o.status === 'open' ? 'Open' : 'Cancelled')),
      h('td', { class: 'muted nowrap', title: fmtDate(o.received_at) }, ago(o.received_at)),
      h('td', null, o.transmittal_number ? h('a', { href: '#/transmittals' }, o.transmittal_number) :
        h('button', { class: 'btn sm', disabled: !!why, title: why || 'Send the released drawings to the supplier through a share link that ends and can be withdrawn', onclick: async e => {
          e.target.disabled = true;
          try { const x = await post(`/api/erp/orders/${o.id}/package`); toast(`Vendor package ${x.transmittal.number} made. Copy its link from Transmittals.`, 'ok'); await ordersTab(box, cfg); } catch (err) { fail(err); e.target.disabled = false; }
        } }, 'Make vendor package')));
  });
  mount(box, h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['Order', 'Kind', 'Supplier', 'Parts', 'Status', 'Received', ''].map(x => h('th', null, x)))),
    h('tbody', null, rows))));
}

// Category → item group and HSN code (the pack proposes; the admin decides), the unit mapping, and what is sent.
async function mappingTab(box, cfg, draw) {
  const m = await get('/api/erp/mapping');
  const admin = S.user.role === 'admin';
  const why = admin ? '' : 'Only administrators can change the mapping.';
  const name = cfg.connector_name || 'the ERP';
  const inputs = {};
  const rows = m.categories.map(c => {
    const group = h('input', { class: 'input', value: c.item_group || '', placeholder: c.pack_item_group || 'Item group', disabled: !admin, title: why, 'aria-label': `Item group for ${c.code}` });
    const hsn = h('input', { class: 'input mono', value: c.hsn || '', placeholder: c.pack_hsn || 'e.g. 84833000', disabled: !admin, title: why, inputmode: 'numeric', 'aria-label': `HSN code for ${c.code}` });
    inputs[c.code] = { group, hsn };
    return h('tr', null, h('td', { class: 'mono' }, c.code), h('td', null, c.name), h('td', null, group), h('td', null, hsn));
  });
  const uom = h('textarea', { class: 'input mono', disabled: !admin, title: why, style: { minHeight: '110px' }, 'aria-label': 'Unit mapping' });
  uom.value = Object.entries(m.uom_map || {}).map(([k, v]) => `${k}=${v}`).join('\n');
  const attach = h('input', { type: 'checkbox', checked: !!m.attach_pdfs, disabled: !admin, title: why });
  const nightly = h('input', { type: 'checkbox', checked: !!m.nightly_reconcile, disabled: !admin, title: why });
  mount(box, h('div', { class: 'col', style: { gap: '18px', maxWidth: '980px' } },
    h('div', { class: 'banner info small' }, icon('info'), h('div', null, `What Kosha sends to ${name} for each category. An empty box uses what the industry pack proposes (shown in grey). The data steward sets HSN codes with accounts.`)),
    rows.length ? h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Category', 'Name', `${name} item group`, 'HSN code'].map(x => h('th', null, x)))), h('tbody', null, rows)))
      : h('div', { class: 'empty' }, 'No categories yet. Install an industry pack under Admin › Packs to get them.'),
    h('div', { class: 'field' }, h('label', null, `Unit mapping (Kosha unit = ${name} unit, one per line)`), uom),
    h('div', { class: 'col' },
      h('label', { class: 'check' }, attach, `Attach the stamped PDF of each released drawing to its ${name} item (earlier revisions are taken off)`),
      h('label', { class: 'check' }, nightly, `Compare Kosha with ${name} every night and show differences on the health page`)),
    admin ? h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: async () => {
      const category_map = Object.fromEntries(Object.entries(inputs).map(([k, x]) => [k, { item_group: x.group.value.trim(), hsn: x.hsn.value.trim() }]));
      const uom_map = Object.fromEntries(uom.value.split('\n').map(l => l.split('=').map(s => s.trim())).filter(p => p.length === 2 && p[0]));
      try { await put('/api/erp/settings', { category_map, uom_map, attach_pdfs: attach.checked, nightly_reconcile: nightly.checked }); toast('Mapping saved', 'ok'); draw(); } catch (e) { fail(e); }
    } }, 'Save mapping')) : h('div', { class: 'muted small' }, why)));
}
