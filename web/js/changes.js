// SPDX-License-Identifier: AGPL-3.0-or-later
// Engineering change orders.
import { S, h, mount, clear, icon, get, post, patch, del, toast, fail, dialog, confirmDlg, stateChip, ago, fmtDate, can, debounce, typeChip } from './core.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const CO_LABEL = { draft: 'Draft', submitted: 'Waiting for approval', approved: 'Approved', rejected: 'Rejected', implemented: 'Implemented', cancelled: 'Cancelled' };

export async function renderChanges(container, _m, params, { setTitle }) {
  setTitle('Change orders');
  let filter = params.state || '';
  const draw = async () => {
    const r = await get(`/api/changes${filter ? `?state=${filter}` : ''}`);
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Change orders'),
        h('div', { class: 'sub' }, `Propose a change, get it approved, and Kosha releases the new revisions and updates ${S.erp}.`)),
      can('engineer') ? h('button', { class: 'btn primary', onclick: () => newChangeDialog([]) }, icon('plus', 16), 'New change request') : null),
      h('div', { class: 'seg', style: { marginBottom: '12px' } }, [['', 'All'], ['draft', 'Draft'], ['submitted', 'Waiting'], ['approved', 'Approved'], ['implemented', 'Implemented'], ['rejected', 'Rejected']]
        .map(([k, l]) => h('button', { 'aria-pressed': String(filter === k), onclick: () => { filter = k; draw(); } }, l))),
      h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Number', 'Title', 'Priority', 'Files', 'Requested by', 'Created', 'State'].map(t => h('th', null, t)))),
        h('tbody', null, r.changes.length ? r.changes.map(c => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/change/${c.id}`; } },
          h('td', { class: 'mono' }, c.number), h('td', null, c.title), h('td', null, c.priority), h('td', { class: 'num' }, c.items),
          h('td', null, c.requested_by_name), h('td', { class: 'muted' }, ago(c.created_at)), h('td', null, h('span', { class: `chip st-${c.state}` }, CO_LABEL[c.state]))))
          : h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, 'No change orders here.'))))))));
  };
  await draw();
}

function docPicker(onPick) {
  const inp = h('input', { class: 'input', placeholder: 'Search files by part number, name or description', 'aria-label': 'Find files', autocomplete: 'off' });
  const list = h('div', { style: { maxHeight: '220px', overflow: 'auto', border: '1px solid var(--line)', borderRadius: '6px', marginTop: '6px' } });
  const run = debounce(async () => {
    const q = inp.value.trim();
    if (!q) { mount(list, h('div', { class: 'empty small' }, 'Type to search.')); return; }
    const r = await get(`/api/search?q=${encodeURIComponent(q)}&limit=30`);
    mount(list, r.documents.length ? r.documents.map(d => h('button', { class: 'list-row', style: { width: '100%', border: 0, background: 'transparent', cursor: 'pointer', textAlign: 'left' }, onclick: () => onPick(d) },
      typeChip(d.ext), h('span', { class: 'ellipsis', style: { flex: 1 } }, d.filename), h('span', { class: 'muted small' }, `rev ${d.revision}`), stateChip(d.state)))
      : h('div', { class: 'empty small' }, 'No files found.'));
  }, 200);
  inp.addEventListener('input', run);
  run();
  return h('div', null, inp, list);
}

export async function newChangeDialog(docIds) {
  const users = (await get('/api/users')).users.filter(u => u.active && (u.role === 'approver' || u.role === 'admin') && u.id !== S.user.id);
  const picked = new Map();
  if (docIds.length) {
    for (const id of docIds) { try { const d = await get(`/api/documents/${id}`); picked.set(d.id, d); } catch (e) { /* ignore */ } }
  }
  const title = h('input', { class: 'input', id: 'co-t', placeholder: 'e.g. Increase rotor seat to Ø115' });
  const reason = h('textarea', { class: 'input', id: 'co-r', placeholder: 'Why is this change needed? (field report, cost, customer request…)' });
  const prio = h('select', { class: 'input', id: 'co-p' }, [['normal', 'Normal'], ['high', 'High'], ['urgent', 'Urgent'], ['low', 'Low']].map(([v, l]) => h('option', { value: v }, l)));
  const chosen = h('div', { class: 'col', style: { gap: '4px' } });
  const drawChosen = () => mount(chosen, picked.size ? [...picked.values()].map(d => h('div', { class: 'row small' }, typeChip(d.ext), h('span', { style: { flex: 1 } }, d.filename), `rev ${d.revision}`, stateChip(d.state),
    h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${d.filename}`, onclick: () => { picked.delete(d.id); drawChosen(); } }, icon('x', 14))))
    : h('span', { class: 'muted small' }, 'No files yet.'));
  drawChosen();
  const appr = users.map(u => ({ u, cb: h('input', { type: 'checkbox', checked: u.role === 'approver' && users.filter(x => x.role === 'approver').length <= 2 }) }));
  dialog({
    title: 'New change request', wide: true,
    body: [
      S.mode === 'guided' ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Describe the change and pick the affected files. After saving you can start new revisions, make your edits, then submit it for approval.')) : null,
      h('div', { class: 'field' }, h('label', { for: 'co-t' }, 'Title'), title),
      h('div', { class: 'field' }, h('label', { for: 'co-r' }, 'Reason'), reason),
      h('div', { class: 'field', style: { maxWidth: '220px' } }, h('label', { for: 'co-p' }, 'Priority'), prio),
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Affected files'), chosen, docPicker(d => { picked.set(d.id, d); drawChosen(); })),
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Approvers'), users.length ? h('div', { class: 'row wrap' }, appr.map(a => h('label', { class: 'check' }, a.cb, `${a.u.full_name} (${a.u.role})`)))
        : h('span', { class: 'muted small' }, 'No approvers are set up yet. Ask your administrator.'))],
    actions: [{ label: 'Cancel' }, { label: 'Create change request', primary: true, onClick: async () => {
      if (!title.value.trim()) { toast('Give the change a title', 'err'); return false; }
      const r = await post('/api/changes', { title: title.value, reason: reason.value, priority: prio.value, document_ids: [...picked.keys()],
        approver_ids: appr.filter(a => a.cb.checked).map(a => a.u.id) });
      changed();
      location.hash = `#/change/${r.id}`;
    } }],
  });
}

export async function renderChange(container, [id], _p, { setTitle }) {
  const users = (await get('/api/users')).users;
  const load = async () => { const c = await get(`/api/changes/${id}`); setTitle(c.number); draw(c); };
  const act = (path, body, msg) => async () => {
    try { const r = await post(`/api/changes/${id}/${path}`, body); toast(typeof msg === 'function' ? msg(r) : msg, 'ok'); changed(); draw(r); } catch (e) { fail(e); }
  };
  const draw = c => {
    const mineReq = c.requested_by === S.user.id;
    const editable = ['draft', 'rejected'].includes(c.state) && (mineReq || can('approver'));
    const myAppr = c.approvers.find(a => a.user_id === S.user.id);
    const steps = [['draft', 'Draft'], ['submitted', 'Approval'], ['approved', 'Approved'], ['implemented', 'Implemented']];
    const idx = steps.findIndex(s => s[0] === c.state);
    const stepper = h('div', { class: 'stepper' });
    steps.forEach(([k, l], i) => {
      if (i) stepper.appendChild(h('span', { class: 'line' }));
      const cls = c.state === 'implemented' || i < idx ? 'done' : i === idx ? 'cur' : '';
      stepper.appendChild(h('span', { class: `s ${cls}` }, h('span', { class: 'dot' }, cls === 'done' ? '✓' : i + 1), l));
    });
    const releasedItems = c.items.filter(i => i.action === 'release' && i.document?.state === 'released');
    const buttons = [];
    if (editable && releasedItems.length) buttons.push(h('button', { class: 'btn', onclick: act('revise', {}, r => `Started ${r.revised} new revision${r.revised === 1 ? '' : 's'}. Check them out and make your changes.`) }, icon('revise', 16), `Start new revisions (${releasedItems.length})`));
    if (editable) buttons.push(h('button', { class: 'btn primary', onclick: act('submit', {}, 'Submitted for approval') }, icon('review', 16), 'Submit for approval'));
    if (c.state === 'submitted' && (myAppr?.decision === 'pending' || (S.user.role === 'admin' && !myAppr))) {
      buttons.push(h('button', { class: 'btn primary', onclick: () => decide('approved') }, icon('check', 16), 'Approve'));
      buttons.push(h('button', { class: 'btn danger', onclick: () => decide('rejected') }, icon('x', 16), 'Reject'));
    }
    if (c.state === 'approved' && can('approver')) buttons.push(h('button', { class: 'btn primary', onclick: async () => {
      if (await confirmDlg('Implement change', `Release all affected files at their new revisions and send the parts to ${S.erp}?`, 'Implement')) act('implement', {}, r => `Implemented: ${(r.done || []).length} file(s) updated`)();
    } }, icon('release', 16), 'Implement'));
    if (!['implemented', 'cancelled'].includes(c.state) && (mineReq || can('approver'))) buttons.push(h('button', { class: 'btn ghost', onclick: async () => {
      if (await confirmDlg('Cancel change order', `Cancel ${c.number}?`, 'Cancel change order', true)) act('cancel', {}, 'Cancelled')();
    } }, 'Cancel order'));

    const decide = decision => {
      const cm = h('textarea', { class: 'input', id: 'dc', placeholder: decision === 'rejected' ? 'What needs to change? (required)' : 'Optional comment' });
      dialog({ title: decision === 'approved' ? 'Approve change' : 'Reject change', body: h('div', { class: 'field' }, h('label', { for: 'dc' }, 'Comment'), cm),
        actions: [{ label: 'Cancel' }, { label: decision === 'approved' ? 'Approve' : 'Reject', primary: decision === 'approved', danger: decision === 'rejected', onClick: async () => {
          const r = await post(`/api/changes/${id}/decide`, { decision, comment: cm.value }); toast(decision === 'approved' ? 'Approved' : 'Rejected', 'ok'); changed(); draw(r);
        } }] });
    };

    const guide = {
      draft: releasedItems.length ? 'Next: press "Start new revisions", check out the new revisions, make your edits and check them in. Then submit for approval.' : 'Next: make sure your edits are checked in, then submit for approval.',
      submitted: 'Waiting for the approvers below. Each approver approves or rejects with a comment.',
      approved: `All approvers agreed. An approver presses Implement to release the files and update ${S.erp}.`,
      rejected: 'Rejected. Read the comments, fix the files, and submit again.',
      implemented: `Done. The new revisions are released and the parts were queued for ${S.erp}.`,
      cancelled: 'This change order was cancelled.',
    }[c.state];

    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } },
        h('div', { class: 'small muted' }, `${c.number} · requested by ${c.requested_by_name} ${ago(c.created_at)} · priority ${c.priority}`),
        h('h1', { style: { margin: '4px 0 10px' } }, c.title), stepper),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } }, buttons)),
      h('div', { class: `banner ${c.state === 'rejected' ? 'err' : c.state === 'implemented' ? 'ok' : 'info'}`, style: { marginBottom: '16px' } }, icon('info'), h('div', null, guide)),
      h('div', { class: 'grid-cards' },
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Details')), h('div', { class: 'panel-b col' },
          h('div', null, h('b', null, 'Reason'), h('p', { style: { margin: '4px 0 0', whiteSpace: 'pre-wrap' } }, c.reason || '—')),
          c.description ? h('div', null, h('b', null, 'Description'), h('p', { style: { margin: '4px 0 0', whiteSpace: 'pre-wrap' } }, c.description)) : null,
          h('dl', { class: 'props' }, [['Submitted', c.submitted_at ? fmtDate(c.submitted_at) : '—'], ['Decided', c.decided_at ? fmtDate(c.decided_at) : '—'], ['Implemented', c.implemented_at ? fmtDate(c.implemented_at) : '—']]
            .flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])))),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Approvers (${c.approvers.length})`), h('span', { class: 'spacer' }),
          editable ? approverAdder(c, users, draw) : null),
        h('div', null, c.approvers.length ? c.approvers.map(a => h('div', { class: 'list-row' }, icon('users', 18), h('div', { style: { flex: 1 } }, h('b', null, a.name),
          a.comment ? h('div', { class: 'small muted' }, `"${a.comment}"`) : null, a.decided_at ? h('div', { class: 'small muted' }, ago(a.decided_at)) : null),
        h('span', { class: `chip st-${a.decision}` }, a.decision),
        editable ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${a.name}`, onclick: async () => { try { draw(await del(`/api/changes/${id}/approvers/${a.user_id}`)); } catch (e) { fail(e); } } }, icon('x', 14)) : null))
          : h('div', { class: 'empty' }, 'No approvers yet.')))),
      h('section', { class: 'panel', style: { marginTop: '16px' } }, h('div', { class: 'panel-h' }, h('h2', null, `Affected files (${c.items.length})`), h('span', { class: 'spacer' }),
        editable ? h('button', { class: 'btn sm', onclick: () => addFiles(c, draw) }, icon('plus', 14), 'Add files') : null),
      h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['File', 'Action', 'From', 'To', 'Now', 'Checked out', ''].map(t => h('th', null, t)))),
        h('tbody', null, c.items.length ? c.items.map(it => {
          const d = it.document;
          return h('tr', null,
            h('td', null, h('a', { href: `#/doc/${d.id}` }, d.filename)),
            h('td', null, it.action === 'release' ? 'Release new revision' : 'Make obsolete'),
            h('td', null, it.from_revision || '—'), h('td', null, h('b', null, it.to_revision || d.revision)),
            h('td', null, `rev ${d.revision} `, stateChip(d.state)),
            h('td', null, d.checked_out_by ? h('span', { class: 'chip out' }, d.checked_out_name) : ''),
            h('td', null, editable ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${d.filename}`, onclick: async () => { try { draw(await del(`/api/changes/${id}/items/${d.id}`)); } catch (e) { fail(e); } } }, icon('x', 14)) : null));
        }) : h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, 'No files yet.'))))))),
      (sec => { import('./diffview.js').then(m => m.changeLogSection(sec, c)).catch(fail); return sec; })(h('section', { class: 'panel', style: { marginTop: '16px' } })),
      h('section', { class: 'panel', style: { marginTop: '16px' } }, h('div', { class: 'panel-h' }, h('h2', null, 'Discussion')),
        h('div', { class: 'panel-b col' }, c.comments.map(m => h('div', { style: { borderBottom: '1px solid var(--line-2)', paddingBottom: '8px' } },
          h('div', { class: 'small muted' }, `${m.name} · ${ago(m.at)}`), h('div', { style: { whiteSpace: 'pre-wrap' } }, m.text))),
        commentBox(id, draw)))));
  };
  await load();
}

function approverAdder(c, users, draw) {
  const opts = users.filter(u => u.active && ['approver', 'admin'].includes(u.role) && !c.approvers.some(a => a.user_id === u.id));
  if (!opts.length) return null;
  const sel = h('select', { class: 'input', style: { width: 'auto', minHeight: '28px' }, 'aria-label': 'Add approver' }, h('option', { value: '' }, 'Add approver…'), opts.map(u => h('option', { value: u.id }, u.full_name)));
  sel.addEventListener('change', async () => { if (!sel.value) return; try { draw(await post(`/api/changes/${c.id}/approvers`, { user_id: Number(sel.value) })); } catch (e) { fail(e); } });
  return sel;
}

function addFiles(c, draw) {
  const action = h('select', { class: 'input', id: 'af-a', style: { maxWidth: '260px' } }, h('option', { value: 'release' }, 'Release a new revision'), h('option', { value: 'obsolete' }, 'Make obsolete'));
  const dlg = dialog({ title: 'Add files to the change', body: [h('div', { class: 'field' }, h('label', { for: 'af-a' }, 'What should happen to it'), action),
    docPicker(async d => { try { draw(await post(`/api/changes/${c.id}/items`, { document_id: d.id, action: action.value })); toast(`Added ${d.filename}`, 'ok'); } catch (e) { fail(e); } })],
  actions: [{ label: 'Done' }] });
  return dlg;
}

function commentBox(id, draw) {
  const t = h('textarea', { class: 'input', placeholder: 'Write a comment', 'aria-label': 'Comment' });
  return h('div', { class: 'col' }, t, h('div', null, h('button', { class: 'btn', onclick: async () => {
    if (!t.value.trim()) return;
    try { draw(await post(`/api/changes/${id}/comments`, { text: t.value })); } catch (e) { fail(e); }
  } }, 'Post comment')));
}
void clear; void patch;
