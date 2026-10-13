// SPDX-License-Identifier: AGPL-3.0-or-later
// Administration (users, rules, audit) and global search results.
import { S, h, mount, icon, get, post, put, patch, toast, fail, dialog, fmtDate, ago, tabs, stateChip, typeChip, thumb, can, upload, newPassword, confirmDlg } from './core.js';
import { addinsTab } from './addins.js';
import { backupsTab, securityTab } from './admin_ops.js';
import { packsTab } from './packs.js';
import { notifyAdminTab } from './notify.js';
import { THEMES, themeLabel, applyTheme, currentTheme } from './theme.js';
import { userDirectoryDialog } from './directory.js';
import { aiAdminTab, askLink } from './ai.js';

export async function renderAdmin(container, _m, params, { setTitle }) {
  setTitle('Administration');
  let tab = params.tab || (S.user.role === 'admin' ? 'users' : 'audit');
  const cleanups = [];
  const draw = async () => {
    while (cleanups.length) { try { cleanups.pop()(); } catch (e) { /* ignore */ } }
    const items = S.user.role === 'admin' ? [['users', 'Users'], ['import', 'Import legacy data'], ['rules', 'Numbering & revisions'], ['cad', 'CAD conversion'], ['addins', 'Add-ins'], ['security', 'Sign-in & sessions'], ['backups', 'Backups'], ['packs', 'Packs'], ['notify', 'Notifications'], ['ai', 'AI assistant'], ['audit', 'Audit log']] : [['audit', 'Audit log']];
    const body = h('div', { class: 'panel-b' });
    mount(container, h('div', { class: 'page-pad' }, h('div', { class: 'page-head' }, h('h1', null, 'Administration')),
      h('div', { class: 'panel' }, tabs(items, tab, k => { tab = k; draw(); }), body)));
    if (tab === 'users') await users(body, draw);
    if (tab === 'import') { const stop = await legacyImport(body); cleanups.push(stop); }
    if (tab === 'rules') await rules(body);
    if (tab === 'cad') await cad(body);
    if (tab === 'addins') await addinsTab(body);
    if (tab === 'security') await securityTab(body);
    if (tab === 'backups') await backupsTab(body);
    if (tab === 'packs') await packsTab(body);
    if (tab === 'notify') await notifyAdminTab(body);
    if (tab === 'ai') await aiAdminTab(body);
    if (tab === 'audit') await audit(body);
  };
  await draw();
  return () => { while (cleanups.length) { try { cleanups.pop()(); } catch (e) { /* ignore */ } } };
}

const GROUPS = [['solidworks', 'SolidWorks (.sldprt .sldasm .slddrw)'], ['cad2d', 'AutoCAD / GStarCAD (.dwg .dxf)'], ['pdf', 'PDF'],
  ['neutral3d', 'STEP / IGES / STL'], ['office', 'Excel / Word'], ['images', 'Images (.png .jpg .tif)']];

async function legacyImport(body) {
  const today = new Date().toISOString().slice(0, 10);
  const src = h('input', { class: 'input mono', id: 'im-src', placeholder: '\\\\fileserver\\Drawings   or   D:\\Old Drawings' });
  const dst = h('input', { class: 'input', id: 'im-dst', value: `Legacy Drawings/Import ${today}` });
  const state = h('select', { class: 'input', id: 'im-state' }, h('option', { value: 'released' }, 'Released (already issued drawings)'), h('option', { value: 'in_work' }, 'In work (to be checked first)'));
  const keepRev = h('input', { type: 'checkbox', checked: true });
  const skipDup = h('input', { type: 'checkbox', checked: true });
  const dry = h('input', { type: 'checkbox', checked: true });
  const meta = h('input', { type: 'file', class: 'input', id: 'im-meta', accept: '.xlsx,.csv' });
  const groups = Object.fromEntries(GROUPS.map(([k], i) => [k, h('input', { type: 'checkbox', checked: i < 4 })]));
  const pv = h('div');
  const jobsBox = h('div');
  const selGroups = () => GROUPS.map(([k]) => k).filter(k => groups[k].checked);
  const fmtBytes = n => n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`;

  const check = async () => {
    try {
      const r = await post('/api/import/preview', { source: src.value.trim(), groups: selGroups() });
      const exts = Object.entries(r.by_ext).sort((a, b) => b[1].files - a[1].files).slice(0, 12);
      mount(pv, h('div', { class: 'banner ok small' }, icon('check'), h('div', null,
        h('b', null, `Found ${r.files.toLocaleString()} files in ${r.folders.toLocaleString()} folders (${fmtBytes(r.bytes)}). `),
        `${r.selected_files.toLocaleString()} match the selected file types. `,
        h('div', { class: 'mono', style: { marginTop: '4px' } }, exts.map(([e, v]) => `.${e} ${v.files}`).join('   ')),
        r.sample_folders.length ? h('div', { style: { marginTop: '4px' } }, 'Folders: ', r.sample_folders.slice(0, 8).join(' · ')) : null,
        !r.dwg_converter && r.by_ext.dwg ? h('div', { class: 'sev-warning', style: { marginTop: '6px' } }, `${r.by_ext.dwg.files} DWG files: install the ODA File Converter first (CAD conversion tab) so they get previews and duplicate detection. Otherwise they are stored without previews.`) : null)));
    } catch (e) { mount(pv, h('div', { class: 'banner err small' }, icon('warn'), h('div', null, e.message))); }
  };

  const start = async () => {
    const fd = new FormData();
    fd.append('source', src.value.trim());
    fd.append('target', dst.value.trim());
    fd.append('state', state.value);
    fd.append('keep_revision', keepRev.checked ? '1' : '0');
    fd.append('skip_identical', skipDup.checked ? '1' : '0');
    fd.append('dry_run', dry.checked ? '1' : '0');
    fd.append('groups', selGroups().join(','));
    if (meta.files[0]) fd.append('metadata', meta.files[0]);
    try { await upload('/api/import/start', fd); toast(dry.checked ? 'Dry run started: nothing will be changed' : 'Import started', 'ok'); loadJobs(); }
    catch (e) { fail(e); }
  };

  const loadJobs = async () => {
    const r = await get('/api/import/jobs');
    mount(jobsBox, r.jobs.length ? h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['#', 'Source → Kosha folder', 'Status', 'Progress', 'Imported', 'Identical', 'Skipped', 'Errors', ''].map(t => h('th', null, t)))),
      h('tbody', null, r.jobs.map(j => h('tr', null,
        h('td', null, j.id),
        h('td', { style: { maxWidth: '380px' } }, h('div', { class: 'mono small ellipsis', title: j.source }, j.source), h('div', { class: 'small' }, '→ ', j.target_folder_id ? h('a', { href: `#/files/${j.target_folder_id}` }, j.target) : j.target),
          j.options.dry_run ? h('span', { class: 'chip st-draft' }, 'dry run') : null),
        h('td', null, h('span', { class: `chip st-${j.status}` }, j.status), h('div', { class: 'small muted' }, j.phase)),
        h('td', { style: { minWidth: '120px' } }, h('div', { class: 'progress' }, h('div', { style: { width: `${j.total ? Math.round(100 * Math.min(j.done, j.total) / j.total) : (j.status === 'done' ? 100 : 0)}%` } })),
          h('div', { class: 'small muted' }, `${j.done} / ${j.total}`)),
        h('td', { class: 'num' }, j.imported), h('td', { class: 'num' }, j.duplicates), h('td', { class: 'num' }, j.skipped),
        h('td', { class: 'num' }, j.errors ? h('b', { class: 'sev-error' }, j.errors) : 0),
        h('td', { class: 'nowrap' }, h('div', { class: 'row' },
          j.report_sha ? h('a', { class: 'btn sm', href: `/api/import/jobs/${j.id}/report?access_token=${encodeURIComponent(S.token)}` }, icon('download', 14), 'Report') : null,
          j.status === 'running' ? h('button', { class: 'btn sm', onclick: async () => { await post(`/api/import/jobs/${j.id}/cancel`); loadJobs(); } }, 'Cancel') : null,
          j.status === 'done' && !j.options.dry_run && j.target_folder_id ? h('a', { class: 'btn sm', href: `#/common?folder=${j.target_folder_id}` }, icon('common', 14), 'Find duplicates') : null))))))
      : h('div', { class: 'empty' }, 'No imports yet.'));
    return r.jobs.some(j => j.status === 'running');
  };

  mount(body, h('div', { class: 'col', style: { gap: '18px', maxWidth: '1100px' } },
    h('div', { class: 'banner info' }, icon('info'), h('div', null,
      h('b', null, 'How it works. '), 'The server reads the folder (a network share or a disk on the server), recreates its sub-folders in Kosha, and checks every file in once. ',
      'Identical copies are found by content and imported only once (the copy with the best name is kept). Part number, description, material and revision are read from title blocks and file names, or from a part master spreadsheet. ',
      'Start with a ', h('b', null, 'dry run'), ': it changes nothing and gives you an Excel report of what would happen. Re-running an import is safe: files already imported are skipped.')),
    h('div', { class: 'form-grid' },
      h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'im-src' }, 'Folder to import (as the server sees it)'),
        h('div', { class: 'row' }, src, h('button', { class: 'btn', onclick: check }, 'Check folder')),
        h('span', { class: 'hint' }, 'The Kosha service account needs read access to network shares.')),
      h('div', { class: 'field' }, h('label', { for: 'im-dst' }, 'Put it in Kosha folder'), dst),
      h('div', { class: 'field' }, h('label', { for: 'im-state' }, 'Imported files are'), state),
      h('div', { class: 'field' }, h('label', { for: 'im-meta' }, 'Part master spreadsheet (optional)'), meta,
        h('span', { class: 'hint' }, 'Excel/CSV with Part number (Item Code), Description, Material, UOM, Make/Buy; a File name column links rows to files.'))),
    pv,
    h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'File types'), h('div', { class: 'row wrap' }, GROUPS.map(([k, l]) => h('label', { class: 'check small' }, groups[k], l)))),
    h('div', { class: 'col', style: { gap: '2px' } },
      h('label', { class: 'check' }, keepRev, 'Keep the revision from the title block (Rev 3, R-2, B…). Otherwise files start at A.'),
      h('label', { class: 'check' }, skipDup, 'Import identical copies only once'),
      h('label', { class: 'check' }, dry, h('b', null, 'Dry run'), ' — only report what would happen')),
    h('div', null, h('button', { class: 'btn primary', onclick: start }, icon('upload', 16), 'Start import')),
    h('h2', null, 'Imports'), jobsBox));
  let running = await loadJobs();
  const timer = setInterval(async () => { if (running || document.querySelector('.chip.st-running')) running = await loadJobs(); }, 1500);
  return () => clearInterval(timer);
}

async function users(body, draw) {
  const [r, settings] = await Promise.all([get('/api/users'), get('/api/settings')]);
  const themeSel = h('select', { class: 'input', id: 'st-theme', style: { width: 'auto', minHeight: '32px' } },
    THEMES.map(([k, label]) => h('option', { value: k, selected: settings.default_theme === k }, label)));
  themeSel.addEventListener('change', async () => {
    try {
      await put('/api/settings', { default_theme: themeSel.value });
      S.defaultTheme = themeSel.value; applyTheme(currentTheme());
      toast(`Saved. Everyone who has not chosen a theme sees ${themeLabel(themeSel.value)} the next time they open Kosha.`, 'ok');
    } catch (e) { fail(e); }
  });
  const roleSel = (u) => {
    const s = h('select', { class: 'input', style: { minHeight: '30px', width: 'auto' }, 'aria-label': `Role of ${u.full_name}` },
      ['viewer', 'engineer', 'approver', 'admin'].map(x => h('option', { value: x, selected: u.role === x }, x)));
    s.addEventListener('change', async () => { try { await patch(`/api/users/${u.id}`, { role: s.value }); toast('Role changed', 'ok'); } catch (e) { fail(e); } });
    return s;
  };
  mount(body,
    h('div', { class: 'row', style: { marginBottom: '12px' } }, h('div', { class: 'muted small', style: { flex: 1 } },
      'Viewer: see and download. Engineer: add, check in/out, revise, build BOMs, raise changes. Approver: also release, approve changes, push to ERP. Admin: everything.'),
    h('button', { class: 'btn primary', onclick: () => addUser(draw) }, icon('plus', 16), 'Add user')),
    h('div', { class: 'row wrap', style: { marginBottom: '12px' } }, h('label', { for: 'st-theme', style: { fontWeight: 600 } }, 'Theme for everyone who has not chosen one'), themeSel,
      h('span', { class: 'muted small' }, 'Each person can choose their own theme in the menu behind their initials.')),
    h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Name', 'User name', 'Role', 'Mode', 'Theme', 'Status', ''].map(t => h('th', null, t)))),
      h('tbody', null, r.users.map(u => h('tr', null, h('td', null, u.full_name), h('td', { class: 'mono' }, u.username), h('td', null, roleSel(u)), h('td', null, u.ui_mode),
        h('td', { class: u.ui_theme ? '' : 'muted' }, u.ui_theme ? themeLabel(u.ui_theme) : 'Installation default'),
        h('td', null, h('span', { class: `chip ${u.active ? 'st-released' : 'st-obsolete'}` }, u.active ? 'Active' : 'Disabled')),
        h('td', { class: 'nowrap' }, h('div', { class: 'row' },
          h('button', { class: 'btn sm', onclick: () => resetPw(u) }, 'Reset password'),
          h('button', { class: 'btn sm', title: 'End all sessions of this user, in browsers and CAD add-ins. They can sign in again at once.', onclick: () => signOutEverywhere(u) }, 'Sign out everywhere'),
          h('button', { class: 'btn sm', title: 'Link this user to an account in Active Directory, LDAP or an OpenID Connect provider', onclick: () => userDirectoryDialog(u) }, 'Directory'),
          u.id !== S.user.id ? h('button', { class: 'btn sm', onclick: async () => { try { await patch(`/api/users/${u.id}`, { active: !u.active }); draw(); } catch (e) { fail(e); } } }, u.active ? 'Disable' : 'Enable') : null)))))));
}

function addUser(draw) {
  const f = { username: h('input', { class: 'input', id: 'nu-u' }), full_name: h('input', { class: 'input', id: 'nu-n' }), email: h('input', { class: 'input', id: 'nu-e', type: 'email' }),
    password: h('input', { class: 'input', id: 'nu-p', type: 'text', value: newPassword() }),
    role: h('select', { class: 'input', id: 'nu-r' }, ['engineer', 'approver', 'viewer', 'admin'].map(x => h('option', { value: x }, x))),
    ui_mode: h('select', { class: 'input', id: 'nu-m' }, h('option', { value: 'guided' }, 'Guided (new users)'), h('option', { value: 'expert' }, 'Expert')) };
  const L = { username: 'User name', full_name: 'Full name', email: 'Email', password: 'Initial password', role: 'Role', ui_mode: 'Starts in' };
  dialog({ title: 'Add user', body: h('div', { class: 'form-grid' }, Object.entries(f).map(([k, el]) => h('div', { class: 'field' }, h('label', { for: el.id }, L[k]), el))),
    actions: [{ label: 'Cancel' }, { label: 'Add user', primary: true, onClick: async () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()]));
      await post('/api/users', body); toast(`User added. Tell them their password: ${body.password}`, 'ok'); draw();
    } }] });
}

function resetPw(u) {
  const p = h('input', { class: 'input', id: 'rp', value: newPassword() });
  dialog({ title: `Reset password for ${u.full_name}`, body: h('div', { class: 'field' }, h('label', { for: 'rp' }, 'New password'), p,
    h('span', { class: 'hint' }, 'At least 10 characters. Resetting the password also signs this user out everywhere.')),
  actions: [{ label: 'Cancel' }, { label: 'Reset', primary: true, onClick: async () => { await patch(`/api/users/${u.id}`, { password: p.value }); toast(`Password reset. Tell ${u.full_name} the new password.`, 'ok'); } }] });
}

async function signOutEverywhere(u) {
  if (!await confirmDlg(`Sign ${u.full_name} out everywhere?`, `This ends every session of ${u.full_name} in browsers and CAD add-ins. Unsaved work in Kosha forms is lost; files and check-outs are safe. They can sign in again at once.`, 'Sign out everywhere')) return;
  try {
    const r = await post(`/api/users/${u.id}/sign-out-everywhere`);
    toast(`${r.sessions_ended} session${r.sessions_ended === 1 ? '' : 's'} ended.`, 'ok');
  } catch (e) { fail(e); }
}

async function rules(body) {
  const s = await get('/api/settings');
  const rx = h('input', { class: 'input mono', id: 'st-rx', value: s.part_number_regex });
  const skip = h('input', { class: 'input mono', id: 'st-skip', value: s.revision_skip_letters });
  const first = h('input', { class: 'input mono', id: 'st-first', value: s.first_revision, style: { maxWidth: '120px' } });
  const eco = h('input', { type: 'checkbox', checked: !!s.require_eco_for_release });
  const sample = h('input', { class: 'input', id: 'st-sample', value: 'MX-100-020 Main Shaft.SLDPRT' });
  const out = h('div', { class: 'small' });
  const test = () => {
    try {
      const m = sample.value.replace(/\.[^.]+$/, '').trim().match(new RegExp(rx.value, 'i'));
      out.textContent = m ? `Part number: ${(m[1] || '').toUpperCase()}   Description: ${m[2] || '(none)'}` : 'No part number found in this name.';
    } catch (e) { out.textContent = `Pattern error: ${e.message}`; }
  };
  rx.addEventListener('input', test); sample.addEventListener('input', test); test();
  mount(body, h('div', { class: 'col', style: { gap: '16px', maxWidth: '820px' } },
    h('div', { class: 'field' }, h('label', { for: 'st-rx' }, 'Part number pattern in file names'), rx,
      h('span', { class: 'hint' }, 'Regular expression. Group 1 is the part number, group 2 the description. Title-block attributes in DWG/DXF and SolidWorks properties take priority.')),
    h('div', { class: 'field' }, h('label', { for: 'st-sample' }, 'Try a file name'), sample, out),
    h('div', { class: 'form-grid' },
      h('div', { class: 'field' }, h('label', { for: 'st-first' }, 'First revision'), first),
      h('div', { class: 'field' }, h('label', { for: 'st-skip' }, 'Letters never used for revisions'), skip, h('span', { class: 'hint' }, 'ASME Y14.35 skips I, O, Q, S, X, Z'))),
    h('label', { class: 'check' }, eco, 'Files can only be released through an approved change order'),
    h('div', null, h('button', { class: 'btn primary', onclick: async () => {
      try { await put('/api/settings', { part_number_regex: rx.value, revision_skip_letters: skip.value, first_revision: first.value, require_eco_for_release: eco.checked }); toast('Saved', 'ok'); } catch (e) { fail(e); }
    } }, 'Save'))));
}

async function cad(body) {
  const s = await get('/api/settings');
  const st = s.dwg_converter;
  mount(body, h('div', { class: 'col', style: { gap: '14px', maxWidth: '820px' } },
    h('h2', null, 'DWG previews (AutoCAD and GStarCAD)'),
    st.oda || st.libredwg ? h('div', { class: 'banner ok' }, icon('check'), h('div', null, `DWG conversion is available: ${st.oda || st.libredwg}`))
      : h('div', { class: 'banner' }, icon('warn'), h('div', null, 'No DWG converter found, so DWG files are stored and versioned but have no preview or common-part detection. Install the free ODA File Converter on the server and set KOSHA_ODA_CONVERTER in kosha.env, then restart Kosha.')),
    h('p', { class: 'muted' }, 'After installing a converter, regenerate previews for files that were added before.'),
    h('div', null, h('button', { class: 'btn', onclick: async () => { try { await post('/api/documents/reprocess-all'); toast('Regenerating previews in the background', 'ok'); } catch (e) { fail(e); } } }, icon('refresh', 16), 'Regenerate previews')),
    h('h2', null, 'SolidWorks'),
    h('p', { class: 'muted', style: { margin: 0, lineHeight: 1.5 } }, 'Install Kosha add-in on each SolidWorks 2021 PC (see addins/solidworks/README.md). It checks files in with their custom properties, a 3D model for the viewer and the assembly structure for the BOM. Files added through the browser show the preview image SolidWorks embeds, when one is present.')));
}

async function audit(body) {
  const r = await get('/api/audit?limit=300');
  mount(body, h('div', { style: { maxHeight: '70vh', overflow: 'auto' } }, h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['When', 'Who', 'Action', 'What', 'Details'].map(t => h('th', null, t)))),
    h('tbody', null, r.entries.map(e => h('tr', null, h('td', { class: 'nowrap', title: fmtDate(e.at) }, ago(e.at)), h('td', null, e.user), h('td', { class: 'mono small' }, e.action),
      h('td', null, e.entity === 'document' && e.entity_id ? h('a', { href: `#/doc/${e.entity_id}` }, `file #${e.entity_id}`) : `${e.entity}${e.entity_id ? ' #' + e.entity_id : ''}`),
      h('td', { class: 'small' }, e.details)))))));
}

export async function renderSearch(container, _m, params, { setTitle }) {
  const q = params.q || '';
  setTitle(`Search: ${q}`);
  const r = await get(`/api/search?q=${encodeURIComponent(q)}`);
  const input = document.getElementById('global-search');
  if (input) input.value = q;
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, `Results for "${q}"`), h('div', { class: 'sub' }, `${r.documents.length} files · ${r.items.length} parts · ${r.changes.length} change orders`)),
      h('span', { class: 'spacer' }), askLink(q), h('a', { class: 'btn', href: `#/inputs?q=${encodeURIComponent(q)}` }, icon('layers', 16), 'Search inputs too')),
    h('div', { class: 'col', style: { gap: '16px' } },
      h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Files')),
        r.documents.length ? h('table', { class: 'tbl' }, h('tbody', null, r.documents.map(d => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/files/${d.folder_id}?select=${d.id}`; } },
          h('td', { style: { width: '52px' } }, thumb(d)), h('td', null, h('b', null, d.filename), h('div', { class: 'small muted' }, d.folder_name)), h('td', null, typeChip(d.ext)),
          h('td', { class: 'mono' }, d.part_number), h('td', null, d.description), h('td', null, `Rev ${d.revision}`), h('td', null, stateChip(d.state))))))
          : h('div', { class: 'empty' }, 'No files found.')),
      h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Parts')),
        r.items.length ? h('table', { class: 'tbl' }, h('tbody', null, r.items.map(it => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/item/${it.id}`; } },
          h('td', { class: 'mono' }, it.part_number), h('td', null, it.description), h('td', null, it.material), h('td', null, it.make_buy), h('td', null, stateChip(it.state))))))
          : h('div', { class: 'empty' }, 'No parts found.')),
      r.changes.length ? h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Change orders')),
        h('table', { class: 'tbl' }, h('tbody', null, r.changes.map(c => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/change/${c.id}`; } },
          h('td', { class: 'mono' }, c.number), h('td', null, c.title), h('td', null, h('span', { class: `chip st-${c.state}` }, c.state))))))) : null)));
  void can;
}
