// SPDX-License-Identifier: AGPL-3.0-or-later
// One legacy import batch (specification 11): the count check, the work queues on an Excel-like review grid, the
// Excel round-trip, the sample check, sign-off and Undo batch.
import { S, h, mount, icon, get, post, api, put, upload, toast, fail, can, fileUrl, dialog, tabs, busy } from './core.js';

const QUEUES = [['review', 'Review'], ['attention', 'Attention'], ['auto', 'Auto'], ['', 'All']];
const QUEUE_CHIP = { auto: 'st-done', review: 'st-pending', attention: 'st-failed' };
const PAGE = 50;

function countCheck(rec, job, reload) {
  const admin = can('admin');
  const parts = Object.entries(rec.by_status).map(([k, n]) => `${n} ${k}`).join(' + ');
  const why = rec.blockers.join(' ');
  const act = (label, fn, opts = {}) => h('button', { class: `btn sm ${opts.primary ? 'primary' : ''} ${opts.danger ? 'danger' : ''}`,
    disabled: !admin || opts.disabled, title: !admin ? 'Only an admin can do this.' : (opts.disabled ? opts.why : opts.title || ''),
    onclick: e => busy(e.currentTarget, fn) }, label);
  return h('section', { class: 'panel', id: 'legacy-check' },
    h('div', { class: 'panel-h' }, h('h2', null, 'Count check and sign-off')),
    h('div', { class: 'panel-b col', style: { gap: '10px' } },
      h('div', { class: `banner ${rec.adds_up ? 'ok' : 'err'}` }, icon(rec.adds_up ? 'check' : 'warn'), h('div', null,
        h('b', null, rec.adds_up ? 'Every file is accounted for. ' : 'The numbers do not add up. '),
        `${rec.found} files found = ${parts || 'nothing yet'}.`)),
      h('div', { class: 'small' },
        rec.verify ? `Stored files read back: ${rec.verify.checked}, ${rec.verify.bad.length ? `${rec.verify.bad.length} wrong` : 'all correct'}.` : 'Stored files not read back yet.',
        ' ',
        rec.sample.size ? `Sample: ${rec.sample.checked} of ${rec.sample.size} checked${rec.sample.accuracy != null ? `, ${rec.sample.accuracy} % right` : ''}.` : 'No sample drawn yet.'),
      job.signed_off_at ? h('div', { class: 'banner ok' }, icon('check'), h('div', null, `Signed off on ${job.signed_off_at.slice(0, 10)}.`)) : null,
      job.undone_at ? h('div', { class: 'banner info' }, icon('info'), h('div', null, `This batch was undone on ${job.undone_at.slice(0, 10)}.`)) : null,
      !job.signed_off_at && !job.undone_at && rec.blockers.length ? h('ul', { class: 'small muted', style: { margin: 0 } }, rec.blockers.map(b => h('li', null, b))) : null,
      job.undone_at || job.signed_off_at ? null : h('div', { class: 'row wrap' },
        act('Read back stored files', async () => { const r = await post(`/api/import/jobs/${job.id}/verify`); toast(r.bad.length ? `${r.bad.length} files do not read back correctly` : `All ${r.checked} files read back correctly`, r.bad.length ? 'err' : 'ok'); reload(); },
          { title: 'Read every stored file of this batch and compare it with its fingerprint' }),
        act('Draw a sample to check', async () => { await post(`/api/import/jobs/${job.id}/sample`); toast('Sample drawn: check the rows marked Sample', 'ok'); reload({ sample: true }); },
          { title: '2 % of the imported files (at least 5) for an engineer to open and check' }),
        act('Sign off batch', async () => { await post(`/api/import/jobs/${job.id}/sign-off`); toast('Batch signed off', 'ok'); reload(); },
          { primary: true, disabled: !rec.can_sign_off, why: why || 'Already signed off.' }),
        act('Undo batch…', () => undoDialog(job, reload), { danger: true, title: 'Take back what this batch made, as long as nobody used it' }))));
}

async function undoDialog(job, reload) {
  const plan = await get(`/api/import/jobs/${job.id}/undo`);
  dialog({
    title: `Undo batch ${job.id}`,
    wide: true,
    body: h('div', { class: 'col', style: { gap: '10px' } },
      h('p', { style: { margin: 0 } }, `${plan.remove.length} files go to the recycle bin (they can be put back). The parts and folders this batch created go too, when nothing else uses them.`),
      plan.keep.length ? h('div', null, h('b', null, `${plan.keep.length} files stay, because they were used since:`),
        h('ul', { class: 'small' }, plan.keep.slice(0, 30).map(k => h('li', null, `${k.filename}: ${k.why}`)))) : null),
    actions: [{ label: 'Cancel' }, { label: `Undo batch (${plan.remove.length} files)`, danger: true, onClick: async () => {
      const r = await post(`/api/import/jobs/${job.id}/undo`);
      toast(`Undone: ${r.summary}`, 'ok'); reload();
    } }],
  });
}

function excelBar(job, queue, reload) {
  const file = h('input', { type: 'file', accept: '.xlsx', id: 'legacy-xlsx', class: 'sr-only' });
  const out = h('div');
  file.addEventListener('change', async () => {
    if (!file.files[0]) return;
    const fd = new FormData();
    fd.append('file', file.files[0]);
    try {
      const r = await upload(`/api/import/jobs/${job.id}/records.xlsx`, fd);
      if (r.problems.length) {
        mount(out, h('div', { class: 'banner err' }, icon('warn'), h('div', null, h('b', null, 'Nothing was changed. Fix these rows and import the file again:'),
          h('ul', { class: 'small', style: { margin: '4px 0 0' } }, r.problems.slice(0, 20).map(p => h('li', null, `Row ${p.row}: ${p.message}`))))));
      } else { mount(out); toast(`${r.applied} rows corrected, ${r.confirmed} confirmed`, 'ok'); reload(); }
    } catch (e) { fail(e); }
    file.value = '';
  });
  return h('div', { class: 'col', style: { gap: '8px' } }, h('div', { class: 'row wrap' },
    h('a', { class: 'btn sm', href: `/api/import/jobs/${job.id}/records.xlsx?queue=${queue}&access_token=${encodeURIComponent(S.token)}` }, icon('download', 14), 'Export this queue to Excel'),
    h('label', { class: 'btn sm', for: 'legacy-xlsx', title: 'Bring back a corrected export; every row is checked before anything changes' }, icon('upload', 14), 'Import corrections from Excel…', file)), out);
}

function grid(data, job, reload) {
  const closed = Boolean(job.undone_at);
  const sel = new Set();
  const selCount = h('span', null, '0');
  const field = h('select', { class: 'input', 'aria-label': 'Field to set' }, h('option', { value: '' }, 'Only confirm'), h('option', { value: 'description' }, 'Description'), h('option', { value: 'material' }, 'Material'));
  const value = h('input', { class: 'input', placeholder: 'New value for the selected rows', 'aria-label': 'Value' });
  const bulkBtn = h('button', { class: 'btn primary sm', disabled: true, title: 'Select rows first',
    onclick: e => busy(e.currentTarget, async () => {
      if (field.value && !value.value.trim()) { toast('Type the value to set first.', 'err'); return; }
      const r = await post(`/api/import/jobs/${job.id}/records/confirm`, { ids: [...sel], field: field.value || undefined, value: value.value });
      toast(`${r.confirmed} rows confirmed`, 'ok'); reload();
    }) }, icon('check', 14), 'Confirm selected (', selCount, ')');
  const tick = () => { selCount.textContent = sel.size; bulkBtn.disabled = closed || !sel.size; bulkBtn.title = closed ? 'This batch was undone.' : (sel.size ? '' : 'Select rows first'); };
  const boxes = [];
  const rows = data.records.map(r => {
    const imported = r.status === 'imported';
    const inputs = {};
    const cell = (k, w) => {
      const el = h('input', { class: `input ${k === 'part_number' ? 'mono' : ''}`, value: r[k] || '', 'aria-label': `${k.replace('_', ' ')} of ${r.source_path}`, disabled: closed || !imported || !r.document_id, style: { width: w } });
      inputs[k] = el;
      return h('td', null, el);
    };
    const box = h('input', { type: 'checkbox', 'aria-label': 'Select row', disabled: closed || !imported, onchange: e => { e.target.checked ? sel.add(r.id) : sel.delete(r.id); tick(); } });
    if (imported) boxes.push([box, r.id]);
    const save = h('button', { class: 'btn sm', disabled: closed || !imported, title: closed ? 'This batch was undone.' : (imported ? 'Save the corrections on this row and mark it right' : 'Only imported files are confirmed'),
      onclick: e => busy(e.currentTarget, async () => {
        const body = { confirm: true };
        for (const [k, el] of Object.entries(inputs)) if (el.value.trim() !== (r[k] || '')) body[k] = el.value;
        await api('PATCH', `/api/import/records/${r.id}`, body); toast('Saved and confirmed', 'ok'); reload();
      }) }, r.confirmed_at ? 'Save' : 'Confirm');
    const sample = r.sample_result === 'pending'
      ? h('div', { class: 'row' }, h('span', { class: 'chip st-pending' }, 'Sample'),
        h('button', { class: 'btn sm', title: 'Opened and checked: number, description, material and revision are right', onclick: async () => { try { await put(`/api/import/records/${r.id}/sample`, { result: 'ok' }); reload(); } catch (e) { fail(e); } } }, 'Right'),
        h('button', { class: 'btn sm danger', onclick: async () => { try { await put(`/api/import/records/${r.id}/sample`, { result: 'wrong' }); reload(); } catch (e) { fail(e); } } }, 'Wrong'))
      : r.sample_result ? h('span', { class: `chip ${r.sample_result === 'ok' ? 'st-done' : 'st-failed'}` }, r.sample_result === 'ok' ? 'Sample: right' : 'Sample: wrong') : null;
    return h('tr', { 'data-record': r.id },
      h('td', null, box),
      h('td', { style: { width: '64px' } }, r.preview_kind ? h('img', { src: fileUrl(r.document_id, 'preview', r.version), alt: '', style: { maxHeight: '48px', maxWidth: '60px' } }) : icon('file', 22)),
      h('td', { style: { maxWidth: '260px' } }, r.document_id ? h('a', { href: `#/doc/${r.document_id}` }, r.source_path.replace(/\\/g, '/').split('/').pop()) : h('span', null, r.source_path.replace(/\\/g, '/').split('/').pop()),
        h('div', { class: 'small muted ellipsis', title: r.source_path }, r.source_path),
        imported ? null : h('div', { class: 'small' }, h('span', { class: 'chip st-draft' }, r.status), ' ', r.message)),
      cell('part_number', '140px'), cell('description', '220px'), cell('material', '110px'),
      h('td', null, r.revision),
      h('td', null, r.score != null ? h('span', { class: `chip ${QUEUE_CHIP[r.queue] || ''}`, title: r.queue_label }, `${r.score}`) : ''),
      h('td', { class: 'small', style: { maxWidth: '300px' } }, r.reasons.length ? h('details', null, h('summary', null, r.reasons.find(x => / of \d+\)$/.test(x) || x.includes(' says ')) || r.reasons[0]), h('ul', { style: { margin: '4px 0 0', paddingLeft: '16px' } }, r.reasons.map(x => h('li', null, x)))) : ''),
      h('td', { class: 'nowrap' }, r.confirmed_at ? h('div', { class: 'small muted' }, `Confirmed by ${r.confirmed_by_name || '?'}`) : null, h('div', { class: 'row' }, save, sample)));
  });
  const all = h('input', { type: 'checkbox', 'aria-label': 'Select every row on this page', disabled: closed || !boxes.length, onchange: e => { for (const [b, id] of boxes) { b.checked = e.target.checked; e.target.checked ? sel.add(id) : sel.delete(id); } tick(); } });
  return h('div', { class: 'col', style: { gap: '8px' } },
    h('div', { class: 'row wrap', id: 'legacy-bulk' }, field, value, bulkBtn),
    data.records.length ? h('div', { style: { overflowX: 'auto' } }, h('table', { class: 'tbl', id: 'legacy-grid' },
      h('thead', null, h('tr', null, h('th', null, all), ...['', 'File and old path', 'Part number', 'Description', 'Material', 'Rev', 'Score', 'Why', ''].map(t => h('th', null, t)))),
      h('tbody', null, rows)))
      : h('div', { class: 'empty' }, 'Nothing in this queue.'));
}

export async function renderLegacy(container, [id], params, { setTitle }) {
  let queue = params.queue ?? 'review';
  let offset = 0;
  let unconfirmed = params.unconfirmed === '1';
  let q = params.q || '';
  let onlySample = false;
  const load = async (opts = {}) => {
    if (opts.sample) onlySample = true;
    const { job } = await get(`/api/import/jobs/${id}`);
    setTitle(`Import batch ${job.id}`);
    const [rec, data] = await Promise.all([get(`/api/import/jobs/${id}/reconcile`),
      get(`/api/import/jobs/${id}/records?queue=${queue}&q=${encodeURIComponent(q)}&unconfirmed=${unconfirmed ? 1 : 0}&offset=${offset}&limit=${onlySample ? 500 : PAGE}`)]);
    if (onlySample) data.records = data.records.filter(r => r.sample_result);
    const search = h('input', { class: 'input', type: 'search', placeholder: 'Find by old path, part number or description', value: q, 'aria-label': 'Find rows' });
    search.addEventListener('keydown', e => { if (e.key === 'Enter') { q = search.value.trim(); offset = 0; load(); } });
    const pager = h('div', { class: 'row' },
      h('button', { class: 'btn sm', disabled: offset === 0, title: offset === 0 ? 'This is the first page' : '', onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, 'Previous'),
      h('span', { class: 'small muted' }, data.total ? `${offset + 1}–${Math.min(offset + PAGE, data.total)} of ${data.total}` : ''),
      h('button', { class: 'btn sm', disabled: offset + PAGE >= data.total, title: offset + PAGE >= data.total ? 'This is the last page' : '', onclick: () => { offset += PAGE; load(); } }, 'Next'));
    mount(container, h('div', { class: 'page-pad col', style: { gap: '16px' } },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('div', { class: 'small muted' }, h('a', { href: '#/admin?tab=import' }, 'Import legacy data'), ' › batch'),
        h('h1', null, `Batch ${job.id}`), h('div', { class: 'sub mono small' }, `${job.source} → ${job.target}`))),
      countCheck(rec, job, load),
      h('section', { class: 'panel' },
        h('div', { class: 'panel-h' }, h('h2', null, 'Review'), h('span', { class: 'spacer' }), h('span', { class: 'small muted' }, `${data.counts.confirmed} confirmed`)),
        tabs(QUEUES.map(([k, l]) => [k || 'all', k ? `${l} (${data.counts[k]})` : l]), onlySample ? '' : (queue || 'all'), k => { queue = k === 'all' ? '' : k; offset = 0; onlySample = false; load(); }),
        h('div', { class: 'panel-b col', style: { gap: '10px' } },
          h('div', { class: 'small muted' }, 'Auto: score 90 and above. Review: 60 to 89. Attention: below 60. Every score shows its reasons under Why.'),
          h('div', { class: 'row wrap' }, search,
            h('label', { class: 'check small' }, h('input', { type: 'checkbox', checked: unconfirmed, onchange: e => { unconfirmed = e.target.checked; offset = 0; load(); } }), 'Only rows not yet confirmed'),
            rec.sample.size ? h('button', { class: 'btn sm', onclick: () => { onlySample = !onlySample; load(); } }, onlySample ? 'Show the queue' : `Show the sample (${rec.sample.size})`) : null),
          excelBar(job, queue, load),
          grid(data, job, load),
          onlySample ? null : pager))));
  };
  await load();
}
