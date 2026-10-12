// SPDX-License-Identifier: AGPL-3.0-or-later
// Duplicate and common-part review: identical files, part-number clashes,
// similar geometry and similar descriptions.
import { S, h, mount, clear, icon, get, post, toast, fail, ago, fileUrl, can, stateChip, confirmDlg } from './core.js';
import { openViewer } from './viewer.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

const KINDS = [
  ['identical', 'Identical files', 'copy', 'The same file is stored more than once, for example copies on different drives. Keep one and retire the others.'],
  ['part_number', 'Same part number', 'warn', 'One part number is used on different drawings or models. Usually one of them needs its own part number.'],
  ['geometry', 'Similar geometry', 'common', 'Different part numbers but the same (or nearly the same) shape and size: the part was drawn again. Choose one standard part.'],
  ['description', 'Similar description', 'list', 'Bought-out and standard parts described the same way with exactly the same sizes, e.g. "HEX BOLT M16X60" and "BLT HEX M16 X 60". Choose one part number.'],
];
const KIND = Object.fromEntries(KINDS.map(k => [k[0], k]));

export async function renderCommon(container, _m, params, { setTitle }) {
  setTitle('Duplicates & common parts');
  let status = 'open';
  let kind = params.kind || '';
  let active = params.group ? Number(params.group) : null;
  let poll = null;
  const folders = (await get('/api/folders')).folders;
  const byId = new Map(folders.map(f => [f.id, f]));
  const pathName = f => { const parts = []; let x = f; while (x) { parts.unshift(x.name); x = byId.get(x.parent_id); } return parts.join(' › '); };

  const scope = h('select', { class: 'input', id: 'cp-scope' }, h('option', { value: '' }, 'Everything in Kosha'),
    folders.map(f => h('option', { value: f.id, selected: Number(params.folder) === f.id }, pathName(f))));
  const thr = h('input', { type: 'range', min: '75', max: '99', value: '90', id: 'cp-thr', style: { width: '100%' } });
  const thrLabel = h('b', null, '90%');
  thr.addEventListener('input', () => { thrLabel.textContent = `${thr.value}%`; });
  const checks = Object.fromEntries(KINDS.map(([k]) => [k, h('input', { type: 'checkbox', checked: true })]));
  const runInfo = h('div', { class: 'small muted' });
  const kindBar = h('div', { class: 'col', style: { gap: '4px' } });
  const list = h('div', { class: 'col', style: { gap: '10px' } });
  const detail = h('div');

  const loadRun = async () => {
    const r = await get('/api/common-parts/runs/latest');
    const run = r.run;
    mount(runInfo, run ? [
      run.status === 'running' ? h('span', { class: 'chip st-running' }, 'Analysing…') : h('span', null, `Last check ${ago(run.finished_at || run.started_at)}: ${run.scanned} files compared, ${run.groups_found} new groups.`),
      h('br'), `${r.files} files and ${r.items} parts in Kosha; ${r.comparable} drawings/models have comparable geometry.`,
      r.not_comparable ? ` ${r.not_comparable} DWG/SolidWorks files have no geometry yet (install the DWG converter or use the SolidWorks add-in).` : '',
    ] : `${r.comparable} files can be compared. No check has run yet.`);
    clear(kindBar);
    const total = Object.values(r.open_by_kind || {}).reduce((a, b) => a + b, 0);
    const opt = (k, label, n) => h('button', { class: 'list-row', style: { border: '1px solid var(--line)', borderRadius: '6px', background: kind === k ? 'var(--sel)' : '#fff', cursor: 'pointer', width: '100%' },
      onclick: () => { kind = k; active = null; loadRun(); loadGroups(); } }, h('span', { style: { flex: 1, textAlign: 'left', fontWeight: kind === k ? 700 : 500 } }, label),
    h('span', { class: `chip ${n ? 'st-submitted' : 'st-draft'}` }, n || 0));
    kindBar.append(opt('', 'All checks', total), ...KINDS.map(([k, l]) => opt(k, l, (r.open_by_kind || {})[k])));
    if (run && run.status === 'running') { if (!poll) poll = setInterval(async () => { const x = await get('/api/common-parts/runs/latest'); if (x.run.status !== 'running') { clearInterval(poll); poll = null; loadRun(); loadGroups(); changed(); } }, 1200); }
  };

  const loadGroups = async () => {
    const r = await get(`/api/common-parts/groups?status=${status}${kind ? `&kind=${kind}` : ''}`);
    clear(list);
    if (!r.groups.length) list.appendChild(h('div', { class: 'empty panel' }, status === 'open' ? 'Nothing to review here. Run a check to look for duplicates.' : 'Nothing here.'));
    for (const g of r.groups) {
      const k = KIND[g.kind] || KIND.geometry;
      const c = h('button', { class: `cp-card ${g.id === active ? 'active' : ''}`, onclick: () => { active = g.id; loadGroups(); } },
        h('div', { class: 'row' }, icon(k[2], 16), h('b', { style: { flex: 1 } }, g.label), h('span', { class: 'chip st-submitted' }, `${g.n}`)),
        g.preview_ids.length ? h('div', { class: 'cp-thumbs' }, g.preview_ids.map(id => h('img', { src: fileUrl(id, 'preview'), alt: '', loading: 'lazy' })))
          : h('div', { class: 'small mono' }, g.item_numbers.join(' · ')),
        h('div', { class: 'small muted' }, k[1], g.kind === 'geometry' || g.kind === 'description' ? ` · ${Math.round(g.score_min * 100)}–${Math.round(g.score_max * 100)}% similar` : ''));
      list.appendChild(c);
    }
    if (active && r.groups.some(g => g.id === active)) showGroup(active);
    else if (r.groups.length) { active = r.groups[0].id; list.firstChild.classList.add('active'); showGroup(active); }
    else clear(detail);
  };

  const showGroup = async gid => {
    const g = await get(`/api/common-parts/groups/${gid}`);
    const k = KIND[g.kind] || KIND.geometry;
    const isItems = g.items.length > 0;
    const decided = ['standardized', 'rejected', 'resolved'].includes(g.status);
    let pick = isItems ? (g.standard_item_id || g.items.find(m => m.is_reference)?.item.id)
      : (g.standard_document_id || g.members.find(m => m.is_reference)?.document.id);
    const ref = isItems ? g.items.find(m => m.is_reference) : g.members.find(m => m.is_reference);
    const fmtSize = s => (Array.isArray(s) ? s.filter(x => x != null).map(x => Math.round(x * 10) / 10).join(' × ') : '—');
    const cards = h('div', { class: 'cp-members' });
    const pickLabel = g.kind === 'identical' ? 'Keep this copy' : g.kind === 'part_number' ? 'This one keeps the number' : 'Make this the standard part';
    const drawCards = () => {
      clear(cards);
      if (isItems) {
        for (const m of g.items) {
          const it = m.item;
          const radio = h('input', { type: 'radio', name: `std-${gid}`, checked: it.id === pick, disabled: decided, onchange: () => { pick = it.id; drawCards(); } });
          cards.appendChild(h('div', { class: `cp-member ${it.id === pick ? 'std' : ''}` },
            h('div', { class: 'b' },
              h('div', { class: 'row' }, h('a', { class: 'mono', style: { flex: 1, fontWeight: 700 }, href: `#/item/${it.id}` }, it.part_number),
                m.is_reference ? h('span', { class: 'chip st-released' }, 'Reference') : h('span', { class: 'chip st-submitted' }, `${Math.round(m.score * 100)}%`)),
              h('div', null, it.description),
              h('div', { class: 'small' }, `${it.make_buy === 'buy' ? 'Bought out' : 'Made'} · unit ${it.uom}${it.item_group ? ` · ${it.item_group}` : ''}`),
              h('div', { class: 'small muted' }, `Used in ${m.where_used} assembl${m.where_used === 1 ? 'y' : 'ies'} · ${m.files} file(s)`,
                it.erp_stock != null ? ` · stock ${it.erp_stock}` : ''),
              h('label', { class: 'check small' }, radio, pickLabel))));
        }
        return;
      }
      for (const m of g.members) {
        const d = m.document;
        const diffSize = ref && fmtSize(m.size) !== fmtSize(ref.size);
        const diffD = ref && JSON.stringify(m.diameters) !== JSON.stringify(ref.diameters);
        const diffMat = ref && (m.material || '') !== (ref.material || '');
        const radio = h('input', { type: 'radio', name: `std-${gid}`, checked: d.id === pick, disabled: decided, onchange: () => { pick = d.id; drawCards(); } });
        cards.appendChild(h('div', { class: `cp-member ${d.id === pick ? 'std' : ''}` },
          h('div', { class: 'img', onclick: () => openViewer(d.id), title: 'Open in viewer' },
            d.preview_kind ? h('img', { src: fileUrl(d.id, 'preview', d.version), alt: d.filename }) : icon('file', 40)),
          h('div', { class: 'b' },
            h('div', { class: 'row' }, h('b', { class: 'mono', style: { flex: 1 } }, d.part_number || '—'),
              m.is_reference ? h('span', { class: 'chip st-released' }, 'Reference') : g.kind === 'geometry' ? h('span', { class: 'chip st-submitted' }, `${Math.round(m.score * 100)}%`) : null),
            h('a', { class: 'ellipsis', href: `#/doc/${d.id}`, title: d.filename }, d.filename),
            h('div', { class: 'small muted ellipsis', title: m.folder }, m.folder),
            m.source_path ? h('div', { class: 'small muted ellipsis', title: m.source_path }, `Imported from ${m.source_path}`) : null,
            g.kind === 'geometry' ? h('div', { class: 'small' }, 'Size ', h('span', { class: diffSize ? 'diff' : '' }, fmtSize(m.size))) : null,
            g.kind === 'geometry' && m.diameters?.length ? h('div', { class: 'small' }, 'Holes Ø ', h('span', { class: diffD ? 'diff' : '' }, m.diameters.join(', '))) : null,
            h('div', { class: 'small' }, 'Material ', h('span', { class: diffMat ? 'diff' : '' }, m.material || '—'), ` · rev ${d.revision}`),
            h('div', { class: 'small muted' }, `Used in ${m.where_used} assembl${m.where_used === 1 ? 'y' : 'ies'} · `, stateChip(d.state)),
            h('label', { class: 'check small' }, radio, pickLabel))));
      }
    };
    drawCards();

    const decide = async (decision) => {
      const body = { decision };
      if (isItems) body.standard_item_id = pick; else body.standard_document_id = pick;
      if (decision === 'standardized') {
        const label = isItems ? g.items.find(m => m.item.id === pick)?.item.part_number : g.members.find(m => m.document.id === pick)?.document.part_number;
        if (!label) { toast('The chosen file has no part number. Give it one first.', 'err'); return; }
        if (!await confirmDlg('Make standard part', `Make ${label} the standard part? The other part numbers are marked as duplicates of it: designers are told to use ${label}, and BOMs that use a duplicate show a warning.`, 'Make standard')) return;
      }
      if (decision === 'obsolete_others') {
        const keep = g.members.find(m => m.document.id === pick)?.document;
        if (!await confirmDlg('Keep one copy', `Keep ${keep.filename} (${keep.folder || ''}) and make the other ${g.members.length - 1} cop${g.members.length === 2 ? 'y' : 'ies'} obsolete? Copies that were never released are deleted.`, 'Keep one copy')) return;
      }
      try {
        const r = await post(`/api/common-parts/groups/${gid}/decide`, body);
        toast({ standardized: 'Standard part set', rejected: 'Marked as not duplicates', later: 'Saved for later', obsolete_others: `Done: ${(r.done || []).join(', ')}`, resolved: 'Marked as resolved' }[decision], 'ok');
        if (r.assemblies_to_update?.length) toast(`Assemblies still using the duplicates: ${r.assemblies_to_update.map(a => a.part_number).join(', ')}. Update them with a change request.`, '');
        active = null; changed(); loadRun(); loadGroups();
      } catch (e) { fail(e); }
    };
    const actions = [];
    if (can('engineer') && !decided) {
      if (g.kind === 'identical') {
        if (can('approver')) actions.push(h('button', { class: 'btn primary', onclick: () => decide('obsolete_others') }, icon('check', 16), 'Keep the chosen copy, retire the others'));
        if (new Set(g.members.map(m => m.document.item_id)).size > 1) actions.push(h('button', { class: 'btn', onclick: () => decide('standardized') }, icon('common', 16), 'Make standard part'));
      } else if (g.kind === 'part_number') {
        actions.push(h('button', { class: 'btn primary', onclick: () => decide('resolved') }, icon('check', 16), 'Renumbered / sorted out'));
      } else {
        actions.push(h('button', { class: 'btn primary', onclick: () => decide('standardized') }, icon('check', 16), 'Make standard part'));
      }
      actions.push(h('button', { class: 'btn', onclick: () => decide('rejected') }, icon('x', 16), 'Not duplicates'));
      actions.push(h('button', { class: 'btn ghost', onclick: () => decide('later') }, 'Review later'));
    }
    mount(detail, h('section', { class: 'panel' },
      h('div', { class: 'panel-h' }, icon(k[2], 18), h('h2', null, g.label), h('span', { class: 'muted' }, k[1]), h('span', { class: 'spacer' }),
        h('span', { class: `chip st-${g.status}` }, g.status)),
      h('div', { class: 'panel-b col', style: { gap: '14px' } },
        !decided ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, k[3],
          g.kind === 'part_number' ? ' Open each file, give the wrong one its own part number (check it out, edit Part number, check in), then mark this as sorted out.' : '',
          g.kind === 'geometry' ? ' Highlighted values differ from the reference.' : '')) : null,
        cards,
        actions.length ? h('div', { class: 'row wrap' }, actions) : null)));
  };

  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Duplicates & common parts'),
      h('div', { class: 'sub' }, 'Find files and parts that exist more than once, especially after importing legacy data, and decide which one to keep.')),
    h('a', { class: 'btn', href: `/api/common-parts/report?status=open&access_token=${encodeURIComponent(S.token)}` }, icon('download', 16), 'Excel report for review')),
    h('div', { class: 'cp-layout' },
      h('div', { class: 'col', style: { gap: '14px' } },
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Run a duplicate check')),
          h('div', { class: 'panel-b col', style: { gap: '12px' } },
            h('div', { class: 'field' }, h('label', { for: 'cp-scope' }, 'Where to look'), scope),
            h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Checks'), KINDS.map(([kk, l]) => h('label', { class: 'check small' }, checks[kk], l))),
            h('div', { class: 'field' }, h('label', { for: 'cp-thr' }, 'Geometry match: ', thrLabel), thr, h('span', { class: 'hint' }, 'Lower finds more possible matches; higher only near-identical parts.')),
            can('engineer') ? h('button', { class: 'btn primary', onclick: async e => {
              e.target.disabled = true;
              try {
                const kinds = KINDS.map(([kk]) => kk).filter(kk => checks[kk].checked);
                await post('/api/common-parts/analyze', { folder_id: scope.value ? Number(scope.value) : null, threshold: Number(thr.value) / 100, kinds });
                toast('Duplicate check started', 'ok'); active = null; await loadRun();
              } catch (x) { fail(x); } finally { e.target.disabled = false; }
            } }, icon('common', 16), 'Find duplicates') : null,
            runInfo)),
        kindBar,
        h('div', { class: 'seg' }, [['open', 'Open'], ['later', 'Later'], ['standardized', 'Standard'], ['resolved', 'Resolved'], ['rejected', 'Not dup.']].map(([kk, l]) =>
          h('button', { 'aria-pressed': String(status === kk), onclick: e => { status = kk; active = null; e.target.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === e.target))); loadGroups(); } }, l))),
        list),
      detail)));
  await loadRun();
  await loadGroups();
  return () => { if (poll) clearInterval(poll); };
}
