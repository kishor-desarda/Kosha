// SPDX-License-Identifier: AGPL-3.0-or-later
// Inputs library (reference material per part or per project) and the project pages.
import {
  S, h, mount, clear, append, icon, get, post, put, patch, del, upload, toast, fail, dialog, confirmDlg, fmtDate, fmtSize,
  ago, store, can, debounce,
} from './core.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));
const ENG_WHY = 'Adding and changing inputs needs the engineer role.';
const SEL_WHY = 'Tick one or more inputs first.';
const ROLES = [['viewer', 'Viewers'], ['engineer', 'Engineers'], ['approver', 'Approvers']];
const TYPE_ICON = { pdf: 'file', image: 'review', spreadsheet: 'grid', document: 'file', presentation: 'layers', cad: 'cube', email: 'file', other: 'file' };
const IMAGE_RE = /\.(png|jpe?g|gif|bmp|webp|tiff?|heic)$/i;

const tok = () => encodeURIComponent(S.token);
export const fileHref = (id, n, what = '', inline = false) =>
  `/api/inputs/${id}/files/${n}${what ? `/${what}` : ''}?${inline ? 'inline=1&' : ''}access_token=${tok()}`;
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const titleFrom = name => name.replace(/\.[^.]+$/, '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim() || name;
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;

// ------------------------------------------------------------------ shared data
let cssReady;
export function ensureCss() {
  if (!cssReady) {
    cssReady = new Promise(resolve => {
      const l = h('link', { rel: 'stylesheet', href: '/static/inputs.css', 'data-inputs-css': '1' });
      l.addEventListener('load', () => resolve());
      l.addEventListener('error', () => resolve());
      document.head.appendChild(l);
    });
  }
  return cssReady;
}
let settingsP = null;
export function inputSettings(force = false) {
  if (!settingsP || force) settingsP = get('/api/inputs/settings').catch(e => { settingsP = null; throw e; });
  return settingsP;
}
let projectsP = null;
function allProjects(force = false) {
  if (!projectsP || force) projectsP = get('/api/projects?limit=1000').then(r => r.projects).catch(e => { projectsP = null; throw e; });
  return projectsP;
}
let usersP = null;
function allUsers() {
  if (!usersP) usersP = get('/api/users').then(r => r.users.filter(u => u.active)).catch(e => { usersP = null; throw e; });
  return usersP;
}
window.addEventListener('kosha:changed', () => { projectsP = null; });

// ------------------------------------------------------------------ small pieces
function hl(segs, fallback) {
  if (!segs || !segs.length) return fallback;
  return segs.map(([t, hit]) => (hit ? h('mark', { class: 'inp-hl' }, t) : t));   // text nodes only, never HTML
}
function kindChip(inp) { return h('span', { class: 'chip inp-kind' }, inp.kind_label || inp.kind); }
function lockChip(inp) {
  return inp.confidentiality === 'restricted'
    ? h('span', { class: 'chip inp-lock', title: 'Restricted: only the people and roles listed on the input (and admins) can see it.' }, icon('checkout', 12), 'Restricted') : null;
}
function pendingChip(inp) {
  return inp.text_status === 'pending' ? h('span', { class: 'chip inp-pending', title: 'Kosha is reading the text of the files for search. You can keep working.' }, 'Reading text…') : null;
}
function thumbBox(inp) {
  if (inp.thumb) return h('img', { src: fileHref(inp.id, inp.thumb, 'thumb'), alt: '', loading: 'lazy' });
  const f = inp.files && inp.files[0];
  if (f) return h('span', { class: 'inp-ext' }, icon(TYPE_ICON[(inp.types || [])[0]] || 'file', 30), (f.ext || 'file').toUpperCase());
  if (inp.url) return h('span', { class: 'inp-ext' }, icon('link', 30), 'LINK');
  return h('span', { class: 'inp-ext' }, icon('edit', 30), 'NOTE');
}
function linkChips(inp) {
  return h('div', { class: 'inp-links' },
    (inp.items || []).map(it => h('a', { class: 'chip mono', href: `#/item/${it.id}`, title: it.description, onclick: e => e.stopPropagation() }, it.part_number)),
    (inp.projects || []).map(p => h('a', { class: 'chip st-open', href: `#/project/${p.id}`, title: p.name, onclick: e => e.stopPropagation() }, p.code)));
}
function snippetEl(inp) {
  if (!inp.snippet) return null;
  return h('div', { class: 'inp-snip' }, inp.snippet_file ? h('span', { class: 'from' }, `${inp.snippet_file}: `) : null, hl(inp.snippet));
}
function disabledBtn(label, ic, why) {
  return h('button', { class: 'btn', disabled: true, title: why, 'aria-disabled': 'true' }, icon(ic, 16), label);
}
function kindSelect(cfg, value, extra) {
  const sel = h('select', { class: 'input' });
  if (extra) sel.appendChild(h('option', { value: '' }, extra));
  for (const k of cfg.kinds) sel.appendChild(h('option', { value: k.key, selected: k.key === value }, k.label));
  return sel;
}

// Chips with an input and a suggestion list. search(q) -> [{value, label, sub}]
function chipPicker({ id, placeholder, search, initial = [], free = false, onChange }) {
  let chosen = [...initial];
  let options = [];
  const listId = `${id}-list`;
  const dl = h('datalist', { id: listId });
  const input = h('input', { id, list: listId, placeholder, autocomplete: 'off' });
  const box = h('div', { class: 'inp-chips' });
  const draw = () => {
    clear(box);
    for (const c of chosen) {
      box.appendChild(h('span', { class: 'inp-chip', title: c.sub || '' }, c.label,
        h('button', { type: 'button', 'aria-label': `Remove ${c.label}`, onclick: () => { chosen = chosen.filter(x => x.value !== c.value); draw(); onChange && onChange(chosen); } }, icon('x', 12))));
    }
    box.append(input, dl);
  };
  const add = c => {
    if (!c || chosen.some(x => String(x.value).toLowerCase() === String(c.value).toLowerCase())) return;
    chosen.push(c); input.value = ''; draw(); input.focus(); onChange && onChange(chosen);
  };
  const refresh = debounce(async () => {
    try {
      options = await search(input.value.trim());
      clear(dl);
      for (const o of options.slice(0, 30)) dl.appendChild(h('option', { value: o.label }, o.sub || ''));
    } catch (e) { /* suggestions are optional */ }
  }, 200);
  const pick = () => {
    const v = input.value.trim();
    if (!v) return false;
    const o = options.find(x => x.label.toLowerCase() === v.toLowerCase());
    if (o) { add(o); return true; }
    if (free) { add({ value: v, label: v }); return true; }
    return false;
  };
  input.addEventListener('focus', refresh);
  input.addEventListener('input', e => {
    if (e.inputType === 'insertReplacementText' || e.inputType === undefined) { if (pick()) return; }
    refresh();
  });
  input.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || (free && e.key === ',')) && input.value.trim()) {
      e.preventDefault();
      if (!pick()) toast('Choose one of the suggestions.', 'err');
    } else if (e.key === 'Backspace' && !input.value && chosen.length) {
      chosen.pop(); draw(); onChange && onChange(chosen);
    }
  });
  input.addEventListener('blur', () => { if (free && input.value.trim()) pick(); });
  draw();
  return { el: box, get: () => chosen, set: v => { chosen = [...v]; draw(); } };
}
const tagPicker = (id, initial = [], onChange) => chipPicker({ id, placeholder: 'Type a tag, press Enter', free: true, onChange,
  initial: initial.map(t => ({ value: t, label: t })),
  search: async q => (await get(`/api/tags?q=${encodeURIComponent(q)}&limit=30`)).tags.map(t => ({ value: t.tag, label: t.tag, sub: `${t.n} input${t.n === 1 ? '' : 's'}` })) });
const itemPicker = (id, initial = [], onChange) => chipPicker({ id, placeholder: 'Part number or description', onChange,
  initial: initial.map(i => ({ value: i.id, label: i.part_number, sub: i.description })),
  search: async q => (await get(`/api/items?q=${encodeURIComponent(q)}&limit=30`)).items.map(i => ({ value: i.id, label: i.part_number, sub: i.description })) });
const projectPicker = (id, initial = []) => chipPicker({ id, placeholder: 'Project code or name',
  initial: initial.map(p => ({ value: p.id, label: p.code, sub: p.name })),
  search: async q => (await allProjects()).filter(p => !q || `${p.code} ${p.name} ${p.customer}`.toLowerCase().includes(q.toLowerCase()))
    .map(p => ({ value: p.id, label: p.code, sub: `${p.name}${p.status === 'closed' ? ' (closed)' : ''}` })) });
const peoplePicker = (id, initial = []) => chipPicker({ id, placeholder: 'Add a person',
  initial: initial.map(u => ({ value: u.id, label: u.full_name, sub: u.username })),
  search: async q => (await allUsers()).filter(u => !q || `${u.full_name} ${u.username}`.toLowerCase().includes(q.toLowerCase()))
    .map(u => ({ value: u.id, label: u.full_name, sub: `${u.username} (${u.role})` })) });

function accessEditor(idp, init = { confidentiality: 'normal', roles: [], users: [] }) {
  const normal = h('input', { type: 'radio', name: `${idp}-conf`, checked: init.confidentiality !== 'restricted' });
  const restricted = h('input', { type: 'radio', name: `${idp}-conf`, checked: init.confidentiality === 'restricted' });
  const roles = ROLES.map(([r, label]) => [r, h('input', { type: 'checkbox', checked: (init.roles || []).includes(r) }), label]);
  const people = peoplePicker(`${idp}-people`, init.users || []);
  const more = h('div', { class: 'col', hidden: init.confidentiality !== 'restricted', style: { paddingLeft: '26px' } },
    h('span', { class: 'small muted' }, 'Who else may see it? Admins and the person who added it always can.'),
    h('div', { class: 'row wrap' }, roles.map(([, box, label]) => h('label', { class: 'check small' }, box, `All ${label.toLowerCase()}`))),
    people.el);
  const sync = () => { more.hidden = !restricted.checked; };
  normal.addEventListener('change', sync); restricted.addEventListener('change', sync);
  const el = h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Who can see it'),
    h('label', { class: 'check' }, normal, 'Everyone in Kosha'),
    h('label', { class: 'check' }, restricted, icon('checkout', 14), 'Restricted'), more);
  return {
    el,
    get: () => ({ confidentiality: restricted.checked ? 'restricted' : 'normal', roles: roles.filter(([, b]) => b.checked).map(([r]) => r), users: people.get().map(u => u.value) }),
  };
}

// ------------------------------------------------------------------ add files / link / note
export async function uploadDialog(files, defaults = {}, onDone) {
  if (!can('engineer')) { toast(ENG_WHY, 'err'); return; }
  if (!files.length) return;
  const cfg = await inputSettings();
  const many = files.length > 1;
  const images = files.filter(f => IMAGE_RE.test(f.name)).length;
  const mixed = images && images < files.length;
  const title = h('input', { class: 'input', id: 'iu-title', value: titleFrom(files[0].name) });
  const titleField = h('div', { class: 'field', hidden: many, style: { gridColumn: '1 / -1' } }, h('label', { for: 'iu-title' }, 'Title'), title);
  const each = h('input', { type: 'radio', name: 'iu-mode', checked: true });
  const group = h('input', { type: 'radio', name: 'iu-mode' });
  const syncMode = () => { titleField.hidden = many && !group.checked; };
  each.addEventListener('change', syncMode); group.addEventListener('change', syncMode);
  const kind = kindSelect(cfg, defaults.kind || (images === files.length ? 'photo' : 'other'), mixed ? 'Photo for images, Other for the rest' : null);
  if (mixed && !defaults.kind) kind.value = '';
  kind.id = 'iu-kind';
  const source = h('input', { class: 'input', id: 'iu-source', placeholder: 'e.g. Trade fair, Customer A, Supplier C catalogue' });
  const date = h('input', { class: 'input', id: 'iu-date', type: 'date', value: today() });
  const tags = tagPicker('iu-tags', defaults.tags || []);
  const items = itemPicker('iu-items', defaults.items || []);
  const projects = projectPicker('iu-projects', defaults.projects || []);
  const access = accessEditor('iu');
  const comment = h('textarea', { class: 'input', id: 'iu-comment', placeholder: 'Why it matters, what to look at' });
  const skip = h('input', { type: 'checkbox' });
  const bar = h('div', { class: 'progress', hidden: true }, h('div'));
  const status = h('div', { class: 'small muted' });
  const shown = files.slice(0, 200);
  dialog({
    title: many ? `Add ${files.length} files to the inputs library` : `Add ${files[0].name}`,
    wide: true,
    body: [
      S.mode === 'guided' ? h('div', { class: 'banner info small' }, icon('info'), h('div', null,
        'Inputs are reference material (benchmarks, requirements, datasheets, photos…). They have no revisions. ',
        'Kosha keeps every file safely, reads its text in the background for search, and never stores the same file twice.')) : null,
      h('div', { style: { maxHeight: '170px', overflow: 'auto', border: '1px solid var(--line)', borderRadius: '6px' } },
        h('table', { class: 'tbl' }, h('tbody', null, shown.map(f => h('tr', null, h('td', { class: 'ellipsis', style: { maxWidth: '520px' }, title: f.name }, f.name), h('td', { class: 'nowrap num' }, fmtSize(f.size)))),
          files.length > shown.length ? h('tr', null, h('td', { colspan: 2, class: 'muted' }, `… and ${files.length - shown.length} more`)) : null))),
      many ? h('div', { class: 'col', style: { gap: '0' } },
        h('label', { class: 'check' }, each, h('span', null, h('b', null, 'Each file becomes its own input'), h('span', { class: 'muted' }, ' (named after the file)'))),
        h('label', { class: 'check' }, group, h('span', null, h('b', null, 'Group into one input'), h('span', { class: 'muted' }, ' (e.g. a benchmark with photos and a PDF)')))) : null,
      h('div', { class: 'form-grid' },
        titleField,
        h('div', { class: 'field' }, h('label', { for: 'iu-kind' }, 'Kind'), kind),
        h('div', { class: 'field' }, h('label', { for: 'iu-date' }, 'Date'), date),
        h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'iu-source' }, 'Source (who or where it came from)'), source),
        h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'iu-tags' }, 'Tags'), tags.el),
        h('div', { class: 'field' }, h('label', { for: 'iu-items' }, 'Parts it informs'), items.el),
        h('div', { class: 'field' }, h('label', { for: 'iu-projects' }, 'Projects'), projects.el),
        h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'iu-comment' }, 'Comment'), comment)),
      access.el,
      h('label', { class: 'check small' }, skip, 'Leave out files that are already in the library'),
      bar, status],
    actions: [{ label: 'Cancel' }, { label: many ? `Add ${files.length} files` : 'Add input', primary: true, onClick: async () => {
      const a = access.get();
      const common = () => {
        const fd = new FormData();
        fd.append('kind', kind.value); fd.append('source', source.value.trim()); fd.append('date', date.value);
        fd.append('tags', JSON.stringify(tags.get().map(t => t.label)));
        fd.append('item_ids', JSON.stringify(items.get().map(i => i.value)));
        fd.append('project_ids', JSON.stringify(projects.get().map(p => p.value)));
        fd.append('confidentiality', a.confidentiality); fd.append('access_roles', JSON.stringify(a.roles));
        fd.append('access_users', JSON.stringify(a.users)); fd.append('comment', comment.value.trim());
        fd.append('skip_duplicates', skip.checked ? '1' : '0');
        return fd;
      };
      const grouped = !many || group.checked;
      if (grouped && !title.value.trim()) { toast('Give the input a title.', 'err'); title.focus(); return false; }
      bar.hidden = false;
      const total = files.reduce((s, f) => s + f.size, 0) || 1;
      let sent = 0;
      const created = []; const errors = []; const dups = [];
      let groupId = null;
      for (let i = 0; i < files.length;) {
        const batch = []; let bytes = 0;
        while (i < files.length && batch.length < 25 && (bytes < 64e6 || !batch.length)) { batch.push(files[i]); bytes += files[i].size; i++; }
        status.textContent = `Uploading ${i} of ${files.length}…`;
        const progress = p => { bar.firstChild.style.width = `${Math.round(100 * (sent + p * bytes) / total)}%`; };
        if (groupId) {
          const fd = new FormData();
          for (const f of batch) fd.append('file', f);
          const r = await upload(`/api/inputs/${groupId}/files`, fd, progress);
          errors.push(...(r.errors || [])); dups.push(...(r.duplicates_found || []));
        } else {
          const fd = common();
          if (grouped) { fd.append('group', '1'); fd.append('title', title.value.trim()); }
          for (const f of batch) fd.append('file', f);
          const r = await upload('/api/inputs', fd, progress);
          created.push(...r.inputs); errors.push(...r.errors); dups.push(...r.duplicates);
          if (grouped && r.inputs.length) groupId = r.inputs[0].id;
        }
        sent += bytes;
      }
      if (created.length) toast(grouped ? `Added "${created[0].title}" with ${plural(files.length - errors.length, 'file')}. Its text is read in the background.` : `Added ${plural(created.length, 'input')}. Their text is read in the background.`, 'ok');
      for (const d of dups.slice(0, 4)) toast(`${d.filename} is already in "${d.inputs[0].title}"${d.skipped ? ', so it was left out' : ' (stored once)'}.`, d.skipped ? '' : 'ok');
      if (dups.length > 4) toast(`${dups.length - 4} more files were already in the library.`);
      for (const er of errors.slice(0, 4)) toast(`${er.filename}: ${er.error}`, 'err');
      if (errors.length > 4) toast(`${errors.length - 4} more files could not be added.`, 'err');
      changed();
      if (onDone) onDone(created);
      return undefined;
    } }],
  });
}

export async function linkOrNoteDialog(defaults = {}, onDone) {
  if (!can('engineer')) { toast(ENG_WHY, 'err'); return; }
  const cfg = await inputSettings();
  const title = h('input', { class: 'input', id: 'in-title' });
  const url = h('input', { class: 'input', id: 'in-url', type: 'url', placeholder: 'https://…' });
  const note = h('textarea', { class: 'input', id: 'in-note', style: { minHeight: '130px' }, placeholder: 'Meeting notes, a phone call, a requirement in your own words…' });
  const kind = kindSelect(cfg, defaults.kind || 'meeting_note'); kind.id = 'in-kind';
  const source = h('input', { class: 'input', id: 'in-source' });
  const date = h('input', { class: 'input', id: 'in-date', type: 'date', value: today() });
  const tags = tagPicker('in-tags', defaults.tags || []);
  const items = itemPicker('in-items', defaults.items || []);
  const projects = projectPicker('in-projects', defaults.projects || []);
  const access = accessEditor('in');
  dialog({
    title: 'Add a web link or a note', wide: true,
    body: [h('div', { class: 'form-grid' },
      h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'in-title' }, 'Title'), title),
      h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'in-url' }, 'Web link (optional)'), url),
      h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'in-note' }, 'Note (optional)'), note),
      h('div', { class: 'field' }, h('label', { for: 'in-kind' }, 'Kind'), kind),
      h('div', { class: 'field' }, h('label', { for: 'in-date' }, 'Date'), date),
      h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'in-source' }, 'Source'), source),
      h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'in-tags' }, 'Tags'), tags.el),
      h('div', { class: 'field' }, h('label', { for: 'in-items' }, 'Parts it informs'), items.el),
      h('div', { class: 'field' }, h('label', { for: 'in-projects' }, 'Projects'), projects.el)),
    access.el],
    actions: [{ label: 'Cancel' }, { label: 'Add input', primary: true, onClick: async () => {
      if (!url.value.trim() && !note.value.trim()) { toast('Add a web link or write a note.', 'err'); return false; }
      const a = access.get();
      const r = await post('/api/inputs', {
        title: title.value.trim(), url: url.value.trim(), note: note.value, kind: kind.value, source: source.value.trim(), date: date.value,
        tags: tags.get().map(t => t.label), item_ids: items.get().map(i => i.value), project_ids: projects.get().map(p => p.value),
        confidentiality: a.confidentiality, access_roles: a.roles, access_users: a.users,
      });
      toast(`Added "${r.inputs[0].title}".`, 'ok');
      changed();
      if (onDone) onDone(r.inputs);
      return undefined;
    } }],
  });
}

function pickFiles(onFiles) {
  const inp = h('input', { type: 'file', multiple: true, style: { display: 'none' } });
  inp.addEventListener('change', () => { if (inp.files.length) onFiles([...inp.files]); inp.remove(); });
  document.body.appendChild(inp);
  inp.click();
}

// ------------------------------------------------------------------ the library (page and embedded)
const LIST_KEYS = ['kind', 'type', 'tag'];
const listOf = v => (v ? String(v).split(',').filter(Boolean) : []);

function library(root, opts = {}) {
  const fixed = opts.fixed || {};
  const p = opts.params || {};
  const st = {
    q: p.q || '', kind: listOf(p.kind), type: listOf(p.type), tag: listOf(p.tag), project: p.project || '', item: p.item || '',
    by: p.by || '', from: p.from || '', to: p.to || '', sort: p.sort || '', dir: p.dir || '', deleted: p.deleted === '1', offset: Number(p.offset) || 0,
    view: store('inputsView') || 'grid',
    showFilters: opts.compact || matchMedia('(max-width: 860px)').matches ? false : store('inputsFilters') !== false,
    sel: new Set(), focus: null, data: null, itemLabel: '', seq: 0, timer: null, alive: true,
  };
  const cfgP = inputSettings();
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search titles, tags, sources, file text, part numbers, project codes…', value: st.q, 'aria-label': 'Search inputs' });
  const sortSel = h('select', { class: 'input', 'aria-label': 'Sort by' },
    [['', 'Best order'], ['relevance', 'Relevance'], ['date', 'Date'], ['added', 'Date added'], ['title', 'Title'], ['kind', 'Kind']].map(([v, l]) => h('option', { value: v, selected: st.sort === v }, l)));
  const dirBtn = h('button', { class: 'btn ghost icon', 'aria-label': 'Sort direction' });
  const viewBtn = h('button', { class: 'btn ghost icon' });
  const filterBtn = h('button', { class: 'btn ghost', 'aria-pressed': String(st.showFilters) }, icon('list', 16), 'Filters');
  const bulkBar = h('div', { class: 'inp-bulk', hidden: true, role: 'toolbar', 'aria-label': 'Selected inputs' });
  const filters = h('aside', { class: 'inp-filters', 'aria-label': 'Filters' });
  const results = h('div', { class: 'inp-results' });
  const preview = h('aside', { class: 'inp-preview', 'aria-label': 'Preview' });
  const body = h('div', { class: 'inp-body' }, filters, results, preview);
  const drop = h('div', { class: 'inp-drop', hidden: true }, icon('upload', 34), 'Drop files to add them as inputs');
  const eng = can('engineer');
  const addBtns = eng ? [
    h('button', { class: 'btn primary', onclick: () => pickFiles(fs => addFiles(fs)) }, icon('upload', 16), 'Add files'),
    h('button', { class: 'btn', onclick: () => linkOrNoteDialog(uploadDefaults(), () => load()) }, icon('link', 16), 'Add link or note'),
  ] : [disabledBtn('Add files', 'upload', ENG_WHY)];
  const bar = h('div', { class: 'inp-bar' },
    h('div', { class: 'inp-search' }, icon('search', 16), search), sortSel, dirBtn, viewBtn, filterBtn,
    h('span', { class: 'spacer' }), opts.compact ? addBtns : null);
  mount(root, h('div', { class: 'inp' }, bar, bulkBar, body, drop));

  const uploadDefaults = () => ({ ...(opts.uploadDefaults || {}) });
  const addFiles = fs => uploadDialog(fs, uploadDefaults(), () => { st.offset = 0; load(); });

  // drag and drop from the desktop
  let dragDepth = 0;
  root.addEventListener('dragenter', e => { if (eng && [...e.dataTransfer.types].includes('Files')) { dragDepth++; drop.hidden = false; } });
  root.addEventListener('dragleave', () => { if (dragDepth && --dragDepth === 0) drop.hidden = true; });
  root.addEventListener('dragover', e => { if (eng && [...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
  root.addEventListener('drop', e => {
    dragDepth = 0; drop.hidden = true;
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    if (!eng) { toast(ENG_WHY, 'err'); return; }
    addFiles([...e.dataTransfer.files]);
  });

  const query = (extra = {}) => {
    const q = new URLSearchParams();
    const put_ = (k, v) => { if (v !== '' && v !== undefined && v !== null && !(Array.isArray(v) && !v.length)) q.set(k, Array.isArray(v) ? v.join(',') : v); };
    put_('q', st.q.trim()); for (const k of LIST_KEYS) put_(k, st[k]);
    put_('project', fixed.project || st.project); put_('item', fixed.item || st.item); put_('by', st.by);
    put_('from', st.from); put_('to', st.to); put_('sort', st.sort); put_('dir', st.dir);
    if (st.deleted) q.set('deleted', '1');
    if (st.offset) q.set('offset', st.offset);
    for (const [k, v] of Object.entries(extra)) q.set(k, v);
    return q;
  };
  const syncUrl = () => {
    if (!opts.syncUrl) return;
    const q = query(); q.delete('offset');
    history.replaceState(null, '', `#/inputs${q.toString() ? `?${q}` : ''}`);
  };

  async function load(keepFocus = true) {
    const seq = ++st.seq;
    clearTimeout(st.timer);
    syncUrl();
    let data;
    try { data = await get(`/api/inputs?${query({ limit: 60 })}`); } catch (e) { if (st.alive) fail(e); return; }
    if (seq !== st.seq || !st.alive || !root.isConnected) return;
    st.data = data;
    const ids = new Set(data.inputs.map(i => i.id));
    if (!keepFocus || !ids.has(st.focus)) st.focus = null;
    if (st.item && !st.itemLabel) {
      const hit = data.inputs.flatMap(i => i.items).find(it => String(it.id) === String(st.item));
      if (hit) st.itemLabel = hit.part_number;
      else get(`/api/items/${st.item}`).then(r => { st.itemLabel = r.item.part_number; drawFilters(); }).catch(() => {});
    }
    drawAll();
    if (data.inputs.some(i => i.text_status === 'pending')) st.timer = setTimeout(() => { if (st.alive && root.isConnected) load(); }, 3500);
  }

  function drawAll() { drawBar(); drawFilters(); drawResults(); drawBulk(); drawPreview(); }

  function drawBar() {
    clear(dirBtn); clear(viewBtn);
    const desc = (st.dir || ((st.sort === 'title' || st.sort === 'kind') ? 'asc' : 'desc')) === 'desc';
    dirBtn.title = desc ? 'Newest or highest first (click to reverse)' : 'Oldest or A to Z first (click to reverse)';
    dirBtn.appendChild(icon(desc ? 'chevD' : 'up', 16));
    viewBtn.title = st.view === 'grid' ? 'Show as a list' : 'Show as tiles';
    viewBtn.setAttribute('aria-label', viewBtn.title);
    viewBtn.appendChild(icon(st.view === 'grid' ? 'list' : 'grid', 16));
    filterBtn.setAttribute('aria-pressed', String(st.showFilters));
    body.classList.toggle('no-filters', !st.showFilters);
    body.classList.toggle('no-preview', !!opts.compact);
  }

  function facetBox(title, key, entries, multi = true) {
    if (!entries.length) return null;
    const box = h('div', { class: 'inp-facet' }, h('h3', null, title));
    for (const e of entries) {
      const on = multi ? st[key].includes(String(e.key)) : String(st[key]) === String(e.key);
      const cb = h('input', { type: 'checkbox', checked: on });
      cb.addEventListener('change', () => {
        if (multi) st[key] = cb.checked ? [...st[key], String(e.key)] : st[key].filter(x => x !== String(e.key));
        else st[key] = cb.checked ? String(e.key) : '';
        st.offset = 0; load();
      });
      box.appendChild(h('label', { class: e.n || on ? '' : 'zero' }, cb, h('span', { class: 'ellipsis' }, e.label), h('span', { class: 'n' }, e.n)));
    }
    return box;
  }

  function drawFilters() {
    clear(filters);
    const f = st.data && st.data.facets;
    if (!f) return;
    const tagSearch = tagPicker('if-tag', st.tag.map(t => f.tag.find(x => x.key === t)?.label || t),
      chosen => { st.tag = chosen.map(c => c.label.toLowerCase()); st.offset = 0; load(); });
    const tagBox = h('div', { class: 'inp-facet' }, h('h3', null, 'Tags'),
      h('div', { class: 'inp-tags' }, f.tag.slice(0, 24).map(t => {
        const on = st.tag.includes(t.key);
        return h('button', { class: 'inp-tag', 'aria-pressed': String(on), onclick: () => { st.tag = on ? st.tag.filter(x => x !== t.key) : [...st.tag, t.key]; st.offset = 0; load(); } },
          t.label, h('span', { class: 'n' }, t.n));
      })),
      f.tag.length > 24 || st.tag.some(t => !f.tag.slice(0, 24).find(x => x.key === t)) ? tagSearch.el : null);
    const projSel = h('select', { class: 'input', 'aria-label': 'Project' }, h('option', { value: '' }, 'All projects'),
      f.project.map(x => h('option', { value: x.key, selected: String(st.project) === String(x.key) }, `${x.label} — ${x.name} (${x.n})`)));
    if (st.project && !f.project.some(x => String(x.key) === String(st.project))) projSel.appendChild(h('option', { value: st.project, selected: true }, `Project ${st.project}`));
    projSel.addEventListener('change', () => { st.project = projSel.value; st.offset = 0; load(); });
    const bySel = h('select', { class: 'input', 'aria-label': 'Added by' }, h('option', { value: '' }, 'Anyone'),
      f.by.map(x => h('option', { value: x.key, selected: String(st.by) === String(x.key) }, `${x.label} (${x.n})`)));
    bySel.addEventListener('change', () => { st.by = bySel.value; st.offset = 0; load(); });
    const from = h('input', { class: 'input', type: 'date', value: st.from, 'aria-label': 'From date' });
    const to = h('input', { class: 'input', type: 'date', value: st.to, 'aria-label': 'To date' });
    from.addEventListener('change', () => { st.from = from.value; st.offset = 0; load(); });
    to.addEventListener('change', () => { st.to = to.value; st.offset = 0; load(); });
    const item = itemPicker('if-item', st.item ? [{ id: st.item, part_number: st.itemLabel || `Part ${st.item}`, description: '' }] : [],
      chosen => { const last = chosen[chosen.length - 1]; st.item = last ? String(last.value) : ''; st.itemLabel = last ? last.label : ''; st.offset = 0; load(); });
    const deletedBox = h('input', { type: 'checkbox', checked: st.deleted });
    deletedBox.addEventListener('change', () => { st.deleted = deletedBox.checked; st.sel.clear(); st.offset = 0; load(); });
    const anyFilter = st.kind.length || st.type.length || st.tag.length || st.project || st.item || st.by || st.from || st.to || st.deleted;
    append(filters, [
      anyFilter ? h('button', { class: 'btn sm', onclick: () => { Object.assign(st, { kind: [], type: [], tag: [], project: '', item: '', itemLabel: '', by: '', from: '', to: '', deleted: false, offset: 0 }); load(); } }, icon('x', 14), 'Clear filters') : null,
      facetBox('Kind', 'kind', f.kind),
      facetBox('File type', 'type', f.type),
      tagBox,
      fixed.project ? null : h('div', { class: 'inp-facet' }, h('h3', null, 'Project'), projSel),
      fixed.item ? null : h('div', { class: 'inp-facet' }, h('h3', null, 'Part'), item.el),
      h('div', { class: 'inp-facet' }, h('h3', null, 'Date'), h('div', { class: 'col', style: { gap: '4px' } }, from, to)),
      h('div', { class: 'inp-facet' }, h('h3', null, 'Added by'), bySel),
      eng ? h('div', { class: 'inp-facet' }, h('label', { title: 'Deleted inputs you added (approvers see all of them); they can be restored.' }, deletedBox, 'Show deleted inputs')) : null]);
  }

  const toggleSel = (id, on) => { if (on) st.sel.add(id); else st.sel.delete(id); drawBulk(); markSel(); };
  const markSel = () => {
    for (const el of results.querySelectorAll('[data-id]')) {
      const id = Number(el.dataset.id);
      el.classList.toggle('sel', st.sel.has(id)); el.classList.toggle('focus', st.focus === id);
      const cb = el.querySelector('input[type=checkbox]'); if (cb) cb.checked = st.sel.has(id);
    }
  };
  const focusOn = inp => {
    if (opts.compact || matchMedia('(max-width: 1250px)').matches) { location.hash = `#/input/${inp.id}`; return; }
    st.focus = inp.id; markSel(); drawPreview();
  };

  function drawResults() {
    clear(results);
    const d = st.data;
    if (!d) return;
    const first = d.total ? d.offset + 1 : 0;
    const last = d.offset + d.inputs.length;
    const allSel = d.inputs.length && d.inputs.every(i => st.sel.has(i.id));
    const selAll = h('input', { type: 'checkbox', checked: allSel, 'aria-label': 'Select all on this page', disabled: !eng, title: eng ? 'Select all on this page' : ENG_WHY });
    selAll.addEventListener('change', () => { for (const i of d.inputs) { if (selAll.checked) st.sel.add(i.id); else st.sel.delete(i.id); } drawBulk(); markSel(); });
    results.appendChild(h('div', { class: 'inp-count' }, h('label', { class: 'check small' }, selAll, 'All'),
      h('span', null, d.total ? `${first}–${last} of ${plural(d.total, 'input')}` : ''),
      d.terms.length ? h('span', null, 'for ', d.terms.map((t, k) => [k ? ' + ' : '', h('b', null, t)])) : null,
      st.deleted ? h('span', { class: 'chip st-failed' }, 'Deleted inputs') : null));
    if (!d.inputs.length) {
      const filtered = st.q || st.kind.length || st.type.length || st.tag.length || st.project || st.item || st.by || st.from || st.to;
      results.appendChild(h('div', { class: 'inp-empty' }, icon('layers', 34),
        h('b', null, filtered ? 'Nothing matches.' : st.deleted ? 'No deleted inputs.' : 'No inputs yet.'),
        h('span', null, filtered ? 'Try fewer words or clear some filters. Search also finds words inside PDFs, Office files and photos once their text has been read.'
          : 'Drop benchmarks, requirements, datasheets, photos or any other files here, or add a web link or a note.'),
        !filtered && eng ? h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => pickFiles(fs => addFiles(fs)) }, icon('upload', 16), 'Add files'),
          h('button', { class: 'btn', onclick: () => linkOrNoteDialog(uploadDefaults(), () => load()) }, icon('link', 16), 'Add link or note')) : null));
      return;
    }
    const pick = inp => {
      const cb = h('input', { type: 'checkbox', checked: st.sel.has(inp.id), 'aria-label': `Select ${inp.title}`, disabled: !eng, title: eng ? 'Select for tagging, linking or deleting' : ENG_WHY });
      cb.addEventListener('click', e => e.stopPropagation());
      cb.addEventListener('change', () => toggleSel(inp.id, cb.checked));
      return cb;
    };
    if (st.view === 'grid') {
      results.appendChild(h('div', { class: 'inp-grid' }, d.inputs.map(inp => {
        const card = h('div', { class: 'inp-card', 'data-id': inp.id, tabindex: '0', role: 'button', 'aria-label': inp.title },
          h('span', { class: 'pick' }, pick(inp)),
          h('div', { class: 'inp-thumb' }, thumbBox(inp)),
          h('div', { class: 'b' },
            h('div', { class: 't', title: inp.title }, hl(inp.title_hl, inp.title)),
            h('div', { class: 'inp-meta' }, kindChip(inp), inp.date ? h('span', null, fmtDate(inp.date, false)) : null, lockChip(inp), pendingChip(inp),
              inp.file_count > 1 ? h('span', null, `${inp.file_count} files`) : null),
            inp.source ? h('div', { class: 'small muted ellipsis', title: inp.source }, inp.source) : null,
            snippetEl(inp),
            inp.tags.length ? h('div', { class: 'inp-tags' }, inp.tags.slice(0, 6).map(t => h('span', { class: 'inp-tag' }, t))) : null,
            inp.items.length || inp.projects.length ? linkChips(inp) : null));
        card.addEventListener('click', () => focusOn(inp));
        card.addEventListener('dblclick', () => { location.hash = `#/input/${inp.id}`; });
        card.addEventListener('keydown', e => { if (e.key === 'Enter') location.hash = `#/input/${inp.id}`; if (e.key === ' ') { e.preventDefault(); if (eng) toggleSel(inp.id, !st.sel.has(inp.id)); } });
        return card;
      })));
    } else {
      results.appendChild(h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl inp-list' },
        h('thead', null, h('tr', null, ['', '', 'Title', 'Kind', 'Date', 'Source', 'Tags', 'Linked to', 'Added by'].map(t => h('th', null, t)))),
        h('tbody', null, d.inputs.map(inp => {
          const tr = h('tr', { class: 'clickable', 'data-id': inp.id },
            h('td', { style: { width: '28px' } }, pick(inp)),
            h('td', { style: { width: '54px' } }, inp.thumb ? h('img', { class: 'mini', src: fileHref(inp.id, inp.thumb, 'thumb'), alt: '', loading: 'lazy' }) : h('span', { class: 'mini' }, icon(inp.url && !inp.file_count ? 'link' : inp.file_count ? 'file' : 'edit', 16))),
            h('td', { style: { minWidth: '260px' } }, h('div', { style: { fontWeight: 600 } }, hl(inp.title_hl, inp.title), ' ', lockChip(inp), ' ', pendingChip(inp)),
              h('div', { class: 'small muted' }, inp.file_count ? inp.files.map(f => f.filename).join(', ') : inp.url || 'Note'), snippetEl(inp)),
            h('td', null, kindChip(inp)), h('td', { class: 'nowrap' }, inp.date ? fmtDate(inp.date, false) : ''),
            h('td', { class: 'small' }, inp.source), h('td', null, h('div', { class: 'inp-tags' }, inp.tags.map(t => h('span', { class: 'inp-tag' }, t)))),
            h('td', null, linkChips(inp)), h('td', { class: 'small nowrap' }, inp.created_by_name));
          tr.addEventListener('click', () => focusOn(inp));
          tr.addEventListener('dblclick', () => { location.hash = `#/input/${inp.id}`; });
          return tr;
        })))));
    }
    if (d.total > d.inputs.length) {
      results.appendChild(h('div', { class: 'inp-pager' },
        h('button', { class: 'btn sm', disabled: d.offset <= 0, title: d.offset <= 0 ? 'This is the first page.' : '', onclick: () => { st.offset = Math.max(0, d.offset - d.limit); load(false); } }, icon('back', 14), 'Previous'),
        h('button', { class: 'btn sm', disabled: last >= d.total, title: last >= d.total ? 'This is the last page.' : '', onclick: () => { st.offset = d.offset + d.limit; load(false); } }, 'Next', icon('fwd', 14))));
    }
    markSel();
  }

  function drawBulk() {
    clear(bulkBar);
    const n = st.sel.size;
    bulkBar.hidden = !n;
    if (!n) return;
    const ids = [...st.sel];
    const run = async (action, extra, msg) => {
      const r = await post('/api/inputs/bulk', { ids, action, ...extra });
      toast(`${msg(r.done)}${r.skipped.length ? ` ${plural(r.skipped.length, 'input')} skipped: ${r.skipped[0].reason}` : ''}`, r.done ? 'ok' : 'err');
      if (action === 'delete' || action === 'restore') st.sel.clear();
      changed(); load();
    };
    append(bulkBar, [h('b', null, `${n} selected`),
      h('button', { class: 'btn sm', onclick: () => tagsDialog(ids, run) }, icon('plus', 14), 'Tags…'),
      h('button', { class: 'btn sm', onclick: () => linkDialog(ids, run) }, icon('link', 14), 'Link to parts or projects…'),
      h('button', { class: 'btn sm', onclick: () => kindDialog(run) }, icon('edit', 14), 'Set kind…'),
      st.deleted ? h('button', { class: 'btn sm', onclick: () => run('restore', {}, k => `Restored ${plural(k, 'input')}.`) }, icon('refresh', 14), 'Restore')
        : h('button', { class: 'btn sm danger', onclick: async () => {
          if (await confirmDlg('Delete inputs', `Delete ${plural(n, 'input')}? They move to "Show deleted inputs" and can be restored; no file is lost.`, 'Delete', true)) run('delete', {}, k => `Deleted ${plural(k, 'input')}.`);
        } }, icon('trash', 14), 'Delete'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm ghost', onclick: () => { st.sel.clear(); drawBulk(); markSel(); } }, 'Clear selection')]);
  }

  async function drawPreview() {
    clear(preview);
    if (opts.compact) return;
    if (!st.focus) {
      preview.appendChild(h('div', { class: 'body' }, h('p', { class: 'muted small', style: { margin: 0 } },
        'Click an input to preview it here. Double-click opens it. ', eng ? 'Drag files from your computer anywhere on this page to add them.' : '')));
      return;
    }
    const id = st.focus;
    let d;
    try { d = await get(`/api/inputs/${id}`); } catch (e) { fail(e); return; }
    if (st.focus !== id || !st.alive) return;
    clear(preview);
    const files = d.files.filter(f => !f.removed);
    const firstThumb = files.find(f => f.has_thumb);
    const excerpt = files.find(f => f.excerpt);
    append(preview, [
      h('div', { class: 'pv' }, firstThumb ? h('img', { src: fileHref(d.id, firstThumb.n, 'thumb'), alt: '' }) : thumbBox(d)),
      h('div', { class: 'body' },
        h('h2', null, d.title),
        h('div', { class: 'inp-meta' }, kindChip(d), d.date ? h('span', null, fmtDate(d.date, false)) : null, lockChip(d)),
        d.source ? h('div', { class: 'small' }, h('span', { class: 'muted' }, 'Source: '), d.source) : null,
        d.url ? h('a', { href: d.url, target: '_blank', rel: 'noopener noreferrer', class: 'small', style: { overflowWrap: 'anywhere' } }, icon('external', 14), ' ', d.url) : null,
        d.comment ? h('div', { class: 'small inp-note' }, d.comment) : null,
        d.note ? h('pre', { class: 'inp-excerpt' }, d.note) : null,
        d.tags.length ? h('div', { class: 'inp-tags' }, d.tags.map(t => h('span', { class: 'inp-tag' }, t))) : null,
        d.items.length || d.projects.length ? linkChips(d) : null,
        files.length ? h('div', { class: 'col', style: { gap: '4px' } }, files.map(f => h('div', { class: 'row small' },
          h('span', { class: 'ellipsis', style: { flex: 1 }, title: f.filename }, f.filename),
          h('span', { class: 'muted nowrap' }, fmtSize(f.size)),
          f.inline ? h('a', { class: 'btn sm ghost icon', href: fileHref(d.id, f.n, '', true), target: '_blank', rel: 'noopener', title: 'Open in the browser', 'aria-label': `Open ${f.filename}` }, icon('review', 14)) : null,
          h('a', { class: 'btn sm ghost icon', href: fileHref(d.id, f.n), download: '', title: 'Download', 'aria-label': `Download ${f.filename}` }, icon('download', 14))))) : null,
        excerpt ? h('div', null, h('div', { class: 'small muted' }, `Text read from ${excerpt.filename}`), h('pre', { class: 'inp-excerpt' }, excerpt.excerpt)) : null,
        files.some(f => f.status === 'pending' || f.status === 'running') ? h('div', { class: 'small muted' }, 'Kosha is still reading the text of these files for search.') : null,
        h('div', { class: 'small muted' }, `Added by ${d.created_by_name} ${ago(d.created_at)}`),
        h('div', { class: 'row wrap' }, h('a', { class: 'btn primary', href: `#/input/${d.id}` }, 'Open'),
          files.length === 1 ? h('a', { class: 'btn', href: fileHref(d.id, files[0].n), download: '' }, icon('download', 16), 'Download') : null))]);
  }

  // events
  const runSearch = debounce(() => { st.q = search.value; st.offset = 0; load(false); }, 300);
  search.addEventListener('input', runSearch);
  search.addEventListener('keydown', e => { if (e.key === 'Enter') { st.q = search.value; st.offset = 0; load(false); } });
  sortSel.addEventListener('change', () => { st.sort = sortSel.value; st.dir = ''; st.offset = 0; load(); });
  dirBtn.addEventListener('click', () => {
    const desc = (st.dir || ((st.sort === 'title' || st.sort === 'kind') ? 'asc' : 'desc')) === 'desc';
    st.dir = desc ? 'asc' : 'desc'; st.offset = 0; load();
  });
  viewBtn.addEventListener('click', () => { st.view = st.view === 'grid' ? 'list' : 'grid'; store('inputsView', st.view); drawBar(); drawResults(); });
  filterBtn.addEventListener('click', () => { st.showFilters = !st.showFilters; if (!opts.compact) store('inputsFilters', st.showFilters); drawBar(); });
  const onChanged = () => { if (st.alive && root.isConnected) load(); };
  window.addEventListener('kosha:inputs', onChanged);
  load();
  return {
    reload: () => load(),
    addFiles,
    stop: () => { st.alive = false; clearTimeout(st.timer); window.removeEventListener('kosha:inputs', onChanged); },
  };
}

function tagsDialog(ids, run) {
  const tags = tagPicker('bt-tags');
  dialog({ title: `Tags for ${plural(ids.length, 'input')}`, body: [h('div', { class: 'field' }, h('label', { for: 'bt-tags' }, 'Tags'), tags.el),
    h('p', { class: 'small muted', style: { margin: 0 } }, 'Add puts these tags on every selected input; Remove takes them off.')],
  actions: [{ label: 'Cancel' },
    { label: 'Remove tags', onClick: async () => { if (!tags.get().length) { toast('Type at least one tag.', 'err'); return false; } await run('remove_tags', { tags: tags.get().map(t => t.label) }, k => `Removed tags from ${plural(k, 'input')}.`); return undefined; } },
    { label: 'Add tags', primary: true, onClick: async () => { if (!tags.get().length) { toast('Type at least one tag.', 'err'); return false; } await run('add_tags', { tags: tags.get().map(t => t.label) }, k => `Tagged ${plural(k, 'input')}.`); return undefined; } }] });
}

function linkDialog(ids, run) {
  const items = itemPicker('bl-items');
  const projects = projectPicker('bl-projects');
  const body = () => ({ item_ids: items.get().map(i => i.value), project_ids: projects.get().map(p => p.value) });
  const check = b => { if (!b.item_ids.length && !b.project_ids.length) { toast('Choose at least one part or project.', 'err'); return false; } return true; };
  dialog({ title: `Link ${plural(ids.length, 'input')}`, body: [
    h('div', { class: 'field' }, h('label', { for: 'bl-items' }, 'Parts'), items.el),
    h('div', { class: 'field' }, h('label', { for: 'bl-projects' }, 'Projects'), projects.el)],
  actions: [{ label: 'Cancel' },
    { label: 'Unlink', onClick: async () => { const b = body(); if (!check(b)) return false; await run('unlink', b, k => `Unlinked ${plural(k, 'input')}.`); return undefined; } },
    { label: 'Link', primary: true, onClick: async () => { const b = body(); if (!check(b)) return false; await run('link', b, k => `Linked ${plural(k, 'input')}.`); return undefined; } }] });
}

async function kindDialog(run) {
  const cfg = await inputSettings();
  const kind = kindSelect(cfg, 'other'); kind.id = 'bk-kind';
  dialog({ title: 'Set the kind', body: h('div', { class: 'field' }, h('label', { for: 'bk-kind' }, 'Kind'), kind),
    actions: [{ label: 'Cancel' }, { label: 'Set kind', primary: true, onClick: () => run('set_kind', { kind: kind.value }, k => `Changed ${plural(k, 'input')}.`) }] });
}

// ------------------------------------------------------------------ pages
export async function renderInputs(container, _m, params, { setTitle }) {
  setTitle('Inputs');
  await ensureCss();
  const root = h('div');
  const admin = S.user.role === 'admin';
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' },
      h('div', { style: { flex: 1, minWidth: '260px' } }, h('h1', null, 'Inputs'),
        h('div', { class: 'sub' }, 'Reference material for parts and projects: competitor benchmarks, customer requirements, datasheets, test reports, photos and notes.')),
      h('div', { class: 'row wrap' },
        can('engineer') ? h('button', { class: 'btn primary', onclick: () => pickFiles(fs => lib.addFiles(fs)) }, icon('upload', 16), 'Add files') : disabledBtn('Add files', 'upload', ENG_WHY),
        can('engineer') ? h('button', { class: 'btn', onclick: () => linkOrNoteDialog({}, () => lib.reload()) }, icon('link', 16), 'Add link or note') : null,
        h('a', { class: 'btn', href: '#/projects' }, icon('kosha', 16), 'Projects'),
        admin ? h('button', { class: 'btn ghost', onclick: () => settingsDialog(() => lib.reload()) }, icon('settings', 16), 'Settings') : null)),
    root));
  const lib = library(root, { params, syncUrl: true });
  return () => lib.stop();
}

export async function renderInput(container, [id], _p, { setTitle }) {
  await ensureCss();
  let timer = null;
  let alive = true;
  let editing = false;
  const load = async () => {
    const d = await get(`/api/inputs/${id}`);
    if (!alive) return;
    setTitle(d.title);
    draw(d);
    clearTimeout(timer);
    if (d.files.some(f => f.status === 'pending' || f.status === 'running')) timer = setTimeout(() => { if (alive && container.isConnected) load().catch(() => {}); }, 3000);
  };
  const cfg = await inputSettings();
  const draw = d => {
    const A = d.actions;
    const files = d.files.filter(f => !f.removed);
    const removed = d.files.filter(f => f.removed);
    const firstThumb = files.find(f => f.has_thumb);
    const act = async (fn, msg) => { try { await fn(); if (msg) toast(msg, 'ok'); changed(); await load(); } catch (e) { fail(e); } };
    const head = h('div', { class: 'page-head' },
      h('div', { class: 'inp-head-thumb' }, firstThumb ? h('img', { src: fileHref(d.id, firstThumb.n, 'thumb'), alt: '' }) : thumbBox(d)),
      h('div', { style: { flex: 1, minWidth: '280px' } },
        h('div', { class: 'small muted' }, h('a', { href: '#/inputs' }, 'Inputs'), ' › ', d.kind_label),
        h('h1', { style: { margin: '4px 0', overflowWrap: 'anywhere' } }, d.title),
        h('div', { class: 'inp-meta' }, kindChip(d), d.date ? h('span', null, fmtDate(d.date, false)) : null, lockChip(d),
          h('span', null, `Added by ${d.created_by_name} ${ago(d.created_at)}`)),
        d.deleted ? h('div', { class: 'banner err small', style: { marginTop: '10px' } }, icon('trash'), h('div', null, 'This input is deleted. Nothing was lost: restore it to show it in the library again.')) : null),
      h('div', { class: 'row wrap', style: { alignSelf: 'flex-start' } },
        files.length === 1 ? h('a', { class: 'btn', href: fileHref(d.id, files[0].n), download: '' }, icon('download', 16), 'Download') : null,
        A.edit.ok ? h('button', { class: 'btn', onclick: () => { editing = true; draw(d); } }, icon('edit', 16), 'Edit') : (S.mode === 'guided' ? disabledBtn('Edit', 'edit', A.edit.why) : null),
        A.restore.ok ? h('button', { class: 'btn primary', onclick: () => act(() => post(`/api/inputs/${d.id}/restore`), 'Restored') }, icon('refresh', 16), 'Restore')
          : A.delete.ok ? h('button', { class: 'btn danger', onclick: async () => {
            if (await confirmDlg('Delete input', `Delete "${d.title}"? It can be restored later; no file is lost.`, 'Delete', true)) act(() => del(`/api/inputs/${d.id}`), 'Deleted');
          } }, icon('trash', 16), 'Delete') : (S.mode === 'guided' && !d.deleted ? disabledBtn('Delete', 'trash', A.delete.why) : null)));

    // details
    let details;
    if (editing) {
      const f = {
        title: h('input', { class: 'input', id: 'ie-title', value: d.title }),
        kind: kindSelect(cfg, d.kind), source: h('input', { class: 'input', id: 'ie-source', value: d.source }),
        date: h('input', { class: 'input', id: 'ie-date', type: 'date', value: d.date || '' }),
        url: h('input', { class: 'input', id: 'ie-url', type: 'url', value: d.url, placeholder: 'https://…' }),
        note: h('textarea', { class: 'input', id: 'ie-note', style: { minHeight: '140px' } }, d.note),
        comment: h('textarea', { class: 'input', id: 'ie-comment' }, d.comment),
      };
      f.kind.id = 'ie-kind';
      f.note.value = d.note; f.comment.value = d.comment;
      const tags = tagPicker('ie-tags', d.tags);
      const access = A.restrict.ok ? accessEditor('ie', { confidentiality: d.confidentiality, roles: d.access.roles, users: d.access.users }) : null;
      details = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Edit details')),
        h('div', { class: 'panel-b col', style: { gap: '12px' } },
          h('div', { class: 'form-grid' },
            h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'ie-title' }, 'Title'), f.title),
            h('div', { class: 'field' }, h('label', { for: 'ie-kind' }, 'Kind'), f.kind),
            h('div', { class: 'field' }, h('label', { for: 'ie-date' }, 'Date'), f.date),
            h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'ie-source' }, 'Source'), f.source),
            h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'ie-url' }, 'Web link'), f.url),
            h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'ie-note' }, 'Note'), f.note),
            h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'ie-comment' }, 'Comment'), f.comment),
            h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'ie-tags' }, 'Tags'), tags.el)),
          access ? access.el : h('p', { class: 'small muted', style: { margin: 0 } }, A.restrict.why),
          h('div', { class: 'row' },
            h('button', { class: 'btn primary', onclick: async () => {
              const body = { title: f.title.value, kind: f.kind.value, source: f.source.value, date: f.date.value, url: f.url.value.trim(), note: f.note.value, comment: f.comment.value, tags: tags.get().map(t => t.label) };
              if (access) { const a = access.get(); Object.assign(body, { confidentiality: a.confidentiality, access_roles: a.roles, access_users: a.users }); }
              try { await patch(`/api/inputs/${d.id}`, body); editing = false; toast('Saved', 'ok'); changed(); await load(); } catch (e) { fail(e); }
            } }, 'Save'),
            h('button', { class: 'btn', onclick: () => { editing = false; draw(d); } }, 'Cancel'))));
    } else {
      details = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Details')),
        h('div', { class: 'panel-b col', style: { gap: '12px' } },
          h('dl', { class: 'props' }, [
            ['Kind', d.kind_label], ['Date', d.date ? fmtDate(d.date, false) : '—'], ['Source', d.source || '—'],
            ['Who can see it', d.confidentiality === 'restricted'
              ? `Restricted: ${[...d.access.roles.map(r => `all ${r}s`), ...d.access.users.map(u => u.full_name)].join(', ') || 'only the person who added it'} (and admins)` : 'Everyone in Kosha'],
            ['Added', `${d.created_by_name}, ${fmtDate(d.created_at)}`], ['Changed', `${d.updated_by_name || d.created_by_name}, ${fmtDate(d.updated_at)}`],
          ].flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])),
          d.url ? h('div', null, h('a', { href: d.url, target: '_blank', rel: 'noopener noreferrer', style: { overflowWrap: 'anywhere' } }, icon('external', 14), ' ', d.url)) : null,
          d.comment ? h('div', null, h('h3', { style: { marginBottom: '4px' } }, 'Comment'), h('div', { class: 'inp-note' }, d.comment)) : null,
          d.note ? h('div', null, h('h3', { style: { marginBottom: '4px' } }, 'Note'), h('div', { class: 'inp-note' }, d.note)) : null,
          d.tags.length ? h('div', { class: 'inp-tags' }, d.tags.map(t => h('a', { class: 'inp-tag', href: `#/inputs?tag=${encodeURIComponent(t.toLowerCase())}` }, t))) : null));
    }

    // files
    const filesPanel = h('section', { class: 'panel' },
      h('div', { class: 'panel-h' }, h('h2', null, `Files (${files.length})`), h('span', { class: 'spacer' }),
        A.edit.ok ? h('button', { class: 'btn sm', onclick: () => pickFiles(fs => addToInput(d, fs, load)) }, icon('upload', 14), 'Add files') : null),
      files.length || removed.length ? h('table', { class: 'tbl inp-files' }, h('tbody', null, [...files, ...removed].map(f => h('tr', null,
        h('td', { style: { width: '54px' } }, f.has_thumb ? h('img', { class: 'mini', src: fileHref(d.id, f.n, 'thumb'), alt: '', style: { width: '44px', height: '34px', objectFit: 'contain' } }) : icon(TYPE_ICON[f.type] || 'file', 18)),
        h('td', null, h('div', { style: { fontWeight: 600, overflowWrap: 'anywhere', textDecoration: f.removed ? 'line-through' : '' } }, f.filename),
          h('div', { class: 'small muted' }, `${fmtSize(f.size)} · ${f.method || f.file_type || f.ext.toUpperCase()}${f.pages ? ` · ${plural(f.pages, f.type === 'spreadsheet' ? 'sheet' : f.type === 'presentation' ? 'slide' : 'page')}` : ''}${f.chars ? ` · ${f.chars.toLocaleString()} characters of text` : ''}`),
          h('div', { class: 'small' }, h('span', { class: `chip ${f.status === 'done' ? 'st-done' : f.status === 'failed' ? 'st-failed' : f.status === 'pending' || f.status === 'running' ? 'inp-pending' : 'st-draft'}` }, f.removed ? 'Removed' : f.status_label), ' ', f.note ? h('span', { class: 'muted' }, f.note) : null),
          f.duplicates.length ? h('div', { class: 'small muted' }, 'The same file is also in: ', f.duplicates.map((x, k) => [k ? ', ' : '', h('a', { href: `#/input/${x.id}` }, x.title)])) : null),
        h('td', { class: 'nowrap', style: { textAlign: 'right' } }, h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
          f.inline && !f.removed ? h('a', { class: 'btn sm', href: fileHref(d.id, f.n, '', true), target: '_blank', rel: 'noopener' }, 'View') : null,
          h('a', { class: 'btn sm', href: fileHref(d.id, f.n), download: '' }, icon('download', 14), 'Download'),
          f.chars ? h('a', { class: 'btn sm ghost', href: fileHref(d.id, f.n, 'text'), download: '', title: 'The text Kosha read from this file, as a .txt file' }, 'Text') : null,
          A.edit.ok && !f.removed && ['failed', 'empty', 'skipped', 'done'].includes(f.status) ? h('button', { class: 'btn sm ghost', title: 'Read the text of this file again (e.g. after OCR was switched on)', onclick: () => act(() => post(`/api/inputs/${d.id}/files/${f.n}/reread`), 'Reading the text again') }, icon('refresh', 14)) : null,
          A.edit.ok && !f.removed ? h('button', { class: 'btn sm ghost', title: 'Remove this file from the input (it stays stored and can be put back)', 'aria-label': `Remove ${f.filename}`, onclick: async () => {
            if (await confirmDlg('Remove file', `Remove ${f.filename} from this input? The file stays stored in Kosha; the person who added the input or an approver can put it back.`, 'Remove', true)) act(() => del(`/api/inputs/${d.id}/files/${f.n}`), 'File removed');
          } }, icon('trash', 14)) : null,
          f.removed && A.restrict.ok ? h('button', { class: 'btn sm', onclick: () => act(() => post(`/api/inputs/${d.id}/files/${f.n}/restore`), 'File put back') }, 'Put back') : null))))))
        : h('div', { class: 'empty' }, d.url ? 'A web link, no files.' : 'A note, no files.'));
    if (A.edit.ok) {
      filesPanel.addEventListener('dragover', e => { if ([...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
      filesPanel.addEventListener('drop', e => { if (e.dataTransfer.files.length) { e.preventDefault(); addToInput(d, [...e.dataTransfer.files], load); } });
    }

    // links
    const items = A.edit.ok ? itemPicker('il-items') : null;
    const projects = A.edit.ok ? projectPicker('il-projects') : null;
    const unlinkBtn = (body, label) => (A.edit.ok ? h('button', { 'aria-label': `Unlink ${label}`, title: `Unlink ${label}`, onclick: () => act(() => post(`/api/inputs/${d.id}/unlink`, body), 'Unlinked') }, icon('x', 12)) : null);
    const linksPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Linked to')),
      h('div', { class: 'panel-b col', style: { gap: '12px' } },
        h('div', null, h('h3', { style: { marginBottom: '6px' } }, 'Parts'), d.items.length ? h('div', { class: 'inp-tags' }, d.items.map(it => h('span', { class: 'inp-chip' }, h('a', { href: `#/item/${it.id}`, class: 'mono', title: it.description }, it.part_number), unlinkBtn({ item_ids: [it.id] }, it.part_number)))) : h('div', { class: 'small muted' }, 'No parts yet.')),
        h('div', null, h('h3', { style: { marginBottom: '6px' } }, 'Projects'), d.projects.length ? h('div', { class: 'inp-tags' }, d.projects.map(p => h('span', { class: 'inp-chip' }, h('a', { href: `#/project/${p.id}`, title: p.name }, p.code), unlinkBtn({ project_ids: [p.id] }, p.code)))) : h('div', { class: 'small muted' }, 'No projects yet.')),
        A.edit.ok ? h('div', { class: 'col' }, h('label', { class: 'small', for: 'il-items' }, 'Link parts'), items.el, h('label', { class: 'small', for: 'il-projects' }, 'Link projects'), projects.el,
          h('div', null, h('button', { class: 'btn sm primary', onclick: () => {
            const body = { item_ids: items.get().map(i => i.value), project_ids: projects.get().map(p => p.value) };
            if (!body.item_ids.length && !body.project_ids.length) { toast('Choose a part or a project first.', 'err'); return; }
            act(() => post(`/api/inputs/${d.id}/link`, body), 'Linked');
          } }, icon('link', 14), 'Link'))) : null));

    // text read from the files
    const texts = files.filter(f => f.excerpt);
    const textPanel = texts.length ? h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Text found in the files')),
      h('div', { class: 'panel-b col', style: { gap: '12px' } }, texts.map(f => h('div', null, h('div', { class: 'small muted', style: { marginBottom: '4px' } }, f.filename, f.chars > f.excerpt.length ? ` (first ${f.excerpt.length.toLocaleString()} of ${f.chars.toLocaleString()} characters)` : ''),
        h('pre', { class: 'inp-excerpt tall' }, f.excerpt))))) : null;

    mount(container, h('div', { class: 'page-pad' }, head,
      h('div', { class: 'inp-two' }, h('div', { class: 'col', style: { gap: '16px' } }, details, filesPanel, textPanel),
        h('div', { class: 'col', style: { gap: '16px' } }, linksPanel))));
  };
  try {
    await load();
  } catch (e) {
    if (e.status !== 404) throw e;
    setTitle('Input not found');
    mount(container, h('div', { class: 'page-pad' }, h('div', { class: 'inp-empty' }, icon('layers', 34), h('b', null, 'Input not found'),
      h('span', null, 'It does not exist, it was deleted, or it is restricted and you are not on its list. Ask the person who added it if you need it.'),
      h('a', { class: 'btn', href: '#/inputs' }, 'Back to Inputs'))));
  }
  return () => { alive = false; clearTimeout(timer); };
}

async function addToInput(d, fs, reload) {
  const fd = new FormData();
  for (const f of fs) fd.append('file', f);
  try {
    const r = await upload(`/api/inputs/${d.id}/files`, fd);
    toast(`Added ${plural(fs.length - r.errors.length, 'file')}. The text is read in the background.`, 'ok');
    for (const x of r.duplicates_found || []) toast(`${x.filename} is already in "${x.inputs[0].title}" (stored once).`);
    for (const er of r.errors) toast(`${er.filename}: ${er.error}`, 'err');
    changed(); reload();
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ settings (admins)
async function settingsDialog(onSaved) {
  const s = await inputSettings(true);
  const kindsBox = h('div', { class: 'inp-kinds' });
  let kinds = s.kinds.map(k => ({ ...k }));
  const drawKinds = () => {
    clear(kindsBox);
    kinds.forEach((k, i) => {
      const inp = h('input', { class: 'input', value: k.label, 'aria-label': `Kind ${i + 1}` });
      inp.addEventListener('input', () => { k.label = inp.value; });
      kindsBox.appendChild(h('div', { class: 'row' }, inp, k.key === 'other'
        ? h('button', { class: 'btn ghost icon', disabled: true, title: '"Other" is always kept: it is used when no kind fits.' }, icon('trash', 16))
        : h('button', { class: 'btn ghost icon', 'aria-label': `Remove ${k.label}`, title: 'Remove this kind (only possible when no input uses it)', onclick: () => { kinds = kinds.filter(x => x !== k); drawKinds(); } }, icon('trash', 16))));
    });
    kindsBox.appendChild(h('div', null, h('button', { class: 'btn sm', onclick: () => { kinds.splice(kinds.length - 1, 0, { key: '', label: '' }); drawKinds(); } }, icon('plus', 14), 'Add a kind')));
  };
  drawKinds();
  const ocr = h('input', { type: 'checkbox', checked: !!s.ocr });
  const lang = h('input', { class: 'input mono', id: 'is-lang', value: s.ocr_lang });
  const pages = h('input', { class: 'input', id: 'is-pages', type: 'number', min: 0, max: 200, value: s.ocr_max_pages });
  const maxmb = h('input', { class: 'input', id: 'is-mb', type: 'number', min: 1, value: s.max_mb });
  const secs = h('input', { class: 'input', id: 'is-secs', type: 'number', min: 5, value: s.time_limit_s });
  dialog({
    title: 'Inputs library settings', wide: true,
    body: [
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Kinds of input'), kindsBox),
      h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Reading text in images and scanned PDFs (OCR)'),
        h('label', { class: 'check' }, ocr, 'Read text in photos and scanned PDFs'),
        s.ocr_available ? h('span', { class: 'hint' }, 'Tesseract is installed on the server.') : h('div', { class: 'banner small' }, icon('warn'), h('div', null, s.ocr_note))),
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { for: 'is-lang' }, 'OCR language'), lang, h('span', { class: 'hint' }, 'Tesseract codes, e.g. eng or eng+deu')),
        h('div', { class: 'field' }, h('label', { for: 'is-pages' }, 'Scanned pages read per PDF'), pages),
        h('div', { class: 'field' }, h('label', { for: 'is-mb' }, 'Read text of files up to (MB)'), maxmb, h('span', { class: 'hint' }, 'Larger files are stored, but their text is not read.')),
        h('div', { class: 'field' }, h('label', { for: 'is-secs' }, 'Time limit per file (seconds)'), secs)),
      h('div', { class: 'row wrap' },
        h('button', { class: 'btn sm', onclick: async () => { try { const r = await post('/api/inputs/reindex', { reread: 'unread' }); toast(`Search index rebuilt for ${plural(r.inputs, 'input')}; ${plural(r.queued || 0, 'file')} will be read again.`, 'ok'); } catch (e) { fail(e); } } }, icon('refresh', 14), 'Rebuild index and read unread files again'))],
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      await put('/api/inputs/settings', { kinds: kinds.filter(k => k.label.trim()), ocr: ocr.checked, ocr_lang: lang.value.trim(), ocr_max_pages: Number(pages.value), max_mb: Number(maxmb.value), time_limit_s: Number(secs.value) });
      await inputSettings(true);
      toast('Settings saved', 'ok');
      if (onSaved) onSaved();
    } }],
  });
}

// ------------------------------------------------------------------ projects
function projectDialog(p, onSaved) {
  const f = {
    code: h('input', { class: 'input mono', id: 'pj-code', value: p?.code || '', placeholder: 'e.g. PRJ-2026-014' }),
    name: h('input', { class: 'input', id: 'pj-name', value: p?.name || '' }),
    customer: h('input', { class: 'input', id: 'pj-customer', value: p?.customer || '' }),
    start_date: h('input', { class: 'input', id: 'pj-start', type: 'date', value: p?.start_date || today() }),
    due_date: h('input', { class: 'input', id: 'pj-due', type: 'date', value: p?.due_date || '' }),
    description: h('textarea', { class: 'input', id: 'pj-desc' }, p?.description || ''),
  };
  f.description.value = p?.description || '';
  dialog({ title: p ? `Edit ${p.code}` : 'New project', body: h('div', { class: 'form-grid' },
    h('div', { class: 'field' }, h('label', { for: 'pj-code' }, 'Project code'), f.code),
    h('div', { class: 'field' }, h('label', { for: 'pj-name' }, 'Name'), f.name),
    h('div', { class: 'field' }, h('label', { for: 'pj-customer' }, 'Customer'), f.customer),
    h('div', { class: 'field' }, h('label', { for: 'pj-start' }, 'Start date'), f.start_date),
    h('div', { class: 'field' }, h('label', { for: 'pj-due' }, 'Due date'), f.due_date),
    h('div', { class: 'field', style: { gridColumn: '1 / -1' } }, h('label', { for: 'pj-desc' }, 'Description'), f.description)),
  actions: [{ label: 'Cancel' }, { label: p ? 'Save' : 'Create project', primary: true, onClick: async () => {
    const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
    const r = p ? await patch(`/api/projects/${p.id}`, body) : await post('/api/projects', body);
    toast(p ? 'Saved' : `Project ${r.project.code} created`, 'ok');
    changed();
    onSaved(r.project);
  } }] });
}

export async function renderProjects(container, _m, params, { setTitle }) {
  setTitle('Projects');
  await ensureCss();
  let status = params.status ?? 'open';
  let q = params.q || '';
  const list = h('div');
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Code, name or customer', value: q, 'aria-label': 'Search projects', style: { maxWidth: '320px' } });
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Status' });
  const drawSeg = () => {
    clear(seg);
    for (const [v, l] of [['open', 'Open'], ['closed', 'Closed'], ['', 'All']]) seg.appendChild(h('button', { 'aria-pressed': String(status === v), onclick: () => { status = v; drawSeg(); load(); } }, l));
  };
  const load = async () => {
    const r = await get(`/api/projects?status=${status}&q=${encodeURIComponent(q)}`);
    mount(list, r.projects.length ? h('div', { class: 'panel' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Code', 'Name', 'Customer', 'Status', 'Start', 'Due', 'Parts', 'Inputs'].map(t => h('th', null, t)))),
      h('tbody', null, r.projects.map(p => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/project/${p.id}`; } },
        h('td', { class: 'mono' }, p.code), h('td', null, p.name), h('td', null, p.customer), h('td', null, h('span', { class: `chip st-${p.status === 'open' ? 'open' : 'done'}` }, p.status_label)),
        h('td', { class: 'nowrap' }, p.start_date ? fmtDate(p.start_date, false) : ''), h('td', { class: 'nowrap' }, p.due_date ? fmtDate(p.due_date, false) : ''),
        h('td', { class: 'num' }, p.item_count), h('td', { class: 'num' }, p.input_count))))))
      : h('div', { class: 'inp-empty' }, icon('kosha', 30), h('b', null, q ? 'No project matches.' : status === 'closed' ? 'No closed projects.' : 'No projects yet.'),
        h('span', null, 'A project groups the parts and the reference material (inputs) of one customer order or development.')));
  };
  search.addEventListener('input', debounce(() => { q = search.value.trim(); load().catch(fail); }, 250));
  drawSeg();
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('div', { class: 'small muted' }, h('a', { href: '#/inputs' }, 'Inputs'), ' › Projects'), h('h1', null, 'Projects'),
      h('div', { class: 'sub' }, 'Customer orders and developments, with their parts and inputs.')),
    can('engineer') ? h('button', { class: 'btn primary', onclick: () => projectDialog(null, p => { location.hash = `#/project/${p.id}`; }) }, icon('plus', 16), 'New project') : disabledBtn('New project', 'plus', 'Creating projects needs the engineer role.')),
    h('div', { class: 'row wrap', style: { marginBottom: '12px' } }, search, seg), list));
  await load();
}

export async function renderProject(container, [id], _p, { setTitle }) {
  await ensureCss();
  let lib = null;
  const load = async () => {
    const { project: p } = await get(`/api/projects/${id}`);
    setTitle(p.code);
    const eng = can('engineer');
    const item = eng ? itemPicker('pp-item') : null;
    const libRoot = h('div');
    if (lib) lib.stop();
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' },
        h('div', { style: { flex: 1, minWidth: '280px' } }, h('div', { class: 'small muted' }, h('a', { href: '#/projects' }, 'Projects'), ' › ', p.code),
          h('h1', null, h('span', { class: 'mono' }, p.code), ' ', p.name),
          h('div', { class: 'inp-meta', style: { marginTop: '6px' } }, h('span', { class: `chip st-${p.status === 'open' ? 'open' : 'done'}` }, p.status_label),
            p.customer ? h('span', null, `Customer: ${p.customer}`) : null,
            p.start_date ? h('span', null, `Start ${fmtDate(p.start_date, false)}`) : null, p.due_date ? h('span', null, `Due ${fmtDate(p.due_date, false)}`) : null,
            p.closed_at ? h('span', null, `Closed ${fmtDate(p.closed_at, false)}`) : null),
          p.description ? h('p', { class: 'inp-note', style: { margin: '10px 0 0' } }, p.description) : null),
        eng ? h('div', { class: 'row wrap' },
          h('button', { class: 'btn', onclick: () => projectDialog(p, () => load()) }, icon('edit', 16), 'Edit'),
          h('button', { class: 'btn', onclick: async () => {
            try { await patch(`/api/projects/${p.id}`, { status: p.status === 'open' ? 'closed' : 'open' }); toast(p.status === 'open' ? 'Project closed. Its parts and inputs stay as they are.' : 'Project reopened', 'ok'); changed(); load(); } catch (e) { fail(e); }
          } }, icon(p.status === 'open' ? 'check' : 'refresh', 16), p.status === 'open' ? 'Close project' : 'Reopen')) : null),
      h('section', { class: 'panel', style: { marginBottom: '16px' } },
        h('div', { class: 'panel-h' }, h('h2', null, `Parts (${p.items.length})`)),
        h('div', { class: 'panel-b col', style: { gap: '10px' } },
          p.items.length ? h('div', { class: 'inp-tags' }, p.items.map(it => h('span', { class: 'inp-chip' }, h('a', { href: `#/item/${it.id}`, class: 'mono', title: it.description }, it.part_number),
            eng ? h('button', { 'aria-label': `Unlink ${it.part_number}`, title: `Unlink ${it.part_number}`, onclick: async () => { try { await del(`/api/projects/${p.id}/items/${it.id}`); load(); } catch (e) { fail(e); } } }, icon('x', 12)) : null)))
            : h('div', { class: 'small muted' }, 'No parts linked yet.'),
          eng ? h('div', { class: 'row wrap' }, h('div', { style: { flex: '1 1 320px', maxWidth: '520px' } }, item.el), h('button', { class: 'btn sm', onclick: async () => {
            const chosen = item.get();
            if (!chosen.length) { toast('Choose a part first.', 'err'); return; }
            try { for (const c of chosen) await post(`/api/projects/${p.id}/items`, { item_id: c.value }); toast('Linked', 'ok'); load(); } catch (e) { fail(e); }
          } }, icon('link', 14), 'Link parts')) : null)),
      h('section', null, h('h2', { style: { margin: '0 0 10px' } }, `Inputs (${p.input_count})`), libRoot)));
    lib = library(libRoot, { fixed: { project: p.id }, compact: true, uploadDefaults: { projects: [{ id: p.id, code: p.code, name: p.name }] } });
  };
  try {
    await load();
  } catch (e) {
    if (e.status !== 404) throw e;
    setTitle('Project not found');
    mount(container, h('div', { class: 'page-pad' }, h('div', { class: 'inp-empty' }, icon('kosha', 34), h('b', null, 'Project not found'),
      h('a', { class: 'btn', href: '#/projects' }, 'All projects'))));
  }
  return () => { if (lib) lib.stop(); };
}

// ------------------------------------------------------------------ part page section
export async function addItemInputs(container, item) {
  await ensureCss();
  const grid = container.querySelector('.grid-cards');
  if (!grid || grid.querySelector('[data-inputs-panel]')) return;
  const panel = h('section', { class: 'panel', 'data-inputs-panel': '1' });
  grid.appendChild(panel);
  const draw = async () => {
    const r = await get(`/api/items/${item.id}/inputs?limit=12`);
    const eng = can('engineer');
    const defaults = { items: [{ id: item.id, part_number: item.part_number, description: item.description }] };
    mount(panel,
      h('div', { class: 'panel-h' }, h('h2', null, `Inputs (${r.total})`), h('span', { class: 'spacer' }),
        eng ? h('button', { class: 'btn sm', onclick: () => pickFiles(fs => uploadDialog(fs, defaults, () => draw())) }, icon('upload', 14), 'Add files')
          : h('button', { class: 'btn sm', disabled: true, title: ENG_WHY }, icon('upload', 14), 'Add files'),
        h('a', { class: 'btn sm', href: `#/inputs?item=${item.id}` }, icon('search', 14), 'Open in Inputs')),
      r.projects.length ? h('div', { class: 'panel-b small', style: { paddingBottom: 0 } }, 'Projects: ', r.projects.map((p, k) => [k ? ', ' : '', h('a', { href: `#/project/${p.id}`, title: p.name }, p.code)])) : null,
      r.inputs.length ? h('div', null, r.inputs.map(inp => h('a', { class: 'list-row', href: `#/input/${inp.id}` },
        inp.thumb ? h('img', { src: fileHref(inp.id, inp.thumb, 'thumb'), alt: '', loading: 'lazy' }) : h('span', { class: 'mini', style: { width: '40px', display: 'inline-flex', justifyContent: 'center', color: 'var(--muted)' } }, icon(inp.url && !inp.file_count ? 'link' : inp.file_count ? 'file' : 'edit', 18)),
        h('div', { style: { minWidth: 0, flex: 1 } }, h('div', { class: 'ellipsis', style: { fontWeight: 600 } }, inp.title),
          h('div', { class: 'small muted ellipsis' }, inp.kind_label, inp.date ? ` · ${fmtDate(inp.date, false)}` : '', inp.source ? ` · ${inp.source}` : '')),
        lockChip(inp))),
      r.total > r.inputs.length ? h('a', { class: 'list-row small', href: `#/inputs?item=${item.id}` }, `Show all ${r.total} inputs`) : null)
        : h('div', { class: 'empty' }, 'No benchmarks, requirements, datasheets or photos linked to this part yet.'));
  };
  try { await draw(); } catch (e) { fail(e); }
}
