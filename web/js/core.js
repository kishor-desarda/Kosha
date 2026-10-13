// SPDX-License-Identifier: AGPL-3.0-or-later
// Kosha — shared client helpers (no build step, plain ES modules)
import { fmtDay, fmtWhen } from './locale.js';

export const S = {
  token: localStorage.getItem('kosha.token') || '',
  user: null,
  erp: 'ERP',          // display name of the ERP the connector add-in talks to
  mode: 'guided',
  defaultTheme: 'system',   // the installation's theme for people who have not chosen one
  settings: {},
};

// ------------------------------------------------------------------ DOM
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value' || k === 'checked' || k === 'selected' || k === 'disabled' || k === 'indeterminate') el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...children) { clear(el); return append(el, children); }

// ------------------------------------------------------------------ icons
const P = {
  star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  pin: '<path d="M9 3h6l-1 6 4 4H6l4-4z"/><path d="M12 13v8"/>',
  command: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10l3 2-3 2M12 15h5"/>',
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
  kosha: '<path d="M3 6h6l2 2h10v11H3z"/>',
  folder: '<path d="M3 6h6l2 2h10v11H3z"/>',
  folderOpen: '<path d="M3 6h6l2 2h9v3"/><path d="M3 19l3-8h16l-3 8z"/>',
  bom: '<rect x="3" y="3" width="7" height="5"/><path d="M6.5 8v10h4.5M6.5 13h4.5"/><rect x="11" y="11" width="10" height="4"/><rect x="11" y="16" width="10" height="4"/>',
  change: '<path d="M4 7h13l-3-3M20 17H7l3 3"/>',
  erp: '<path d="M20 12a8 8 0 0 1-14 5M4 12a8 8 0 0 1 14-5"/><path d="M18 3v4h-4M6 21v-4h4"/>',
  common: '<rect x="3" y="3" width="12" height="12"/><rect x="9" y="9" width="12" height="12"/>',
  cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
  admin: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20h4"/>',
  checkin: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
  checkout: '<rect x="5" y="11" width="14" height="10" rx="1"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  unlock: '<rect x="5" y="11" width="14" height="10" rx="1"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 16v4h16v-4"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
  revise: '<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 8v8M18 10c0 4-6 4-10 7"/>',
  release: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
  review: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  fwd: '<path d="M9 6l6 6-6 6"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  chevR: '<path d="M9 6l6 6-6 6"/>',
  chevD: '<path d="M6 9l6 6 6-6"/>',
  link: '<path d="M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  folderPlus: '<path d="M3 6h6l2 2h10v11H3z"/><path d="M12 11v5M9.5 13.5h5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  edit: '<path d="M4 20l4-1 11-11-3-3L5 16z"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M15 4v16"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  measure: '<path d="M3 17L17 3l4 4L7 21z"/><path d="M7 13l2 2M10 10l2 2M13 7l2 2"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  zoomIn: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6M11 8v6"/>',
  zoomOut: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="1"/><path d="M4 16V4h12"/>',
  move: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3 2.7-5 6-5s6 2 6 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 15.2c2.7.3 5.5 1.8 5.5 4.8"/>',
  logout: '<path d="M9 21H4V3h5M16 17l5-5-5-5M21 12H9"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-3-6.2"/><path d="M20 4v5h-5"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7M12 17v.5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  theme: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  qr: '<rect x="4" y="4" width="6" height="6"/><rect x="14" y="4" width="6" height="6"/><rect x="4" y="14" width="6" height="6"/><path d="M14 14h2v2h-2zM18 18h2v2h-2zM14 18h2M18 14h2"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
};

export function icon(name, size = 18, cls = '') {
  const s = document.createElement('span');
  s.className = 'icon-file ' + cls;
  s.setAttribute('aria-hidden', 'true');
  s.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${P[name] || P.file}</svg>`;
  return s;
}

// The Kosha emblem (docs/brand/kosha-emblem.svg); its colour comes from --brand in app.css.
const EMBLEM = 'M38,208.9 C22.9,205.9 8.7,193.2 2.7,177.2 C0.5,171.5 0.5,170.4 0.2,108.3 C-0.1,38.3 -0,37.6 6.2,25.6 C10.2,17.9 19.1,9 26.2,5.5 C37.2,0.1 39,0 85.2,0 L128,0 L128,9.4 L128,18.9 L85.2,19.2 L42.5,19.5 L36.9,22.1 C30.1,25.3 25,30.1 21.8,36.6 L19.5,41.5 L19.5,105.5 L19.5,169.5 L21.7,173.7 C27.9,185.3 36,190 50.9,190.7 L60.7,191.2 L62.7,195.3 C63.8,197.6 66.8,201.8 69.4,204.7 L74.1,210 L58.3,209.8 C49.6,209.8 40.4,209.3 38,208.9 Z M97.7,208 C85.5,204.1 77.1,196.7 71.6,185.1 C68.6,178.8 68.5,177.7 68,163.5 C67.7,155.2 67.7,133.8 68,116 C68.4,86.6 68.6,83.1 70.3,80 C74,73.2 78.1,69 83.7,66.3 L89.5,63.5 L115.2,63.1 L141,62.8 L141.1,60.1 C141.2,58.7 141.4,52.1 141.4,45.5 L141.5,33.5 L156,33.5 C167.4,33.5 171.6,33.8 175.7,35.3 C182.7,37.7 191,45.3 194.1,52.2 L196.5,57.5 L196.5,98.5 L196.5,139.5 L193.8,145.1 C190.4,152.3 184.3,158.4 177.1,161.8 L171.5,164.5 L136.5,165.1 C105,165.7 90.8,166.4 87.2,167.5 C86.3,167.8 86.3,168.9 87.4,172.5 C89.6,179.8 93.4,184.6 99.2,187.7 L104.5,190.5 L146.5,190.5 C188.4,190.5 188.5,190.4 193.7,188.1 C200.2,185.2 207,178 209.5,171.4 C211.3,166.7 211.5,163.2 211.5,104.5 L211.5,42.5 L208.7,36.8 C205.5,30.3 199.7,24.7 193.1,21.6 C188.9,19.6 186.4,19.4 165.3,19.1 L142.1,18.8 L141.5,14 C141.1,11.3 141.1,7.1 141.5,4.5 L142.2,-0.1 L167.8,0.1 L193.5,0.5 L201.2,4.2 C212.2,9.4 219.9,16.9 225.3,27.8 L229.5,36.3 L229.7,102.2 C230,175.2 230.1,175.1 223.7,185.8 C216.9,197.1 204.9,206.1 193,208.9 C190.2,209.5 171.8,209.9 146,209.9 C107.6,209.8 102.9,209.7 97.7,208 Z M140.5,144.1 C142.1,143.2 144.5,140.4 145.7,138 C147.8,133.8 148,132.4 148,111.8 C148,97.1 147.6,89.2 146.8,87.4 C144.5,82.5 141.9,82 118.8,82 C93.9,82 92,82.5 88.2,89.9 C86,94.2 86,95.4 86,120.6 L86,146.7 L111.7,146.2 C133.2,145.8 137.9,145.5 140.5,144.1 Z M165.2,144.9 C169.9,143.6 174.8,139.4 176.6,135.3 C177.6,132.7 177.9,124.7 177.9,99.2 C177.9,69.4 177.7,66.1 176,62.4 C173.9,57.6 169.4,54.1 164.5,53.3 L161,52.7 L161,61.7 C161,69.2 161.4,71.6 163.4,76.1 C165.9,81.3 165.9,82 165.9,105.2 C166,130.8 165.3,135.1 160.3,141.6 C159,143.4 158,145.1 158,145.4 C158,146.3 161.1,146 165.2,144.9 Z M49.5,167.8 C43.7,166.5 39.7,163.6 36.9,158.7 L34.5,154.5 L34.2,111.5 C34,84.3 34.3,66.8 34.9,63.7 C37.5,51.8 45,42.1 55.3,37.4 C63.7,33.6 71.4,32.9 100.5,33.2 L127.5,33.5 L127.7,42.6 C128,51.3 127.9,51.7 125.7,52.3 C124.5,52.6 111.2,52.9 96.2,52.9 C65.4,53 64.7,53.1 57.7,60.8 C52.4,66.7 52,70.4 52,110 L52,145 L54.5,147.5 C56.8,149.9 57,150.7 57,159.5 C57,170 57.3,169.6 49.5,167.8 Z';
export function logo(size = 30) {
  const s = document.createElement('span');
  s.className = 'brand-mark';
  s.setAttribute('aria-hidden', 'true');
  s.innerHTML = `<svg width="${size}" height="${size}" viewBox="-10 -20 250 250"><path fill-rule="evenodd" d="${EMBLEM}"/></svg>`;
  return s;
}

const TYPE_LABEL = { sldprt: 'SLDPRT', sldasm: 'SLDASM', slddrw: 'SLDDRW', dwg: 'DWG', dxf: 'DXF', pdf: 'PDF', stl: 'STL', step: 'STEP', stp: 'STEP' };
const TYPE_TINT = new Set(['sldprt', 'sldasm', 'slddrw', 'dwg', 'dxf', 'pdf', 'stl']);   // .chip.type.t-<ext> in app.css
export function typeChip(ext) {
  const e = (ext || '').toLowerCase();
  return h('span', { class: `chip type${TYPE_TINT.has(e) ? ' t-' + e : ''}` }, TYPE_LABEL[e] || e.toUpperCase() || 'FILE');
}

export const STATE_LABEL = { in_work: 'In work', in_review: 'In review', released: 'Released', spares_only: 'Spares only', obsolete: 'Obsolete' };
export const STATE_PLAIN = {
  in_work: 'In work — being designed, can be changed',
  in_review: 'In review — waiting for a checker',
  released: 'Released — safe to manufacture',
  spares_only: 'Spares only — supplied as a spare, not for new machines',
  obsolete: 'Obsolete — do not use',
};
const STATE_ICON = { in_work: 'edit', in_review: 'review', released: 'check', spares_only: 'warn', obsolete: 'x' };
export function stateChip(state, label) {
  const text = label || STATE_LABEL[state] || (state || '').replace(/_/g, ' ');
  const guided = S.mode === 'guided' && STATE_PLAIN[state] && label === undefined;
  // a shape as well as a colour, so the state reads the same for people who do not tell the colours apart
  const mark = STATE_ICON[state];
  return h('span', { class: `chip st-${state}`, title: STATE_PLAIN[state] || '' }, mark ? icon(mark, 12, 'st-ic') : null,
    guided ? STATE_PLAIN[state].split(' — ')[0] : text);
}

// ------------------------------------------------------------------ format
// In the company's locale: 10-Oct-2026 14:05 in India (locale.js)
export function fmtDate(iso, withTime = true) {
  return withTime ? fmtWhen(iso) : fmtDay(iso);
}
export function ago(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return fmtDate(iso, false);
}
export function fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
export function initials(name) { return (name || '?').split(/\s+/).map(p => p[0]).slice(0, 2).join('').toUpperCase(); }

// ------------------------------------------------------------------ API
export class ApiError extends Error {
  constructor(message, status, code, data) { super(message); this.status = status; this.code = code; this.data = data; }
}

// opts.background: a poll the user did not ask for, which must not keep an idle session open
export async function api(method, path, body, opts = {}) {
  const opt = { method, headers: {} };
  if (S.token) opt.headers.Authorization = `Bearer ${S.token}`;
  if (opts.background) opt.headers['X-Kosha-Background'] = '1';
  if (body instanceof FormData) opt.body = body;
  else if (body instanceof Blob) { opt.headers['Content-Type'] = 'application/octet-stream'; opt.body = body; }
  else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw new ApiError(leaving ? '' : 'Cannot reach the Kosha server. Check your network connection.', 0, leaving ? 'aborted' : 'network'); }
  if (r.status === 401 && !path.includes('/auth/login')) { logout(true); throw new ApiError('Please sign in again.', 401, 'unauthorized'); }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.blob();
  if (!r.ok) throw new ApiError((data && data.error) || `Error ${r.status}`, r.status, data && data.code, data);
  return data;
}
export const get = p => api('GET', p);
export const post = (p, b = {}) => api('POST', p, b);
export const put = (p, b = {}) => api('PUT', p, b);
export const patch = (p, b = {}) => api('PATCH', p, b);
export const del = p => api('DELETE', p);

export function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', path);
    if (S.token) x.setRequestHeader('Authorization', `Bearer ${S.token}`);
    x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let data = {};
      try { data = JSON.parse(x.responseText || '{}'); } catch (e) { /* ignore */ }
      if (x.status === 401) { logout(true); reject(new ApiError('Please sign in again.', 401)); return; }
      if (x.status >= 400) reject(new ApiError(data.error || `Error ${x.status}`, x.status, data.code, data));
      else resolve(data);
    };
    x.onerror = () => reject(new ApiError('Upload failed: cannot reach the server.', 0, 'network'));
    x.send(form);
  });
}

// Big files go in 8 MB pieces that resume after a network break; the server checks the whole file's SHA-256
// before it keeps it. finish: { folder_id, path?, comment, keep_checked_out } or { document_id, ... }.
export const RESUMABLE_FROM = 32 * 1024 * 1024;
export async function resumableUpload(file, finish, onProgress = () => {}) {
  const { sha256File } = await import('./sha256.js');
  const sha256 = await sha256File(file, p => onProgress(0.1 * p));      // reading the file: the first tenth
  const up = await post('/api/uploads', { filename: file.name, size: file.size, sha256 });
  let at = up.received;
  let tries = 0;
  while (at < file.size) {
    const piece = file.slice(at, at + up.piece_size);
    try {
      at = (await api('PUT', `/api/uploads/${up.id}?offset=${at}`, piece)).received;
      tries = 0;
      onProgress(0.1 + 0.9 * at / file.size);
    } catch (e) {
      if (e.status && e.code !== 'wrong_offset') throw e;
      if (++tries > 8) throw new ApiError(`The connection keeps breaking. ${Math.round(100 * at / file.size)}% of ${file.name} arrived; nothing was added yet. Try again when the network is steady.`, 0, 'network');
      await new Promise(r => setTimeout(r, Math.min(30000, 1000 * 2 ** tries)));      // wait, then ask how far it got
      try { at = (await get(`/api/uploads/${up.id}`)).received; } catch (e2) { /* still offline: try again */ }
    }
  }
  return post(`/api/uploads/${up.id}/finish`, finish);
}

export function fileUrl(id, kind = 'preview', version) {
  const v = version ? `version=${version}&` : '';
  return `/api/documents/${id}/${kind}?${v}access_token=${encodeURIComponent(S.token)}`;
}

export async function downloadDoc(id, version) {
  const a = h('a', { href: fileUrl(id, 'file', version), download: '' });
  document.body.appendChild(a); a.click(); a.remove();
}

export function logout(expired) {
  // end the session on the server too, so the token can't be used again
  if (!expired && S.token) fetch('/api/auth/logout', { method: 'POST', keepalive: true, headers: { Authorization: `Bearer ${S.token}` } }).catch(() => {});
  S.token = ''; S.user = null;
  if (window.koshaLive) { window.koshaLive.close(); window.koshaLive = null; }   // the live updates connection
  localStorage.removeItem('kosha.token');
  if (expired) sessionStorage.setItem('kosha.expired', '1');
  location.hash = '#/login';
}

// ------------------------------------------------------------------ feedback
let toastBox;
// undo: { label: 'Undo', run: async () => {...} } adds a button that reverses what was just done (it stays longer).
export function toast(msg, kind = '', undo = null) {
  if (!toastBox) { toastBox = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(toastBox); }
  const t = h('div', { class: `toast ${kind}` }, icon(kind === 'err' ? 'warn' : kind === 'ok' ? 'check' : 'info', 18), h('div', null, msg),
    undo ? h('button', { class: 'toast-undo', onclick: async e => {
      e.target.disabled = true;
      try { await undo.run(); t.remove(); } catch (x) { fail(x); e.target.disabled = false; }
    } }, undo.label || 'Undo') : null);
  toastBox.appendChild(t);
  setTimeout(() => t.remove(), undo ? 10000 : kind === 'err' ? 8000 : 4000);
}
// Requests cut off because the page is closing or reloading are not errors the user needs to see.
let leaving = false;
window.addEventListener('pagehide', () => { leaving = true; });
window.addEventListener('beforeunload', () => { leaving = true; });
export function fail(e) { if (e && e.code === 'aborted') return; console.error(e); toast(e.message || String(e), 'err'); }

export async function busy(btn, fn) {
  const b = btn instanceof Element ? btn : null;
  if (b) b.disabled = true;
  try { return await fn(); } catch (e) { fail(e); return undefined; } finally { if (b) b.disabled = false; }
}

// ------------------------------------------------------------------ dialogs
export function dialog({ title, body, actions = [], wide = false, onClose }) {
  const d = h('dialog', { class: `dlg ${wide ? 'wide' : ''}`, 'aria-label': title });
  const close = (val) => { d.close(); d.remove(); if (onClose) onClose(val); };
  const foot = h('div', { class: 'dlg-f' });
  for (const a of actions) {
    const b = h('button', { class: `btn ${a.primary ? 'primary' : ''} ${a.danger ? 'danger' : ''}`, type: a.submit ? 'submit' : 'button' }, a.label);
    b.addEventListener('click', async (ev) => {
      ev.preventDefault();
      if (!a.onClick) return close(a.value);
      b.disabled = true;
      try {
        const r = await a.onClick(close);
        if (r !== false) close(a.value);
      } catch (e) { fail(e); } finally { b.disabled = false; }
    });
    foot.appendChild(b);
  }
  append(d, [h('div', { class: 'dlg-h' }, h('h2', null, title), h('span', { class: 'spacer' }),
    h('button', { class: 'btn ghost icon', 'aria-label': 'Close', onclick: () => close() }, icon('x'))),
    h('div', { class: 'dlg-b' }, body), actions.length ? foot : null]);
  d.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
  document.body.appendChild(d);
  d.showModal();
  const first = d.querySelector('input:not([type=hidden]),select,textarea');
  if (first) setTimeout(() => first.focus(), 30);
  return { el: d, close };
}

export function confirmDlg(title, message, okLabel = 'OK', danger = false) {
  return new Promise(resolve => {
    dialog({ title, body: h('p', { style: { margin: 0, lineHeight: 1.5 } }, message),
      actions: [{ label: 'Cancel', value: false }, { label: okLabel, primary: !danger, danger, value: true }],
      onClose: v => resolve(!!v) });
  });
}

export function prompt(title, label, value = '', okLabel = 'OK', hint = '') {
  return new Promise(resolve => {
    const inp = h('input', { class: 'input', value });
    let result = null;
    const dlg = dialog({ title, body: h('div', { class: 'field' }, h('label', null, label), inp, hint ? h('span', { class: 'hint' }, hint) : null),
      actions: [{ label: 'Cancel' }, { label: okLabel, primary: true, onClick: () => { result = inp.value.trim(); } }],
      onClose: () => resolve(result) });
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { result = inp.value.trim(); dlg.close(); } });
  });
}

let ctx;
export function contextMenu(x, y, items) {
  closeMenu();
  ctx = h('div', { class: 'ctxmenu', role: 'menu' });
  for (const it of items) {
    if (it === '-') { ctx.appendChild(h('hr')); continue; }
    if (!it || it.hidden) continue;
    const b = h('button', { role: 'menuitem', disabled: it.disabled, title: it.why || '' },
      icon(it.icon || 'file', 16), h('span', null, it.label), it.key ? h('span', { class: 'k' }, it.key) : null);
    b.addEventListener('click', () => { closeMenu(); it.run && it.run(); });
    ctx.appendChild(b);
  }
  document.body.appendChild(ctx);
  const r = ctx.getBoundingClientRect();
  ctx.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
  ctx.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
  const first = ctx.querySelector('button:not(:disabled)');
  if (first) first.focus();
  ctx.addEventListener('keydown', e => {
    const btns = [...ctx.querySelectorAll('button:not(:disabled)')];
    const i = btns.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length].focus(); }
    if (e.key === 'Escape') closeMenu();
  });
}
export function closeMenu() { if (ctx) { ctx.remove(); ctx = null; } }
document.addEventListener('mousedown', e => { if (ctx && !ctx.contains(e.target)) closeMenu(); });
window.addEventListener('blur', closeMenu);

// ------------------------------------------------------------------ misc
// A random password that meets Kosha's password rules (14 characters, no look-alike letters).
export function newPassword(length = 14) {
  const chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const r = new Uint32Array(length);
  crypto.getRandomValues(r);
  return Array.from(r, x => chars[x % chars.length]).join('');
}
export function debounce(fn, ms = 250) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export function store(key, val) {
  try {
    if (val === undefined) return JSON.parse(localStorage.getItem('kosha.' + key));
    localStorage.setItem('kosha.' + key, JSON.stringify(val));
  } catch (e) { return undefined; }
  return val;
}
// Unsaved edits kept in this browser (properties, BOMs, change logs) and offered again after a crash or a closed tab.
export const drafts = {
  key: k => `draft.${S.user ? S.user.id : 0}.${k}`,
  get: k => store(drafts.key(k)) || null,
  set: (k, values) => store(drafts.key(k), { at: new Date().toISOString(), values }),
  clear: k => { try { localStorage.removeItem('kosha.' + drafts.key(k)); } catch (e) { /* storage blocked */ } },
};

// "Unsaved changes from 5 min ago were kept in this browser. Restore them / Discard", or null when there are none.
export function draftBanner(k, onRestore) {
  const d = drafts.get(k);
  if (!d) return null;
  const el = h('div', { class: 'banner info draft-banner' }, icon('info'),
    h('div', { style: { flex: 1 } }, `Unsaved changes from ${ago(d.at)} were kept in this browser.`),
    h('button', { class: 'btn sm', onclick: () => { el.remove(); onRestore(d.values); } }, 'Restore them'),
    h('button', { class: 'btn sm ghost', onclick: () => { drafts.clear(k); el.remove(); } }, 'Discard'));
  return el;
}

// Keep the values of some inputs as a draft while they are edited. Returns { banner, clear }: show the banner
// (null when there is nothing to offer) and call clear() once the edits are saved.
export function keepDraft(k, fields) {
  const values = () => Object.fromEntries(Object.entries(fields).map(([n, el]) => [n, el.value]));
  const initial = JSON.stringify(values());
  const save = debounce(() => { const v = values(); if (JSON.stringify(v) === initial) drafts.clear(k); else drafts.set(k, v); }, 400);
  Object.values(fields).forEach(el => el.addEventListener('input', save));
  const d = drafts.get(k);
  const fresh = d && JSON.stringify(d.values) !== initial;
  if (d && !fresh) drafts.clear(k);
  const banner = fresh ? draftBanner(k, v => { for (const [n, el] of Object.entries(fields)) if (n in v) el.value = v[n]; save(); }) : null;
  return { banner, clear: () => drafts.clear(k) };
}

// roles stack on the ladder; extra roles (data_steward, spares, quality) are held beside it, and admins hold them all
const LADDER = { viewer: 0, engineer: 1, checker: 2, approver: 3, admin: 4 };
// a vendor account only reads, whatever its role
export const can = role => !!S.user && (S.user.kind === 'vendor' ? role === 'viewer' : role in LADDER ? LADDER[S.user.role] >= LADDER[role]
  : S.user.role === 'admin' || (S.user.roles || []).includes(role));

export function tabs(items, active, onSelect) {
  const bar = h('div', { class: 'tabs', role: 'tablist' });
  for (const [key, label] of items) {
    bar.appendChild(h('button', { role: 'tab', 'aria-selected': String(key === active), onclick: () => onSelect(key) }, label));
  }
  return bar;
}

export function thumb(doc, cls = 'mini') {
  if (doc.preview_kind) return h('img', { class: cls, src: fileUrl(doc.id, 'preview', doc.version), alt: '', loading: 'lazy' });
  return h('span', { class: `${cls} thumb-ph` }, icon(doc.doc_type === 'assembly' ? 'cube' : 'file', 16));
}
