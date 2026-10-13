// SPDX-License-Identifier: AGPL-3.0-or-later
import { S, h, mount, icon, get, stateChip, ago, fileUrl, store, thumb } from './core.js';
import { deviationsHomePanel } from './deviations.js';

export async function renderHome(container, _m, _p, { setTitle }) {
  setTitle('Home');
  mount(container, h('div', { class: 'page-pad' }, h('p', { class: 'muted' }, 'Loading…')));
  const d = await get('/api/dashboard');
  const hour = new Date().getHours();
  const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const first = S.user.full_name.split(' ')[0];
  const summary = [];
  if (d.checked_out.length) summary.push(`${d.checked_out.length} file${d.checked_out.length > 1 ? 's' : ''} checked out`);
  if (d.approvals.length) summary.push(`${d.approvals.length} change${d.approvals.length > 1 ? 's' : ''} waiting for your approval`);
  if (d.in_review.length) summary.push(`${d.in_review.length} file${d.in_review.length > 1 ? 's' : ''} to review`);

  const card = (href, ic, title, text) => h('a', { class: 'action-card', href }, h('span', { class: 'ic' }, icon(ic, 22)), h('strong', null, title), h('span', null, text));
  const tourDismissed = store('tourDone');
  const guided = S.mode === 'guided';

  const panel = (title, count, body, link) => h('section', { class: 'panel' },
    h('div', { class: 'panel-h' }, h('h2', null, title), count ? h('span', { class: 'badge', style: { background: '#2A3A55' } }, count) : null, h('span', { class: 'spacer' }), link || null),
    body);
  const empty = t => h('div', { class: 'empty' }, t);

  const docRow = doc => h('a', { class: 'list-row', href: `#/files/${doc.folder_id}?select=${doc.id}` }, thumb(doc, ''),
    h('div', { style: { minWidth: 0, flex: 1 } }, h('div', { class: 'ellipsis', style: { fontWeight: 600 } }, doc.filename),
      h('div', { class: 'small muted ellipsis' }, doc.folder_name || '', ' · rev ', doc.revision, doc.checked_out_at ? ` · checked out ${ago(doc.checked_out_at)}` : '')),
    stateChip(doc.state));

  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, `${greet}, ${first}`),
      h('div', { class: 'sub' }, summary.length ? `You have ${summary.join(', ')}.` : 'Nothing is waiting for you right now.'))),

    guided && !tourDismissed ? h('div', { class: 'banner info', style: { marginBottom: '18px' } }, icon('help'),
      h('div', { style: { flex: 1 } }, h('b', null, 'New to Kosha? Four things to know'),
        h('ol', { style: { margin: '6px 0 0', paddingLeft: '20px', lineHeight: 1.7 } },
          h('li', null, h('b', null, 'Check out'), ' a file before you edit it. That locks it so nobody overwrites your work.'),
          h('li', null, h('b', null, 'Check in'), ' when you have saved. Every check-in is kept as a version you can go back to.'),
          h('li', null, h('b', null, 'Released'), ' files are locked and safe to manufacture. To change one, press ', h('b', null, 'Revise'), '.'),
          h('li', null, 'Right-click anything in the Explorer to see all you can do. Switch to ', h('b', null, 'Expert'), ' mode (top right) for shortcuts.'))),
      h('button', { class: 'btn sm', onclick: e => { store('tourDone', true); e.target.closest('.banner').remove(); } }, 'Got it')) : null,

    guided ? h('section', { style: { marginBottom: '22px' } }, h('h2', { style: { marginBottom: '12px' } }, 'What would you like to do?'),
      h('div', { class: 'action-cards' },
        card('#/files', 'search', 'Find a drawing or part', 'Browse product folders or search by part number.'),
        card('#/files', 'checkin', 'Add or check in my work', 'Drag files into a folder, or check in what you have edited.'),
        card('#/changes', 'change', 'Start a change request', 'Propose a change to a released part and get it approved.'),
        card('#/boms', 'bom', 'Build or check a BOM', 'Generate the engineering BOM from an assembly and send it to ERP.'))) :
      h('div', { class: 'kpis', style: { marginBottom: '20px' } },
        [['Files', d.counts.documents], ['Released', d.counts.released], ['Items', d.counts.items], ['BOMs', d.counts.boms],
          ['ERP queue', d.erp.pending], ['ERP failures', d.erp.failed]].map(([k, v]) => h('div', { class: 'kpi' }, h('b', null, v ?? 0), h('span', null, k)))),

    h('div', { class: 'grid-cards' },
      panel('Checked out to me', d.checked_out.length, d.checked_out.length ? h('div', null, d.checked_out.map(docRow)) :
        empty('No files checked out. Check out a file in the Explorer to edit it.'), h('a', { href: '#/files' }, 'Open Explorer')),

      panel('Waiting for my approval', d.approvals.length + d.in_review.length,
        (d.approvals.length || d.in_review.length) ? h('div', null,
          d.approvals.map(a => h('a', { class: 'list-row', href: `#/change/${a.id}` }, icon('change', 20),
            h('div', { style: { flex: 1, minWidth: 0 } }, h('div', { style: { fontWeight: 600 } }, `${a.number} · ${a.title}`),
              h('div', { class: 'small muted' }, `From ${a.requested_by_name} · submitted ${ago(a.submitted_at)}`)),
            a.priority !== 'normal' ? h('span', { class: `chip ${a.priority === 'urgent' || a.priority === 'high' ? 'st-failed' : 'st-draft'}` }, a.priority) : null)),
          d.in_review.map(docRow)) : empty('Nothing to approve.')),

      deviationsHomePanel(),

      panel('Recently released', null, d.recent_releases.length ? h('div', null, d.recent_releases.map(r =>
        h('a', { class: 'list-row', href: `#/doc/${r.id}` }, icon('release', 20),
          h('div', { style: { flex: 1, minWidth: 0 } }, h('div', { class: 'ellipsis', style: { fontWeight: 600 } }, r.filename),
            h('div', { class: 'small muted' }, `Rev ${r.revision} by ${r.by} · ${ago(r.at)}`))))) : empty('No releases yet.')),

      panel('My change requests', d.my_changes.length, d.my_changes.length ? h('div', null, d.my_changes.map(c =>
        h('a', { class: 'list-row', href: `#/change/${c.id}` }, h('span', { class: 'mono' }, c.number), h('span', { class: 'ellipsis', style: { flex: 1 } }, c.title),
          h('span', { class: `chip st-${c.state}` }, c.state)))) : empty('You have no open change requests.'), h('a', { href: '#/changes' }, 'All changes')),

      panel('ERP sync', null, h('div', { class: 'panel-b col' },
        h('div', { class: 'row' }, h('span', { class: `chip ${d.erp.enabled && !d.erp.dry_run ? 'st-sent' : 'st-dry_run'}` }, d.erp.enabled && !d.erp.dry_run ? `Connected to ${S.erp}` : 'Dry run (not sending)'),
          h('span', { class: 'muted' }, `${d.erp.pending} waiting · ${d.erp.failed} failed`)),
        d.erp.failed ? h('div', { class: 'banner err small' }, icon('warn'), h('div', null, `${d.erp.failed} transfer${d.erp.failed > 1 ? 's' : ''} to ${S.erp} failed. Open ERP sync to see why and retry.`)) : null),
      h('a', { href: '#/erp' }, 'Open ERP sync')),

      panel('Possible common parts', d.common_parts.length, d.common_parts.length ? h('div', null, d.common_parts.map(g =>
        h('a', { class: 'list-row', href: `#/common?group=${g.id}` }, icon('common', 20),
          h('div', { style: { flex: 1 } }, h('div', { style: { fontWeight: 600 } }, g.label),
            h('div', { class: 'small muted' }, `${g.n} drawings · ${Math.round(g.score_min * 100)}–${Math.round(g.score_max * 100)}% similar`))))) :
        empty('No duplicates waiting for review.'), h('a', { href: '#/common' }, 'Common parts'))),
  ));
  void fileUrl;
}
