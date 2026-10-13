// SPDX-License-Identifier: AGPL-3.0-or-later
// Administration: directory sign-in (LDAP / Active Directory, OpenID Connect): groups → roles, checking an account,
// and linking Kosha users to directory accounts.
import { h, mount, icon, get, post, put, del, toast, fail, dialog, fmtDate, confirmDlg } from './core.js';

const ROLE_WORDS = { viewer: 'Viewer', engineer: 'Engineer', approver: 'Approver', admin: 'Admin' };

function panel(title, ...children) {
  return h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, title)),
    h('div', { class: 'panel-b col', style: { gap: '12px' } }, children));
}

function roleSelect(roles, value, label, allowNone = '') {
  return h('select', { class: 'input', 'aria-label': label, style: { width: 'auto' } },
    allowNone ? h('option', { value: '', selected: !value }, allowNone) : null,
    roles.map(r => h('option', { value: r, selected: r === value }, ROLE_WORDS[r] || r)));
}

function providerRow(p) {
  const what = p.style === 'redirect' ? 'Adds a "Sign in with" button to the sign-in page'
    : p.style === 'password' ? 'Checks the user name and password typed into the sign-in page' : '';
  return h('tr', null,
    h('td', null, h('b', null, p.name), what ? h('div', { class: 'small muted' }, what) : null),
    h('td', null, p.ready ? h('span', { class: 'chip st-released' }, 'Ready') : h('span', { class: 'chip out' }, 'Not in use'),
      p.ready ? null : h('div', { class: 'small muted', style: { maxWidth: '380px', whiteSpace: 'normal' } }, p.why)),
    h('td', null, p.redirect_uri ? h('div', null, h('div', { class: 'small muted' }, 'Redirect address to enter at the provider:'),
      h('code', { class: 'mono small', style: { overflowWrap: 'anywhere' } }, p.redirect_uri)) : ''),
    h('td', { class: 'nowrap' }, h('a', { class: 'btn sm', href: '#/admin?tab=addins' }, 'Settings in Add-ins')));
}

export async function directorySection(container) {
  const box = h('div', { class: 'col', style: { gap: '16px', maxWidth: '1100px', marginTop: '28px' } });
  container.appendChild(box);
  const draw = async () => {
    const d = await get('/api/admin/directory');
    const rows = [];
    const list = h('tbody');
    const addRow = (group = '', role = 'engineer') => {
      const g = h('input', { class: 'input', value: group, placeholder: 'e.g. Kosha Engineers', 'aria-label': 'Directory group' });
      const r = roleSelect(d.roles, role, 'Kosha role for this group');
      const entry = { g, r };
      const tr = h('tr', null, h('td', null, g), h('td', null, r),
        h('td', null, h('button', { class: 'btn sm ghost', type: 'button', onclick: () => { rows.splice(rows.indexOf(entry), 1); tr.remove(); } }, icon('trash', 14), 'Remove')));
      rows.push(entry);
      list.appendChild(tr);
    };
    d.settings.group_roles.forEach(x => addRow(x.group, x.role));
    if (!rows.length) addRow();
    const others = roleSelect(d.roles, d.settings.default_role, 'Role for everyone else', 'Cannot sign in');
    const create = h('input', { type: 'checkbox', id: 'dir-create', checked: d.settings.create_users });
    const follow = h('input', { type: 'checkbox', id: 'dir-follow', checked: d.settings.roles_from_directory });
    const save = h('button', { class: 'btn primary', onclick: async (e) => {
      e.target.disabled = true;
      try {
        await put('/api/admin/directory', { group_roles: rows.map(x => ({ group: x.g.value.trim(), role: x.r.value })),
          default_role: others.value, create_users: create.checked, roles_from_directory: follow.checked });
        toast('Saved. The roles apply from each person\'s next sign-in.', 'ok');
        await draw();
      } catch (ex) { fail(ex); } finally { e.target.disabled = false; }
    } }, 'Save groups and roles');

    const passwordProviders = d.providers.filter(p => p.ready && p.style === 'password');
    const checkWho = h('select', { class: 'input', id: 'dir-chk-p', style: { width: 'auto' } }, passwordProviders.map(p => h('option', { value: p.id }, p.name)));
    const checkUser = h('input', { class: 'input', id: 'dir-chk-u', autocomplete: 'off' });
    const checkPw = h('input', { class: 'input', id: 'dir-chk-pw', type: 'password', autocomplete: 'new-password' });
    const checkOut = h('div');
    const noCheck = 'Set up and switch on a sign-in add-in that checks passwords (LDAP / Active Directory) first.';
    const checkBtn = h('button', { class: 'btn', disabled: !passwordProviders.length, title: passwordProviders.length ? '' : noCheck, onclick: async (e) => {
      e.target.disabled = true;
      try {
        const r = await post('/api/admin/directory/test', { provider: checkWho.value, username: checkUser.value.trim(), password: checkPw.value });
        checkPw.value = '';
        if (!r.ok) { mount(checkOut, h('div', { class: 'banner err small' }, icon('warn'), h('div', null, r.message))); return; }
        const i = r.identity;
        mount(checkOut, h('div', { class: 'banner ok small' }, icon('check'), h('div', { class: 'col', style: { gap: '4px' } },
          h('b', null, `${r.message} ${i.full_name || i.username} (${i.username})`),
          h('div', null, 'Groups: ', i.groups.length ? i.groups.join(' · ') : 'none'),
          h('div', null, 'Kosha role: ', r.role ? ROLE_WORDS[r.role] : 'none, so this person cannot sign in'),
          h('div', null, r.account),
          h('div', { class: 'small muted mono' }, `Directory id: ${i.external_id}`))));
      } catch (ex) { fail(ex); } finally { e.target.disabled = !passwordProviders.length; }
    } }, 'Check account');

    const linkRows = d.links.map(l => h('tr', null,
      h('td', null, l.full_name, h('div', { class: 'small muted mono' }, l.username)),
      h('td', null, h('span', { class: 'mono' }, l.directory_username), h('div', { class: 'small muted' }, l.provider_name)),
      h('td', null, l.pending ? h('span', { class: 'chip st-in_review', title: 'Linked at this person\'s next sign-in through the directory' }, 'Waiting for first sign-in')
        : h('span', { class: 'chip st-released' }, 'Linked')),
      h('td', null, l.last_sign_in_at ? fmtDate(l.last_sign_in_at) : '—'),
      h('td', { style: { whiteSpace: 'normal', maxWidth: '280px' }, class: 'small' }, (l.groups || []).join(' · ') || '—'),
      h('td', null, h('button', { class: 'btn sm', onclick: () => unlink(l, draw) }, 'Unlink'))));

    mount(box,
      h('h2', { style: { margin: '8px 0 0' } }, 'Directory sign-in'),
      h('div', { class: 'banner info' }, icon('info'), h('div', null,
        'People can sign in with an account they already have: Active Directory or LDAP (user name and password), or an OpenID Connect provider such as Keycloak (a button on the sign-in page). ',
        'The local ', h('b', { class: 'mono' }, d.local_admin), ' account always signs in with its Kosha password, so you can get in when the directory is down. ',
        'A Kosha account with the same user name as a directory account is never taken over: link it under Users › Directory.')),
      panel('Sign-in add-ins',
        d.providers.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Add-in', 'Status', '', ''].map(t => h('th', null, t)))),
          h('tbody', null, d.providers.map(providerRow)))
          : h('div', { class: 'empty' }, 'No sign-in add-in is installed.')),
      panel('Groups and Kosha roles',
        h('p', { class: 'muted small', style: { margin: 0 } }, 'Write each group as the directory names it (for Active Directory and LDAP the group\'s name, for OpenID Connect the value in the groups claim). Someone in several groups gets the highest role.'),
        h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Directory group', 'Kosha role', ''].map(t => h('th', null, t)))), list),
        h('div', null, h('button', { class: 'btn sm', type: 'button', onclick: () => addRow() }, icon('plus', 14), 'Add group')),
        h('div', { class: 'form-grid' },
          h('div', { class: 'field' }, h('label', null, 'People in none of these groups'), others,
            h('span', { class: 'hint' }, '"Cannot sign in" keeps Kosha to the groups listed above.')),
          h('label', { class: 'row', for: 'dir-create', style: { gap: '8px', alignItems: 'flex-start' } }, create,
            h('span', null, 'Create a Kosha account at a person\'s first directory sign-in', h('div', { class: 'hint small muted' }, 'Off: you add and link every account yourself.'))),
          h('label', { class: 'row', for: 'dir-follow', style: { gap: '8px', alignItems: 'flex-start' } }, follow,
            h('span', null, 'Update the role from the groups at every sign-in', h('div', { class: 'hint small muted' }, 'Off: the role is set once, then you manage it under Users.')))),
        h('div', null, save)),
      panel('Check a directory account',
        h('p', { class: 'muted small', style: { margin: 0 } }, 'See who the directory says this is, their groups and the Kosha role they would get. Nobody is signed in and nothing is changed.'),
        h('div', { class: 'form-grid' },
          h('div', { class: 'field' }, h('label', { for: 'dir-chk-p' }, 'Directory'), passwordProviders.length ? checkWho : h('span', { class: 'hint' }, noCheck)),
          h('div', { class: 'field' }, h('label', { for: 'dir-chk-u' }, 'User name'), checkUser),
          h('div', { class: 'field' }, h('label', { for: 'dir-chk-pw' }, 'Password'), checkPw)),
        h('div', null, checkBtn), checkOut),
      panel('Linked users',
        linkRows.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Kosha user', 'Directory account', 'Status', 'Last directory sign-in', 'Groups', ''].map(t => h('th', null, t)))),
          h('tbody', null, linkRows)) : h('div', { class: 'empty' }, 'No user is linked to a directory account yet.')));
  };
  try { await draw(); } catch (e) { mount(box, h('div', { class: 'banner err' }, icon('warn'), h('div', null, `Directory sign-in settings could not be loaded: ${e.message}`))); }
}

async function unlink(l, after) {
  const ok = await confirmDlg(`Unlink ${l.full_name}?`,
    `${l.full_name} will no longer sign in with the directory account ${l.directory_username}, and is signed out everywhere now. ` +
    'They can then sign in only with a Kosha password: set one with Reset password under Users. Their work in Kosha is not affected.', 'Unlink', true);
  if (!ok) return;
  try {
    await del(`/api/users/${l.user_id}/directory/${l.id}`);
    toast(`${l.full_name} is no longer linked.`, 'ok');
    await after();
  } catch (e) { fail(e); }
}

// Users › Directory: links of one user, and linking a directory account by its user name.
export async function userDirectoryDialog(u) {
  const body = h('div', { class: 'col', style: { gap: '14px' } });
  const draw = async () => {
    const d = await get(`/api/users/${u.id}/directory`);
    const usable = d.providers.filter(p => !d.links.some(l => l.provider === p.id));
    const who = h('select', { class: 'input', id: 'ud-p' }, usable.map(p => h('option', { value: p.id }, p.name + (p.ready ? '' : ' (not in use yet)'))));
    const name = h('input', { class: 'input', id: 'ud-n', value: u.username, autocomplete: 'off' });
    const why = d.local_admin ? 'The built-in admin account stays a local account, so someone can always sign in when the directory is down.'
      : !d.providers.length ? 'No sign-in add-in is installed.'
        : !usable.length ? 'This user is already linked to every sign-in add-in.' : '';
    const linkBtn = h('button', { class: 'btn primary', disabled: !!why, title: why, onclick: async (e) => {
      e.target.disabled = true;
      try {
        await post(`/api/users/${u.id}/directory`, { provider: who.value, directory_username: name.value.trim() });
        toast(`Linked. ${u.full_name} signs in through the directory from their next sign-in on.`, 'ok');
        await draw();
      } catch (ex) { fail(ex); e.target.disabled = false; }
    } }, 'Link');
    mount(body,
      d.links.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Directory account', 'Status', ''].map(t => h('th', null, t)))),
        h('tbody', null, d.links.map(l => h('tr', null,
          h('td', null, h('span', { class: 'mono' }, l.directory_username), h('div', { class: 'small muted' }, l.provider_name)),
          h('td', null, l.pending ? 'Waiting for first sign-in' : `Linked${l.last_sign_in_at ? `, last sign-in ${fmtDate(l.last_sign_in_at)}` : ''}`),
          h('td', null, h('button', { class: 'btn sm', onclick: () => unlink({ ...l, full_name: u.full_name }, draw) }, 'Unlink'))))))
        : h('p', { class: 'muted', style: { margin: 0 } }, `${u.full_name} signs in with a Kosha password (not linked to a directory).`),
      h('div', { class: 'banner info small' }, icon('info'), h('div', null,
        'Linking keeps this Kosha account, its history and its work. From the person\'s next sign-in through the directory, they sign in there and their Kosha password is no longer used; their role follows their groups when that is switched on under Sign-in & sessions.')),
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { for: 'ud-p' }, 'Directory'), usable.length ? who : h('span', { class: 'hint' }, why || '—')),
        h('div', { class: 'field' }, h('label', { for: 'ud-n' }, 'User name in the directory'), name)),
      h('div', null, linkBtn, why ? h('span', { class: 'hint small muted', style: { marginLeft: '10px' } }, why) : null));
  };
  dialog({ title: `Directory account of ${u.full_name}`, body, wide: true, actions: [{ label: 'Close' }] });
  try { await draw(); } catch (e) { mount(body, h('div', { class: 'banner err' }, icon('warn'), h('div', null, e.message))); }
}
