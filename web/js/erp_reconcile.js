// SPDX-License-Identifier: AGPL-3.0-or-later
// ERP reconciliation: compare Kosha's released items and BOMs with the ERP, list the differences in plain words,
// send fixable ones again, export to Excel. The check only reads; the ERP changes only when someone presses Send again.
import { S, h, mount, icon, get, post, toast, fail, ago, fmtDate } from './core.js';

/** Draws the tab into `box`; returns a function that stops its timers. */
export async function reconcileTab(box, cfg, onChanged) {
  const erp = cfg.connector_name || S.erp || 'the ERP';
  const SCOPE = { item: 'Item', bom: 'BOM', erp };
  const connected = !!cfg.enabled;
  let runId = null;
  let filter = '';
  let timer = null;
  let refreshes = 0;
  let stopped = false;
  const stop = () => { stopped = true; clearTimeout(timer); };

  const label = (checks, code) => (checks[code] || code).replace('the ERP', erp);
  const schedule = (ms) => { clearTimeout(timer); if (!stopped) timer = setTimeout(load, ms); };

  async function load() {
    let d;
    try { d = await get(`/api/erp/reconcile${runId ? `?run=${runId}` : ''}`); } catch (e) { fail(e); return; }
    if (stopped) return;
    draw(d);
    const waitingJobs = d.rows.some(r => r.resend_job_id && ['pending', 'running'].includes(r.job_status));
    if (d.run && d.run.status === 'running') schedule(1000);
    else if (waitingJobs && refreshes++ < 15) schedule(1500);
  }

  async function start(btn) {
    btn.disabled = true;
    try { const d = await post('/api/erp/reconcile'); runId = null; filter = ''; draw(d); schedule(800); } catch (e) { fail(e); btn.disabled = false; }
  }

  function draw(d) {
    const run = d.run;
    const running = run && run.status === 'running';
    const fixableLeft = d.rows.filter(r => r.fix_kind && !r.resend_job_id).length;
    const why = !connected ? `Tick "Connect to ${erp}" under Connection settings first.` : running ? 'A check is running.' : '';
    const startBtn = h('button', { class: 'btn primary', disabled: !!why, title: why || `Read ${erp} and compare. Nothing is changed.`, onclick: e => start(e.currentTarget) },
      icon('refresh', 16), run ? 'Check again' : 'Check now');
    const resendAll = h('button', { class: 'btn', disabled: !run || running || !fixableLeft,
      title: !run ? 'Run a check first.' : running ? 'Wait until the check has finished.' : !fixableLeft ? 'Nothing left that sending again can fix.' : `Queue ${fixableLeft} difference(s) for sending to ${erp}.`,
      onclick: async e => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try { const r = await post(`/api/erp/reconcile/${run.id}/resend`); toast(`${r.queued} transfer(s) queued for ${erp}`, 'ok'); refreshes = 0; draw(r); onChanged(); schedule(1500); } catch (x) { fail(x); btn.disabled = false; }
      } }, icon('play', 16), `Send all fixable again${fixableLeft ? ` (${fixableLeft})` : ''}`);
    const exportBtn = run && !running
      ? h('a', { class: 'btn', href: `/api/erp/reconcile/${run.id}/export?access_token=${encodeURIComponent(S.token)}`, download: '' }, icon('download', 16), 'Export to Excel')
      : h('button', { class: 'btn', disabled: true, title: run ? 'Wait until the check has finished.' : 'Run a check first.' }, icon('download', 16), 'Export to Excel');
    const stopBtn = running ? h('button', { class: 'btn', onclick: async () => { try { draw(await post(`/api/erp/reconcile/${run.id}/cancel`)); } catch (x) { fail(x); } } }, 'Stop') : null;
    const earlier = d.runs.length > 1 ? h('select', { class: 'input', style: { width: 'auto', minHeight: '32px' }, 'aria-label': 'Show an earlier check',
      onchange: e => { runId = Number(e.target.value); filter = ''; load(); } },
    d.runs.map(r => h('option', { value: r.id, selected: run && r.id === run.id }, `Check #${r.id} · ${fmtDate(r.started_at)} · ${r.status === 'done' ? `${r.differences} difference(s)` : r.status}`))) : null;

    const parts = [
      h('div', { class: 'banner info small' }, icon('info'), h('div', null,
        `Compares Kosha's released items and BOMs with what ${erp} holds: missing items, description, unit, revision and material, BOM lines and quantities, and items ${erp} marks as coming from Kosha that Kosha no longer has. `,
        h('b', null, `The check only reads: nothing changes in ${erp}`), ' unless you press Send again.')),
      cfg.dry_run && connected ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, 'Dry run is on: Send again prepares the data but sends nothing. Switch it off under Connection settings to send for real.')) : null,
      h('div', { class: 'row wrap' }, startBtn, stopBtn, resendAll, exportBtn, h('span', { class: 'spacer' }), earlier),
    ];
    if (!run) {
      parts.push(h('div', { class: 'empty' }, connected ? `No check yet. Press "Check now" to compare Kosha with ${erp}.` : `Connect to ${erp} first, then run a check.`));
      mount(box, h('div', { class: 'col', style: { gap: '14px' } }, parts));
      return;
    }
    if (running) {
      const pct = run.total ? Math.round(100 * Math.min(run.done, run.total) / run.total) : 0;
      parts.push(h('div', { class: 'col', style: { gap: '6px' } }, h('div', { class: 'small' }, `${run.phase || 'Working'}… ${run.done} of ${run.total || '?'}`),
        h('div', { class: 'progress', role: 'progressbar', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('div', { style: { width: `${pct}%` } }))));
    } else {
      const kind = run.status === 'done' ? (run.differences ? 'info' : 'ok') : 'err';
      parts.push(h('div', { class: `banner ${kind} small` }, icon(kind === 'err' ? 'warn' : kind === 'ok' ? 'check' : 'info'), h('div', null,
        h('b', null, run.message || run.status), ' ',
        h('span', { class: 'muted', title: fmtDate(run.started_at) }, `Check #${run.id}, ${ago(run.finished_at || run.started_at)}: ${run.items_checked} items and ${run.boms_checked} BOMs compared with ${run.connector_name}.`),
        (run.notes || '').split('\n').filter(Boolean).map(n => h('div', { class: 'muted' }, n)))));
    }
    const counts = {};
    for (const r of d.rows) counts[r.check_code] = (counts[r.check_code] || 0) + 1;
    if (d.rows.length) {
      parts.push(h('div', { class: 'row wrap filter-chips', role: 'group', 'aria-label': 'Show only' },
        h('button', { class: 'chip', 'aria-pressed': String(!filter), onclick: () => { filter = ''; draw(d); } }, `All ${d.rows.length}`),
        Object.keys(d.checks).filter(c => counts[c]).map(c => h('button', { class: 'chip', 'aria-pressed': String(filter === c), onclick: () => { filter = filter === c ? '' : c; draw(d); } }, `${label(d.checks, c)} ${counts[c]}`))));
      parts.push(table(d, run, filter ? d.rows.filter(r => r.check_code === filter) : d.rows));
    }
    mount(box, h('div', { class: 'col', style: { gap: '14px' } }, parts));
  }

  function table(d, run, rows) {
    const action = (r) => {
      if (r.resend_job_id) {
        return h('div', { class: 'small' }, h('span', { class: `chip st-${r.job_status || 'pending'}` }, (r.job_status || 'queued').replace('_', ' ')),
          ' ', h('a', { href: '#/erp', title: r.job_error || '' }, `job #${r.resend_job_id}`),
          r.job_status === 'failed' && r.job_error ? h('div', { class: 'sev-error' }, r.job_error.slice(0, 160)) : null);
      }
      if (!r.fix_kind) return h('span', { class: 'small muted' }, `By hand in ${erp}`);
      return h('button', { class: 'btn sm', disabled: run.status === 'running', title: run.status === 'running' ? 'Wait until the check has finished.' : `Queue a transfer of this ${r.fix_kind === 'bom' ? 'BOM' : 'item'} to ${erp}.`,
        onclick: async e => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try { const res = await post(`/api/erp/reconcile/rows/${r.id}/resend`); toast(`Queued: job #${res.job.id}`, 'ok'); refreshes = 0; draw(res); onChanged(); schedule(1500); } catch (x) { fail(x); btn.disabled = false; }
        } }, icon('play', 14), 'Send again');
    };
    return h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Part', 'Where', 'Difference', 'In Kosha', `In ${erp}`, 'What to do', ''].map(x => h('th', null, x)))),
      h('tbody', null, rows.map(r => h('tr', null,
        h('td', { class: 'mono nowrap' }, r.item_id ? h('a', { href: `#/item/${r.item_id}` }, r.part_number) : r.part_number),
        h('td', { class: 'small' }, SCOPE[r.scope] || r.scope),
        h('td', null, h('b', null, label(d.checks, r.check_code))),
        h('td', { class: 'small' }, r.kosha_value || '—'),
        h('td', { class: 'small' }, r.erp_value || '—'),
        h('td', { class: 'small', style: { maxWidth: '420px' } }, h('div', null, r.message), r.advice ? h('div', { class: 'muted' }, r.advice) : null),
        h('td', { class: 'nowrap' }, action(r)))))));
  }

  await load();
  return stop;
}
