// SPDX-License-Identifier: AGPL-3.0-or-later
// Change requests (ECR: "please change this"), approval routes (Admin › Rules) and a stand-in while away.
import { S, h, mount, icon, get, post, put, del, toast, fail, dialog, ago, can, stateChip } from './core.js';
import { discussionPanel } from './discussions.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const CR_CHIP = { open: 'st-open', accepted: 'st-approved', declined: 'st-rejected', withdrawn: 'st-cancelled' };
const crChip = (state, label) => h('span', { class: `chip ${CR_CHIP[state] || ''}` }, label || state);

/** Raise a change request, from a file page or from the list. */
export function raiseRequestDialog(doc) {
  const title = h('input', { class: 'input', id: 'cr-t', placeholder: 'e.g. Add lifting eyes to the housing' });
  const reason = h('textarea', { class: 'input', id: 'cr-r', placeholder: 'Why? A site report, a cost, a customer request, a problem in the workshop…' });
  dialog({
    title: 'Raise a change request',
    body: [
      h('p', { class: 'muted', style: { margin: '0 0 8px' } }, doc ? `For ${doc.filename}. An engineer takes it into a change order, or an approver declines it with a reason.`
        : 'An engineer takes it into a change order, or an approver declines it with a reason.'),
      h('div', { class: 'field' }, h('label', { for: 'cr-t' }, 'What should change'), title),
      h('div', { class: 'field' }, h('label', { for: 'cr-r' }, 'Why'), reason)],
    actions: [{ label: 'Cancel' }, { label: 'Raise request', primary: true, onClick: async () => {
      const r = await post('/api/change-requests', { title: title.value, reason: reason.value, document_id: doc ? doc.id : null });
      toast(`${r.number} raised`, 'ok');
      changed();
      location.hash = `#/ecr/${r.id}`;
    } }],
  });
}

export async function renderRequests(container, _m, params, { setTitle }) {
  setTitle('Change requests');
  let filter = params.state ?? 'open';
  const draw = async () => {
    const r = await get(`/api/change-requests${filter ? `?state=${filter}` : ''}`);
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Change requests'),
        h('div', { class: 'sub' }, 'Anyone can ask for a change. An engineer takes the request into a change order; an approver can decline it with a reason.')),
      h('a', { class: 'btn', href: '#/changes' }, icon('change', 16), 'Change orders'),
      h('button', { class: 'btn primary', onclick: () => raiseRequestDialog(null) }, icon('plus', 16), 'Raise change request')),
      h('div', { class: 'seg', style: { marginBottom: '12px' } }, [['open', 'Open'], ['accepted', 'In a change order'], ['declined', 'Declined'], ['withdrawn', 'Withdrawn'], ['', 'All']]
        .map(([k, l]) => h('button', { 'aria-pressed': String(filter === k), onclick: () => { filter = k; draw(); } }, l))),
      h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Number', 'What should change', 'Part', 'Raised by', 'Raised', 'State'].map(t => h('th', null, t)))),
        h('tbody', null, r.requests.length ? r.requests.map(c => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/ecr/${c.id}`; } },
          h('td', { class: 'mono' }, c.number), h('td', null, c.title), h('td', { class: 'mono' }, c.part_number || '—'),
          h('td', null, c.raised_by_name), h('td', { class: 'muted' }, ago(c.raised_at)),
          h('td', null, crChip(c.state, c.co_number ? `${c.state_label}: ${c.co_number}` : c.state_label))))
          : h('tr', null, h('td', { colspan: 6 }, h('div', { class: 'empty' }, 'No change requests here.'))))))));
  };
  await draw();
}

export async function renderRequest(container, [id], _p, { setTitle }) {
  const draw = c => {
    setTitle(c.number);
    const act = (path, body, msg) => async () => {
      try { const r = await post(`/api/change-requests/${id}/${path}`, body); toast(msg, 'ok'); changed(); draw(r); } catch (e) { fail(e); }
    };
    const btn = (a, label, cls, run) => h('button', { class: `btn ${cls}`, disabled: !a.ok, title: a.ok ? '' : a.why, onclick: run }, label);
    const decline = () => {
      const note = h('textarea', { class: 'input', id: 'cr-note', placeholder: 'Why not? (required)' });
      dialog({ title: `Decline ${c.number}`, body: h('div', { class: 'field' }, h('label', { for: 'cr-note' }, 'Reason'), note),
        actions: [{ label: 'Cancel' }, { label: 'Decline', danger: true, onClick: async () => {
          const r = await post(`/api/change-requests/${id}/decline`, { note: note.value }); toast('Declined', 'ok'); changed(); draw(r);
        } }] });
    };
    const convert = async () => {
      const drafts = (await get('/api/changes?state=draft')).changes;
      const into = h('select', { class: 'input', id: 'cr-into' }, h('option', { value: '' }, 'A new change order'),
        drafts.map(d => h('option', { value: d.id }, `${d.number} ${d.title}`)));
      dialog({ title: `Take ${c.number} into a change order`,
        body: [h('div', { class: 'field' }, h('label', { for: 'cr-into' }, 'Into'), into),
          h('div', { class: 'small muted' }, c.document ? `${c.document.filename} is added to the change order. ` : '',
            'Approvers come from the approval route of the parts; you can change them on the change order.')],
        actions: [{ label: 'Cancel' }, { label: 'Take into change order', primary: true, onClick: async () => {
          const r = await post(`/api/change-requests/${id}/convert`, { change_id: into.value ? Number(into.value) : null });
          toast(`${c.number} is now in ${r.change_order.number}`, 'ok'); changed(); location.hash = `#/change/${r.change_id}`;
        } }] });
    };
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } },
        h('div', { class: 'small muted' }, `${c.number} · raised by ${c.raised_by_name} ${ago(c.raised_at)}`),
        h('h1', { style: { margin: '4px 0 10px' } }, c.title), crChip(c.state, c.state_label)),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } },
        btn(c.actions.convert, 'Take into change order', 'primary', convert),
        btn(c.actions.decline, 'Decline', 'danger', decline),
        btn(c.actions.withdraw, 'Withdraw', 'ghost', act('withdraw', {}, 'Withdrawn')))),
      c.change_order ? h('div', { class: 'banner ok', style: { marginBottom: '16px' } }, icon('info'), h('div', null, 'Being worked on in ',
        h('a', { href: `#/change/${c.change_order.id}` }, `${c.change_order.number} ${c.change_order.title}`), '.')) : null,
      c.state === 'declined' ? h('div', { class: 'banner err', style: { marginBottom: '16px' } }, icon('info'),
        h('div', null, `Declined by ${c.decided_by_name}: ${c.decision_note}`)) : null,
      h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Request')), h('div', { class: 'panel-b col' },
        h('div', null, h('b', null, 'Why'), h('p', { style: { margin: '4px 0 0', whiteSpace: 'pre-wrap' } }, c.reason)),
        c.document ? h('div', null, h('b', null, 'File '), h('a', { href: `#/doc/${c.document.id}` }, c.document.filename), ` rev ${c.document.revision} `, stateChip(c.document.state)) : null,
        c.item ? h('div', null, h('b', null, 'Part '), h('a', { href: `#/item/${c.item.id}` }, c.item.part_number), ` ${c.item.description || ''}`) : null)),
      h('div', { class: 'disc-slot' }, discussionPanel('change_request', c.id))));
  };
  try { draw(await get(`/api/change-requests/${id}`)); } catch (e) { fail(e); }
}

/** "Stand-in while I'm away": an approver names a colleague who decides in their name until a date. */
export async function delegateDialog() {
  const [me, users] = await Promise.all([get('/api/me/delegate'), get('/api/users')]);
  const who = h('select', { class: 'input', id: 'dl-who' }, h('option', { value: '' }, 'Nobody'),
    users.users.filter(u => u.active && u.id !== S.user.id && (u.role === 'approver' || u.role === 'admin' || (u.roles || []).includes('quality')))
      .map(u => h('option', { value: u.id, selected: me.delegate && me.delegate.id === u.id }, u.full_name)));
  const until = h('input', { class: 'input', id: 'dl-until', type: 'date', value: me.delegate ? me.delegate.until : '' });
  dialog({
    title: 'Stand-in while I am away',
    body: [h('p', { class: 'muted', style: { margin: '0 0 8px' } }, 'Your stand-in can approve or reject change orders in your name until the date you set. Both names are kept with each decision.'),
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'dl-who' }, 'Stand-in'), who),
        h('div', { class: 'field' }, h('label', { for: 'dl-until' }, 'Until (last day of leave)'), until))],
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      const r = await put('/api/me/delegate', { user_id: who.value ? Number(who.value) : null, until: until.value });
      toast(r.delegate ? `${r.delegate.name} stands in for you until ${r.delegate.until}` : 'No stand-in', 'ok');
    } }],
  });
}

/** Admin › Rules: approval routes. */
export async function routesSection(container) {
  const draw = async () => {
    const [r, users] = await Promise.all([get('/api/approval-routes'), get('/api/users')]);
    const editor = route => routeDialog(route, r, users.users, draw);
    mount(container, h('section', { class: 'panel', style: { marginTop: '16px' } },
      h('div', { class: 'panel-h' }, h('h2', null, 'Approval routes'), h('span', { class: 'spacer' }),
        h('button', { class: 'btn sm', onclick: () => editor(null) }, icon('plus', 14), 'Add route')),
      h('div', { class: 'panel-b col' },
        h('div', { class: 'small muted' }, `Who approves a change order, by category, by project, or for every change. Approvers who wait ${r.remind_after_days} working days are reminded; after ${r.escalate_after_days} the route's design lead is told (or the administrators).`),
        r.routes.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Route', 'For', 'Approvers', 'Asked', 'Design lead', ''].map(t => h('th', null, t)))),
          h('tbody', null, r.routes.map(x => h('tr', null, h('td', null, x.name), h('td', null, x.scope === 'default' ? x.scope_label : `${x.scope_label} ${x.value}`),
            h('td', null, x.approvers.map(a => a.name).join(x.mode === 'sequential' ? ' → ' : ', ')), h('td', null, x.mode_label), h('td', null, x.lead_name || '—'),
            h('td', { class: 'row' }, h('button', { class: 'btn ghost sm', onclick: () => editor(x) }, 'Edit'),
              h('button', { class: 'btn ghost sm', 'aria-label': `Remove route ${x.name}`, onclick: async () => { try { await del(`/api/approval-routes/${x.id}`); toast('Route removed', 'ok'); draw(); } catch (e) { fail(e); } } }, icon('x', 14)))))))
          : h('div', { class: 'empty' }, 'No routes yet: approvers are picked by hand on each change order.'))));
  };
  await draw();
}

function routeDialog(route, meta, users, done) {
  const able = users.filter(u => u.active && (u.role === 'approver' || u.role === 'admin' || (u.roles || []).includes('quality')));
  const name = h('input', { class: 'input', id: 'rt-name', value: route ? route.name : '', placeholder: 'e.g. Rotating parts' });
  const scope = h('select', { class: 'input', id: 'rt-scope' }, Object.entries(meta.scopes).map(([k, l]) => h('option', { value: k, selected: route ? route.scope === k : k === 'default' }, l)));
  const value = h('input', { class: 'input mono', id: 'rt-value', value: route ? route.value : '', placeholder: 'ROT, or a project code' });
  const valueField = h('div', { class: 'field' }, h('label', { for: 'rt-value' }, 'Category or project code'), value);
  const mode = h('select', { class: 'input', id: 'rt-mode' }, Object.entries(meta.modes).map(([k, l]) => h('option', { value: k, selected: route ? route.mode === k : k === 'parallel' }, l)));
  const lead = h('select', { class: 'input', id: 'rt-lead' }, h('option', { value: '' }, 'The administrators'),
    users.filter(u => u.active).map(u => h('option', { value: u.id, selected: route && route.lead_id === u.id }, u.full_name)));
  const picked = (route ? route.approvers.map(a => a.id) : []);
  const order = h('div', { class: 'small muted' });
  const showOrder = () => { order.textContent = picked.length ? `Asked: ${picked.map(id => able.find(u => u.id === id)?.full_name || '?').join(mode.value === 'sequential' ? ' → ' : ', ')}` : 'Tick the approvers in the order they are asked.'; };
  const boxes = able.map(u => {
    const cb = h('input', { type: 'checkbox', checked: picked.includes(u.id) });
    cb.addEventListener('change', () => { const i = picked.indexOf(u.id); if (cb.checked && i < 0) picked.push(u.id); if (!cb.checked && i >= 0) picked.splice(i, 1); showOrder(); });
    return h('label', { class: 'check' }, cb, u.full_name);
  });
  const sync = () => { valueField.hidden = scope.value === 'default'; showOrder(); };
  scope.addEventListener('change', sync); mode.addEventListener('change', sync); sync();
  dialog({
    title: route ? `Edit route ${route.name}` : 'Add approval route', wide: true,
    body: [h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'rt-name' }, 'Name'), name),
      h('div', { class: 'field' }, h('label', { for: 'rt-scope' }, 'Used for'), scope), valueField,
      h('div', { class: 'field' }, h('label', { for: 'rt-mode' }, 'Approvers are asked'), mode),
      h('div', { class: 'field' }, h('label', { for: 'rt-lead' }, 'Told when approvals wait too long'), lead)),
    h('div', { class: 'field', style: { marginTop: '12px' } }, h('span', { class: 'lbl' }, 'Approvers'),
      able.length ? h('div', { class: 'row wrap' }, boxes) : h('span', { class: 'muted small' }, 'Nobody can approve yet: give someone the approver role, or the extra role Quality and purchase.'), order)],
    actions: [{ label: 'Cancel' }, { label: 'Save route', primary: true, onClick: async () => {
      const body = { name: name.value, scope: scope.value, value: value.value, mode: mode.value, approver_ids: picked, lead_id: lead.value ? Number(lead.value) : null };
      if (route) await put(`/api/approval-routes/${route.id}`, body); else await post('/api/approval-routes', body);
      toast('Route saved', 'ok'); done();
    } }],
  });
}

void can;
