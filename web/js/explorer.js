// SPDX-License-Identifier: AGPL-3.0-or-later
// The Explorer: folders, files, ribbon, preview pane, right-click menus, drag & drop.
import {
  S, h, mount, clear, icon, get, post, patch, del, upload, toast, fail, busy, dialog, confirmDlg, prompt,
  contextMenu, closeMenu, stateChip, typeChip, fmtDate, ago, fmtSize, fileUrl, downloadDoc, store, can, thumb, STATE_PLAIN,
} from './core.js';
import { openViewer } from './viewer.js';

const E = {
  folders: [], byId: new Map(), kids: new Map(),
  expanded: new Set(store('expanded') || []),
  cur: null, data: null, sel: new Set(), anchor: null, focus: null,
  sort: store('sort') || { key: 'name', dir: 1 },
  view: store('view') || 'details',
  showPreview: store('preview') !== false,
  filter: '', details: new Map(),
};
let ui = null;

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

export async function renderExplorer(container, [folderParam], params, { setTitle }) {
  setTitle('Explorer');
  await loadFolders();
  let fid = folderParam ? Number(folderParam) : (store('folder') || null);
  if (!fid || !E.byId.has(fid)) fid = rootId();
  if (!folderParam) { history.replaceState(null, '', `#/files/${fid}`); }
  if (E.cur !== fid) { E.sel.clear(); E.focus = null; E.anchor = null; E.filter = ''; }
  E.cur = fid;
  store('folder', fid);
  for (const f of pathTo(fid)) E.expanded.add(f.id);
  buildLayout(container);
  await loadContents();
  if (params.select) { E.sel = new Set([`d:${params.select}`]); E.focus = `d:${params.select}`; }
  renderAll();
  ui.list.focus({ preventScroll: true });
  const onKey = e => globalKeys(e);
  document.addEventListener('keydown', onKey);
  return () => { document.removeEventListener('keydown', onKey); ui = null; };
}

// ------------------------------------------------------------------ data
async function loadFolders() {
  const r = await get('/api/folders');
  E.folders = r.folders;
  E.byId = new Map(r.folders.map(f => [f.id, f]));
  E.kids = new Map();
  for (const f of r.folders) {
    const k = f.parent_id || 0;
    if (!E.kids.has(k)) E.kids.set(k, []);
    E.kids.get(k).push(f);
  }
}
const rootId = () => (E.kids.get(0) || [])[0]?.id;
function pathTo(id) {
  const out = [];
  let f = E.byId.get(id);
  const seen = new Set();
  while (f && !seen.has(f.id)) { seen.add(f.id); out.unshift(f); f = E.byId.get(f.parent_id); }
  return out;
}
async function loadContents() {
  E.data = await get(`/api/folders/${E.cur}/contents`);
  E.details.clear();
  const keys = new Set(rows().map(r => r.key));
  for (const k of [...E.sel]) if (!keys.has(k)) E.sel.delete(k);
}
async function refresh(keepSel = true) {
  const sel = new Set(E.sel);
  await loadFolders();
  await loadContents();
  if (keepSel) { const keys = new Set(rows().map(r => r.key)); E.sel = new Set([...sel].filter(k => keys.has(k))); }
  renderAll();
  changed();
}

function rows() {
  if (!E.data) return [];
  const f = E.filter.toLowerCase();
  const folders = E.data.folders.map(x => ({ key: `f:${x.id}`, kind: 'folder', ...x, filename: x.name }));
  const docs = E.data.documents.map(x => ({ key: `d:${x.id}`, kind: 'doc', ...x }));
  const match = r => !f || [r.filename, r.part_number, r.description].some(v => (v || '').toLowerCase().includes(f));
  const k = E.sort.key;
  const val = r => {
    if (k === 'name') return (r.filename || '').toLowerCase();
    if (k === 'updated_at') return r.updated_at || r.created_at || '';
    const v = r[k];
    return typeof v === 'string' ? v.toLowerCase() : (v ?? '');
  };
  const cmp = (a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * E.sort.dir;
  return [...folders.filter(match).sort(cmp), ...docs.filter(match).sort(cmp)];
}
const selectedDocs = () => rows().filter(r => r.kind === 'doc' && E.sel.has(r.key));
const selectedRows = () => rows().filter(r => E.sel.has(r.key));

// ------------------------------------------------------------------ action rules
function acts(d) {
  const det = E.details.get(d.id);
  if (det && det.actions) return det.actions;
  const me = S.user.id;
  const eng = can('engineer'), appr = can('approver');
  const mine = d.checked_out_by === me, out = !!d.checked_out_by;
  const A = (ok, why) => ({ ok, why: ok ? '' : why });
  return {
    checkout: A(eng && !out && d.state === 'in_work', !eng ? 'View-only access' : out ? (mine ? 'Already checked out to you' : `Checked out by ${d.checked_out_name}`) : d.state === 'released' ? 'Released: press Revise first' : 'Not in work'),
    checkin: A(mine, 'Check out first'),
    undo_checkout: A(mine || (out && S.user.role === 'admin'), 'Not checked out to you'),
    submit: A(eng && d.state === 'in_work' && !out, out ? 'Check in first' : 'Only files in work'),
    send_back: A(eng && d.state === 'in_review', 'Only files in review'),
    release: A(appr && ['in_work', 'in_review'].includes(d.state) && !out, !appr ? 'Needs approver role' : out ? 'Check in first' : 'Already released'),
    revise: A(eng && d.state === 'released', 'Only released files are revised'),
    obsolete: A(appr && d.state === 'released', 'Needs approver and a released file'),
    delete: A(eng && !out && d.state === 'in_work', 'Released files cannot be deleted'),
    edit: A(mine, 'Check out to edit'),
    generate_bom: A(eng && d.doc_type === 'assembly', 'Only assemblies'),
  };
}
function allOk(name, docs) { return docs.length > 0 && docs.every(d => acts(d)[name]?.ok); }
function why(name, docs) {
  if (!docs.length) return 'Select a file first';
  const bad = docs.find(d => !acts(d)[name]?.ok);
  return bad ? acts(bad)[name]?.why || '' : '';
}

// ------------------------------------------------------------------ layout
function buildLayout(container) {
  const toolbar = h('div', { class: 'toolbar', role: 'toolbar', 'aria-label': 'File actions' });
  const banner = h('div', { class: 'ex-banner' });
  const back = h('button', { class: 'btn ghost icon', 'aria-label': 'Back', title: 'Back (Alt+Left)', onclick: () => history.back() }, icon('back'));
  const fwd = h('button', { class: 'btn ghost icon', 'aria-label': 'Forward', title: 'Forward (Alt+Right)', onclick: () => history.forward() }, icon('fwd'));
  const up = h('button', { class: 'btn ghost icon', 'aria-label': 'Up one folder', title: 'Up (Backspace)', onclick: goUp }, icon('up'));
  const crumbs = h('div', { class: 'crumbs', role: 'navigation', 'aria-label': 'Folder path' });
  const filter = h('input', { class: 'input filter', type: 'search', placeholder: 'Filter this folder', 'aria-label': 'Filter this folder', value: E.filter });
  filter.addEventListener('input', () => { E.filter = filter.value; renderList(); renderStatus(); });
  const viewBtn = h('button', { class: 'btn ghost icon', 'aria-label': 'Switch view', title: 'Details / tiles', onclick: () => { E.view = E.view === 'details' ? 'tiles' : 'details'; store('view', E.view); renderList(); } }, icon('grid'));
  const pvBtn = h('button', { class: 'btn ghost icon', 'aria-label': 'Preview pane', title: 'Show or hide preview pane', onclick: () => { E.showPreview = !E.showPreview; store('preview', E.showPreview); ui.body.classList.toggle('no-preview', !E.showPreview); renderPreview(); } }, icon('panel'));
  const addr = h('div', { class: 'addr' }, back, fwd, up, crumbs, filter, viewBtn, pvBtn);
  const tree = h('div', { class: 'ex-tree', role: 'tree', 'aria-label': 'Folders' });
  const list = h('div', { class: 'ex-list', tabindex: '0', role: 'grid', 'aria-label': 'Files' });
  const preview = h('aside', { class: 'ex-preview', 'aria-label': 'Preview' });
  const body = h('div', { class: `ex-body ${E.showPreview ? '' : 'no-preview'}` }, tree, list, preview);
  const status = h('div', { class: 'statusbar', role: 'status' });
  mount(container, h('div', { class: 'explorer' }, toolbar, banner, addr, body, status));
  ui = { toolbar, banner, crumbs, tree, list, preview, body, status, filter };

  list.addEventListener('keydown', listKeys);
  list.addEventListener('contextmenu', e => {
    if (e.target.closest('tr[data-key], .tile')) return;
    e.preventDefault();
    E.sel.clear(); renderSelection();
    contextMenu(e.clientX, e.clientY, [
      { label: 'Add files…', icon: 'upload', run: pickFiles, disabled: !can('engineer') },
      { label: 'New folder', icon: 'folderPlus', run: newFolder, disabled: !can('engineer') },
      '-',
      { label: 'Refresh', icon: 'refresh', key: 'F5', run: () => refresh() },
    ]);
  });
  list.addEventListener('mousedown', e => { if (!e.target.closest('tr[data-key], .tile') && e.button === 0) { E.sel.clear(); renderSelection(); } });
  // drag & drop from the desktop
  list.addEventListener('dragover', e => {
    if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); list.classList.add('dragover'); }
  });
  list.addEventListener('dragleave', e => { if (e.target === list) list.classList.remove('dragover'); });
  list.addEventListener('drop', e => {
    list.classList.remove('dragover');
    if (e.dataTransfer.files.length) { e.preventDefault(); dropped(e.dataTransfer, E.cur); }
  });
}

function renderAll() {
  renderTree(); renderCrumbs(); renderList(); renderSelection();
}

function renderCrumbs() {
  clear(ui.crumbs);
  pathTo(E.cur).forEach((f, i, arr) => {
    if (i) ui.crumbs.appendChild(h('span', { class: 'sep' }, icon('chevR', 14)));
    ui.crumbs.appendChild(h('button', { onclick: () => go(f.id), 'aria-current': i === arr.length - 1 ? 'page' : null }, f.name));
  });
}

// ------------------------------------------------------------------ tree
function renderTree() {
  clear(ui.tree);
  const add = (parent, depth) => {
    for (const f of (E.kids.get(parent) || [])) {
      const hasKids = (E.kids.get(f.id) || []).length > 0;
      const open = E.expanded.has(f.id);
      const tw = h('button', { class: 'tw', 'aria-label': open ? 'Collapse' : 'Expand', tabindex: '-1', style: { visibility: hasKids ? 'visible' : 'hidden' } }, icon(open ? 'chevD' : 'chevR', 14));
      tw.addEventListener('click', ev => { ev.stopPropagation(); toggle(f.id); });
      const node = h('div', { class: `tree-node ${f.id === E.cur ? 'active' : ''}`, role: 'treeitem', 'aria-expanded': hasKids ? String(open) : null, 'aria-selected': String(f.id === E.cur), tabindex: f.id === E.cur ? '0' : '-1', style: { paddingLeft: `${6 + depth * 14}px` } },
        tw, icon(open && hasKids ? 'folderOpen' : 'folder', 16), h('span', { class: 'ellipsis' }, f.name), f.files ? h('span', { class: 'cnt' }, f.files) : null);
      node.addEventListener('click', () => go(f.id));
      node.addEventListener('dblclick', () => toggle(f.id));
      node.addEventListener('keydown', e => {
        if (e.key === 'ArrowRight' && hasKids && !open) toggle(f.id);
        if (e.key === 'ArrowLeft' && open) toggle(f.id);
        if (e.key === 'Enter') go(f.id);
      });
      node.addEventListener('contextmenu', e => { e.preventDefault(); folderMenu(e, f); });
      // drop target: move files, or upload from desktop
      node.addEventListener('dragover', e => { e.preventDefault(); node.classList.add('drop'); });
      node.addEventListener('dragleave', () => node.classList.remove('drop'));
      node.addEventListener('drop', e => {
        e.preventDefault(); node.classList.remove('drop');
        const ids = e.dataTransfer.getData('application/x-kosha-docs');
        if (ids) moveDocs(ids.split(',').map(Number), f.id);
        else if (e.dataTransfer.files.length) dropped(e.dataTransfer, f.id);
      });
      ui.tree.appendChild(node);
      if (open) add(f.id, depth + 1);
    }
  };
  add(0, 0);
}
function toggle(id) { E.expanded.has(id) ? E.expanded.delete(id) : E.expanded.add(id); store('expanded', [...E.expanded]); renderTree(); }

function go(id) { if (id && id !== E.cur) location.hash = `#/files/${id}`; }
function goUp() { const f = E.byId.get(E.cur); if (f && f.parent_id) go(f.parent_id); }

// ------------------------------------------------------------------ list
const COLS = [
  ['name', 'Name', null, true],
  ['part_number', 'Part number', 120, false],
  ['description', 'Description', 180, true],
  ['revision', 'Rev', 48, true],
  ['state', 'State', 104, true],
  ['checked_out_name', 'Checked out', 96, true],
  ['version', 'Ver', 44, false],
  ['updated_at', 'Modified', 0, true],
];

function renderList() {
  clear(ui.list);
  const rs = rows();
  if (!rs.length) {
    ui.list.appendChild(h('div', { class: 'empty' },
      h('div', { style: { marginBottom: '10px' } }, icon('folderOpen', 34)),
      E.filter ? `Nothing in this folder matches "${E.filter}".` : 'This folder is empty.',
      can('engineer') && !E.filter ? h('div', { style: { marginTop: '12px' } },
        h('p', { class: 'muted' }, 'Drag files here from your computer, or'),
        h('button', { class: 'btn primary', onclick: pickFiles }, icon('upload'), 'Add files')) : null));
    renderStatus();
    return;
  }
  if (E.view === 'tiles') return renderTiles(rs);
  const cols = COLS.filter(c => S.mode === 'expert' || c[3]);
  const width = c => (c[0] === 'updated_at' ? (S.mode === 'expert' ? 128 : 96) : c[2]);
  const minWidth = cols.reduce((a, c) => a + (width(c) || 0), 0) + 240;
  const table = h('table', { style: { minWidth: `${minWidth}px` } },
    h('colgroup', null, cols.map(c => h('col', width(c) ? { style: { width: `${width(c)}px` } } : null))),
    h('thead', null, h('tr', null, cols.map(([k, label]) => {
      const th = h('th', { scope: 'col', 'aria-sort': E.sort.key === k ? (E.sort.dir > 0 ? 'ascending' : 'descending') : null }, label, E.sort.key === k ? (E.sort.dir > 0 ? ' ▲' : ' ▼') : '');
      th.addEventListener('click', () => { E.sort = { key: k, dir: E.sort.key === k ? -E.sort.dir : 1 }; store('sort', E.sort); renderList(); renderSelection(); });
      return th;
    }))));
  const tb = h('tbody');
  for (const r of rs) tb.appendChild(rowEl(r, cols));
  table.appendChild(tb);
  ui.list.appendChild(table);
  renderStatus();
}

function rowEl(r, cols) {
  const tr = h('tr', { 'data-key': r.key, 'aria-selected': String(E.sel.has(r.key)), class: E.sel.has(r.key) ? 'sel' : '' });
  for (const [k] of cols) {
    let cell;
    if (k === 'name') {
      cell = h('div', { class: 'name' }, r.kind === 'folder' ? icon('folder', 20) : thumb(r),
        h('span', { class: 'fn' }, r.filename),
        r.kind === 'doc' && S.mode === 'expert' ? typeChip(r.ext) : null,
        r.standard_part ? h('span', { class: 'chip', style: { background: '#EEF3FB', color: '#1E3A8A' }, title: `Duplicate of standard part ${r.standard_part}` }, 'Use ' + r.standard_part) : null);
    } else if (r.kind === 'folder') {
      cell = k === 'description' ? h('span', { class: 'muted' }, `${r.files} file${r.files === 1 ? '' : 's'}`) : k === 'updated_at' ? h('span', { class: 'muted' }, fmtDate(r.created_at, false)) : '';
    } else if (k === 'state') cell = stateChip(r.state);
    else if (k === 'checked_out_name') cell = r.checked_out_by ? h('span', { class: 'chip out', title: `Checked out ${ago(r.checked_out_at)}` }, icon('checkout', 13), r.checked_out_by === S.user.id ? 'You' : r.checked_out_name) : '';
    else if (k === 'updated_at') cell = h('span', { class: 'muted', title: fmtDate(r.updated_at) }, S.mode === 'guided' ? ago(r.updated_at) : new Date(r.updated_at).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }));
    else if (k === 'part_number') cell = h('span', { class: 'mono' }, r.part_number || '');
    else if (k === 'revision') cell = h('b', null, r.revision);
    else cell = r[k] ?? '';
    tr.appendChild(h('td', { title: k === 'description' || k === 'name' ? (r[k === 'name' ? 'filename' : k] || '') : null }, cell));
  }
  attachRowEvents(tr, r);
  return tr;
}

function renderTiles(rs) {
  const grid = h('div', { class: 'ex-tiles' });
  for (const r of rs) {
    const t = h('div', { class: `tile ${E.sel.has(r.key) ? 'sel' : ''}`, 'data-key': r.key, role: 'gridcell', 'aria-selected': String(E.sel.has(r.key)) },
      h('div', { class: 'tim' }, r.kind === 'folder' ? icon('folder', 48) : (r.preview_kind ? h('img', { src: fileUrl(r.id, 'preview', r.version), alt: '', loading: 'lazy' }) : icon('file', 40))),
      h('div', { class: 'tb' }, h('b', { class: 'ellipsis', title: r.filename }, r.filename),
        r.kind === 'doc' ? h('div', { class: 'row' }, stateChip(r.state), h('span', { class: 'muted' }, `Rev ${r.revision}`),
          r.checked_out_by ? h('span', { class: 'chip out' }, icon('checkout', 12)) : null) : h('span', { class: 'muted' }, `${r.files} files`)));
    attachRowEvents(t, r);
    grid.appendChild(t);
  }
  ui.list.appendChild(grid);
  renderStatus();
}

function attachRowEvents(el, r) {
  el.addEventListener('mousedown', e => {
    if (e.button === 2 && E.sel.has(r.key)) return;
    select(r.key, e.ctrlKey || e.metaKey, e.shiftKey);
  });
  el.addEventListener('dblclick', () => open(r));
  el.addEventListener('contextmenu', e => { e.preventDefault(); rowMenu(e, r); });
  if (r.kind === 'doc') {
    el.setAttribute('draggable', 'true');
    el.addEventListener('dragstart', e => {
      if (!E.sel.has(r.key)) select(r.key, false, false);
      e.dataTransfer.setData('application/x-kosha-docs', selectedDocs().map(d => d.id).join(','));
      e.dataTransfer.effectAllowed = 'move';
    });
  } else {
    el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('sel'); });
    el.addEventListener('dragleave', () => { if (!E.sel.has(r.key)) el.classList.remove('sel'); });
    el.addEventListener('drop', e => {
      e.preventDefault(); e.stopPropagation();
      const ids = e.dataTransfer.getData('application/x-kosha-docs');
      if (ids) moveDocs(ids.split(',').map(Number), r.id);
      else if (e.dataTransfer.files.length) dropped(e.dataTransfer, r.id);
    });
  }
}

function select(key, additive, range) {
  const rs = rows();
  if (range && E.anchor) {
    const a = rs.findIndex(x => x.key === E.anchor), b = rs.findIndex(x => x.key === key);
    if (!additive) E.sel.clear();
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) E.sel.add(rs[i].key);
  } else if (additive) {
    E.sel.has(key) ? E.sel.delete(key) : E.sel.add(key);
    E.anchor = key;
  } else {
    E.sel = new Set([key]);
    E.anchor = key;
  }
  E.focus = key;
  renderSelection();
}

function renderSelection() {
  if (!ui) return;
  for (const el of ui.list.querySelectorAll('[data-key]')) {
    const on = E.sel.has(el.dataset.key);
    el.classList.toggle('sel', on);
    el.classList.toggle('focus', el.dataset.key === E.focus);
    el.setAttribute('aria-selected', String(on));
  }
  renderToolbar(); renderBanner(); renderPreview(); renderStatus();
  const one = selectedDocs();
  if (one.length === 1 && !E.details.has(one[0].id)) loadDetail(one[0].id);
}

async function loadDetail(id) {
  try {
    const d = await get(`/api/documents/${id}`);
    E.details.set(id, d);
    const cur = selectedDocs();
    if (cur.length === 1 && cur[0].id === id) { renderToolbar(); renderBanner(); renderPreview(); }
  } catch (e) { /* deleted meanwhile */ }
}

function renderStatus() {
  if (!ui) return;
  const rs = rows();
  const mine = (E.data?.documents || []).filter(d => d.checked_out_by === S.user.id).length;
  mount(ui.status,
    h('span', null, `${rs.length} item${rs.length === 1 ? '' : 's'}`),
    E.sel.size ? h('span', null, `${E.sel.size} selected`) : null,
    mine ? h('span', null, `${mine} checked out to you`) : null,
    h('span', { class: 'spacer' }),
    S.mode === 'expert' ? h('span', null, 'Ctrl+O check out · Ctrl+I check in · Ctrl+R revise · Ctrl+U where used · F2 rename · Space view') : h('span', null, 'Tip: right-click any file for everything you can do with it'));
}

// ------------------------------------------------------------------ toolbar
function tbBtn(ic, label, desc, run, enabled, whyText, key, primary) {
  const b = h('button', { class: `tb-btn ${primary ? 'primary' : ''}`, disabled: !enabled, title: enabled ? (key ? `${label} (${key})` : label) : whyText || label },
    h('span', { class: 't' }, icon(ic, 17), h('span', null, label), key ? h('span', { class: 'kbd' }, key) : null),
    h('span', { class: 'd' }, desc));
  b.addEventListener('click', () => run());
  return b;
}

function renderToolbar() {
  if (!ui) return;
  const docs = selectedDocs();
  const one = docs.length === 1 ? docs[0] : null;
  const eng = can('engineer');
  const g = (...b) => h('div', { class: 'tb-group' }, b);
  const mineOut = docs.length && docs.every(d => d.checked_out_by === S.user.id);
  mount(ui.toolbar,
    g(tbBtn('upload', 'Add files', 'Put new files in this folder', pickFiles, eng, 'View-only access', null, !docs.length && eng),
      tbBtn('folderPlus', 'New folder', 'Make a folder here', newFolder, eng, 'View-only access')),
    g(tbBtn('checkout', 'Check out', 'Lock it so you can edit', () => doCheckout(docs), allOk('checkout', docs), why('checkout', docs), 'Ctrl+O', !mineOut && allOk('checkout', docs) && S.mode === 'guided'),
      tbBtn('checkin', 'Check in', 'Save your new version', () => doCheckin(docs), docs.length > 0 && mineOut, why('checkin', docs), 'Ctrl+I', mineOut && S.mode === 'guided'),
      tbBtn('unlock', 'Undo check-out', 'Unlock without saving', () => doUndo(docs), allOk('undo_checkout', docs), why('undo_checkout', docs)),
      tbBtn('download', 'Download', 'Copy to your computer', () => docs.forEach(d => downloadDoc(d.id)), docs.length > 0, 'Select a file first')),
    g(tbBtn('review', 'Submit', 'Ask a checker to review', () => doState(docs, 'in_review'), allOk('submit', docs), why('submit', docs)),
      tbBtn('release', 'Release', 'Approve for manufacture', () => doState(docs, 'released'), allOk('release', docs), why('release', docs)),
      tbBtn('revise', 'Revise', one ? `Start revision ${acts(one).next_revision || 'next'}` : 'Start the next revision', () => doRevise(docs), allOk('revise', docs), why('revise', docs), 'Ctrl+R'),
      tbBtn('change', 'Change request', 'Propose a change for approval', () => newChange(docs), eng && docs.length > 0, 'Select files first')),
    g(tbBtn('cube', 'View', 'Open the viewer', () => one && openViewer(one), !!one, 'Select one file', 'Space'),
      tbBtn('link', 'Where used', 'Which machines use this part', () => one && whereUsed(one), !!one && !!one.item_id, one && !one.item_id ? 'This file has no part number' : 'Select one file', 'Ctrl+U'),
      tbBtn('bom', 'BOM', one?.doc_type === 'assembly' ? 'Open or build the BOM' : 'Assemblies only', () => one && openBom(one), !!one && one.doc_type === 'assembly', 'Select an assembly'),
      tbBtn('erp', 'Send to ERP', `Push item to ${S.erp}`, () => one && pushErp(one), !!one && !!one.item_id && can('approver'), can('approver') ? 'Select a file with a part number' : 'Needs approver role')));
}

// ------------------------------------------------------------------ guidance
function renderBanner() {
  if (!ui) return;
  clear(ui.banner);
  if (S.mode !== 'guided') return;
  const docs = selectedDocs();
  let msg;
  let kind = '';
  if (!docs.length) {
    const outs = (E.data?.documents || []).filter(d => d.checked_out_by === S.user.id);
    msg = outs.length
      ? [h('b', null, 'You have ', outs.length, ' file', outs.length > 1 ? 's' : '', ' checked out here. '), 'Select one and press ', h('b', null, 'Check in'), ' when you have saved your changes.']
      : ['Click a file to see its preview and what you can do with it. ', h('b', null, 'Double-click'), ' opens it in the viewer. Drag files from your computer into this window to add them.'];
    kind = 'info';
  } else if (docs.length > 1) {
    msg = [`${docs.length} files selected. The buttons above act on all of them.`];
    kind = 'info';
  } else {
    const d = docs[0];
    const a = acts(d);
    if (d.checked_out_by === S.user.id) msg = [h('b', null, 'Next step: '), 'this file is checked out to you. Open your downloaded copy in your CAD program, save it, then press ', h('b', null, 'Check in'), ' and choose the saved file.'];
    else if (d.checked_out_by) msg = [h('b', null, `${d.checked_out_name} is editing this file. `), 'You can view and download it, but not change it until it is checked in.'];
    else if (d.state === 'in_work') msg = [h('b', null, 'In work. '), 'To change it press ', h('b', null, 'Check out'), '. When the design is finished, press ', h('b', null, 'Submit'), ' so a checker can review it.'];
    else if (d.state === 'in_review') msg = [h('b', null, 'Waiting for review. '), can('approver') ? ['Check it in the viewer, then press ', h('b', null, 'Release'), ' or send it back.'] : 'An approver will release it or send it back to you.'];
    else if (d.state === 'released') { msg = [h('b', null, 'Released: safe to manufacture. '), `Revision ${d.revision} is locked. To change it, press `, h('b', null, 'Revise'), ` to start revision ${a.next_revision || 'next'}, or raise a `, h('b', null, 'Change request'), '.']; kind = 'ok'; }
    else msg = [h('b', null, 'Obsolete. '), 'Do not use this file for new work.'];
  }
  ui.banner.appendChild(h('div', { class: `banner ${kind}` }, icon(kind === 'ok' ? 'release' : 'info'), h('div', null, msg)));
}

// ------------------------------------------------------------------ preview pane
function renderPreview() {
  if (!ui) return;
  clear(ui.preview);
  if (!E.showPreview) return;
  const sel = selectedRows();
  if (!sel.length) {
    const f = E.data?.folder;
    ui.preview.appendChild(h('div', { class: 'pv-body' }, h('h2', null, f?.name || ''),
      h('p', { class: 'muted' }, `${E.data?.folders.length || 0} folders, ${E.data?.documents.length || 0} files`),
      h('p', { class: 'muted' }, 'Select a file to preview it here.')));
    return;
  }
  if (sel.length > 1) {
    ui.preview.appendChild(h('div', { class: 'pv-body' }, h('h2', null, `${sel.length} items selected`),
      h('ul', { class: 'small', style: { paddingLeft: '18px', margin: 0 } }, sel.slice(0, 12).map(r => h('li', null, r.filename)))));
    return;
  }
  const r = sel[0];
  if (r.kind === 'folder') {
    ui.preview.appendChild(h('div', { class: 'pv-body' }, h('div', { class: 'row' }, icon('folder', 28), h('h2', null, r.name)),
      h('p', { class: 'muted' }, `${r.files} files`), h('button', { class: 'btn', onclick: () => go(r.id) }, 'Open folder')));
    return;
  }
  const det = E.details.get(r.id);
  const img = r.preview_kind
    ? h('div', { class: `pv-img ${r.preview_kind === 'svg' ? 'light' : ''}`, title: 'Open in viewer', onclick: () => openViewer(r) },
      h('img', { src: fileUrl(r.id, 'preview', r.version), alt: `Preview of ${r.filename}` }),
      h('button', { class: 'btn sm open', onclick: e => { e.stopPropagation(); openViewer(r); } }, icon('cube', 14), 'Open viewer'))
    : h('div', { class: 'pv-img light' }, h('div', { class: 'muted', style: { textAlign: 'center', padding: '0 20px' } }, icon('file', 36),
      h('p', { class: 'small' }, det?.meta?.preview_note || 'No preview for this file type.')));
  const item = det?.item;
  const dl = h('dl', { class: 'props' }, [
    ['Part number', h('span', { class: 'mono' }, r.part_number || '—')],
    ['Description', r.description || '—'],
    ['Material', item?.material || '—'],
    ['Revision', h('span', null, h('b', null, r.revision), ' ', stateChip(r.state))],
    ['Version', `${r.version}`],
    ['Type', r.type_label],
    ['Checked out', r.checked_out_by ? `${r.checked_out_by === S.user.id ? 'You' : r.checked_out_name}, ${ago(r.checked_out_at)}` : 'No'],
    ['Modified', fmtDate(r.updated_at)],
  ].flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)]));
  const body = h('div', { class: 'pv-body' },
    h('div', null, h('h2', { style: { overflowWrap: 'anywhere' } }, r.filename), S.mode === 'guided' ? h('p', { class: 'small muted', style: { margin: '4px 0 0' } }, STATE_PLAIN[r.state]) : null),
    item?.standard_part ? h('div', { class: 'banner info small' }, icon('common'), h('div', null, 'Duplicate of standard part ', h('a', { href: `#/item/${item.standard_part.id}` }, item.standard_part.part_number), '. Use the standard part in new designs.')) : null,
    dl,
    h('div', { class: 'row wrap' },
      h('a', { class: 'btn sm', href: `#/doc/${r.id}` }, icon('info', 14), 'All details'),
      h('button', { class: 'btn sm', onclick: () => downloadDoc(r.id) }, icon('download', 14), 'Download')),
    det ? historyMini(det) : h('p', { class: 'muted small' }, 'Loading history…'));
  ui.preview.append(img, body);
}

function historyMini(det) {
  const ev = det.events.slice(0, 6);
  return h('div', null, h('h3', { style: { margin: '6px 0 8px' } }, 'History'),
    h('div', { class: 'timeline' }, ev.map(e => h('div', { class: 'tl' }, h('span', { class: `dot ${e.to_state}` }),
      h('div', { class: 'small' }, h('b', null, `Rev ${e.revision} · ${({ in_work: 'In work', in_review: 'In review', released: 'Released', obsolete: 'Obsolete' })[e.to_state]}`),
        h('div', { class: 'muted' }, `${e.by}, ${ago(e.at)}`, e.change_number ? ` · ${e.change_number}` : ''),
        e.comment ? h('div', null, e.comment) : null)))),
    det.where_used.length ? h('div', null, h('h3', { style: { margin: '12px 0 6px' } }, `Used in ${det.where_used.length}`),
      det.where_used.slice(0, 5).map(w => h('div', { class: 'small' }, h('a', { href: `#/bom/${w.bom_id}` }, w.part_number), ` ${w.description} · qty ${w.qty}`))) : null);
}

// ------------------------------------------------------------------ menus
function rowMenu(e, r) {
  if (!E.sel.has(r.key)) select(r.key, false, false);
  if (r.kind === 'folder') return folderMenu(e, r);
  const docs = selectedDocs();
  const one = docs.length === 1 ? docs[0] : null;
  const it = (label, ic, ok, whyText, run, key) => ({ label, icon: ic, disabled: !ok, why: whyText, run, key });
  contextMenu(e.clientX, e.clientY, [
    it('Open in viewer', 'cube', !!one, 'Select one file', () => openViewer(one), 'Space'),
    it('Open details page', 'info', !!one, '', () => { location.hash = `#/doc/${one.id}`; }, 'Enter'),
    it('Download', 'download', true, '', () => docs.forEach(d => downloadDoc(d.id))),
    '-',
    it('Check out', 'checkout', allOk('checkout', docs), why('checkout', docs), () => doCheckout(docs), 'Ctrl+O'),
    it('Check in…', 'checkin', docs.every(d => d.checked_out_by === S.user.id), why('checkin', docs), () => doCheckin(docs), 'Ctrl+I'),
    it('Undo check-out', 'unlock', allOk('undo_checkout', docs), why('undo_checkout', docs), () => doUndo(docs)),
    '-',
    it('Submit for review', 'review', allOk('submit', docs), why('submit', docs), () => doState(docs, 'in_review')),
    it('Send back to In work', 'back', allOk('send_back', docs), why('send_back', docs), () => doState(docs, 'in_work')),
    it('Release', 'release', allOk('release', docs), why('release', docs), () => doState(docs, 'released')),
    it(one ? `Revise to ${acts(one).next_revision || 'next'}…` : 'Revise…', 'revise', allOk('revise', docs), why('revise', docs), () => doRevise(docs), 'Ctrl+R'),
    it('Make obsolete…', 'x', allOk('obsolete', docs), why('obsolete', docs), () => doState(docs, 'obsolete')),
    it('Start a change request…', 'change', can('engineer'), 'View-only access', () => newChange(docs)),
    '-',
    it('Where used', 'link', !!one?.item_id, 'No part number', () => whereUsed(one), 'Ctrl+U'),
    one?.doc_type === 'assembly' ? it('Open / build BOM', 'bom', true, '', () => openBom(one)) : null,
    it('Send item to ERP', 'erp', !!one?.item_id && can('approver'), 'Needs approver role and a part number', () => pushErp(one)),
    it('Copy part number', 'copy', !!one?.part_number, 'No part number', () => navigator.clipboard.writeText(one.part_number).then(() => toast('Copied'))),
    '-',
    it('Rename…', 'edit', !!one && acts(one).edit?.ok, one ? acts(one).edit?.why : '', () => rename(one), 'F2'),
    it('Delete', 'trash', allOk('delete', docs), why('delete', docs), () => doDelete(docs), 'Del'),
  ]);
}

function folderMenu(e, f) {
  contextMenu(e.clientX, e.clientY, [
    { label: 'Open', icon: 'folderOpen', run: () => go(f.id) },
    { label: 'Add files here…', icon: 'upload', disabled: !can('engineer'), run: () => pickFiles(f.id) },
    { label: 'New folder inside…', icon: 'folderPlus', disabled: !can('engineer'), run: () => newFolder(f.id) },
    { label: 'Find common parts in this folder', icon: 'common', run: () => { location.hash = `#/common?folder=${f.id}`; } },
    '-',
    { label: 'Rename…', icon: 'edit', disabled: !can('engineer') || !f.parent_id, run: () => renameFolder(f) },
    { label: 'Delete folder', icon: 'trash', disabled: !can('engineer') || !f.parent_id, run: () => deleteFolder(f) },
  ]);
}

// ------------------------------------------------------------------ keyboard
function listKeys(e) {
  const rs = rows();
  if (!rs.length) return;
  const idx = rs.findIndex(r => r.key === E.focus);
  const docs = selectedDocs();
  const one = docs.length === 1 ? docs[0] : null;
  const ctrl = e.ctrlKey || e.metaKey;
  const move = n => {
    e.preventDefault();
    const i = Math.max(0, Math.min(rs.length - 1, (idx < 0 ? 0 : idx + n)));
    select(rs[i].key, false, e.shiftKey);
    const el = ui.list.querySelector(`[data-key="${rs[i].key}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  };
  if (e.key === 'ArrowDown') return move(1);
  if (e.key === 'ArrowUp') return move(-1);
  if (e.key === 'Home') return move(-rs.length);
  if (e.key === 'End') return move(rs.length);
  if (e.key === 'Enter') { e.preventDefault(); const r = rs[idx]; if (r) (r.kind === 'folder' ? go(r.id) : (location.hash = `#/doc/${r.id}`)); return; }
  if (e.key === ' ') { e.preventDefault(); if (one) openViewer(one); return; }
  if (e.key === 'Backspace') { e.preventDefault(); goUp(); return; }
  if (e.key === 'Delete' && docs.length && allOk('delete', docs)) { e.preventDefault(); doDelete(docs); return; }
  if (e.key === 'F2' && one) { e.preventDefault(); rename(one); return; }
  if (ctrl && e.key.toLowerCase() === 'a') { e.preventDefault(); E.sel = new Set(rs.map(r => r.key)); renderSelection(); return; }
  if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
    e.preventDefault();
    const el = ui.list.querySelector(`[data-key="${E.focus}"]`);
    if (el) { const b = el.getBoundingClientRect(); rowMenu({ clientX: b.left + 60, clientY: b.bottom, preventDefault() {} }, rs[idx]); }
  }
}

function globalKeys(e) {
  if (!ui || document.querySelector('dialog[open], .viewer')) return;
  const ctrl = e.ctrlKey || e.metaKey;
  const docs = selectedDocs();
  const one = docs.length === 1 ? docs[0] : null;
  if (e.key === 'F5') { e.preventDefault(); refresh(); return; }
  if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); history.back(); return; }
  if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); history.forward(); return; }
  if (!ctrl) return;
  const k = e.key.toLowerCase();
  if (k === 'o' && allOk('checkout', docs)) { e.preventDefault(); doCheckout(docs); }
  else if (k === 'i' && docs.length && docs.every(d => d.checked_out_by === S.user.id)) { e.preventDefault(); doCheckin(docs); }
  else if (k === 'r') { e.preventDefault(); if (allOk('revise', docs)) doRevise(docs); }
  else if (k === 'u' && one?.item_id) { e.preventDefault(); whereUsed(one); }
}

// ------------------------------------------------------------------ actions
async function doCheckout(docs) {
  const ok = [];
  for (const d of docs) {
    try { await post(`/api/documents/${d.id}/checkout`); ok.push(d); } catch (e) { fail(e); }
  }
  if (ok.length) {
    if (ok.length === 1 && S.mode === 'guided') {
      downloadDoc(ok[0].id);
      toast(`${ok[0].filename} is checked out to you and downloading. Edit it, save it, then press Check in.`, 'ok');
    } else toast(`Checked out ${ok.length} file${ok.length > 1 ? 's' : ''}.`, 'ok');
  }
  await refresh();
}

function doCheckin(docs) {
  if (docs.length === 1) return checkinDialog(docs[0]);
  // several: release locks only (files unchanged) or upload via drag & drop
  confirmDlg('Check in files', `Check in ${docs.length} files without new content? To upload new versions, drag the saved files into this folder — files with the same name become new versions.`, 'Check in')
    .then(async yes => { if (!yes) return; for (const d of docs) { try { await post(`/api/documents/${d.id}/undo-checkout`); } catch (e) { fail(e); } } refresh(); });
}

function checkinDialog(d) {
  const file = h('input', { type: 'file', class: 'input', accept: `.${d.ext}`, id: 'ci-file' });
  const comment = h('textarea', { class: 'input', id: 'ci-comment', placeholder: 'What did you change? e.g. "Bore changed to Ø82 H7"' });
  const keep = h('input', { type: 'checkbox' });
  const bar = h('div', { class: 'progress', hidden: true }, h('div'));
  dialog({
    title: `Check in ${d.filename}`,
    body: [
      h('div', { class: 'banner info small' }, icon('info'), h('div', null, `Choose the .${d.ext} file you saved. It becomes version ${d.version + 1} of revision ${d.revision}.`)),
      h('div', { class: 'field' }, h('label', { for: 'ci-file' }, 'Saved file'), file),
      h('div', { class: 'field' }, h('label', { for: 'ci-comment' }, 'Comment'), comment),
      h('label', { class: 'check' }, keep, 'Keep it checked out (I will keep working on it)'),
      bar],
    actions: [
      { label: 'Cancel' },
      { label: 'Check in without changes', onClick: async () => { await post(`/api/documents/${d.id}/undo-checkout`); toast('Check-out released', 'ok'); refresh(); } },
      { label: 'Check in', primary: true, onClick: async () => {
        if (!file.files[0]) { toast('Choose the saved file first', 'err'); return false; }
        const fd = new FormData();
        fd.append('file', file.files[0]);
        fd.append('comment', comment.value);
        fd.append('keep_checked_out', keep.checked ? '1' : '0');
        bar.hidden = false;
        const r = await upload(`/api/documents/${d.id}/checkin`, fd, p => { bar.firstChild.style.width = `${Math.round(p * 100)}%`; });
        toast(`Checked in ${r.filename} as version ${r.version}.`, 'ok');
        await refresh();
      } }],
  });
}

async function doUndo(docs) {
  if (!await confirmDlg('Undo check-out', `Unlock ${docs.length === 1 ? docs[0].filename : docs.length + ' files'}? Changes you have not checked in will not be saved in Kosha.`, 'Undo check-out')) return;
  for (const d of docs) { try { await post(`/api/documents/${d.id}/undo-checkout`); } catch (e) { fail(e); } }
  refresh();
}

async function doState(docs, to) {
  const labels = { in_review: 'Submit for review', released: 'Release', in_work: 'Send back to In work', obsolete: 'Make obsolete' };
  const comment = h('textarea', { class: 'input', id: 'st-c', placeholder: to === 'in_work' ? 'What needs fixing?' : 'Optional note' });
  dialog({
    title: labels[to],
    body: [h('p', { style: { margin: 0 } }, docs.length === 1 ? `${docs[0].filename}, revision ${docs[0].revision}` : `${docs.length} files`),
      to === 'released' ? h('div', { class: 'banner ok small' }, icon('release'), h('div', null, `Released revisions are locked and become available to production. Items with an ERP link are queued for ${S.erp}.`)) : null,
      to === 'obsolete' ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, 'Obsolete files stay in Kosha for history but are marked "do not use".')) : null,
      h('div', { class: 'field' }, h('label', { for: 'st-c' }, 'Comment'), comment)],
    actions: [{ label: 'Cancel' }, { label: labels[to], primary: true, onClick: async () => {
      let n = 0;
      for (const d of docs) { try { await post(`/api/documents/${d.id}/state`, { to, comment: comment.value }); n++; } catch (e) { fail(e); } }
      if (n) toast(`${labels[to]}: ${n} file${n > 1 ? 's' : ''} done.`, 'ok');
      await refresh();
    } }],
  });
}

async function doRevise(docs) {
  const d = docs[0];
  const nxt = acts(d).next_revision || 'the next revision';
  if (!await confirmDlg('Start a new revision', docs.length === 1
    ? `Start revision ${nxt} of ${d.filename}? Revision ${d.revision} stays released and unchanged for production. You can then check out ${nxt} and edit it.`
    : `Start new revisions of ${docs.length} released files?`, `Start revision ${docs.length === 1 ? nxt : ''}`)) return;
  for (const x of docs) { try { await post(`/api/documents/${x.id}/revise`); } catch (e) { fail(e); } }
  toast('New revision started. Check it out to make your changes.', 'ok');
  refresh();
}

async function doDelete(docs) {
  if (!await confirmDlg('Delete', `Delete ${docs.length === 1 ? docs[0].filename : docs.length + ' files'}? Only files that were never released can be deleted.`, 'Delete', true)) return;
  for (const d of docs) { try { await del(`/api/documents/${d.id}`); } catch (e) { fail(e); } }
  E.sel.clear();
  refresh();
}

async function rename(d) {
  const name = await prompt('Rename', 'New file name', d.filename, 'Rename', 'Keep the same extension. The file must be checked out to you.');
  if (!name || name === d.filename) return;
  try { await patch(`/api/documents/${d.id}`, { filename: name }); refresh(); } catch (e) { fail(e); }
}

async function moveDocs(ids, folderId) {
  if (folderId === E.cur) return;
  let n = 0;
  for (const id of ids) { try { await patch(`/api/documents/${id}`, { folder_id: folderId }); n++; } catch (e) { fail(e); } }
  if (n) toast(`Moved ${n} file${n > 1 ? 's' : ''} to ${E.byId.get(folderId)?.name}.`, 'ok');
  E.sel.clear();
  refresh(false);
}

async function newFolder(parent = E.cur) {
  const name = await prompt('New folder', 'Folder name', '', 'Create');
  if (!name) return;
  try { await post('/api/folders', { parent_id: parent, name }); E.expanded.add(parent); store('expanded', [...E.expanded]); refresh(); } catch (e) { fail(e); }
}
async function renameFolder(f) {
  const name = await prompt('Rename folder', 'Folder name', f.name, 'Rename');
  if (!name || name === f.name) return;
  try { await patch(`/api/folders/${f.id}`, { name }); refresh(); } catch (e) { fail(e); }
}
async function deleteFolder(f) {
  if (!await confirmDlg('Delete folder', `Delete the folder "${f.name}"? Only empty folders can be deleted.`, 'Delete', true)) return;
  try { await del(`/api/folders/${f.id}`); if (f.id === E.cur) goUp(); else refresh(); } catch (e) { fail(e); }
}

function pickFiles(folderId = E.cur) {
  const inp = h('input', { type: 'file', multiple: true, style: { display: 'none' } });
  inp.addEventListener('change', () => { if (inp.files.length) uploadDialog([...inp.files], folderId); inp.remove(); });
  document.body.appendChild(inp);
  inp.click();
}

// Files and whole folders dragged from Windows Explorer
async function dropped(dt, folderId) {
  const roots = [...(dt.items || [])].map(i => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
  if (!roots.some(r => r.isDirectory)) return uploadDialog([...dt.files], folderId);
  const out = [];
  const readAll = reader => new Promise((res, rej) => {
    const acc = [];
    const next = () => reader.readEntries(batch => { if (!batch.length) res(acc); else { acc.push(...batch); next(); } }, rej);
    next();
  });
  const walk = async (entry, dir) => {
    if (entry.isFile) {
      if (/^(~\$|\.)|\.(bak|dwl2?|tmp)$|^thumbs\.db$/i.test(entry.name)) return;
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, dir });
    } else if (entry.isDirectory) {
      const kids = await readAll(entry.createReader());
      for (const k of kids) await walk(k, dir ? `${dir}/${entry.name}` : entry.name);
    }
  };
  toast('Reading the dropped folders…');
  for (const r of roots) await walk(r, '');
  if (!out.length) { toast('No files found in the dropped folders.', 'err'); return; }
  uploadDialog(out, folderId);
}

function uploadDialog(files, folderId) {
  const entries = files.map(f => (f.file ? f : { file: f, dir: '' }));
  const withDirs = entries.some(e => e.dir);
  const existing = new Map((folderId === E.cur ? E.data.documents : []).map(d => [d.filename.toLowerCase(), d]));
  const comment = h('textarea', { class: 'input', id: 'up-c', placeholder: 'e.g. "First issue" or "Updated from customer comments"' });
  const keep = h('input', { type: 'checkbox' });
  const bar = h('div', { class: 'progress', hidden: true }, h('div'));
  const status = h('div', { class: 'small muted' });
  const shown = entries.slice(0, 300);
  const list = h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th', null, 'File'), h('th', null, 'Size'), h('th', null, 'Becomes'))),
    h('tbody', null, shown.map(({ file: f, dir }) => {
      const ex = dir ? null : existing.get(f.name.toLowerCase());
      const what = !ex ? h('span', { class: 'chip st-in_work' }, 'New file')
        : ex.checked_out_by === S.user.id ? h('span', { class: 'chip st-in_review' }, `Version ${ex.version + 1} of rev ${ex.revision}`)
          : h('span', { class: 'chip st-failed' }, ex.checked_out_by ? `Locked by ${ex.checked_out_name}` : 'Check out first');
      return h('tr', null, h('td', { class: 'ellipsis', style: { maxWidth: '420px' }, title: dir ? `${dir}/${f.name}` : f.name },
        dir ? h('span', { class: 'muted' }, `${dir}/`) : null, f.name), h('td', { class: 'nowrap' }, fmtSize(f.size)), h('td', null, what));
    }), entries.length > shown.length ? h('tr', null, h('td', { colspan: 3, class: 'muted' }, `… and ${entries.length - shown.length} more`)) : null));
  const dirs = new Set(entries.map(e => e.dir).filter(Boolean));
  dialog({
    title: `Add ${entries.length} file${entries.length > 1 ? 's' : ''} to ${E.byId.get(folderId)?.name || 'folder'}`,
    wide: entries.length > 3,
    body: [
      withDirs ? h('div', { class: 'banner info small' }, icon('folderPlus'), h('div', null, `The ${dirs.size} sub-folder${dirs.size > 1 ? 's' : ''} will be recreated in Kosha.`,
        entries.length > 500 ? [' For thousands of files, an administrator can use ', h('b', null, 'Admin › Import legacy data'), ' instead: it also finds identical copies and writes a report.'] : '')) : null,
      S.mode === 'guided' ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Kosha reads the part number from the file name (e.g. "MX-100-020 Main Shaft.dxf") or the drawing title block. New files start at revision A, "In work".')) : null,
      h('div', { style: { maxHeight: '260px', overflow: 'auto', border: '1px solid var(--line)', borderRadius: '6px' } }, list),
      h('div', { class: 'field' }, h('label', { for: 'up-c' }, 'Comment'), comment),
      h('label', { class: 'check' }, keep, 'Keep checked out to me after adding'),
      bar, status],
    actions: [{ label: 'Cancel' }, { label: 'Add to Kosha', primary: true, onClick: async () => {
      bar.hidden = false;
      const totalBytes = entries.reduce((a, e) => a + e.file.size, 0) || 1;
      let sent = 0;
      const added = [];
      const errors = [];
      // send in batches so large drops survive and show progress
      for (let i = 0; i < entries.length;) {
        const batch = [];
        let bytes = 0;
        while (i < entries.length && batch.length < 25 && (bytes < 64e6 || !batch.length)) { batch.push(entries[i]); bytes += entries[i].file.size; i++; }
        const fd = new FormData();
        fd.append('folder_id', folderId);
        fd.append('comment', comment.value);
        fd.append('keep_checked_out', keep.checked ? '1' : '0');
        fd.append('paths', JSON.stringify(batch.map(e => e.dir)));
        for (const e of batch) fd.append('file', e.file);
        status.textContent = `Uploading ${i} of ${entries.length}…`;
        const r = await upload('/api/documents/upload', fd, p => { bar.firstChild.style.width = `${Math.round(100 * (sent + p * bytes) / totalBytes)}%`; });
        sent += bytes;
        added.push(...r.documents);
        errors.push(...r.errors);
      }
      if (added.length) toast(`Added ${added.length} file${added.length > 1 ? 's' : ''}${dirs.size ? ` in ${dirs.size} folders` : ''}.`, 'ok');
      for (const er of errors.slice(0, 5)) toast(`${er.filename}: ${er.error}`, 'err');
      if (errors.length > 5) toast(`${errors.length - 5} more files could not be added.`, 'err');
      if (folderId !== E.cur) go(folderId); else await refresh();
      if (added.length === 1 && folderId === E.cur && !withDirs) { E.sel = new Set([`d:${added[0].id}`]); E.focus = `d:${added[0].id}`; renderSelection(); }
    } }],
  });
}

async function whereUsed(d) {
  try {
    const r = await get(`/api/items/${d.item_id}/where-used`);
    dialog({ title: `Where ${d.part_number} is used`, body: r.where_used.length
      ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th', null, 'Assembly'), h('th', null, 'Description'), h('th', null, 'BOM rev'), h('th', { class: 'num' }, 'Qty'))),
        h('tbody', null, r.where_used.map(w => h('tr', { class: 'clickable', onclick: () => { document.querySelector('dialog[open]')?.close(); location.hash = `#/bom/${w.bom_id}`; } },
          h('td', { class: 'mono' }, w.part_number), h('td', null, w.description), h('td', null, w.bom_revision, ' ', stateChip(w.bom_state === 'released' ? 'released' : 'in_work', w.bom_state)), h('td', { class: 'num' }, w.qty)))))
      : h('p', { class: 'muted' }, 'This part is not used in any BOM yet.'),
    actions: [{ label: 'Close' }] });
  } catch (e) { fail(e); }
}

async function openBom(d) {
  const det = E.details.get(d.id) || await get(`/api/documents/${d.id}`);
  if (det.bom) { location.hash = `#/bom/${det.bom.id}`; return; }
  if (!await confirmDlg('Build the engineering BOM', `There is no BOM for ${d.part_number || d.filename} revision ${d.revision} yet. Build it from the assembly now?`, 'Build BOM')) return;
  try {
    const r = await post('/api/boms/generate', { document_id: d.id });
    toast(`BOM built with ${r.lines} lines${r.quantities_verified ? '' : ' (check quantities)'}.`, 'ok');
    location.hash = `#/bom/${r.bom_id}`;
  } catch (e) {
    if (e.status === 400 && det.item) {
      if (await confirmDlg('Start an empty BOM', `${e.message}\n\nStart an empty BOM to fill in or import instead?`, 'Start BOM')) {
        const r = await post('/api/boms', { item_id: det.item.id });
        location.hash = `#/bom/${r.bom_id}`;
      }
    } else fail(e);
  }
}

async function pushErp(d) {
  try {
    const r = await post('/api/erp/push', { kind: 'item', id: d.item_id });
    const j = r.job;
    toast(j.status === 'failed' ? `ERP: ${j.error}` : `ERP: ${j.response_text}`, j.status === 'failed' ? 'err' : 'ok');
  } catch (e) { fail(e); }
}

function newChange(docs) {
  import('./changes.js').then(m => m.newChangeDialog(docs.map(d => d.id)));
}

function open(r) { if (r.kind === 'folder') go(r.id); else openViewer(r); }
export { closeMenu };
