// SPDX-License-Identifier: AGPL-3.0-or-later
// Administration: Backups (with server health) and Sign-in & sessions settings (directory sign-in: directory.js).
import { h, mount, icon, get, put, toast, fail, fmtDate, ago, fmtSize } from './core.js';
import { directorySection } from './directory.js';

const when = x => (x && x.at ? `${fmtDate(x.at)} (${ago(x.at)})` : 'Never');

function banner(kind, text) {
  return h('div', { class: `banner ${kind}` }, icon(kind === 'ok' ? 'check' : 'warn'), h('div', null, text));
}

function props(pairs) {
  return h('dl', { class: 'props' }, pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v === '' || v == null ? '—' : v)]));
}

function panel(title, ...children) {
  return h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, title)),
    h('div', { class: 'panel-b col', style: { gap: '12px' } }, children));
}

const code = text => h('code', { class: 'mono small' }, text);

export async function backupsTab(body) {
  const d = await get('/api/admin/diagnostics');
  const b = d.backup.last;
  const t = d.restore_test.last;
  const backupState = !b ? banner('', d.backup.warning) : d.backup.warning ? banner(b.ok ? '' : 'err', d.backup.warning) : banner('ok', 'The last backup worked and every copied file was checked.');
  const testState = !t ? banner('', d.restore_test.warning) : d.restore_test.warning ? banner(t.ok ? '' : 'err', d.restore_test.warning) : banner('ok', 'The last restore test worked: the backup can be restored completely.');
  const disk = d.disk;
  const counts = obj => Object.keys(obj || {}).length ? Object.entries(obj).map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(' · ') : 'none';
  const scanText = d.virus_scan.configured
    ? (d.virus_scan.reachable ? `On (${d.virus_scan.version || d.virus_scan.address})` : `Not reachable at ${d.virus_scan.address}: uploads are refused`)
    : 'Off (set KOSHA_CLAMD to scan every upload)';
  mount(body, h('div', { class: 'col', style: { gap: '18px', maxWidth: '1100px' } },
    h('div', { class: 'grid-cards' },
      panel('Last backup', backupState, props([
        ['When', when(b)],
        ['Result', b ? (b.ok ? 'Worked' : 'Failed') : ''],
        ['Backup folder', b ? h('span', { class: 'mono small' }, b.folder) : ''],
        b && b.ok ? ['Database copy', h('span', { class: 'mono small' }, b.database_copy)] : null,
        b && b.ok ? ['New files copied', String(b.new_files)] : null,
        b && !b.ok ? ['What happened', b.message] : null,
      ])),
      panel('Last restore test', testState, props([
        ['When', when(t)],
        ['Result', t ? (t.ok ? 'Worked' : 'Failed') : ''],
        ['Backup tested', t ? h('span', { class: 'mono small' }, `${t.backup_folder}${t.database_copy ? ' · ' + t.database_copy : ''}`) : ''],
        t && t.integrity ? ['Database check', t.integrity === 'ok' ? 'Passed' : t.integrity] : null,
        t ? ['Files checked', `${t.files_checked} (${t.file_versions} file versions), ${t.missing} missing, ${t.damaged} damaged`] : null,
        t && !t.ok ? ['What happened', t.message] : null,
      ]))),
    panel('How backups work',
      h('p', { class: 'muted', style: { margin: 0, lineHeight: 1.55 } },
        'The backup copies the database while Kosha runs, checks the copy, and copies every new file version, checking each one against its fingerprint. ',
        'The restore test restores the newest backup into a temporary folder and reads back every file. Keep a second copy of the backup folder on another machine.'),
      props([
        ['Back up', code('python -m kosha.backup <backup folder>')],
        ['Test a restore', code('python -m kosha.backup --verify <backup folder>')],
        ['Restore', code('python -m kosha.restore <backup folder> <new data folder>')],
        ['Export all data', code('python -m kosha.export <file.zip>')],
      ]),
      h('p', { class: 'muted small', style: { margin: 0 } }, 'On Linux, the installer runs the backup every night and the restore test every month (systemd timers kosha-backup and kosha-restore-test).')),
    panel('Server',
      disk.warning ? banner('err', disk.warning) : null,
      props([
        ['Data folder', h('span', { class: 'mono small' }, disk.path)],
        ['Free space', disk.total_bytes ? `${fmtSize(disk.free_bytes)} of ${fmtSize(disk.total_bytes)} (${disk.free_percent}%)` : disk.error],
        ['Kosha', d.versions.kosha],
        ['Database', `SQLite ${d.versions.sqlite}, ${d.versions.database_changes} database changes (latest ${d.versions.latest_database_change})`],
        ['Software', `Python ${d.versions.python}, Starlette ${d.versions.starlette}, Uvicorn ${d.versions.uvicorn}`],
        ['Add-ins', counts(d.addins)],
        ['ERP queue', counts(d.queues.erp)],
        ['Imports', counts(d.queues.imports)],
        ['Virus scan', scanText],
        ['HTTPS', d.https ? 'On (browsers are told to use HTTPS only)' : 'Not declared (set KOSHA_HTTPS=1 when Kosha is reached through HTTPS)'],
        ['Largest upload', `${d.limits.max_upload_mb} MB per request`],
      ]))));
}

const FIELDS = [
  ['session_idle_minutes', 'Sign out after this many minutes without activity', 'OWASP ASVS level 2 recommends 30 minutes.'],
  ['session_absolute_hours', 'Sign in again after this many hours in any case', 'OWASP ASVS level 2 recommends 12 hours.'],
  ['sign_in_max_failures', 'Failed sign-ins for one user name before sign-in pauses', 'The same message is shown whether or not the user name exists.'],
  ['sign_in_lock_minutes', 'Pause after too many failed sign-ins (minutes)', 'Failed sign-ins older than this are forgotten.'],
  ['sign_in_ip_max_failures', 'Failed sign-ins from one network address before sign-in pauses', 'Keep it higher than the per-user limit: many people can share one address.'],
];

export async function securityTab(body) {
  const r = await get('/api/admin/security');
  const inputs = Object.fromEntries(FIELDS.map(([k]) => [k, h('input', { class: 'input', id: `sec-${k}`, type: 'number', min: r.limits[k].min, max: r.limits[k].max, value: r.settings[k], style: { maxWidth: '140px' } })]));
  mount(body, h('div', { class: 'col', style: { gap: '16px', maxWidth: '820px' } },
    h('div', { class: 'banner info' }, icon('info'), h('div', null,
      'Passwords: ', r.password_hint, ' A password change, a reset by an admin, deactivating a user and ', h('b', null, 'Sign out everywhere'), ' (Users tab) end that user\'s sessions at once, in browsers and CAD add-ins.')),
    h('div', { class: 'form-grid' }, FIELDS.map(([k, label, hint]) => h('div', { class: 'field' }, h('label', { for: `sec-${k}` }, label), inputs[k],
      h('span', { class: 'hint' }, `${hint} Allowed: ${r.limits[k].min} to ${r.limits[k].max}; default ${r.defaults[k]}.`)))),
    h('div', null, h('button', { class: 'btn primary', onclick: async (e) => {
      e.target.disabled = true;
      try {
        await put('/api/admin/security', Object.fromEntries(FIELDS.map(([k]) => [k, Number(inputs[k].value)])));
        toast('Saved. The new limits apply to open sessions at once.', 'ok');
      } catch (ex) { fail(ex); } finally { e.target.disabled = false; }
    } }, 'Save'))));
  await directorySection(body);
}
