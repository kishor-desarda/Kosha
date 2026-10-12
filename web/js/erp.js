// SPDX-License-Identifier: AGPL-3.0-or-later
// ERP integration: the transfer queue and the connection settings of the ERP connector add-in.
import { S, h, mount, icon, get, post, put, toast, fail, ago, fmtDate, can, tabs } from './core.js';
import { schemaForm } from './addins.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

export async function renderErp(container, _m, params, { setTitle }) {
  setTitle('ERP sync');
  let tab = params.tab || 'queue';
  let status = '';
  const draw = async () => {
    const [jobs, cfg] = await Promise.all([get(`/api/erp/jobs${status ? `?status=${status}` : ''}`), can('approver') ? get('/api/erp/settings') : Promise.resolve(null)]);
    const live = cfg && cfg.enabled && !cfg.dry_run;
    const head = h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'ERP sync'),
      h('div', { class: 'sub' }, `Released items and BOMs are sent to ${S.erp}. Stock and valuation come back.`)),
    h('div', { class: 'row' },
      cfg ? h('span', { class: `chip ${live ? 'st-sent' : 'st-dry_run'}` }, live ? `Live: ${cfg.base_url}` : cfg.enabled ? 'Dry run (nothing is sent)' : 'Switched off — dry run') : null,
      can('approver') ? h('button', { class: 'btn', onclick: async e => { e.target.disabled = true; try { const r = await post('/api/erp/process'); toast(`Processed ${r.processed} waiting job(s)`, 'ok'); changed(); draw(); } catch (x) { fail(x); } } }, icon('play', 16), 'Send waiting now') : null));
    const kpis = h('div', { class: 'kpis', style: { marginBottom: '16px' } },
      [['pending', 'Waiting'], ['sent', 'Sent'], ['failed', 'Failed'], ['dry_run', 'Dry run']].map(([k, l]) =>
        h('button', { class: 'kpi', style: { textAlign: 'left', cursor: 'pointer', outline: status === k ? '2px solid var(--blue)' : '' }, onclick: () => { status = status === k ? '' : k; draw(); } },
          h('b', null, jobs.counts[k] || 0), h('span', null, l))));
    const body = h('div', { class: 'panel' });
    const t = tabs([['queue', 'Queue'], ...(can('approver') ? [['settings', 'Connection settings']] : [])], tab, k => { tab = k; draw(); });
    body.appendChild(t);
    if (tab === 'queue') body.appendChild(queue(jobs.jobs, draw));
    else body.appendChild(settings(cfg, draw));
    mount(container, h('div', { class: 'page-pad' }, head, kpis, body));
  };
  await draw();
}

function queue(jobs, draw) {
  const tbody = h('tbody');
  for (const j of jobs) {
    const detail = h('tr', { hidden: true }, h('td', { colspan: 7, style: { background: 'var(--panel-2)', padding: '10px 14px' } },
      h('div', { class: 'small' }, h('b', null, 'Response: '), j.response_text || '—'),
      j.error ? h('div', { class: 'small sev-error' }, h('b', null, 'Error: '), j.error) : null,
      j.payload_json ? h('pre', { class: 'mono small', style: { whiteSpace: 'pre-wrap', maxHeight: '220px', overflow: 'auto', background: '#fff', border: '1px solid var(--line)', padding: '8px', borderRadius: '6px' } }, JSON.stringify(JSON.parse(j.payload_json), null, 2)) : null));
    const row = h('tr', { class: 'clickable', onclick: () => { detail.hidden = !detail.hidden; } },
      h('td', { class: 'mono' }, `#${j.id}`), h('td', null, j.kind === 'bom' ? 'BOM' : j.kind === 'refresh' ? 'Stock & cost' : 'Item'), h('td', null, j.label),
      h('td', null, h('span', { class: `chip st-${j.status}` }, j.status.replace('_', ' '))),
      h('td', { class: 'small' }, j.error ? h('span', { class: 'sev-error' }, j.error.slice(0, 120)) : (j.response_text || '').slice(0, 80)),
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
  uom.value = Object.entries(cfg.uom_map || {}).map(([k, v]) => `${k}=${v}`).join('\n');
  const conn = schemaForm(cfg.connector_fields || [], cfg, admin);
  const result = h('div');
  const name = cfg.connector_name || 'the ERP';
  return h('div', { class: 'panel-b col', style: { gap: '18px', maxWidth: '980px' } },
    !admin ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Only administrators can change these settings.')) : null,
    !cfg.connector ? h('div', { class: 'banner err small' }, icon('warn'), h('div', null, cfg.connector_help || 'No ERP connector add-in is installed.')) : null,
    cfg.connector && cfg.connector_status !== 'enabled' ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, `The ${name} add-in is switched off. Switch it on under Admin › Add-ins.`)) : null,
    h('div', { class: 'col' }, box('enabled', `Connect to ${name}`), box('dry_run', 'Dry run: prepare the data but do not send it (safe for testing)'),
      box('auto_push_on_release', 'Send automatically when an item or BOM is released')),
    cfg.connector ? h('div', null, h('h3', { style: { marginBottom: '8px' } }, `${name} connection`),
      cfg.connector_help ? h('div', { class: 'banner info small', style: { marginBottom: '12px' } }, icon('info'), h('div', null, cfg.connector_help)) : null,
      conn.el) : null,
    h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'erp-default-uom' }, 'Default unit'), defUom, h('span', { class: 'hint' }, 'Used when an item has no unit, e.g. Nos'))),
    h('div', { class: 'field' }, h('label', { for: 'erp-uom' }, `Unit mapping (Kosha unit = ${name} unit, one per line)`), uom),
    admin ? h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: async () => {
        const body = { ...conn.values(), default_uom: defUom.value.trim() };
        for (const [k, el] of Object.entries(f)) body[k] = el.checked;
        body.uom_map = Object.fromEntries(uom.value.split('\n').map(l => l.split('=').map(s => s.trim())).filter(p => p.length === 2 && p[0]));
        try { await put('/api/erp/settings', body); toast('ERP settings saved', 'ok'); draw(); } catch (e) { fail(e); }
      } }, 'Save settings'),
      h('button', { class: 'btn', onclick: async () => {
        try { const r = await post('/api/erp/test'); mount(result, h('div', { class: `banner ${r.ok ? 'ok' : 'err'} small` }, icon(r.ok ? 'check' : 'warn'), h('div', null, r.message))); } catch (e) { fail(e); }
      } }, 'Test connection')) : null,
    result);
}
