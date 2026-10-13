// SPDX-License-Identifier: AGPL-3.0-or-later
// Kosha task pane for Word, Excel and PowerPoint (Office JavaScript API).
//
// The pane is served by the Kosha server itself, so every call goes to the same origin with the user's token.
// Without Office (a plain browser) it runs in test mode: Insert writes into a text box and Check in sends a
// file chosen in the page. The document in Office is only ever read, never changed, by a check-in.
(function () {
  'use strict';

  const TOKEN_KEY = 'kosha.office.token';
  const FOLDER_KEY = 'kosha.office.folder';
  const FIELDS_KEY = 'kosha.office.fields';
  const SLICE_SIZE = 4 * 1024 * 1024;          // Office hands the document over in slices of up to 4 MB
  const OFFICE_WAIT_MS = 5000;
  const STATE_LABEL = { in_work: 'In work', in_review: 'In review', released: 'Released', obsolete: 'Obsolete' };
  const DEFAULT_EXT = { Word: 'docx', Excel: 'xlsx', PowerPoint: 'pptx' };
  const HOST_EXTS = {
    Word: ['docx', 'docm', 'dotx', 'dotm'],
    Excel: ['xlsx', 'xlsm', 'xltx', 'xltm'],
    PowerPoint: ['pptx', 'pptm', 'potx', 'potm', 'ppsx'],
  };

  const S = {
    host: null,          // 'Word' | 'Excel' | 'PowerPoint' | null (test mode)
    token: '',
    user: null,
    folders: [],         // [{ value: 'Kosha/Products/Mixers', label: 'Products / Mixers' }]
    status: null,        // what Kosha knows about the chosen folder + file name
    busy: false,
    nameEdited: false,
    statusTimer: 0,
    statusSeq: 0,
  };

  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------ storage
  // Browser storage can be switched off; the pane then works, it only forgets the sign-in.
  const store = {
    get(key) { try { return window.localStorage.getItem(key) || ''; } catch (e) { return ''; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* not kept */ } },
    remove(key) { try { window.localStorage.removeItem(key); } catch (e) { /* nothing to remove */ } },
  };

  // ------------------------------------------------------------ messages
  function say(id, text, kind) {
    const el = $(id);
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('ok', kind === 'ok');
    el.classList.toggle('error', kind === 'error');
  }

  class KoshaError extends Error {
    constructor(message, status, code) {
      super(message);
      this.status = status || 0;
      this.code = code || '';
    }
  }

  function messageOf(err, fallback) {
    if (err instanceof KoshaError) return err.message;
    return (fallback || 'Something went wrong in the Kosha pane') + (err && err.message ? ': ' + err.message : '.');
  }

  // ------------------------------------------------------------ Kosha API
  async function api(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (S.token) headers.Authorization = 'Bearer ' + S.token;
    let payload;
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(path, { method, headers, body: payload, cache: 'no-store', credentials: 'same-origin' });
    } catch (e) {
      throw new KoshaError('Kosha cannot be reached. Check your network and try again; nothing was changed.', 0, 'network');
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (resp.status === 401 && path !== '/api/auth/login') {
      signedOut('Your Kosha sign-in has ended. Sign in again; nothing was changed.');
      throw new KoshaError('Please sign in again.', 401, 'unauthorized');
    }
    if (!resp.ok) throw new KoshaError((data && data.error) || ('Kosha answered with error ' + resp.status + '.'), resp.status, data && data.code);
    return data || {};
  }

  /** Multipart upload with progress (fetch cannot report upload progress). */
  function upload(path, form, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', path);
      xhr.setRequestHeader('Authorization', 'Bearer ' + S.token);
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.timeout = 15 * 60 * 1000;
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && e.total) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(data || {}); return; }
        if (xhr.status === 401) signedOut('Your Kosha sign-in has ended. Sign in again, then check in again.');
        reject(new KoshaError((data && data.error) || ('Kosha answered with error ' + xhr.status + '.'), xhr.status, data && data.code));
      };
      xhr.onerror = () => reject(new KoshaError('The upload was interrupted. Nothing was checked in and your document is unchanged; try again.', 0, 'network'));
      xhr.ontimeout = () => reject(new KoshaError('Kosha did not answer in time. Nothing was checked in and your document is unchanged; try again.', 0, 'timeout'));
      xhr.send(form);
    });
  }

  function openInBrowser(url) {
    const full = new URL(url, window.location.origin).href;
    try {
      if (S.host && window.Office && Office.context && Office.context.ui && typeof Office.context.ui.openBrowserWindow === 'function') {
        Office.context.ui.openBrowserWindow(full);
        return;
      }
    } catch (e) { /* fall back to a new window */ }
    window.open(full, '_blank', 'noopener');
  }

  // ------------------------------------------------------------ start
  function start() {
    $('server-name').textContent = window.location.host || 'this server';
    wire();
    if (typeof window.Office === 'undefined' || !Office || typeof Office.onReady !== 'function') {
      init(null);
      return;
    }
    let done = false;
    const fallback = setTimeout(() => { if (!done) { done = true; init(null); } }, OFFICE_WAIT_MS);
    Promise.resolve(Office.onReady()).then((info) => {
      if (done) return;
      done = true;
      clearTimeout(fallback);
      init(info && info.host ? String(info.host) : null);
    }, () => {
      if (done) return;
      done = true;
      clearTimeout(fallback);
      init(null);
    });
  }

  // ------------------------------------------------------------ theme
  // Inside Office the pane takes Office's own theme (light, or dark such as "Black"), read from the colour Office
  // gives for the pane background. Without one (test mode, older Office) it follows the computer (taskpane.css).
  function isDark(colour) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(colour || '').trim());
    if (!m) return null;
    const [r, g, b] = m.slice(1).map((x) => parseInt(x, 16) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5;
  }

  function followOfficeTheme() {
    const apply = () => {
      try {
        const theme = Office.context.officeTheme;
        const dark = theme ? isDark(theme.bodyBackgroundColor) : null;
        if (dark === null) document.documentElement.removeAttribute('data-theme');
        else document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
      } catch (e) { /* no theme from Office: follow the computer */ }
    };
    apply();
    try {
      if (Office.EventType && Office.EventType.OfficeThemeChanged && Office.context.document && Office.context.document.addHandlerAsync) {
        Office.context.document.addHandlerAsync(Office.EventType.OfficeThemeChanged, apply);
      }
    } catch (e) { /* this Office cannot tell about theme changes; the pane picks it up when opened again */ }
  }

  function init(host) {
    S.host = DEFAULT_EXT[host] ? host : null;
    if (S.host) followOfficeTheme();
    $('loading').hidden = true;
    $('testmode').hidden = !!S.host;
    $('testdoc').hidden = !!S.host;
    $('insert-hint').textContent = S.host === 'Excel' ? 'Insert writes into the selected cells, as one row starting at the first cell.'
      : S.host === 'Word' ? 'Insert writes at the cursor, replacing any selected text.'
      : S.host === 'PowerPoint' ? 'Insert writes into the selected text box.'
      : 'Insert writes into the test document at the bottom.';
    restoreFields();
    $('filename').value = documentName();
    S.token = store.get(TOKEN_KEY);
    if (!S.token) { showSignIn(); return; }
    api('GET', '/api/auth/me').then((r) => {
      S.user = r.user || null;
      showMain();
    }, (err) => {
      if (err.status !== 401) showSignIn(messageOf(err));
    });
  }

  function wire() {
    $('signin-form').addEventListener('submit', (e) => { e.preventDefault(); signIn(); });
    $('signout-button').addEventListener('click', () => signedOut(''));
    $('search-form').addEventListener('submit', (e) => { e.preventDefault(); search(); });
    $('fields').addEventListener('change', saveFields);
    $('folder-filter').addEventListener('input', renderFolders);
    $('folder').addEventListener('change', () => { store.set(FOLDER_KEY, $('folder').value); refreshStatusSoon(0); });
    $('filename').addEventListener('input', () => { S.nameEdited = true; refreshStatusSoon(400); });
    $('checkin-button').addEventListener('click', checkIn);
    $('checkout-button').addEventListener('click', checkOut);
    $('open-button').addEventListener('click', () => { if (S.status && S.status.exists) openInBrowser('/#/doc/' + S.status.id); });
    $('test-file').addEventListener('change', () => {
      const f = $('test-file').files[0];
      if (f && !S.nameEdited) { $('filename').value = f.name; refreshStatusSoon(0); }
      updateButtons();
    });
  }

  // ------------------------------------------------------------ sign-in
  function showSignIn(message) {
    $('signin-view').hidden = false;
    $('main-view').hidden = true;
    $('signout-button').hidden = true;
    $('who').textContent = '';
    say('signin-message', message || '', message ? 'error' : '');
    $('username').focus();
  }

  function signedOut(message) {
    S.token = '';
    S.user = null;
    S.status = null;
    store.remove(TOKEN_KEY);
    showSignIn(message);
  }

  async function signIn() {
    const username = $('username').value.trim();
    const password = $('password').value;
    if (!username || !password) {
      say('signin-message', 'Enter your Kosha user name and password.', 'error');
      (username ? $('password') : $('username')).focus();
      return;
    }
    $('signin-button').disabled = true;
    say('signin-message', 'Signing in…');
    try {
      const r = await api('POST', '/api/auth/login', { username, password });
      S.token = r.token;
      S.user = r.user || null;
      store.set(TOKEN_KEY, S.token);
      $('password').value = '';
      say('signin-message', '');
      showMain();
    } catch (err) {
      say('signin-message', messageOf(err), 'error');
      $('password').select();
    } finally {
      $('signin-button').disabled = false;
    }
  }

  function showMain() {
    $('signin-view').hidden = true;
    $('main-view').hidden = false;
    $('signout-button').hidden = false;
    $('who').textContent = S.user ? (S.user.full_name || S.user.username) : '';
    loadFolders();
    updateButtons();
    $('search').focus();
  }

  // ------------------------------------------------------------ parts
  function restoreFields() {
    const saved = store.get(FIELDS_KEY);
    if (!saved) return;
    const on = new Set(saved.split(','));
    for (const box of document.querySelectorAll('#fields input[name="field"]')) box.checked = on.has(box.value);
  }

  function saveFields() {
    store.set(FIELDS_KEY, chosenFields().join(','));
  }

  function chosenFields() {
    return Array.from(document.querySelectorAll('#fields input[name="field"]:checked')).map((b) => b.value);
  }

  async function search() {
    const q = $('search').value.trim();
    const list = $('results');
    if (!q) {
      say('parts-message', 'Type part of a part number, a description or a material, then press Search.', 'error');
      $('search').focus();
      return;
    }
    $('search-button').disabled = true;
    say('parts-message', 'Searching…');
    try {
      const r = await api('GET', '/api/search?q=' + encodeURIComponent(q) + '&limit=30');
      const items = r.items || [];
      list.textContent = '';
      for (const item of items) list.appendChild(partRow(item));
      say('parts-message', items.length
        ? (items.length === 1 ? 'One part found.' : items.length + ' parts found.')
        : 'No parts match "' + q + '". Try part of a number, such as MX-100.');
    } catch (err) {
      if (err.status !== 401) say('parts-message', messageOf(err), 'error');
    } finally {
      $('search-button').disabled = false;
    }
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function partRow(item) {
    const li = el('li');
    li.appendChild(el('div', 'pn', item.part_number));
    if (item.description) li.appendChild(el('div', 'desc', item.description));
    const meta = ['Revision ' + (item.revision || '-'), STATE_LABEL[item.state] || item.state || '', item.material || ''].filter(Boolean);
    li.appendChild(el('div', 'meta', meta.join(' · ')));
    const buttons = el('div', 'buttons');
    const insert = el('button', 'small', 'Insert');
    insert.type = 'button';
    insert.setAttribute('aria-label', 'Insert ' + item.part_number + ' into the document');
    insert.addEventListener('click', () => insertPart(item));
    const open = el('button', 'small link', 'Open in Kosha');
    open.type = 'button';
    open.setAttribute('aria-label', 'Open ' + item.part_number + ' in Kosha');
    open.addEventListener('click', () => openInBrowser('/#/item/' + item.id));
    buttons.appendChild(insert);
    buttons.appendChild(open);
    li.appendChild(buttons);
    return li;
  }

  /** The chosen fields of a part, in a fixed order. */
  function partValues(item, fields) {
    const out = [];
    for (const f of ['part_number', 'description', 'revision', 'material']) {
      if (fields.includes(f)) out.push({ field: f, value: String(item[f] || '') });
    }
    return out;
  }

  /** One line of text: "MX-100-020 Agitator Shaft, revision A, 42CrMo4". */
  function partText(values) {
    let text = '';
    for (const v of values) {
      if (!v.value) continue;
      if (v.field === 'description') text += (text ? ' ' : '') + v.value;
      else if (v.field === 'revision') text += (text ? ', ' : '') + 'revision ' + v.value;
      else text += (text ? ', ' : '') + v.value;
    }
    return text;
  }

  async function insertPart(item) {
    const values = partValues(item, chosenFields());
    if (!values.length) {
      say('parts-message', 'Choose at least one thing to insert: part number, description, revision or material.', 'error');
      return;
    }
    try {
      if (S.host === 'Excel') {
        await Excel.run(async (ctx) => {
          const first = ctx.workbook.getSelectedRange().getCell(0, 0);
          const row = first.getBoundingRect(first.getOffsetRange(0, values.length - 1));
          row.numberFormat = [values.map(() => '@')];       // text, so 0012 stays 0012
          row.values = [values.map((v) => v.value)];
          await ctx.sync();
        });
      } else if (S.host === 'Word') {
        await Word.run(async (ctx) => {
          const range = ctx.document.getSelection().insertText(partText(values), 'Replace');
          range.select('End');
          await ctx.sync();
        });
      } else if (S.host === 'PowerPoint') {
        await new Promise((resolve, reject) => {
          Office.context.document.setSelectedDataAsync(partText(values), { coercionType: Office.CoercionType.Text }, (res) => {
            if (res.status === Office.AsyncResultStatus.Succeeded) resolve();
            else reject(new Error(res.error ? res.error.message : 'PowerPoint did not accept the text'));
          });
        });
      } else {
        insertIntoTestDocument(partText(values));
      }
      say('parts-message', 'Inserted ' + item.part_number + '.', 'ok');
    } catch (err) {
      say('parts-message', 'Could not insert ' + item.part_number + ': ' + (err && err.message ? err.message : 'unknown problem') +
        '. Click into the document where it should go, then try again.', 'error');
    }
  }

  function insertIntoTestDocument(text) {
    const box = $('test-text');
    const start = box.selectionStart || 0;
    const end = box.selectionEnd || 0;
    box.value = box.value.slice(0, start) + text + box.value.slice(end);
    box.selectionStart = box.selectionEnd = start + text.length;
  }

  // ------------------------------------------------------------ folders and status
  function folderOptions(folders) {
    const byId = new Map(folders.map((f) => [f.id, f]));
    const out = [];
    for (const f of folders) {
      const names = [];
      const seen = new Set();
      let cur = f;
      while (cur && !seen.has(cur.id)) {
        seen.add(cur.id);
        names.unshift(cur.name);
        cur = cur.parent_id ? byId.get(cur.parent_id) : null;
      }
      // the top folder itself takes no files; below it, "Kosha/<path>" is understood by every add-in endpoint
      if (names.length > 1) out.push({ value: 'Kosha/' + names.slice(1).join('/'), label: names.slice(1).join(' / ') });
    }
    out.sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));
    return out;
  }

  async function loadFolders() {
    try {
      const r = await api('GET', '/api/folders');
      S.folders = folderOptions(r.folders || []);
      renderFolders();
      const saved = store.get(FOLDER_KEY);
      if (saved && S.folders.some((f) => f.value === saved)) $('folder').value = saved;
      refreshStatusSoon(0);
    } catch (err) {
      if (err.status !== 401) say('checkin-message', messageOf(err), 'error');
    }
  }

  function renderFolders() {
    const select = $('folder');
    const keep = select.value;
    const words = $('folder-filter').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    select.textContent = '';
    for (const f of S.folders) {
      const label = f.label.toLowerCase();
      if (words.length && !words.every((w) => label.includes(w))) continue;
      const opt = document.createElement('option');
      opt.value = f.value;
      opt.textContent = f.label;
      select.appendChild(opt);
    }
    if (keep && Array.from(select.options).some((o) => o.value === keep)) select.value = keep;
    updateButtons();
  }

  /** The document's file name, from Office, or a name for the kind of document. */
  function documentName() {
    let name = '';
    try {
      const url = S.host && Office.context && Office.context.document ? Office.context.document.url || '' : '';
      name = decodeURIComponent(url.split(/[\\/]/).pop() || '').split(/[?#]/)[0];
    } catch (e) {
      name = '';
    }
    if (!S.host) return name;
    const def = DEFAULT_EXT[S.host];
    const m = /^(.*)\.([A-Za-z0-9]+)$/.exec(name);
    if (!name) return 'Document.' + def;
    if (!m) return name + '.' + def;
    // Office hands over the document in its Open XML format; an old .doc or .xls is checked in as .docx or .xlsx
    return HOST_EXTS[S.host].includes(m[2].toLowerCase()) ? name : m[1] + '.' + def;
  }

  function fileNameProblem(name) {
    if (!name) return 'Enter the file name to use in Kosha.';
    if (/[<>:"/\\|?*\x00-\x1f]/.test(name)) return 'The file name may not contain < > : " / \\ | ? *';
    if (!/\.[A-Za-z0-9]+$/.test(name)) return 'Give the file name an extension, such as .docx.';
    return '';
  }

  function refreshStatusSoon(delay) {
    clearTimeout(S.statusTimer);
    S.statusTimer = setTimeout(refreshStatus, delay);
  }

  async function refreshStatus() {
    const folder = $('folder').value;
    const name = $('filename').value.trim();
    const seq = ++S.statusSeq;
    S.status = null;
    if (!S.token || !folder || fileNameProblem(name)) { renderStatus(); return; }
    try {
      const st = await api('GET', '/api/addin/status?folder=' + encodeURIComponent(folder) + '&filename=' + encodeURIComponent(name));
      if (seq !== S.statusSeq) return;      // the choice changed meanwhile
      S.status = st;
    } catch (err) {
      if (seq !== S.statusSeq) return;
      if (err.status !== 401) say('checkin-message', messageOf(err), 'error');
    }
    renderStatus();
  }

  function mine(st) {
    return !!(st && st.checked_out_by && S.user && st.checked_out_by === S.user.id);
  }

  function renderStatus() {
    const st = S.status;
    const line = $('doc-status');
    if (!st) line.textContent = '';
    else if (!st.exists) line.textContent = 'New in Kosha: checking in adds it to this folder as a new file.';
    else {
      const where = 'In Kosha: revision ' + st.revision + ', ' + (st.state_label || st.state) + ', version ' + st.version + '. ';
      if (mine(st)) line.textContent = where + 'Checked out to you: checking in makes version ' + (st.version + 1) + '.';
      else if (st.checked_out_by) line.textContent = where + 'Checked out by ' + (st.checked_out_name || 'someone else') + '.';
      else line.textContent = where + 'Not checked out.';
    }
    updateButtons();
  }

  /** Enables what can be done now, and says why the rest cannot. */
  function updateButtons() {
    const checkin = $('checkin-button');
    const checkout = $('checkout-button');
    const st = S.status;
    const name = $('filename').value.trim();
    let why = '';
    if (S.busy) why = 'Kosha is working; wait for it to finish.';
    else if (!$('folder').value) why = S.folders.length ? 'Choose the Kosha folder for this document.' : 'There are no Kosha folders yet; ask your Kosha administrator to create one.';
    else if (fileNameProblem(name)) why = fileNameProblem(name);
    else if (!S.host && !($('test-file').files && $('test-file').files.length)) why = 'Test mode: choose a file to check in.';
    else if (!st) why = 'Asking Kosha about this file…';
    else if (st.exists && st.checked_out_by && !mine(st)) why = 'Checked out by ' + (st.checked_out_name || 'someone else') + ', so you cannot check it in.';
    else if (st.exists && !mine(st)) {
      const co = st.actions && st.actions.checkout;
      why = co && co.ok ? 'Check it out first, so nobody else changes it at the same time.' : ('It cannot be changed now: ' + ((co && co.why) || 'it is locked') + '');
    }
    checkin.disabled = !!why;
    checkin.title = why;
    $('checkin-why').textContent = why;
    const canCheckOut = !S.busy && st && st.exists && !st.checked_out_by && st.actions && st.actions.checkout && st.actions.checkout.ok;
    checkout.hidden = !canCheckOut;
    $('open-button').hidden = !(st && st.exists);
  }

  async function checkOut() {
    const st = S.status;
    if (!st || !st.exists || S.busy) return;
    S.busy = true;
    updateButtons();
    try {
      await api('POST', '/api/addin/checkout', { folder: $('folder').value, filename: $('filename').value.trim() });
      say('checkin-message', st.filename + ' is checked out to you. Edit the document, then check it in.', 'ok');
    } catch (err) {
      if (err.status !== 401) say('checkin-message', messageOf(err), 'error');
    } finally {
      S.busy = false;
    }
    await refreshStatus();
  }

  // ------------------------------------------------------------ check-in
  function progress(fraction, text) {
    $('progress-box').hidden = fraction === null;
    if (fraction === null) return;
    $('progress').value = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
    $('progress-label').textContent = text;
  }

  /** Office.AsyncResult callbacks as promises. */
  function officeCall(fn) {
    return new Promise((resolve, reject) => {
      fn((res) => {
        if (res.status === Office.AsyncResultStatus.Succeeded) resolve(res.value);
        else reject(new Error(res.error ? res.error.message : 'Office did not answer'));
      });
    });
  }

  /**
   * The whole document as Office has it now (saved or not), read slice by slice and joined.
   * The file handle is always closed again, also after an error: Office allows only one open at a time.
   */
  async function readDocument(onSlice) {
    const file = await officeCall((cb) => Office.context.document.getFileAsync(Office.FileType.Compressed, { sliceSize: SLICE_SIZE }, cb));
    try {
      const parts = [];
      let total = 0;
      for (let i = 0; i < file.sliceCount; i++) {
        const slice = await officeCall((cb) => file.getSliceAsync(i, cb));
        const bytes = new Uint8Array(slice.data);
        parts.push(bytes);
        total += bytes.length;
        onSlice(i + 1, file.sliceCount);
      }
      if (typeof file.size === 'number' && file.size > 0 && total !== file.size) {
        throw new Error('Office handed over ' + total + ' of ' + file.size + ' bytes');
      }
      const all = new Uint8Array(total);
      let at = 0;
      for (const p of parts) { all.set(p, at); at += p.length; }
      return all;
    } finally {
      await new Promise((resolve) => {
        try { file.closeAsync(() => resolve()); } catch (e) { resolve(); }
      });
    }
  }

  async function readTestFile() {
    const f = $('test-file').files[0];
    if (!f) throw new Error('choose a file first');
    return new Uint8Array(await f.arrayBuffer());
  }

  async function checkIn() {
    if (S.busy || $('checkin-button').disabled) return;
    const folder = $('folder').value;
    const name = $('filename').value.trim();
    S.busy = true;
    updateButtons();
    say('checkin-message', '');
    let bytes;
    try {
      progress(0, S.host ? 'Reading the document from ' + S.host + '…' : 'Reading the test file…');
      bytes = S.host
        ? await readDocument((done, count) => progress(0.4 * done / count, 'Reading the document from ' + S.host + ': part ' + done + ' of ' + count + '…'))
        : await readTestFile();
    } catch (err) {
      S.busy = false;
      progress(null);
      updateButtons();
      say('checkin-message', 'Could not read the document' + (err && err.message ? ' (' + err.message + ')' : '') +
        '. Nothing was checked in; your document is unchanged. Try again.', 'error');
      return;
    }
    const form = new FormData();
    form.append('folder', folder);
    form.append('comment', $('comment').value);
    form.append('keep_checked_out', $('keep').checked ? '1' : '0');
    form.append('source', 'Office add-in (' + (S.host || 'test mode') + ')');
    form.append('props', '{}');
    form.append('file', new Blob([bytes], { type: 'application/octet-stream' }), name);
    try {
      progress(0.4, 'Sending ' + name + ' to Kosha…');
      const r = await upload('/api/addin/checkin', form, (f) => progress(0.4 + 0.6 * f, 'Sending ' + name + ' to Kosha: ' + Math.round(f * 100) + '%'));
      $('comment').value = '';
      say('checkin-message', 'Checked in ' + r.filename + ' as version ' + r.version + ' of revision ' + r.revision + '.' +
        ($('keep').checked ? ' It stays checked out to you.' : ''), 'ok');
    } catch (err) {
      if (err.status !== 401) say('checkin-message', messageOf(err) + ' Nothing was checked in; your document is unchanged.', 'error');
    } finally {
      S.busy = false;
      progress(null);
    }
    await refreshStatus();
  }

  // Exposed for the browser test only; the pane itself does not use it.
  window.KoshaTaskpane = { folderOptions, partText, partValues, documentName };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
