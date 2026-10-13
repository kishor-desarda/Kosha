// SPDX-License-Identifier: AGPL-3.0-or-later
// The workbench (specification 9.1): Ctrl+K to go anywhere, favourites (the star on a record's page), saved views,
// pages pinned to the strip at the bottom, recent pages, and the accent colour and text size of the screen.
// Favourites, views and preferences are saved with the person's account; recent pages stay in this browser.
import { S, h, mount, icon, get, post, put, del, toast, fail, store, stateChip, dialog } from './core.js';

const RECENT_MAX = 12;
let screens = [];          // [label, icon, hash] of the screens this person may open, from main.js
let strip = null;
let currentTitle = '';

export const ACCENTS = [['blue', 'Blue'], ['teal', 'Teal'], ['green', 'Green'], ['purple', 'Purple'], ['orange', 'Orange']];
export const TEXT_SIZES = [[90, 'Smaller'], [100, 'Normal'], [115, 'Larger'], [130, 'Largest']];

let ready = null;            // resolves when the person's workbench has loaded
export function setScreens(list) { screens = list; }

export function loadWorkbench() {
  ready = fetchWorkbench();
  return ready;
}
async function fetchWorkbench() {
  try { S.wb = await get('/api/me/workbench'); } catch (e) { S.wb = { favourites: [], views: [], prefs: { accent: 'blue', text_size: 100, pins: [] } }; }
  applyPrefs(S.wb.prefs);
  drawStrip();
}

export function applyPrefs(p) {
  const root = document.documentElement;
  if (p.accent && p.accent !== 'blue') root.setAttribute('data-accent', p.accent); else root.removeAttribute('data-accent');
  if (p.text_size && p.text_size !== 100) root.setAttribute('data-text', String(p.text_size)); else root.removeAttribute('data-text');
  store('look', { accent: p.accent, text: p.text_size });     // theme-boot.js applies it before the next paint
}

export async function savePrefs(changes) {
  const r = await put('/api/me/prefs', changes);
  S.wb.prefs = r.prefs;
  applyPrefs(r.prefs);
  drawStrip();
  return r.prefs;
}

// ------------------------------------------------------------------ favourites --

const favOf = (kind, id) => (S.wb ? S.wb.favourites : []).find(f => f.kind === kind && f.ref_id === Number(id));

export function favButton(kind, id) {
  const b = h('button', { class: 'fav-btn', type: 'button' }, icon('star', 20));
  const paint = () => {
    const on = !!favOf(kind, id);
    b.setAttribute('aria-pressed', String(on));
    b.setAttribute('aria-label', on ? 'Remove from favourites' : 'Add to favourites');
    b.title = on ? 'In your favourites (Ctrl+K lists them). Click to remove.' : 'Add to your favourites, to find it again with Ctrl+K';
  };
  b.addEventListener('click', async () => {
    b.disabled = true;
    await ready;
    try { if (favOf(kind, id)) await removeFavourite(kind, id); else await addFavourite(kind, id); } catch (e) { fail(e); }
    b.disabled = false;
    paint();
  });
  paint();
  if (ready) ready.then(paint);
  return b;
}

async function addFavourite(kind, id) {
  const r = await post('/api/me/favourites', { kind, ref_id: Number(id) });
  S.wb.favourites = [r.favourite, ...S.wb.favourites.filter(f => !(f.kind === kind && f.ref_id === Number(id)))];
  toast(`${r.favourite.label} is in your favourites. Press Ctrl+K to find it.`, 'ok');
}

async function removeFavourite(kind, id) {
  const f = favOf(kind, id);
  await del(`/api/me/favourites/${kind}/${Number(id)}`);
  S.wb.favourites = S.wb.favourites.filter(x => x !== f);
  toast(`${f ? f.label : 'It'} is no longer a favourite.`, 'ok', { label: 'Undo', run: () => addFavourite(kind, id) });
}

// ------------------------------------------------------------------- recent pages --

const recentKey = () => `recent.${S.user ? S.user.id : 0}`;
export function recordRecent(hash, title) {
  currentTitle = title;
  if (!title || !hash || hash === '#/home') return;
  const list = (store(recentKey()) || []).filter(r => r.hash !== hash);
  list.unshift({ hash, title });
  store(recentKey(), list.slice(0, RECENT_MAX));
  drawStrip();
}

// ------------------------------------------------------------------- pinned pages --

export function pinStrip() {
  strip = h('nav', { class: 'pin-strip', 'aria-label': 'Pinned pages', hidden: true });
  drawStrip();
  return strip;
}

function drawStrip() {
  if (!strip || !S.wb) return;
  const pins = S.wb.prefs.pins || [];
  strip.hidden = !pins.length;
  mount(strip, pins.map(p => h('span', { class: `pin-tab ${p.hash === location.hash ? 'active' : ''}` },
    h('a', { href: p.hash, style: { color: 'inherit', textDecoration: 'none' } }, p.title),
    h('button', { type: 'button', 'aria-label': `Unpin ${p.title}`, title: 'Unpin', onclick: () => unpin(p) }, icon('x', 14)))));
}

export async function pinCurrent() {
  const pins = S.wb.prefs.pins || [];
  if (pins.some(p => p.hash === location.hash)) { toast('This page is pinned already. Its tab is in the strip at the bottom.'); return; }
  if (pins.length >= 8) { toast('You can pin 8 pages. Unpin one in the strip at the bottom first.', 'err'); return; }
  await savePrefs({ pins: [...pins, { hash: location.hash, title: currentTitle || document.title }] });
  toast('Pinned. The page has a tab in the strip at the bottom of the screen.', 'ok');
}

async function unpin(p) {
  const before = S.wb.prefs.pins || [];
  try {
    await savePrefs({ pins: before.filter(x => x.hash !== p.hash) });
    toast(`${p.title} unpinned.`, 'ok', { label: 'Undo', run: () => savePrefs({ pins: before }) });
  } catch (e) { fail(e); }
}

// -------------------------------------------------------------------- saved views --

export function saveViewDialog() {
  const name = h('input', { class: 'input', id: 'view-name', value: currentTitle || '', maxlength: 80 });
  dialog({ title: 'Save this view', body: [
    h('div', { class: 'field' }, h('label', { for: 'view-name' }, 'Name'), name,
      h('span', { class: 'hint' }, 'For example Released pump drawings. The screen is saved with its filters; Ctrl+K lists your views.'))],
  actions: [{ label: 'Cancel' }, { label: 'Save view', primary: true, onClick: async () => {
    const r = await post('/api/me/views', { name: name.value, hash: location.hash });
    S.wb.views = [...S.wb.views.filter(v => v.id !== r.view.id), r.view].sort((a, b) => a.name.localeCompare(b.name));
    toast(`Saved as "${r.view.name}".`, 'ok');
  } }] });
}

async function deleteView(v) {
  await del(`/api/me/views/${v.id}`);
  S.wb.views = S.wb.views.filter(x => x.id !== v.id);
  toast(`View "${v.name}" deleted.`, 'ok', { label: 'Undo', run: async () => {
    const r = await post('/api/me/views', { name: v.name, hash: v.hash });
    S.wb.views = [...S.wb.views, r.view].sort((a, b) => a.name.localeCompare(b.name));
  } });
}

// ------------------------------------------------------------------------ Ctrl+K --

let open = null;

export function openPalette() {
  if (open) { open.q.focus(); return; }
  const q = h('input', { class: 'palette-q', type: 'search', placeholder: 'Go to a screen, part, file, change, favourite or view…', 'aria-label': 'Search everything', autocomplete: 'off', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'palette-list' });
  const list = h('div', { class: 'palette-list', id: 'palette-list', role: 'listbox' });
  const d = h('dialog', { class: 'dlg palette', 'aria-label': 'Go to' }, q, list,
    h('div', { class: 'palette-hint' }, '↑ ↓ to choose · Enter to open · Esc to close · Ctrl+K anywhere'));
  let entries = [];
  let sel = 0;
  let seq = 0;
  let timer = null;
  const close = () => { d.close(); d.remove(); open = null; clearTimeout(timer); };
  const go = e => { if (!e) return; close(); if (e.hash) location.hash = e.hash; else if (e.run) Promise.resolve(e.run()).catch(fail); };

  const draw = () => {
    const nodes = [];
    let last = '';
    entries.forEach((e, i) => {
      if (e.group !== last) { nodes.push(h('div', { class: 'palette-group', role: 'presentation' }, e.group)); last = e.group; }
      const row = h('div', { class: 'palette-item', role: 'option', id: `pal-${i}`, 'aria-selected': String(i === sel), onclick: () => go(e),
        onmousemove: () => { if (sel !== i) { sel = i; mark(); } } },
      icon(e.icon || 'fwd', 16), h('span', null, e.label), e.sub ? h('span', { class: 'sub' }, e.sub) : null,
      e.state ? stateChip(e.state) : null, h('span', { class: 'spacer' }),
      e.remove ? h('button', { class: 'fav-btn', type: 'button', 'aria-label': `Delete ${e.label}`, title: 'Delete this view', onclick: ev => { ev.stopPropagation(); e.remove().then(() => { entries.splice(i, 1); draw(); }).catch(fail); } }, icon('trash', 14)) : null);
      nodes.push(row);
    });
    if (!entries.length) nodes.push(h('div', { class: 'palette-item', style: { cursor: 'default' } }, h('span', { class: 'sub' }, 'Nothing found. Try a part number, a file name or a few words of a description.')));
    mount(list, nodes);
    mark();
  };
  const mark = () => {
    list.querySelectorAll('.palette-item[role=option]').forEach((el, i) => el.setAttribute('aria-selected', String(i === sel)));
    const cur = list.querySelector(`#pal-${sel}`);
    if (cur) { cur.scrollIntoView({ block: 'nearest' }); q.setAttribute('aria-activedescendant', cur.id); }
  };

  const local = text => {
    const t = text.toLowerCase();
    const hit = (...xs) => !t || xs.some(x => (x || '').toLowerCase().includes(t));
    const out = [];
    if (!t) {
      out.push({ group: 'This page', label: 'Pin this page', sub: 'A tab in the strip at the bottom', icon: 'pin', run: pinCurrent });
      out.push({ group: 'This page', label: 'Save this view', sub: 'The screen with its filters, under a name', icon: 'star', run: saveViewDialog });
    }
    for (const f of S.wb.favourites) if (hit(f.label, f.sub)) out.push({ group: 'Favourites', label: f.label, sub: f.sub, state: f.state, icon: 'star', hash: f.hash });
    for (const v of S.wb.views) if (hit(v.name)) out.push({ group: 'Saved views', label: v.name, icon: 'list', hash: v.hash, remove: () => deleteView(v) });
    for (const p of S.wb.prefs.pins || []) if (hit(p.title)) out.push({ group: 'Pinned', label: p.title, icon: 'pin', hash: p.hash });
    for (const r of (store(recentKey()) || []).slice(0, t ? RECENT_MAX : 6)) if (hit(r.title)) out.push({ group: 'Recent', label: r.title, icon: 'refresh', hash: r.hash });
    for (const [label, ic, hash] of screens) if (hit(label)) out.push({ group: 'Screens', label, icon: ic, hash });
    return out;
  };

  const update = () => {
    const text = q.value.trim();
    entries = local(text);
    sel = 0;
    draw();
    clearTimeout(timer);
    if (text.length < 2) return;
    const mine = ++seq;
    timer = setTimeout(async () => {
      try {
        const r = await get(`/api/search?q=${encodeURIComponent(text)}&limit=8`);
        if (mine !== seq || !open) return;
        const found = [
          ...r.items.slice(0, 8).map(i => ({ group: 'Parts', label: i.part_number, sub: i.description, state: i.state, icon: 'cube', hash: `#/item/${i.id}` })),
          ...r.documents.slice(0, 8).map(x => ({ group: 'Files', label: x.filename, sub: [x.part_number, x.revision && `Rev ${x.revision}`].filter(Boolean).join(' '), state: x.state, icon: 'file', hash: `#/doc/${x.id}` })),
          ...(r.changes || []).slice(0, 5).map(c => ({ group: 'Changes', label: c.number, sub: c.title, icon: 'change', hash: `#/change/${c.id}` })),
          { group: 'Search', label: `Search for "${text}"`, sub: 'Every match, with filters', icon: 'search', hash: `#/search?q=${encodeURIComponent(text)}` },
        ];
        entries = [...local(text), ...found];
        draw();
      } catch (e) { /* the local entries still work; search errors show on the search page */ }
    }, 180);
  };

  q.addEventListener('input', update);
  q.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(entries.length - 1, sel + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); go(entries[sel]); }
  });
  d.addEventListener('cancel', e => { e.preventDefault(); close(); });
  d.addEventListener('click', e => { if (e.target === d) close(); });     // a click on the backdrop
  document.body.appendChild(d);
  d.showModal();
  open = { q };
  update();
  q.focus();
}

// --------------------------------------------------------- accent and text size --

export function lookSection() {
  const p = (S.wb && S.wb.prefs) || { accent: 'blue', text_size: 100 };
  const save = async (changes, undo) => {
    try { await savePrefs(changes); } catch (e) { fail(e); applyPrefs(undo); }
  };
  const accents = ACCENTS.map(([key, label]) => {
    const input = h('input', { type: 'radio', name: 'accent', value: key, checked: key === (p.accent || 'blue') });
    input.addEventListener('change', () => { if (input.checked) save({ accent: key }, p); });
    return h('label', { class: 'check' }, input, label);
  });
  const sizes = TEXT_SIZES.map(([size, label]) => {
    const input = h('input', { type: 'radio', name: 'text-size', value: String(size), checked: size === (p.text_size || 100) });
    input.addEventListener('change', () => { if (input.checked) save({ text_size: size }, p); });
    return h('label', { class: 'check' }, input, label);
  });
  return h('div', { class: 'col', style: { gap: '12px' } },
    h('div', null, h('h3', { style: { margin: '0 0 6px' } }, 'Accent colour'),
      h('div', { class: 'row', role: 'radiogroup', 'aria-label': 'Accent colour', style: { flexWrap: 'wrap', gap: '14px' } }, accents),
      h('small', { class: 'muted' }, 'Buttons, tabs and selections. High contrast keeps its own colours.')),
    h('div', null, h('h3', { style: { margin: '0 0 6px' } }, 'Text size'),
      h('div', { class: 'row', role: 'radiogroup', 'aria-label': 'Text size', style: { flexWrap: 'wrap', gap: '14px' } }, sizes)));
}
