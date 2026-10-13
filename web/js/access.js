// SPDX-License-Identifier: AGPL-3.0-or-later
// Admin › Access: rules that limit a folder, project, customer or category to named people and roles; the access
// settings of one account (extra roles, kind, view only, end date, projects); and the quarterly access review.
import { S, h, mount, icon, get, post, put, patch, del, toast, fail, dialog, confirmDlg, fmtDate, ago } from './core.js';

let cache = null;
const meta = async () => (cache = cache || await get('/api/access'));

function folderPaths(folders) {
  const by = new Map(folders.map(f => [f.id, f]));
  const path = f => (f.parent_id && by.get(f.parent_id) ? `${path(by.get(f.parent_id))} › ` : '') + f.name;
  return folders.map(f => ({ id: f.id, label: path(f) })).sort((a, b) => a.label.localeCompare(b.label));
}

function memberPicker(users, roles, chosen = []) {
  const boxes = [];
  const box = (value, label, hint) => {
    const cb = h('input', { type: 'checkbox', value, checked: chosen.includes(value) });
    boxes.push(cb);
    return h('label', { class: 'check', title: hint || '' }, cb, label);
  };
  const el = h('div', { class: 'col', style: { gap: '8px' } },
    h('div', { class: 'small muted' }, 'Roles (everyone holding the role, now and later)'),
    h('div', { class: 'row', style: { flexWrap: 'wrap', gap: '2px 16px' } }, roles.filter(r => r.id !== 'admin').map(r => box(`role:${r.id}`, r.name, r.help))),
    h('div', { class: 'small muted' }, 'People'),
    h('div', { class: 'row', style: { flexWrap: 'wrap', gap: '2px 16px', maxHeight: '200px', overflow: 'auto' } },
      users.filter(u => u.active && u.role !== 'admin').map(u => box(`user:${u.id}`, u.full_name, u.username))));
  return { el, value: () => boxes.filter(b => b.checked).map(b => b.value) };
}

export async function accessTab(body) {
  const [m, us, rev] = await Promise.all([get('/api/access'), get('/api/users'), get('/api/access/review')]);
  cache = m;
  const scopeName = Object.fromEntries(Object.entries(m.scopes));
  mount(body,
    h('div', { class: 'row', style: { marginBottom: '12px' } }, h('div', { class: 'muted small', style: { flex: 1 } },
      'Everyone sees everything unless a rule limits it. A rule on a folder, project or category hides it from everyone not named on it. ',
      'A rule on a customer names who may see that customer\'s restricted parts. Admins always see everything.'),
    h('button', { class: 'btn primary', onclick: () => ruleDialog(m, us.users, null, () => accessTab(body)) }, icon('plus', 16), 'Add rule')),
    m.rules.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Limits', 'Who may see it', 'Note', ''].map(t => h('th', null, t)))),
      h('tbody', null, m.rules.map(r => h('tr', null,
        h('td', null, h('b', null, scopeName[r.scope] || r.scope), ' ', r.label),
        h('td', null, r.member_names.join(', ')),
        h('td', { class: 'muted' }, r.note || ''),
        h('td', { class: 'nowrap' }, h('div', { class: 'row' },
          h('button', { class: 'btn sm', onclick: () => ruleDialog(m, us.users, r, () => accessTab(body)) }, 'Change'),
          h('button', { class: 'btn sm', onclick: async () => {
            if (!await confirmDlg('Remove rule', `Remove the rule on ${r.label}? Everyone will see it again.`, 'Remove', true)) return;
            try { await del(`/api/access/rules/${r.id}`); toast('Rule removed', 'ok'); accessTab(body); } catch (e) { fail(e); }
          } }, 'Remove'))))))) : h('div', { class: 'empty' }, 'No rules: everyone with an account sees all parts and files.'),
    reviewSection(rev, m));
}

export async function reviewTab(body) {
  const [rev, m] = await Promise.all([get('/api/access/review'), meta()]);
  mount(body, reviewSection(rev, m));
}

function reviewSection(rev, m) {
  const name = id => (m.roles.find(r => r.id === id) || { name: id }).name;
  return h('section', { style: { marginTop: '22px' } },
    h('div', { class: 'row', style: { marginBottom: '8px' } }, h('h2', { style: { flex: 1, margin: 0 } }, 'Access review'),
      h('a', { class: 'btn', href: `/api/access/review?format=csv&access_token=${encodeURIComponent(S.token || '')}`, download: '' }, icon('download', 16), 'Download for review (CSV)')),
    h('p', { class: 'muted small', style: { marginTop: 0 } }, 'Who can see what. Go through it every quarter and switch off accounts nobody needs. Rows marked in the last column need a look.'),
    h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Name', 'Kind', 'Roles', 'Limited areas they see', 'End date', 'Last sign-in', 'Please check'].map(t => h('th', null, t)))),
      h('tbody', null, rev.users.map(u => h('tr', { class: u.active ? '' : 'muted' },
        h('td', null, u.full_name, h('div', { class: 'small muted mono' }, u.username)),
        h('td', null, u.kind === 'person' ? 'Person' : u.kind === 'vendor' ? `Vendor${u.company ? ` (${u.company})` : ''}` : 'Service account',
          u.projects.length ? h('div', { class: 'small muted' }, u.projects.join(', ')) : null),
        h('td', null, [u.role, ...u.roles].map(name).join(', '), u.view_only ? h('div', { class: 'small muted' }, 'view only') : null),
        h('td', { class: 'small' }, u.sees_limited.join('; ') || '—'),
        h('td', null, u.expires_on || '—'),
        h('td', null, u.last_sign_in ? ago(u.last_sign_in) : 'never'),
        h('td', null, u.active ? (u.check ? h('span', { class: 'chip out' }, u.check) : '') : 'switched off'))))));
}

async function ruleDialog(m, users, rule, done) {
  const scope = h('select', { class: 'input', id: 'ar-scope', disabled: !!rule },
    Object.entries(m.scopes).map(([k, l]) => h('option', { value: k, selected: rule && rule.scope === k }, l)));
  const holder = h('div');
  let valueEl = null;
  const drawValue = async () => {
    const s = scope.value;
    if (rule) { valueEl = h('input', { class: 'input', id: 'ar-value', value: rule.label, disabled: true }); }
    else if (s === 'folder') {
      valueEl = h('select', { class: 'input', id: 'ar-value' }, folderPaths((await get('/api/folders')).folders).map(f => h('option', { value: f.id }, f.label)));
    } else if (s === 'project') {
      valueEl = h('select', { class: 'input', id: 'ar-value' }, (await get('/api/projects')).projects.map(p => h('option', { value: p.id }, `${p.code} ${p.name || ''}`)));
    } else {
      valueEl = h('input', { class: 'input', id: 'ar-value', placeholder: s === 'customer' ? 'Customer name, as on the parts' : 'Category code, for example SHF' });
    }
    mount(holder, h('div', { class: 'field' }, h('label', { for: 'ar-value' }, m.scopes[s]), valueEl));
  };
  scope.addEventListener('change', () => drawValue().catch(fail));
  await drawValue();
  const pick = memberPicker(users, m.roles, rule ? rule.members : []);
  const note = h('input', { class: 'input', id: 'ar-note', value: rule ? rule.note : '', placeholder: 'Why (optional), for example "customer NDA"' });
  dialog({
    title: rule ? `Who may see ${rule.label}` : 'Limit who may see…', wide: true,
    body: h('div', { class: 'col', style: { gap: '12px' } },
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'ar-scope' }, 'Limit a'), scope), holder),
      pick.el, h('div', { class: 'field' }, h('label', { for: 'ar-note' }, 'Note'), note)),
    actions: [{ label: 'Cancel' }, { label: rule ? 'Save' : 'Add rule', primary: true, onClick: async () => {
      const members = pick.value();
      if (rule) await put(`/api/access/rules/${rule.id}`, { members, note: note.value });
      else await post('/api/access/rules', { scope: scope.value, value: valueEl.value, members, note: note.value });
      toast('Saved', 'ok'); done();
    } }],
  });
}

// the access settings of one account, from Admin › Users
export async function userAccessDialog(u, done) {
  const [m, projects] = await Promise.all([meta(), get('/api/projects')]);
  const extra = m.roles.filter(r => !r.main).map(r => ({ r, cb: h('input', { type: 'checkbox', checked: u.roles.includes(r.id) }) }));
  const kind = h('select', { class: 'input', id: 'ua-kind', disabled: u.id === S.user.id }, Object.entries(m.kinds).map(([k, l]) => h('option', { value: k, selected: u.kind === k }, l)));
  const company = h('input', { class: 'input', id: 'ua-company', value: u.company || '', placeholder: 'For vendors and outside designers' });
  const viewOnly = h('input', { type: 'checkbox', checked: !!u.view_only, disabled: u.id === S.user.id });
  const ends = h('input', { class: 'input', id: 'ua-ends', type: 'date', value: u.expires_on || '', disabled: u.id === S.user.id });
  const projBoxes = projects.projects.map(p => ({ p, cb: h('input', { type: 'checkbox', checked: u.projects.includes(p.id) }) }));
  const projWrap = h('div', { class: 'col', style: { gap: '4px' } },
    h('div', { class: 'small muted' }, 'Projects this vendor may see (nothing else)'),
    projBoxes.length ? h('div', { class: 'row', style: { flexWrap: 'wrap', gap: '2px 16px', maxHeight: '180px', overflow: 'auto' } }, projBoxes.map(({ p, cb }) => h('label', { class: 'check' }, cb, `${p.code} ${p.name || ''}`)))
      : h('div', { class: 'small' }, 'No projects yet. Add one under Projects first.'));
  const sync = () => { projWrap.style.display = kind.value === 'vendor' ? '' : 'none'; };
  kind.addEventListener('change', sync); sync();
  dialog({
    title: `Access of ${u.full_name}`, wide: true,
    body: h('div', { class: 'col', style: { gap: '12px' } },
      u.id === S.user.id ? h('p', { class: 'small muted', style: { margin: 0 } }, 'Some settings are locked on your own account so you cannot lock yourself out.') : null,
      h('div', { class: 'small muted' }, 'Extra roles, besides the main role'),
      h('div', { class: 'row', style: { flexWrap: 'wrap', gap: '2px 16px' } }, extra.map(({ r, cb }) => h('label', { class: 'check', title: r.help }, cb, r.name))),
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { for: 'ua-kind' }, 'Kind of account'), kind),
        h('div', { class: 'field' }, h('label', { for: 'ua-company' }, 'Company'), company),
        h('div', { class: 'field' }, h('label', { for: 'ua-ends' }, 'Account ends on'), ends, h('span', { class: 'hint' }, 'Leave empty for staff. The account stops working the day after.'))),
      h('label', { class: 'check', title: u.id === S.user.id ? 'Locked on your own account.' : '' }, viewOnly, 'View only: may look at previews but not download files'),
      projWrap),
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      const body = { roles: extra.filter(x => x.cb.checked).map(x => x.r.id), company: company.value,
        projects: kind.value === 'vendor' ? projBoxes.filter(x => x.cb.checked).map(x => x.p.id) : [] };
      if (u.id !== S.user.id) Object.assign(body, { kind: kind.value, view_only: viewOnly.checked, expires_on: ends.value || null });
      await patch(`/api/users/${u.id}`, body); toast('Saved', 'ok'); done();
    } }],
  });
}

export function accessChips(u) {
  const out = [];
  for (const r of u.roles || []) out.push(h('span', { class: 'chip std' }, (cache && cache.roles.find(x => x.id === r)?.name) || r));
  if (u.kind === 'vendor') out.push(h('span', { class: 'chip out', title: u.company || '' }, 'Vendor'));
  if (u.kind === 'service') out.push(h('span', { class: 'chip std' }, 'Service account'));
  if (u.view_only) out.push(h('span', { class: 'chip std' }, 'View only'));
  if (u.expires_on) out.push(h('span', { class: 'chip std', title: 'The account stops working after this day' }, `Ends ${fmtDate(u.expires_on, false)}`));
  return out;
}

export const loadAccessMeta = meta;
