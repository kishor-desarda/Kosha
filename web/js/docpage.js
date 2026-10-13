// SPDX-License-Identifier: AGPL-3.0-or-later
// File details page and item (part master) page.
import {
  S, h, mount, clear, icon, get, post, patch, toast, fail, dialog, confirmDlg, stateChip, typeChip, fmtDate, ago, fmtSize,
  fileUrl, downloadDoc, tabs, can, thumb, STATE_PLAIN,
} from './core.js';
import { openViewer } from './viewer.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

function stepper(state) {
  const steps = [['in_work', 'In work'], ['in_review', 'In review'], ['released', 'Released']];
  const idx = state === 'obsolete' ? 3 : steps.findIndex(s => s[0] === state);
  const out = h('div', { class: 'stepper', 'aria-label': `Lifecycle: ${state}` });
  steps.forEach(([k, label], i) => {
    if (i) out.appendChild(h('span', { class: 'line' }));
    const cls = i < idx || (state === 'released' && i === idx) || state === 'obsolete' ? 'done' : i === idx ? 'cur' : '';
    out.appendChild(h('span', { class: `s ${cls}` }, h('span', { class: 'dot' }, cls === 'done' ? '✓' : i + 1), label));
  });
  if (state === 'obsolete') { out.appendChild(h('span', { class: 'line' })); out.appendChild(h('span', { class: 's cur' }, h('span', { class: 'dot' }, '4'), 'Obsolete')); }
  return out;
}

export async function renderDoc(container, [id], params, { setTitle }) {
  let tab = params.tab || 'overview';
  const load = async () => {
    const d = await get(`/api/documents/${id}`);
    setTitle(d.filename);
    draw(d);
  };
  const draw = d => {
    const A = d.actions;
    const act = (label, ic, key, run, primary) => A[key]?.ok || key === null
      ? h('button', { class: `btn ${primary ? 'primary' : ''}`, onclick: run }, icon(ic, 16), label)
      : (S.mode === 'guided' ? h('button', { class: 'btn', disabled: true, title: A[key]?.why }, icon(ic, 16), label) : null);
    const doAct = async (path, body, msg) => { try { await post(`/api/documents/${d.id}/${path}`, body); toast(msg, 'ok'); changed(); load(); } catch (e) { fail(e); } };
    const head = h('div', { class: 'page-head' },
      h('div', { class: 'pv-img light', style: { width: '210px', height: '150px', borderRadius: '8px', border: '1px solid var(--line)', flex: 'none' }, onclick: () => openViewer(d) },
        d.preview_kind ? h('img', { src: fileUrl(d.id, 'preview', d.version), alt: '', style: { maxHeight: '140px' } }) : icon('file', 40)),
      h('div', { style: { flex: 1, minWidth: '280px' } },
        h('div', { class: 'small muted' }, d.path.map(p => p.name).join(' › ')),
        h('h1', { style: { margin: '4px 0', overflowWrap: 'anywhere' } }, d.filename),
        h('div', { class: 'row wrap' }, typeChip(d.ext), d.part_number ? h('span', { class: 'mono' }, d.part_number) : null,
          h('span', null, d.description), h('span', { class: 'muted' }, `· Rev ${d.revision} · v${d.version}`)),
        h('div', { style: { marginTop: '10px' } }, stepper(d.state)),
        S.mode === 'guided' ? h('p', { class: 'muted small', style: { margin: '6px 0 0' } }, STATE_PLAIN[d.state]) : null,
        d.checked_out_by ? h('div', { class: 'banner small', style: { marginTop: '10px' } }, icon('checkout'), h('div', null, `Checked out by ${d.checked_out_by === S.user.id ? 'you' : d.checked_out_name} ${ago(d.checked_out_at)}.`)) : null),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } },
        h('button', { class: 'btn', onclick: () => openViewer(d) }, icon('cube', 16), 'View'),
        h('button', { class: 'btn', onclick: () => downloadDoc(d.id) }, icon('download', 16), 'Download'),
        act('Check out', 'checkout', 'checkout', () => doAct('checkout', {}, 'Checked out to you'), true),
        act('Submit', 'review', 'submit', () => doAct('state', { to: 'in_review' }, 'Submitted for review')),
        act('Release', 'release', 'release', () => doAct('state', { to: 'released' }, 'Released'), true),
        act(`Revise to ${A.next_revision}`, 'revise', 'revise', async () => {
          if (await confirmDlg('Start a new revision', `Start revision ${A.next_revision}? Revision ${d.revision} stays released.`, 'Start revision')) doAct('revise', {}, `Revision ${A.next_revision} started`);
        }),
        h('a', { class: 'btn ghost', href: `#/files/${d.folder_id}?select=${d.id}` }, icon('kosha', 16), 'Show in folder')));

    const tabItems = [['overview', 'Overview'], ['versions', `Versions (${d.versions.length})`], ['history', 'History'],
      ['where', `Where used (${d.where_used.length})`], ['related', `Related files (${d.related.length})`], ['changes', `Changes (${d.changes.length})`]];
    if (d.item) tabItems.push(['erp', 'ERP']);
    const body = h('div', { class: 'panel', style: { marginTop: '6px' } });
    const setTab = t => { tab = t; drawTab(); };
    const drawTab = () => {
      clear(body);
      body.appendChild(tabs(tabItems, tab, setTab));
      const c = h('div', { class: 'panel-b' });
      body.appendChild(c);
      if (tab === 'overview') overview(c, d, load);
      if (tab === 'versions') versionsTab(c, d);
      if (tab === 'history') historyTab(c, d);
      if (tab === 'where') whereTab(c, d);
      if (tab === 'related') c.appendChild(d.related.length ? docTable(d.related) : h('div', { class: 'empty' }, 'No other files share this part number.'));
      if (tab === 'changes') changesTab(c, d);
      if (tab === 'erp') erpTab(c, d.item, load);
    };
    drawTab();
    mount(container, h('div', { class: 'page-pad' }, head, body));
  };
  await load();
}

function overview(c, d, reload) {
  const it = d.item;
  const tb = d.meta?.title_block || {};
  const editable = d.actions.edit.ok;
  const desc = h('input', { class: 'input', value: d.description, disabled: !editable, id: 'f-desc' });
  const pn = h('input', { class: 'input mono', value: d.part_number, disabled: !editable, id: 'f-pn' });
  const left = h('div', { class: 'col', style: { flex: '1 1 380px', gap: '14px' } },
    h('h2', null, 'File properties'),
    !editable ? h('p', { class: 'muted small', style: { margin: 0 } }, d.actions.edit.why) : null,
    h('div', { class: 'form-grid' },
      h('div', { class: 'field' }, h('label', { for: 'f-pn' }, 'Part number'), pn),
      h('div', { class: 'field' }, h('label', { for: 'f-desc' }, 'Description'), desc)),
    editable ? h('div', null, h('button', { class: 'btn primary', onclick: async () => {
      try { await patch(`/api/documents/${d.id}`, { part_number: pn.value, description: desc.value }); toast('Saved', 'ok'); reload(); } catch (e) { fail(e); }
    } }, 'Save properties')) : null,
    h('dl', { class: 'props' }, [
      ['Type', d.type_label], ['Revision', d.revision], ['Version', d.version], ['Created', fmtDate(d.created_at)], ['Modified', fmtDate(d.updated_at)],
      ['Size', fmtSize(d.versions[0]?.size)],
      d.meta?.dwg_version ? ['DWG format', d.meta.dwg_version] : null,
      d.meta?.units ? ['Drawing units', d.meta.units] : null,
      d.meta?.mesh ? ['Model size', d.meta.mesh.bbox.join(' × ') + ' mm'] : null,
      d.meta?.source ? ['Checked in from', d.meta.source === 'solidworks-addin' ? 'SolidWorks add-in' : d.meta.source] : null,
    ].filter(Boolean).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, String(v))])),
    d.meta?.preview_note ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, d.meta.preview_note)) : null,
    Object.keys(tb).length ? h('div', null, h('h3', { style: { margin: '6px 0' } }, 'Title block'),
      h('dl', { class: 'props' }, Object.entries(tb).flatMap(([k, v]) => [h('dt', null, k.replace(/_/g, ' ')), h('dd', null, v)]))) : null,
    d.meta?.references?.length ? h('div', null, h('h3', { style: { margin: '6px 0' } }, `Referenced files (${d.meta.references.length})`),
      h('div', { class: 'small mono' }, d.meta.references.join(', '))) : null);
  const right = it ? h('div', { class: 'col', style: { flex: '1 1 320px', gap: '12px' } },
    h('div', { class: 'row' }, h('h2', null, 'Part'), h('span', { class: 'spacer' }), h('a', { class: 'btn sm', href: `#/item/${it.id}` }, 'Open part')),
    h('dl', { class: 'props' }, [
      ['Part number', it.part_number], ['Description', it.description], ['Type', it.item_type], ['Material', it.material || '—'],
      ['Make / buy', it.make_buy], ['Unit', it.uom], ['Weight', it.weight_kg ? `${it.weight_kg} kg` : '—'],
      ['Released rev', `${it.revision} (${it.state.replace('_', ' ')})`],
      [`In ${S.erp}`, it.erp_synced_at ? `Yes, ${ago(it.erp_synced_at)}` : 'Not yet'],
      it.erp_stock != null ? ['Stock', String(it.erp_stock)] : null,
    ].filter(Boolean).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])),
    it.standard_part ? h('div', { class: 'banner info small' }, icon('common'), h('div', null, 'Duplicate of standard part ', h('a', { href: `#/item/${it.standard_part.id}` }, it.standard_part.part_number), '.')) : null,
    d.bom ? h('a', { class: 'btn', href: `#/bom/${d.bom.id}` }, icon('bom', 16), `Open BOM rev ${d.bom.revision} (${d.bom.lines} lines)`) :
      d.actions.generate_bom.ok ? h('button', { class: 'btn', onclick: async () => {
        try { const r = await post('/api/boms/generate', { document_id: d.id }); location.hash = `#/bom/${r.bom_id}`; } catch (e) { fail(e); }
      } }, icon('bom', 16), 'Build BOM from this assembly') : null) : null;
  c.appendChild(h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '28px' } }, left, right));
}

function versionsTab(c, d) {
  c.appendChild(h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['Version', 'Revision', 'Checked in', 'By', 'Comment', 'Size', ''].map(t => h('th', null, t)))),
    h('tbody', null, d.versions.map(v => h('tr', null,
      h('td', null, h('b', null, `v${v.version}`)), h('td', null, v.revision), h('td', { class: 'nowrap' }, fmtDate(v.created_at)), h('td', null, v.by),
      h('td', null, v.comment || h('span', { class: 'muted' }, '—')), h('td', { class: 'nowrap' }, fmtSize(v.size)),
      h('td', { class: 'nowrap' }, h('div', { class: 'row' },
        v.viewer_kind ? h('button', { class: 'btn sm', onclick: () => openViewer(d, { version: v.version }) }, 'View') : null,
        h('button', { class: 'btn sm', onclick: () => downloadDoc(d.id, v.version) }, 'Download'))))))));
}

function historyTab(c, d) {
  c.appendChild(h('div', { class: 'timeline' }, d.events.map(e => h('div', { class: 'tl' }, h('span', { class: `dot ${e.to_state}` }),
    h('div', null, h('div', null, h('b', null, `Revision ${e.revision}: ${({ in_work: 'In work', in_review: 'In review', released: 'Released', obsolete: 'Obsolete' })[e.to_state]}`),
      e.change_number ? [' · ', h('a', { href: `#/change/${e.change_order_id}` }, e.change_number)] : null),
    h('div', { class: 'small muted' }, `${e.by} · ${fmtDate(e.at)}`), e.comment ? h('div', { class: 'small' }, e.comment) : null)))));
}

function whereTab(c, d) {
  c.appendChild(d.where_used.length ? h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['Assembly', 'Description', 'BOM revision', 'Qty'].map(t => h('th', null, t)))),
    h('tbody', null, d.where_used.map(w => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/bom/${w.bom_id}`; } },
      h('td', { class: 'mono' }, w.part_number), h('td', null, w.description), h('td', null, `${w.bom_revision} (${w.bom_state})`), h('td', { class: 'num' }, w.qty)))))
    : h('div', { class: 'empty' }, d.item ? 'Not used in any BOM yet.' : 'This file has no part number, so it cannot be used in a BOM.'));
}

function changesTab(c, d) {
  c.appendChild(h('div', { class: 'row', style: { marginBottom: '10px' } },
    can('engineer') ? h('button', { class: 'btn', onclick: () => import('./changes.js').then(m => m.newChangeDialog([d.id])) }, icon('plus', 16), 'New change request') : null));
  c.appendChild(d.changes.length ? h('table', { class: 'tbl' }, h('tbody', null, d.changes.map(x => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/change/${x.id}`; } },
    h('td', { class: 'mono' }, x.number), h('td', null, x.title), h('td', null, x.action), h('td', null, h('span', { class: `chip st-${x.state}` }, x.state))))))
    : h('div', { class: 'empty' }, 'No change orders mention this file.'));
}

function erpTab(c, it, reload) {
  c.append(
    h('dl', { class: 'props' }, [
      [`${S.erp} item code`, it.erp_item_code || 'Not created yet'], ['Last pushed', it.erp_synced_at ? fmtDate(it.erp_synced_at) : '—'],
      ['Stock on hand', it.erp_stock ?? '—'], ['Valuation rate', it.erp_cost ?? '—'],
    ].flatMap(([k, v]) => [h('dt', null, k), h('dd', null, String(v))])),
    can('approver') ? h('div', { class: 'row', style: { marginTop: '12px' } },
      h('button', { class: 'btn primary', onclick: () => erpPush('item', it.id, reload) }, icon('erp', 16), `Send item to ${S.erp}`),
      h('button', { class: 'btn', onclick: () => erpPush('refresh', it.id, reload) }, icon('refresh', 16), `Read stock & cost from ${S.erp}`)) : null);
}

async function erpPush(kind, id, reload) {
  try {
    const r = await post('/api/erp/push', { kind, id });
    toast(r.job.status === 'failed' ? `${S.erp}: ${r.job.error}` : `${S.erp}: ${r.job.response_text?.slice(0, 160)}`, r.job.status === 'failed' ? 'err' : 'ok');
    changed(); reload && reload();
  } catch (e) { fail(e); }
}

export function docTable(docs) {
  return h('table', { class: 'tbl' }, h('tbody', null, docs.map(x => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/doc/${x.id}`; } },
    h('td', { style: { width: '52px' } }, thumb(x, 'mini')), h('td', null, x.filename), h('td', null, typeChip(x.ext)), h('td', null, `Rev ${x.revision}`), h('td', null, stateChip(x.state))))));
}

// ------------------------------------------------------------------ item page
export async function renderItem(container, [id], _p, { setTitle }) {
  const load = async () => {
    const r = await get(`/api/items/${id}`);
    const it = r.item;
    setTitle(it.part_number);
    const f = {};
    const field = (key, label, el) => { f[key] = el; el.id = `it-${key}`; return h('div', { class: 'field' }, h('label', { for: el.id }, label), el); };
    const sel = (key, opts) => h('select', { class: 'input' }, opts.map(([v, l]) => h('option', { value: v, selected: it[key] === v }, l)));
    const editable = can('engineer');
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('div', { class: 'small muted' }, 'Part'),
        h('h1', { class: 'mono' }, it.part_number), h('div', { class: 'sub' }, it.description),
        h('div', { class: 'row', style: { marginTop: '8px' } }, stateChip(it.state), h('span', { class: 'muted' }, `Revision ${it.revision} · ${it.item_type} · ${it.make_buy}`))),
      can('approver') ? h('div', { class: 'row' },
        h('button', { class: 'btn primary', onclick: () => erpPush('item', it.id, load) }, icon('erp', 16), `Send to ${S.erp}`),
        h('button', { class: 'btn', onclick: () => erpPush('refresh', it.id, load) }, icon('refresh', 16), 'Stock & cost')) : null),
      r.standard_part ? h('div', { class: 'banner info', style: { marginBottom: '14px' } }, icon('common'), h('div', null, 'This part is a duplicate of standard part ', h('a', { href: `#/item/${r.standard_part.id}` }, r.standard_part.part_number), ` (${r.standard_part.description}). Use it in new designs.`)) : null,
      r.duplicates.length ? h('div', { class: 'banner ok', style: { marginBottom: '14px' } }, icon('common'), h('div', null, `Standard part. It replaces: ${r.duplicates.map(x => x.part_number).join(', ')}.`)) : null,
      h('div', { class: 'grid-cards' },
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Properties')), h('div', { class: 'panel-b col', style: { gap: '12px' } },
          h('div', { class: 'form-grid' },
            field('description', 'Description', h('input', { class: 'input', value: it.description, disabled: !editable })),
            field('material', 'Material', h('input', { class: 'input', value: it.material, disabled: !editable })),
            field('uom', 'Unit of measure', h('input', { class: 'input', value: it.uom, disabled: !editable })),
            field('weight_kg', 'Weight (kg)', h('input', { class: 'input', type: 'number', step: 'any', value: it.weight_kg ?? '', disabled: !editable })),
            field('make_buy', 'Make or buy', sel('make_buy', [['make', 'Make (manufactured)'], ['buy', 'Buy (purchased)']])),
            field('item_type', 'Type', sel('item_type', [['part', 'Part'], ['assembly', 'Assembly']])),
            field('item_group', `${S.erp} item group`, h('input', { class: 'input', value: it.item_group, disabled: !editable, placeholder: 'Default from ERP settings' }))),
          editable ? h('div', null, h('button', { class: 'btn primary', onclick: async () => {
            const body = {}; for (const [k, el] of Object.entries(f)) body[k] = el.value;
            try { await patch(`/api/items/${it.id}`, body); toast('Saved', 'ok'); load(); } catch (e) { fail(e); }
          } }, 'Save')) : null)),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Files (${r.documents.length})`)),
          r.documents.length ? docTable(r.documents) : h('div', { class: 'empty' }, 'No files yet (purchased parts often have none).')),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'BOMs'), h('span', { class: 'spacer' }),
          can('engineer') ? h('button', { class: 'btn sm', onclick: async () => { try { const x = await post('/api/boms', { item_id: it.id }); location.hash = `#/bom/${x.bom_id}`; } catch (e) { fail(e); } } }, icon('plus', 14), 'New BOM') : null),
          r.boms.length ? h('table', { class: 'tbl' }, h('tbody', null, r.boms.map(b => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/bom/${b.id}`; } },
            h('td', null, `Revision ${b.revision}`), h('td', null, h('span', { class: `chip st-${b.state === 'released' ? 'released' : 'draft'}` }, b.state)), h('td', { class: 'muted' }, ago(b.updated_at))))))
            : h('div', { class: 'empty' }, 'No BOM.')),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `Where used (${r.where_used.length})`)),
          r.where_used.length ? h('table', { class: 'tbl' }, h('tbody', null, r.where_used.map(w => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/bom/${w.bom_id}`; } },
            h('td', { class: 'mono' }, w.part_number), h('td', null, w.description), h('td', { class: 'num' }, `× ${w.qty}`)))))
            : h('div', { class: 'empty' }, 'Not used in any BOM.')),
        h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `${S.erp}`)), h('div', { class: 'panel-b' },
          h('dl', { class: 'props' }, [['Item code', it.erp_item_code || `Not in ${S.erp} yet`], ['Last pushed', it.erp_synced_at ? fmtDate(it.erp_synced_at) : '—'],
            ['Stock on hand', it.erp_stock ?? '—'], ['Valuation rate', it.erp_cost ?? '—']].flatMap(([k, v]) => [h('dt', null, k), h('dd', null, String(v))])),
          r.erp_jobs.length ? h('div', { style: { marginTop: '10px' } }, r.erp_jobs.slice(0, 5).map(j => h('div', { class: 'small row' }, h('span', { class: `chip st-${j.status}` }, j.status), `${j.kind} · ${ago(j.updated_at)}`, j.error ? h('span', { class: 'sev-error' }, j.error) : null))) : null)))));
    import('./inputs.js').then(m => m.addItemInputs(container, it)).catch(fail);   // Inputs section (inputs library)
  };
  await load();
  void dialog;
}
