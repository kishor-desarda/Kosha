// SPDX-License-Identifier: AGPL-3.0-or-later
import { S, h, mount, icon, logo, api, get, post, toast, fail, initials, logout, contextMenu, closeMenu, store, can } from './core.js';
import { renderHome } from './home.js';
import { renderExplorer } from './explorer.js';
import { closeViewer } from './viewer.js';
import { renderDoc, renderItem } from './docpage.js';
import { renderBoms, renderBom } from './bom.js';
import { renderChanges, renderChange } from './changes.js';
import { renderRequests, renderRequest, delegateDialog } from './ecr.js';
import { renderErp } from './erp.js';
import { renderCommon } from './common.js';
import { renderAdmin, renderSearch } from './admin.js';
import { renderLibrary } from './library.js';
import { renderDeviations, renderDeviation } from './deviations.js';
import { notificationBell } from './notifications.js';
import { renderInputs, renderInput, renderProjects, renderProject } from './inputs.js';
import { applyTheme, currentTheme, themeDialog, themeLabel } from './theme.js';
import { providerChoices, finishSso, ssoErrorText } from './signin.js';
import { renderAsk, askEntry } from './ai.js';

const NAV = [
  ['home', 'Home', 'home', '#/home'],
  ['kosha', 'Explorer', 'kosha', '#/files'],
  ['inputs', 'Inputs', 'layers', '#/inputs'],
  ['boms', 'BOMs', 'bom', '#/boms'],
  ['changes', 'Changes', 'change', '#/changes'],
  ['deviations', 'Deviations', 'warn', '#/deviations'],
  ['erp', 'ERP sync', 'erp', '#/erp'],
  ['common', 'Duplicates', 'common', '#/common'],
  ['library', 'Library', 'grid', '#/library'],
];

// a vendor account sees only its projects and their parts and files
const VENDOR_NAV = [['projects', 'My projects', 'layers', '#/projects']];
const VENDOR_ROUTES = new Set(['projects', 'project', 'item', 'doc', 'search']);
const isVendor = () => S.user && S.user.kind === 'vendor';
const homeRoute = () => (isVendor() ? '#/projects' : '#/home');

const ROUTES = [
  [/^#\/home$/, 'home', () => renderHome],
  [/^#\/files(?:\/(\d+))?$/, 'kosha', () => renderExplorer],
  [/^#\/doc\/(\d+)$/, 'kosha', () => renderDoc],
  [/^#\/item\/(\d+)$/, 'boms', () => renderItem],
  [/^#\/boms$/, 'boms', () => renderBoms],
  [/^#\/bom\/(\d+)$/, 'boms', () => renderBom],
  [/^#\/changes$/, 'changes', () => renderChanges],
  [/^#\/change\/(\d+)$/, 'changes', () => renderChange],
  [/^#\/ecrs$/, 'changes', () => renderRequests],
  [/^#\/ecr\/(\d+)$/, 'changes', () => renderRequest],
  [/^#\/deviations$/, 'deviations', () => renderDeviations],
  [/^#\/deviation\/(\d+)$/, 'deviations', () => renderDeviation],
  [/^#\/erp$/, 'erp', () => renderErp],
  [/^#\/common$/, 'common', () => renderCommon],
  [/^#\/library$/, 'library', () => renderLibrary],
  [/^#\/admin$/, 'admin', () => renderAdmin],
  [/^#\/search$/, 'search', () => renderSearch],
  [/^#\/inputs$/, 'inputs', () => renderInputs],
  [/^#\/input\/(\d+)$/, 'inputs', () => renderInput],
  [/^#\/projects$/, 'inputs', () => renderProjects],
  [/^#\/project\/(\d+)$/, 'inputs', () => renderProject],
  [/^#\/ask$/, 'search', () => renderAsk],
];

const root = document.getElementById('app');
let shell = null;
let cleanup = null;
let routeSeq = 0;

function setMode(mode, save = true) {
  S.mode = mode;
  document.body.classList.toggle('mode-guided', mode === 'guided');
  document.body.classList.toggle('mode-expert', mode === 'expert');
  if (shell) {
    for (const b of shell.modeBtns) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
  }
  if (save && S.user) { post('/api/auth/mode', { mode }).catch(() => {}); S.user.ui_mode = mode; route(); }
}

function buildShell() {
  const rail = h('nav', { class: 'rail', 'aria-label': 'Main' }, h('div', { class: 'logo' }, logo(32)));
  const links = {};
  for (const [key, label, ic, href] of (isVendor() ? VENDOR_NAV : NAV)) {
    const badge = h('span', { class: 'badge', hidden: true });
    const a = h('a', { href }, icon(ic, 22), label, badge);
    links[key] = { a, badge };
    rail.appendChild(a);
  }
  rail.appendChild(h('div', { class: 'spacer' }));
  if (S.user.role === 'admin' || S.user.role === 'approver') {
    const a = h('a', { href: '#/admin' }, icon('admin', 22), 'Admin');
    links.admin = { a };
    rail.appendChild(a);
  }
  const titleEl = h('div', { class: 'title' });
  const q = h('input', { type: 'search', id: 'global-search', placeholder: 'Search part number, description or file name   ( / )', autocomplete: 'off' });
  q.addEventListener('keydown', e => {
    if (e.key === 'Enter' && q.value.trim()) location.hash = `#/search?q=${encodeURIComponent(q.value.trim())}`;
  });
  const modeBtns = ['guided', 'expert'].map(m => h('button', { 'data-mode': m, 'aria-pressed': 'false', onclick: () => setMode(m),
    title: m === 'guided' ? 'Bigger buttons, plain-language help and next-step hints' : 'Compact lists, keyboard shortcuts' },
  m === 'guided' ? 'Guided' : 'Expert'));
  const avatar = h('button', { class: 'avatar', 'aria-label': 'Account menu', title: S.user.full_name }, initials(S.user.full_name));
  avatar.addEventListener('click', e => {
    const r = avatar.getBoundingClientRect();
    contextMenu(r.right - 240, r.bottom + 6, [
      { label: `${S.user.full_name} (${S.user.role})`, icon: 'users', disabled: true },
      '-',
      { label: 'My notifications', icon: 'bell', run: () => import('./notify.js').then(m => m.myNotifications()) },
      ...(can('approver') || can('quality') ? [{ label: 'Stand-in while I am away', icon: 'users', run: delegateDialog }] : []),
      { label: `Theme: ${themeLabel(currentTheme())}`, icon: 'theme', run: themeDialog },
      { label: 'Change password', icon: 'checkout', run: changePassword },
      { label: 'Sign out', icon: 'logout', run: () => logout() },
    ]);
  });
  const top = h('header', { class: 'topbar' }, titleEl,
    h('div', { class: 'search' }, h('label', { for: 'global-search', class: 'sr-only' }, 'Search Kosha'), icon('search', 16), q),
    askEntry(q),     // only when an administrator switched Ask Kosha on
    h('span', { class: 'spacer' }),
    h('div', { class: 'seg', role: 'group', 'aria-label': 'Interface mode' }, modeBtns),
    notificationBell(), avatar);
  const page = h('main', { class: 'page', id: 'page', tabindex: '-1' });
  const app = h('div', { class: 'app' }, rail, h('div', { class: 'main' }, top, page));
  mount(root, app);
  shell = { links, titleEl, page, modeBtns, q };
  setMode(S.mode, false);
  refreshBadges();
}

async function refreshBadges() {
  if (!shell || isVendor()) return;
  try {
    const d = await api('GET', '/api/dashboard', undefined, { background: true });
    const set = (key, n) => { const l = shell.links[key]; if (l && l.badge) { l.badge.hidden = !n; l.badge.textContent = n; } };
    set('changes', d.approvals.length);
    set('erp', d.erp.failed);
    set('common', d.common_parts.length);
    set('home', d.checked_out.length);
    set('deviations', (await get('/api/deviations/summary')).waiting_for_me);
  } catch (e) { /* ignore */ }
}
setInterval(refreshBadges, 60000);
window.addEventListener('kosha:changed', () => setTimeout(refreshBadges, 300));

export function setTitle(t) { if (shell) shell.titleEl.textContent = t; document.title = `${t} — Kosha`; }

function changePassword() {
  import('./core.js').then(({ dialog, h: hh, post: pp, toast: tt }) => {
    const cur = hh('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
    const nw = hh('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
    dialog({ title: 'Change password', body: [hh('div', { class: 'field' }, hh('label', null, 'Current password'), cur),
      hh('div', { class: 'field' }, hh('label', null, 'New password'), nw, hh('span', { class: 'hint' }, 'At least 10 characters. Not your user name and not a common password. You stay signed in here; Kosha signs you out everywhere else.'))],
    actions: [{ label: 'Cancel' }, { label: 'Change', primary: true, onClick: async () => {
      const r = await pp('/api/auth/password', { current: cur.value, new: nw.value });
      if (r.token) { S.token = r.token; localStorage.setItem('kosha.token', r.token); }
      tt('Password changed. Other sessions were signed out.', 'ok');
    } }] });
  });
}

function signedIn(r) {
  S.token = r.token; S.user = r.user; S.erp = r.erp_name || 'ERP'; S.mode = r.user.ui_mode || 'guided';
  S.defaultTheme = r.default_theme || 'system';
  applyTheme(currentTheme());
  localStorage.setItem('kosha.token', r.token);
  buildShell();
  location.hash = store('lastRoute') && store('lastRoute') !== '#/login' && !isVendor() ? store('lastRoute') : homeRoute();
  route();
}

function renderLogin(message = '') {
  shell = null;
  const user = h('input', { class: 'input', id: 'u', autocomplete: 'username', required: true });
  const pw = h('input', { class: 'input', id: 'p', type: 'password', autocomplete: 'current-password', required: true });
  const shown = message || (sessionStorage.getItem('kosha.expired') ? 'Your session ended. Please sign in again.' : '');
  const err = h('div', { class: 'banner err', role: 'alert', hidden: !shown }, icon('warn'), h('span', null, shown));
  sessionStorage.removeItem('kosha.expired');
  const btn = h('button', { class: 'btn primary', type: 'submit', style: { minHeight: '46px', justifyContent: 'center', fontSize: '15px' } }, 'Sign in');
  const subtitle = h('p', { class: 'muted', style: { margin: '6px 0 0' } }, 'Use the account your Kosha administrator gave you.');
  const choices = h('div', { class: 'col', style: { gap: '16px' }, hidden: true });
  const form = h('form', { novalidate: true },
    h('div', null, h('h1', { style: { fontSize: '30px' } }, 'Sign in'), subtitle),
    err,
    choices,
    h('div', { class: 'field' }, h('label', { for: 'u' }, 'User name'), user),
    h('div', { class: 'field' }, h('label', { for: 'p' }, 'Password'), pw),
    btn);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    btn.disabled = true;
    try {
      signedIn(await api('POST', '/api/auth/login', { username: user.value.trim(), password: pw.value }));
    } catch (ex) {
      let msg = ex.message;
      if (ex.code === 'sign_in_paused' && ex.data && ex.data.retry_at) {
        const at = new Date(ex.data.retry_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        msg = `${msg} (You can try again at ${at}.)`;
      }
      err.hidden = false; mount(err, icon('warn'), h('span', null, msg));
    } finally { btn.disabled = false; }
  });
  mount(root, h('div', { class: 'login' },
    h('div', { class: 'art' },
      h('div', { class: 'row' }, h('img', { class: 'login-logo', src: '/static/brand/kosha-logo-dark.svg', alt: 'Kosha', width: 166, height: 48 }),
        h('span', { class: 'wordmark' }, 'PLM')),
      h('div', null, h('h1', null, 'The right revision, every time.'),
        h('p', { class: 'lead' },
          'Drawings, models, BOMs and change orders for every product line, in one place.')),
      h('div', { class: 'foot' }, 'Kosha — open-source PLM')),
    h('div', { class: 'form' }, form)));
  providerChoices(choices, subtitle);
  setTimeout(() => user.focus(), 50);
}

// Back from a redirect sign-in (OpenID Connect): the server put a one-time ticket, or why it failed, in the address.
async function returnFromSso(hash) {
  const ticket = hash.match(/^#\/sso\/([\w-]+)$/);
  const failed = hash.match(/^#\/sso-error\/(\w+)$/);
  if (!ticket && !failed) return false;
  history.replaceState(null, '', '#/login');      // the ticket works once; keep it out of the history
  if (failed) {
    if (S.token) { toast(ssoErrorText(failed[1]), 'err'); location.hash = '#/home'; } else renderLogin(ssoErrorText(failed[1]));
    return true;
  }
  try {
    signedIn(await finishSso(ticket[1]));
  } catch (e) {
    if (S.token) { toast(e.message, 'err'); location.hash = '#/home'; } else renderLogin(e.message);
  }
  return true;
}

async function route() {
  closeMenu();
  closeViewer();
  document.querySelectorAll('dialog[open]').forEach(d => { d.close(); d.remove(); });
  const hash = location.hash || '#/home';
  if (await returnFromSso(hash)) return;
  // changing the hash routes again and draws the sign-in form then; drawing it here as well made it appear twice,
  // and what someone typed into the first one was lost
  if (!S.token) { if (hash !== '#/login') location.hash = '#/login'; else renderLogin(); return; }
  if (hash === '#/login') { location.hash = homeRoute(); return; }
  if (!S.user) {
    try {
      const me = await get('/api/auth/me');
      S.user = me.user; S.erp = me.erp_name || 'ERP'; S.mode = S.user.ui_mode || 'guided';
      S.defaultTheme = me.default_theme || 'system';
      applyTheme(currentTheme());    // the account's choice wins over the one remembered in this browser
    } catch (e) { return; }
  }
  if (!shell) buildShell();
  const [path, query] = hash.split('?');
  if (isVendor() && !VENDOR_ROUTES.has((path.match(/^#\/(\w+)/) || [])[1])) { location.hash = '#/projects'; return; }
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  for (const [re, key, loader] of ROUTES) {
    const m = path.match(re);
    if (!m) continue;
    for (const [k, l] of Object.entries(shell.links)) l.a.classList.toggle('active', k === key);
    if (typeof cleanup === 'function') { try { cleanup(); } catch (e) { /* ignore */ } }
    cleanup = null;
    store('lastRoute', hash);
    // Every page gets a fresh container: a page still loading when the user moves on finishes into a detached
    // element instead of overwriting the page they moved to, and its clean-up runs straight away.
    const seq = ++routeSeq;
    const container = h('div', { class: 'route-view' });
    mount(shell.page, container);
    shell.page.scrollTop = 0;
    try {
      const done = await loader()(container, m.slice(1), params, { setTitle });
      if (seq === routeSeq) cleanup = done;
      else if (typeof done === 'function') { try { done(); } catch (e) { /* ignore */ } }
    } catch (e) { if (seq === routeSeq) fail(e); }
    return;
  }
  location.hash = '#/home';
}

window.addEventListener('hashchange', route);
document.addEventListener('keydown', e => {
  if (e.key === '/' && shell && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
    e.preventDefault(); shell.q.focus(); shell.q.select();
  }
});
document.body.classList.add('mode-guided');
route();
export { refreshBadges, toast };
