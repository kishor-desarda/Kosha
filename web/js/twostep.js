// SPDX-License-Identifier: AGPL-3.0-or-later
// Two-step sign-in (authenticator app), its admin settings, and the per-person security actions in Admin › Users:
// reset two-step sign-in, let paused downloads continue, export or erase personal data.
import { h, mount, get, post, put, api, toast, fail, dialog, confirmDlg, icon } from './core.js';

const codeInput = id => h('input', { class: 'input mono', id, inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 12, placeholder: '123456', style: { maxWidth: '180px', fontSize: '18px', letterSpacing: '2px' } });

export function recoveryDialog(codes) {
  dialog({ title: 'Recovery codes', body: [
    h('p', { style: { marginTop: 0 } }, 'If you lose your phone, sign in with one of these codes instead of the six digits. Each works once. Print them or keep them somewhere safe; Kosha cannot show them again.'),
    h('pre', { class: 'mono', style: { columns: 2, fontSize: '15px', lineHeight: 1.8, margin: 0 } }, codes.join('\n'))],
  actions: [{ label: 'Copy', onClick: async () => { try { await navigator.clipboard.writeText(codes.join('\n')); toast('Copied', 'ok'); } catch (e) { toast('Select the codes and copy them with Ctrl+C.'); } return false; } },
    { label: 'I have kept them', primary: true }] });
}

function keyBlock(setup) {
  return h('div', { class: 'col', style: { gap: '8px' } },
    h('ol', { style: { margin: 0, paddingLeft: '20px', lineHeight: 1.6 } },
      h('li', null, 'Open an authenticator app on your phone (Microsoft Authenticator, Google Authenticator, FreeOTP …).'),
      h('li', null, 'Add an account, choose to enter a key, and type this key:'),
      h('li', { style: { listStyle: 'none', margin: '4px 0' } }, h('code', { class: 'mono', style: { fontSize: '16px', userSelect: 'all' } }, setup.secret)),
      h('li', null, 'Type the six digits the app then shows.')),
    h('a', { href: setup.uri, class: 'small' }, 'On this phone? Open the authenticator app directly'));
}

// After the password, when the server asks for the second step. done(answer) gets the signed-in answer.
export function secondStep(box, first, done) {
  const code = codeInput('ts-code');
  const err = h('div', { class: 'banner err', role: 'alert', hidden: true });
  const btn = h('button', { class: 'btn primary', type: 'submit', style: { minHeight: '46px', justifyContent: 'center' } }, 'Continue');
  const show = msg => { err.hidden = false; mount(err, icon('warn'), h('span', null, msg)); };
  const form = h('form', { novalidate: true });
  const submitWith = async (path, body) => {
    btn.disabled = true;
    try { done(await api('POST', path, body)); } catch (ex) {
      if (ex.code === 'challenge_expired') { show(ex.message); setTimeout(() => location.reload(), 2500); } else show(ex.message);
    } finally { btn.disabled = false; }
  };
  if (first.two_step === 'verify') {
    mount(form, h('div', null, h('h1', { style: { fontSize: '30px' } }, 'Two-step sign-in'),
      h('p', { class: 'muted', style: { margin: '6px 0 0' } }, `Hello ${first.full_name}. Type the six digits your authenticator app shows for Kosha. Lost your phone? Type one of your recovery codes.`)),
    err, h('div', { class: 'field' }, h('label', { for: 'ts-code' }, 'Code'), code), btn);
    form.addEventListener('submit', e => { e.preventDefault(); submitWith('/api/auth/two-step', { challenge: first.challenge, code: code.value }); });
    mount(box, form);
    setTimeout(() => code.focus(), 50);
    return;
  }
  // set up during sign-in: the role (or signing in from outside the office) needs it
  mount(box, h('div', { class: 'muted' }, 'Preparing …'));
  api('POST', '/api/auth/two-step/setup', { challenge: first.challenge }).then(setup => {
    mount(form, h('div', null, h('h1', { style: { fontSize: '28px' } }, 'Set up two-step sign-in'),
      h('p', { class: 'muted', style: { margin: '6px 0 0' } }, 'Your account needs a code from your phone as well as the password. This takes a minute, once.')),
    err, keyBlock(setup), h('div', { class: 'field' }, h('label', { for: 'ts-code' }, 'Code from the app'), code), btn);
    form.addEventListener('submit', e => { e.preventDefault(); submitWith('/api/auth/two-step/setup/confirm', { challenge: first.challenge, code: code.value }); });
    mount(box, form);
    setTimeout(() => code.focus(), 50);
  }).catch(ex => { show(ex.message); mount(box, err); });
}

// Account menu › Two-step sign-in
export async function twoStepDialog() {
  const st = await get('/api/me/two-step');
  if (!st.on) {
    const setup = await post('/api/me/two-step/start');
    const code = codeInput('ts-new');
    dialog({ title: 'Two-step sign-in', body: [
      h('p', { style: { marginTop: 0 } }, 'With two-step sign-in, signing in needs your password and a six-digit code from your phone, so a stolen password alone is not enough.'),
      keyBlock(setup), h('div', { class: 'field' }, h('label', { for: 'ts-new' }, 'Code from the app'), code)],
    actions: [{ label: 'Cancel' }, { label: 'Switch on', primary: true, onClick: async () => {
      const r = await post('/api/me/two-step/confirm', { code: code.value });
      toast('Two-step sign-in is on.', 'ok');
      recoveryDialog(r.recovery_codes);
    } }] });
    return;
  }
  const code = codeInput('ts-off');
  dialog({ title: 'Two-step sign-in', body: [
    h('div', { class: 'banner ok' }, icon('check'), h('div', null, `On since ${new Date(st.since).toLocaleDateString()}. ${st.recovery_left} recovery code${st.recovery_left === 1 ? '' : 's'} left.`)),
    st.required ? h('p', null, 'Your role needs two-step sign-in, so it stays on. For a new phone, ask your Kosha administrator to reset it; you set it up again at your next sign-in.')
      : h('div', { class: 'field' }, h('label', { for: 'ts-off' }, 'To switch it off, type a code from the app'), code)],
  actions: st.required ? [{ label: 'Close', primary: true }] : [{ label: 'Close' }, { label: 'Switch off', danger: true, onClick: async () => {
    await post('/api/me/two-step/off', { code: code.value });
    toast('Two-step sign-in is off.', 'ok');
  } }] });
}

// Admin › Sign-in & sessions
const ROLE_NAMES = [['admin', 'Admins'], ['approver', 'Approvers'], ['checker', 'Checkers'], ['engineer', 'Engineers'], ['viewer', 'Viewers']];
export function twoStepSettings(body, s) {
  const boxes = ROLE_NAMES.map(([k, l]) => [k, h('input', { type: 'checkbox', checked: s.two_step_roles.includes(k) }), l]);
  const outside = h('input', { type: 'checkbox', checked: s.two_step_outside_office });
  const nets = h('input', { class: 'input', id: 'office-nets', value: s.office_networks, placeholder: '192.168.1.0/24 10.8.0.0/16' });
  const places = h('input', { type: 'checkbox', checked: s.new_place_alerts });
  body.appendChild(h('section', { class: 'col', style: { gap: '10px', maxWidth: '820px', marginTop: '24px' } },
    h('h2', null, 'Two-step sign-in'),
    h('p', { class: 'muted', style: { margin: 0 } }, 'A six-digit code from an authenticator app on the phone, after the password. Anyone can switch it on for themselves in the menu behind their initials; here you make it a must. People who must use it set it up the next time they sign in. Directory single sign-on (OpenID Connect) uses the provider\'s own second step.'),
    h('div', { class: 'row wrap' }, h('b', null, 'Required for:'), boxes.map(([, c, l]) => h('label', { class: 'check' }, c, l))),
    h('label', { class: 'check' }, outside, 'Required for everyone signing in from outside the office networks'),
    h('div', { class: 'field' }, h('label', { for: 'office-nets' }, 'Office networks'), nets, h('span', { class: 'hint' }, 'Addresses or networks, separated by spaces, for example 192.168.1.0/24. The company VPN counts as inside when its addresses are listed.')),
    h('label', { class: 'check' }, places, 'Tell admins when someone signs in from a network they never used before'),
    h('div', null, h('button', { class: 'btn', onclick: async () => {
      try {
        await put('/api/admin/security', { two_step_roles: boxes.filter(([, c]) => c.checked).map(([k]) => k), two_step_outside_office: outside.checked, office_networks: nets.value, new_place_alerts: places.checked });
        toast('Saved', 'ok');
      } catch (e) { fail(e); }
    } }, 'Save two-step settings'))));
}

// Admin › Audit log
export function chainCheck() {
  const out = h('span', { class: 'small', role: 'status' });
  const btn = h('button', { class: 'btn sm', title: 'Recompute the fingerprint chain of the whole audit log', onclick: async () => {
    btn.disabled = true;
    try {
      const r = await get('/api/audit/verify');
      mount(out, r.ok ? h('span', { class: 'sev-info' }, `The chain is whole: ${r.checked} entries checked.`)
        : h('span', { class: 'sev-error', style: { color: 'var(--err)' } }, `Entry ${r.broken_at} (${r.at}) does not fit: ${r.reason} Keep a copy of the database and tell your IT department.`));
    } catch (e) { fail(e); } finally { btn.disabled = false; }
  } }, 'Check the audit chain');
  return h('div', { class: 'row', style: { gap: '10px', marginBottom: '10px' } }, btn, out);
}

// Admin › Users › Security (one person)
export function userSecurityDialog(u, redraw) {
  const save = (name, data) => {
    const a = h('a', { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })), download: name });
    document.body.appendChild(a); a.click(); a.remove();
  };
  const confirmBox = h('input', { class: 'input', placeholder: u.username, 'aria-label': 'User name to confirm' });
  const d = dialog({ title: `Security: ${u.full_name}`, wide: true, body: [
    h('h3', { style: { marginTop: 0 } }, 'Two-step sign-in'),
    h('div', { class: 'row' }, h('span', { style: { flex: 1 } }, u.two_step_on ? 'On.' : 'Off.'),
      h('button', { class: 'btn sm', disabled: !u.two_step_on, title: u.two_step_on ? 'For a lost phone: switch it off and sign them out. Where their role needs it, they set it up again at the next sign-in.' : 'Two-step sign-in is off for this person', onclick: async () => {
        if (!await confirmDlg('Reset two-step sign-in', `${u.full_name} is signed out and sets up two-step sign-in again (if their role needs it) at the next sign-in. Only do this when you are sure it is them asking.`, 'Reset')) return;
        try { await post(`/api/users/${u.id}/two-step/reset`); toast('Reset', 'ok'); d.close(); redraw(); } catch (e) { fail(e); }
      } }, 'Reset (lost phone)')),
    h('h3', null, 'Downloads'),
    h('div', { class: 'row' }, h('span', { style: { flex: 1 } }, u.downloads_paused_at ? `Paused by the bulk-download alarm on ${new Date(u.downloads_paused_at).toLocaleString()}.` : 'Not paused.'),
      h('button', { class: 'btn sm', disabled: !u.downloads_paused_at, title: u.downloads_paused_at ? 'Let this person download again' : 'Their downloads are not paused', onclick: async () => {
        try { await post(`/api/users/${u.id}/downloads/resume`); toast('Downloads continue', 'ok'); d.close(); redraw(); } catch (e) { fail(e); }
      } }, 'Let downloads continue')),
    h('h3', null, 'Personal data'),
    h('div', { class: 'row' }, h('span', { style: { flex: 1 } }, 'Everything Kosha holds about this person, as one file, for a data-protection request.'),
      h('button', { class: 'btn sm', onclick: async () => { try { save(`kosha-personal-data-${u.username}.json`, await get(`/api/users/${u.id}/personal-data`)); } catch (e) { fail(e); } } }, 'Export')),
    h('p', { class: 'small muted' }, 'Erasing removes their name, user name, e-mail, sign-in history and settings, and switches the account off. Drawings, approvals and the audit log stay, naming them "Former user". It cannot be undone.'),
    h('div', { class: 'row' }, confirmBox, h('button', { class: 'btn sm danger', onclick: async () => {
      try { await post(`/api/users/${u.id}/erase`, { confirm: confirmBox.value }); toast('Personal data erased', 'ok'); d.close(); redraw(); } catch (e) { fail(e); }
    } }, 'Erase personal data'))],
  actions: [{ label: 'Close' }] });
}
