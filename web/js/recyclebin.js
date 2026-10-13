// SPDX-License-Identifier: AGPL-3.0-or-later
// Recycle bin: deleted files wait here and can be put back; admins purge for good, one asking and another confirming.
import { S, h, mount, get, post, put, toast, fail, dialog, ago, fmtDate, can, icon } from './core.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

function purgeDialog(files, done) {
  const note = h('textarea', { class: 'input', rows: 3, placeholder: 'For example: customer drawing returned under the contract', 'aria-label': 'Reason' });
  dialog({ title: `Purge ${files.length} file${files.length > 1 ? 's' : ''} for good`, body: [
    h('p', { style: { marginTop: 0 } }, 'Purging removes the stored content for good; the record and its history stay. A second admin must confirm it. Backups still hold the file until they expire.'),
    h('ul', null, files.slice(0, 10).map(f => h('li', null, f.filename)), files.length > 10 ? h('li', null, `… and ${files.length - 10} more`) : null),
    h('div', { class: 'field' }, h('label', null, 'Why must these files be removed?'), note)],
  actions: [{ label: 'Cancel' }, { label: 'Ask a second admin', danger: true, onClick: async () => {
    await post('/api/purges', { document_ids: files.map(f => f.id), note: note.value });
    toast('Asked. Another admin confirms it under Recycle bin.', 'ok');
    done();
  } }] });
}

async function adminPart(r, draw) {
  const p = await get('/api/purges');
  const days = h('input', { class: 'input', type: 'number', min: 1, max: 3650, value: r.days, style: { maxWidth: '100px' }, 'aria-label': 'Days in the recycle bin' });
  return [
    h('div', { class: 'row wrap', style: { margin: '18px 0 8px' } }, h('h2', { style: { flex: 1, margin: 0 } }, 'Purges'),
      h('label', { class: 'muted small' }, 'People can put back their own files for'), days, h('span', { class: 'muted small' }, 'days; after that an admin can.'),
      h('button', { class: 'btn sm', onclick: async () => { try { await put('/api/settings', { recycle_bin_days: Number(days.value) }); toast('Saved', 'ok'); draw(); } catch (e) { fail(e); } } }, 'Save')),
    p.requests.length ? h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Asked', 'By', 'Files', 'Reason', 'State', ''].map(t => h('th', null, t)))),
      h('tbody', null, p.requests.map(q => {
        const mine = q.requested_by === S.user.id;
        return h('tr', null, h('td', { class: 'small', title: q.requested_at }, ago(q.requested_at)), h('td', null, q.requested_by_name),
          h('td', { class: 'small' }, q.files.map(f => f.filename).join(', ')), h('td', { class: 'small' }, q.note),
          h('td', null, q.status === 'waiting' ? h('span', { class: 'chip st-in_review' }, 'Waiting for a second admin')
            : h('span', { class: `chip ${q.status === 'done' ? 'st-obsolete' : ''}` }, `${q.status === 'done' ? 'Purged' : 'Cancelled'} by ${q.decided_by_name} ${fmtDate(q.decided_at)}`)),
          h('td', { class: 'nowrap' }, q.status !== 'waiting' ? null : h('div', { class: 'row' },
            h('button', { class: 'btn sm danger', disabled: mine, title: mine ? 'A second admin confirms; you asked for this purge.' : 'Remove the content of these files for good',
              onclick: async () => { try { const d = await post(`/api/purges/${q.id}/confirm`); toast(`Purged. ${d.removed_files} stored file${d.removed_files === 1 ? '' : 's'} removed.`, 'ok'); draw(); } catch (e) { fail(e); } } }, 'Confirm purge'),
            h('button', { class: 'btn sm', onclick: async () => { try { await post(`/api/purges/${q.id}/cancel`); draw(); } catch (e) { fail(e); } } }, 'Cancel'))));
      }))) : h('p', { class: 'muted' }, 'No purges asked for.'),
  ];
}

export async function renderBin(container, _m, _p, { setTitle }) {
  setTitle('Recycle bin');
  const admin = can('admin');
  const draw = async () => {
    const r = await get('/api/recycle-bin');
    const picked = new Set();
    const purgeBtn = h('button', { class: 'btn danger', disabled: true, title: 'Tick files first', onclick: () => purgeDialog(r.files.filter(f => picked.has(f.id)), draw) }, 'Purge for good…');
    const pick = (f, on) => {
      if (on) picked.add(f.id); else picked.delete(f.id);
      purgeBtn.disabled = !picked.size;
      purgeBtn.title = picked.size ? '' : 'Tick files first';
    };
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Recycle bin'),
        h('div', { class: 'sub' }, `Deleted files wait here and go back to their folder with one click. People can put back their own files for ${r.days} days; after that an admin can. Nothing leaves the bin by itself.`)),
      h('a', { class: 'btn', href: '#/files' }, icon('folder', 16), 'Back to files'),
      admin ? purgeBtn : null),
      h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, [admin ? '' : null, 'File', 'Part', 'Was in', 'Deleted', 'By', ''].filter(x => x !== null).map(t => h('th', null, t)))),
        h('tbody', null, r.files.length ? r.files.map(f => h('tr', null,
          admin ? h('td', null, f.purged_at || f.purge_waiting ? null : h('input', { type: 'checkbox', 'aria-label': `Pick ${f.filename}`, onchange: e => pick(f, e.target.checked) })) : null,
          h('td', null, f.filename), h('td', { class: 'mono' }, f.part_number || ''), h('td', { class: 'small' }, f.where || 'Top folder'),
          h('td', { class: 'small', title: f.deleted_at || '' }, f.deleted_at ? ago(f.deleted_at) : ''), h('td', null, f.deleted_by_name || ''),
          h('td', { class: 'nowrap' }, f.purged_at ? h('span', { class: 'chip st-obsolete' }, 'Purged')
            : [f.purge_waiting ? h('span', { class: 'chip st-in_review', style: { marginRight: '6px' } }, 'Purge asked') : null,
              h('button', { class: 'btn sm', disabled: !f.can_restore, title: f.can_restore ? 'Put it back where it was' : f.why_not, onclick: async () => {
                try { const d = await post(`/api/recycle-bin/${f.id}/restore`); toast(`${d.filename} is back.`, 'ok'); changed(); draw(); } catch (e) { fail(e); }
              } }, 'Put back')])))
          : h('tr', null, h('td', { colspan: admin ? 7 : 6 }, h('div', { class: 'empty' }, 'The recycle bin is empty.')))))),
      admin ? await adminPart(r, draw) : null));
  };
  await draw();
}
