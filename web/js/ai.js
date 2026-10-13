// SPDX-License-Identifier: AGPL-3.0-or-later
// AI assistance (optional): suggestion panels for parts and files, the AI draft in the change-log editor,
// the "Ask Kosha" screen and Admin › AI. When AI is off, every function here adds nothing to the screen
// (Ask Kosha and Admin › AI say plainly that it is off). Suggestions are never saved without a click.
import { S, h, mount, icon, get, post, put, toast, fail, dialog, ago, fmtDate, can, upload } from './core.js';

const OFF = { on: false, features: { classify: false, changelog: false, ask: false, drawing_check: false, duplicates: false, ocr: false, voice: false }, provider: '', why: '' };
let statusPromise = null;
let statusAt = 0;

/** Which AI features may be offered now. Kept for a minute; `force` asks the server again. */
export function aiStatus(force = false) {
  if (force || !statusPromise || Date.now() - statusAt > 60000) {
    statusAt = Date.now();
    statusPromise = get('/api/ai/status').catch(() => OFF);
  }
  return statusPromise;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/** Wait for a pending suggestion (the provider answers in the background). Gives up after `limitS` seconds. */
export async function waitForSuggestion(sug, onTick, limitS = 180) {
  const until = Date.now() + limitS * 1000;
  let delay = 800;
  while (sug.status === 'pending' && Date.now() < until) {
    if (onTick) onTick(sug);
    await sleep(delay);
    delay = Math.min(delay * 1.4, 3000);
    sug = await get(`/api/ai/suggestions/${sug.id}`);
  }
  if (sug.status === 'pending') sug = { ...sug, status: 'failed', error: 'The AI assistant is taking too long. Your work is safe; try again later.' };
  return sug;
}

function working(text = 'The AI assistant is working on it…') {
  return h('div', { class: 'ai-box small muted row' }, h('span', { class: 'ai-spin', 'aria-hidden': 'true' }), h('span', { role: 'status' }, text));
}

function failed(sug, retry) {
  return h('div', { class: 'banner err small' }, icon('warn'), h('div', { style: { flex: 1 } }, sug.error || 'The AI assistant could not be reached; your work is safe.',
    sug.detail ? h('div', { class: 'small', style: { marginTop: '4px' } }, `For the administrator: ${sug.detail}`) : null),
  retry ? h('button', { class: 'btn sm', onclick: retry }, icon('refresh', 14), 'Try again') : null);
}

function aiBadge(text = 'AI suggestion') {
  return h('span', { class: 'chip ai-chip', title: 'Written by the AI assistant. Check it before you accept it.' }, icon('sparkle', 12), text);
}

// ------------------------------------------------------------------ classification
/**
 * "Suggest with AI" for an item or a file. Returns an element that stays empty when classification is off.
 * opts.onApplied() runs after the person accepted suggestions (to reload the page).
 */
export function classifyBox(subject, id, opts = {}) {
  const box = h('div', { class: 'col', style: { gap: '8px' } });
  aiStatus().then(st => {
    if (!st.features.classify) return;
    const allowed = can('engineer');
    const out = h('div');
    const btn = h('button', { class: 'btn sm', disabled: !allowed, title: allowed ? 'Ask the AI assistant for a category, material, matching standard part and a cleaner description. Nothing changes until you accept.' : 'You have view-only access.' },
      icon('sparkle', 14), 'Suggest with AI');
    const run = async () => {
      btn.disabled = true;
      mount(out, working());
      try {
        let sug = await post('/api/ai/classify', { subject, id: Number(id) });
        sug = await waitForSuggestion(sug, () => mount(out, working()));
        if (!out.isConnected) return;
        if (sug.status !== 'ready') { mount(out, failed(sug, run)); return; }
        mount(out, classificationPanel(sug, opts, () => mount(out)));
      } catch (e) { mount(out, failed({ error: e.message }, null)); } finally { btn.disabled = !allowed; }
    };
    btn.addEventListener('click', run);
    mount(box, h('div', { class: 'row wrap' }, btn, h('span', { class: 'small muted' }, `AI assistant${st.provider ? ` (${st.provider})` : ''}: suggestions only, you decide.`)), out);
  });
  return box;
}

function classificationPanel(sug, opts, close) {
  const r = sug.result;
  const rows = [];
  const picks = {};
  const locked = opts.locked || {};       // field -> why this person cannot change it (released part, file not checked out)
  for (const f of r.fields) {
    const why = locked[f.key] || '';
    const cb = h('input', { type: 'checkbox', checked: !why, disabled: !!why, title: why, 'aria-label': `Accept the suggested ${f.label.toLowerCase()}` });
    const inp = h('input', { class: 'input', value: f.suggested, disabled: !!why, title: why, 'aria-label': `Suggested ${f.label.toLowerCase()} (you can edit it)` });
    picks[f.key] = { cb, inp };
    rows.push(h('tr', null, h('td', null, cb), h('td', null, h('b', null, f.label)),
      h('td', { class: 'small muted' }, f.current || '—'),
      h('td', { style: { minWidth: '220px' } }, inp, f.note ? h('div', { class: 'small muted' }, f.note) : null, why ? h('div', { class: 'small sev-warning' }, why) : null),
      h('td', { class: 'small muted' }, f.reason || '')));
  }
  let std = null;
  if (r.standard_part) {
    const sp = r.standard_part;
    const libLink = h('a', { href: `#/library?q=${encodeURIComponent(sp.designation || sp.part_number)}` }, 'Open in the library');
    if (sp.existing) {
      std = h('input', { type: 'checkbox', 'aria-label': `Mark as a duplicate of ${sp.existing.part_number}` });
      rows.push(h('tr', null, h('td', null, std), h('td', null, h('b', null, 'Standard part')), h('td', { class: 'small muted' }, '—'),
        h('td', null, h('a', { href: `#/item/${sp.existing.id}` }, sp.existing.part_number), ` ${sp.designation || ''}`,
          h('div', { class: 'small muted' }, 'Tick to mark this part as a duplicate of the standard part.')),
        h('td', { class: 'small muted' }, sp.reason || '')));
    } else {
      rows.push(h('tr', null, h('td', null), h('td', null, h('b', null, 'Standard part')), h('td', { class: 'small muted' }, '—'),
        h('td', null, h('span', { class: 'mono' }, sp.part_number), ` ${sp.designation || ''}`, h('div', { class: 'small' }, libLink, h('span', { class: 'muted' }, ' — not in Kosha yet; use it from the library if it is the same part.'))),
        h('td', { class: 'small muted' }, sp.reason || '')));
    }
  }
  const head = h('div', { class: 'row' }, aiBadge(), h('b', null, r.nothing ? 'The AI assistant has nothing to change.' : 'Review the suggestions'), h('span', { class: 'spacer' }),
    h('button', { class: 'btn ghost sm', 'aria-label': 'Close the suggestions', title: 'Close (nothing is changed)', onclick: () => { post(`/api/ai/suggestions/${sug.id}/dismiss`).catch(() => {}); close(); } }, icon('x', 14)));
  const ignored = (r.ignored || []).length ? h('div', { class: 'small muted' }, r.ignored.map(x => h('div', null, x))) : null;
  const note = r.note ? h('div', { class: 'small sev-warning' }, r.note) : null;
  if (r.nothing) return h('div', { class: 'ai-box col' }, head, ignored, note);
  const accept = h('button', { class: 'btn primary sm' }, icon('check', 14), 'Accept ticked');
  accept.addEventListener('click', async () => {
    const fields = {};
    for (const [k, p] of Object.entries(picks)) if (p.cb.checked) fields[k] = p.inp.value.trim();
    accept.disabled = true;
    try {
      await post(`/api/ai/suggestions/${sug.id}/accept`, { fields, mark_standard: !!(std && std.checked) });
      toast('Accepted. The change is in the audit log.', 'ok');
      close();
      if (opts.onApplied) opts.onApplied();
    } catch (e) { fail(e); } finally { accept.disabled = false; }
  });
  const dismiss = h('button', { class: 'btn sm', onclick: async () => { try { await post(`/api/ai/suggestions/${sug.id}/dismiss`); close(); } catch (e) { fail(e); } } }, 'Dismiss');
  return h('div', { class: 'ai-box col' }, head,
    h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['', 'Field', 'Now', 'Suggested (edit if needed)', 'Why'].map(t => h('th', null, t)))), h('tbody', null, rows))),
    ignored, note,
    h('div', { class: 'row' }, accept, dismiss, h('span', { class: 'small muted' }, 'Only the ticked lines change, with the text as shown.')));
}

// ------------------------------------------------------------------ drawing check
const SEV = { problem: 'sev-error', missing: 'sev-warning', high: 'sev-error', medium: 'sev-warning', low: 'muted' };

/** "Check drawing": Kosha's own checks (always), then "Ask AI for more" when the drawing check is switched on. */
export function drawingCheckBox(docId) {
  const out = h('div', { class: 'col', style: { gap: '8px' } });
  const btn = h('button', { class: 'btn sm', id: 'drawing-check', title: 'Check the title block, material, revision, notes and dimensions before you send the drawing for approval. Nothing is changed.' }, icon('check', 14), 'Check drawing');
  const result = h('div');
  const aiOut = h('div');
  const run = async () => {
    btn.disabled = true;
    try {
      const r = await get(`/api/documents/${docId}/drawing-check`);
      const st = await aiStatus();
      const allowed = can('engineer');
      const more = st.features.drawing_check ? h('button', { class: 'btn sm', disabled: !allowed, title: allowed ? 'Ask the AI assistant what a checker would still question. Suggestions only.' : 'You have view-only access.' }, icon('sparkle', 14), 'Ask AI for more') : null;
      if (more) {
        const ask = async () => {
          more.disabled = true;
          mount(aiOut, working('The AI assistant is looking at the drawing…'));
          try {
            const sug = await waitForSuggestion(await post('/api/ai/drawing-check', { id: Number(docId) }));
            if (sug.status !== 'ready') { mount(aiOut, failed(sug, ask)); return; }
            const f = sug.result.findings;
            mount(aiOut, h('div', { class: 'ai-box col' }, h('div', { class: 'row' }, aiBadge(), h('b', null, f.length ? 'A checker would also look at' : 'The AI assistant found nothing more to question.')),
              f.length ? h('ul', { class: 'check-list' }, f.map(x => h('li', null, h('span', { class: SEV[x.severity] || '' }, x.text),
                x.evidence ? h('div', { class: 'small muted' }, `Based on: ${x.evidence}`) : null))) : null,
              sug.result.note ? h('div', { class: 'small sev-warning' }, sug.result.note) : null));
          } catch (e) { mount(aiOut, failed({ error: e.message }, null)); } finally { more.disabled = !allowed; }
        };
        more.addEventListener('click', ask);
      }
      mount(result, h('div', { class: 'ai-box col' },
        h('div', { class: 'row' }, h('b', { style: { flex: 1 } }, r.summary), more),
        h('ul', { class: 'check-list' }, r.checks.map(c => h('li', { class: 'row', style: { alignItems: 'flex-start' } },
          icon(c.ok ? 'check' : c.ok === false ? 'warn' : 'info', 14, c.ok ? 'sev-ok' : c.ok === false ? (SEV[c.severity] || '') : 'muted'),
          h('div', null, h('b', null, c.label), ' ', h('span', { class: c.ok === false ? (SEV[c.severity] || '') : 'muted' }, c.text))))),
        aiOut));
    } catch (e) { mount(result, failed({ error: e.message }, null)); } finally { btn.disabled = false; }
  };
  btn.addEventListener('click', run);
  mount(out, h('div', { class: 'row wrap' }, btn, h('span', { class: 'small muted' }, 'Before approval: title block, material, revision, notes.')), result);
  return out;
}

// ------------------------------------------------------------------ title-block reading (OCR)
const SCANS = /\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i;

/** "Read title block" for scanned drawings and photos; nothing when the feature is off or the file is not a scan. */
export function ocrBox(doc, opts = {}) {
  const box = h('div', { class: 'col', style: { gap: '8px' } });
  if (!SCANS.test(doc.filename || '')) return box;
  aiStatus().then(st => {
    if (!st.features.ocr) return;
    const allowed = can('engineer');
    const out = h('div');
    const btn = h('button', { class: 'btn sm', id: 'ocr-read', disabled: !allowed, title: allowed ? 'Send only the title-block corner of the first page to the AI assistant and read part number, description, material and revision. Nothing changes until you accept.' : 'You have view-only access.' },
      icon('sparkle', 14), 'Read title block');
    const run = async () => {
      btn.disabled = true;
      mount(out, working('Reading the title block…'));
      try {
        const sug = await waitForSuggestion(await post('/api/ai/ocr', { id: Number(doc.id) }));
        if (!out.isConnected) return;
        if (sug.status !== 'ready') { mount(out, failed(sug, run)); return; }
        const r = sug.result;
        const read = Object.entries(r.read).filter(([, v]) => v);
        mount(out, h('div', { class: 'col', style: { gap: '8px' } },
          h('div', { class: 'ai-box col' }, h('div', { class: 'row' }, aiBadge('Read by AI'), h('b', null, 'Read from the title block')),
            read.length ? h('dl', { class: 'props' }, read.flatMap(([k, v]) => [h('dt', null, k.replace(/_/g, ' ')), h('dd', null, v)])) : h('div', { class: 'small muted' }, 'Nothing could be read.'),
            (r.checks || []).map(c => h('div', { class: 'small sev-warning' }, c))),
          r.fields.length ? classificationPanel(sug, opts, () => mount(out)) : null));
      } catch (e) { mount(out, failed({ error: e.message }, null)); } finally { btn.disabled = !allowed; }
    };
    btn.addEventListener('click', run);
    mount(box, h('div', { class: 'row wrap' }, btn, h('span', { class: 'small muted' }, 'Only the title-block corner is sent.')), out);
  });
  return box;
}

// ------------------------------------------------------------------ duplicates
/** "Why alike?" on a group of look-alike parts: Kosha's own line, then one AI sentence when switched on. */
export function whyAlike(groupId) {
  const out = h('div', { class: 'col', style: { gap: '6px' } });
  const btn = h('button', { class: 'btn sm', id: 'why-alike', title: 'Say what is alike and what differs between these parts' }, icon('info', 14), 'Why alike?');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const r = await get(`/api/common-parts/groups/${groupId}/why`);
      const line = h('div', { class: 'small' }, r.line);
      const aiLine = h('div');
      mount(out, h('div', { class: 'ai-box col' }, line, aiLine));
      const st = await aiStatus();
      if (st.features.duplicates && can('engineer')) {
        mount(aiLine, working());
        const sug = await waitForSuggestion(await post('/api/ai/duplicates', { id: Number(groupId) }));
        mount(aiLine, sug.status === 'ready' ? h('div', { class: 'row' }, aiBadge(), h('span', null, sug.result.line),
          h('span', { class: 'small muted' }, sug.result.interchangeable === 'likely' ? 'One can probably replace the other.' : sug.result.interchangeable === 'unlikely' ? 'Probably not interchangeable.' : ''))
          : failed(sug, null));
      }
    } catch (e) { mount(out, failed({ error: e.message }, null)); } finally { btn.disabled = false; }
  });
  mount(out, btn);
  return out;
}

// ------------------------------------------------------------------ voice
/** A microphone button that fills `input` with what was said. Nothing when voice is off or the browser cannot record. */
export function micButton(input) {
  const slot = h('span');
  if (!navigator.mediaDevices || !window.MediaRecorder) return slot;
  aiStatus().then(st => {
    if (!st.features.voice) return;
    let rec = null;
    const btn = h('button', { class: 'btn', type: 'button', id: 'ask-mic', 'aria-pressed': 'false', title: 'Speak your question (English, Hindi or a mix). Press again to stop.' }, icon('mic', 16), 'Speak');
    const stop = () => { if (rec && rec.state === 'recording') rec.stop(); };
    btn.addEventListener('click', async () => {
      if (rec && rec.state === 'recording') { stop(); return; }
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e) { toast('Kosha may not use the microphone. Allow it in the browser, or type the question.', 'err'); return; }
      const chunks = [];
      rec = new MediaRecorder(stream);
      rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        btn.classList.remove('primary');
        btn.setAttribute('aria-pressed', 'false');
        btn.lastChild.textContent = 'Speak';
        const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
        const form = new FormData();
        form.append('file', blob, 'question.webm');
        form.append('language', (S.user && S.user.language) || '');
        btn.disabled = true;
        try { input.value = (await upload('/api/ai/transcribe', form)).text; input.focus(); } catch (e) { fail(e); } finally { btn.disabled = false; }
      };
      rec.start();
      setTimeout(stop, 60000);      // a question, not a speech
      btn.classList.add('primary');
      btn.setAttribute('aria-pressed', 'true');
      btn.lastChild.textContent = 'Stop';
    });
    mount(slot, btn);
  });
  return slot;
}

// ------------------------------------------------------------------ change-log draft
/**
 * "Draft with AI" next to "Build draft" in the change-log editor. Returns null when the feature is off.
 * ctx: { subject, id, revision, note() , fill(sections), readOnly, why, setSuggestion(id), area }
 */
export async function aiDraftButton(ctx) {
  const st = await aiStatus();
  if (!st.features.changelog) return null;
  const btn = h('button', { class: 'btn', disabled: ctx.readOnly, title: ctx.readOnly ? ctx.why : 'Ask the AI assistant to write a draft from the measured changes and your note (replaces the text below; nothing is saved until you press Save)' },
    icon('sparkle', 16), 'Draft with AI');
  const run = async () => {
    btn.disabled = true;
    mount(ctx.area, working('The AI assistant is writing a draft from the measured changes…'));
    try {
      let sug = await post('/api/ai/changelog', { subject: ctx.subject, id: ctx.id, revision: ctx.revision || undefined, note: ctx.note() });
      sug = await waitForSuggestion(sug);
      if (sug.status !== 'ready') { mount(ctx.area, failed(sug, run)); return; }
      ctx.fill(sug.result.sections);
      ctx.setSuggestion(sug.id);
      mount(ctx.area, h('div', { class: 'banner info small' }, icon('sparkle'), h('div', null,
        aiBadge('AI draft'), ' Written by the AI assistant from the measured changes and your note. Check every line before you save; nothing is saved yet.',
        sug.result.from_rules && sug.result.from_rules.length ? h('div', { class: 'small muted' }, `Some sections came back empty and were filled from the measured changes: ${sug.result.from_rules.join(', ')}.`) : null,
        sug.result.note ? h('div', { class: 'small sev-warning' }, sug.result.note) : null)));
    } catch (e) { mount(ctx.area, failed({ error: e.message }, null)); } finally { btn.disabled = ctx.readOnly; }
  };
  btn.addEventListener('click', run);
  return btn;
}

// ------------------------------------------------------------------ Ask Kosha
/** A small "Ask Kosha" button for the top bar; appears only when the feature is on. */
export function askEntry(searchInput) {
  const slot = h('span', { class: 'ask-slot' });
  aiStatus().then(st => {
    if (!st.features.ask) return;
    mount(slot, h('button', { class: 'btn sm', title: 'Ask a question; the AI assistant answers from Kosha\'s records, with links', onclick: () => {
      const q = searchInput && searchInput.value.trim();
      location.hash = q ? `#/ask?q=${encodeURIComponent(q)}` : '#/ask';
    } }, icon('sparkle', 14), 'Ask Kosha'));
  });
  return slot;
}

/** "Ask Kosha instead" on the search results page; nothing when the feature is off. */
export function askLink(q) {
  const slot = h('span', { class: 'ask-slot' });
  aiStatus().then(st => {
    if (st.features.ask) mount(slot, h('a', { class: 'btn', href: `#/ask?q=${encodeURIComponent(q || '')}` }, icon('sparkle', 16), 'Ask Kosha instead'));
  });
  return slot;
}

function answerText(text, sources) {
  const byRef = Object.fromEntries((sources || []).map(s => [s.ref.toUpperCase(), s]));
  const out = h('p', { class: 'ai-answer' });
  for (const part of String(text).split(/(\[R\d+\])/gi)) {
    const m = part.match(/^\[(R\d+)\]$/i);
    const s = m && byRef[m[1].toUpperCase()];
    out.appendChild(s ? h('a', { href: s.link, class: 'ai-cite', title: s.label }, `[${s.ref}]`) : document.createTextNode(part));
  }
  return out;
}

const KIND = { item: 'Part', document: 'File', change_order: 'Change order', input: 'Input' };

function recordList(list) {
  return h('ul', { class: 'ai-sources' }, list.map(s => h('li', null, h('span', { class: 'chip st-draft' }, s.ref), ' ', h('span', { class: 'small muted' }, KIND[s.kind] || s.kind), ' ',
    h('a', { href: s.link }, s.label))));
}

function answerCard(q, sug, retry) {
  if (sug.status === 'pending') return h('div', { class: 'panel panel-b col' }, h('b', null, q), working());
  if (sug.status !== 'ready') return h('div', { class: 'panel panel-b col' }, h('b', null, q), failed(sug, retry));
  const r = sug.result;
  return h('div', { class: 'panel panel-b col', style: { gap: '10px' } },
    h('div', { class: 'row' }, h('b', { style: { flex: 1 } }, r.question), aiBadge('AI answer'), h('span', { class: 'small muted' }, ago(sug.finished_at || sug.created_at))),
    r.left_out ? h('div', { class: 'small muted' }, r.left_out) : null,
    r.known ? null : h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'The assistant does not know the answer from the records you may open. Check the records below, or search for them yourself.')),
    answerText(r.answer, r.sources),
    r.note ? h('div', { class: 'small sev-warning' }, r.note) : null,
    r.sources.length ? h('div', null, h('div', { class: 'small', style: { fontWeight: 600 } }, 'Records cited'), recordList(r.sources))
      : r.records.length ? h('div', null, h('div', { class: 'small', style: { fontWeight: 600 } }, 'Records that came closest'), recordList(r.records)) : null,
    h('div', { class: 'small muted' }, 'The AI assistant can be wrong. Open the records to check before you act on the answer.'));
}

const asked = [];   // this browser tab's questions and answers (answers are also kept on the server for the asker)

export async function renderAsk(container, _m, params, { setTitle }) {
  setTitle('Ask Kosha');
  const st = await aiStatus(true);
  if (!st.features.ask) {
    mount(container, h('div', { class: 'page-pad' }, h('div', { class: 'page-head' }, h('h1', null, 'Ask Kosha')),
      h('div', { class: 'banner info' }, icon('info'), h('div', null, 'Ask Kosha is switched off. Kosha works fully without it: use the search box at the top to find parts, files and change orders. ',
        S.user.role === 'admin' ? h('a', { href: '#/admin?tab=ai' }, 'Switch it on under Admin › AI.') : 'An administrator can switch it on under Admin › AI.'))));
    return;
  }
  const q = h('textarea', { class: 'input', id: 'ask-q', rows: 2, maxlength: 500, placeholder: 'For example: Which change orders touch the agitator shaft, and what is its material?', value: params.q || '' });
  const btn = h('button', { class: 'btn primary', type: 'submit' }, icon('sparkle', 16), 'Ask');
  const list = h('div', { class: 'col', style: { gap: '14px' } });
  const draw = () => mount(list, asked.length ? asked.slice().reverse().map(a => answerCard(a.q, a.sug, a.retry)) :
    h('div', { class: 'empty' }, 'Ask about parts, files, change orders and inputs. The answer comes from Kosha\'s own records that you may open, with links to them.'));
  const ask = async (text) => {
    const entry = { q: text, sug: { status: 'pending' } };
    entry.retry = () => { asked.splice(asked.indexOf(entry), 1); ask(text); };
    asked.push(entry);
    draw();
    try {
      entry.sug = await post('/api/ai/ask', { question: text });
      draw();
      entry.sug = await waitForSuggestion(entry.sug);
    } catch (e) { entry.sug = { status: 'failed', error: e.message }; }
    if (container.isConnected) draw();
  };
  const form = h('form', { class: 'panel panel-b col', style: { gap: '10px' } },
    h('div', { class: 'field' }, h('label', { for: 'ask-q' }, 'Your question'), q,
      h('span', { class: 'hint' }, `The AI assistant${st.provider ? ` (${st.provider})` : ''} sees only records you may open, and only what your administrator allows to be sent.`)),
    h('div', { class: 'row' }, btn, micButton(q), h('span', { class: 'spacer' }), h('a', { class: 'btn ghost sm', href: `#/search?q=${encodeURIComponent(params.q || '')}` }, icon('search', 14), 'Search instead')));
  form.addEventListener('submit', e => {
    e.preventDefault();
    const text = q.value.trim();
    if (text.length < 3) { toast('Type a question first.', 'err'); return; }
    q.value = '';
    ask(text);
  });
  q.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  mount(container, h('div', { class: 'page-pad', style: { maxWidth: '980px' } },
    h('div', { class: 'page-head' }, h('div', null, h('h1', null, 'Ask Kosha'), h('div', { class: 'sub' }, 'Questions answered from Kosha\'s records, with links to the records. Suggestions only: check the records before you act.'))),
    form, h('div', { style: { marginTop: '16px' } }, list)));
  draw();
  if (params.q && !asked.some(a => a.q === params.q.trim())) { q.value = ''; ask(params.q.trim()); }
}

// ------------------------------------------------------------------ Admin › AI
const FEATURE_NAME = { classify: 'Classification', changelog: 'Change-log draft', ask: 'Ask Kosha', drawing_check: 'Drawing check', duplicates: 'Duplicate explanation', ocr: 'Title-block reading', voice: 'Voice' };
const SUBJECT_LINK = { item: id => `#/item/${id}`, document: id => `#/doc/${id}`, change_order: id => `#/change/${id}`, cp_group: id => `#/common?group=${id}` };

export async function aiAdminTab(body) {
  const d = await get('/api/ai/settings');
  const cfg = d.settings;
  const result = h('div');
  const say = r => mount(result, h('div', { class: `banner ${r.ok ? 'ok' : 'err'} small` }, icon(r.ok ? 'check' : 'warn'), h('div', null, r.message)));
  const provider = h('select', { class: 'input', id: 'ai-provider', style: { maxWidth: '420px' } },
    h('option', { value: '', selected: !cfg.provider }, 'None: AI assistance is off'),
    d.providers.map(p => h('option', { value: p.id, selected: cfg.provider === p.id }, `${p.name}${p.ready ? '' : ' (not ready)'}`)));
  const provInfo = h('div', { class: 'small' });
  const drawProv = () => {
    const p = d.providers.find(x => x.id === provider.value);
    mount(provInfo, !provider.value ? h('span', { class: 'muted' }, 'Nothing is sent anywhere while no provider is chosen.')
      : p && p.ready ? h('span', { class: 'muted' }, p.description)
        : h('span', { class: 'sev-warning' }, p ? p.why : 'Not installed.'),
    provider.value ? [' ', h('a', { href: '#/admin?tab=addins' }, 'Add-in settings')] : null);
  };
  provider.addEventListener('change', drawProv);
  drawProv();
  const feats = Object.fromEntries(Object.keys(d.features).map(k => [k, h('input', { type: 'checkbox', checked: !!cfg.features[k] })]));
  const sends = Object.fromEntries(Object.keys(d.send_options).map(k => [k, h('input', { type: 'checkbox', checked: !!cfg[k] })]));
  const timeout = h('input', { class: 'input', id: 'ai-timeout', type: 'number', min: 10, max: 600, value: cfg.timeout_s });
  const maxRec = h('input', { class: 'input', id: 'ai-max', type: 'number', min: 3, max: 30, value: cfg.max_records });
  // a model for each job, with a fallback
  const provOpts = (sel, none) => [h('option', { value: '', selected: !sel }, none), d.providers.map(p => h('option', { value: p.id, selected: sel === p.id }, p.name))];
  const jobs = Object.fromEntries(Object.keys(d.features).map(k => {
    const j = cfg.jobs[k] || {};
    return [k, {
      model: h('input', { class: 'input', value: j.model || '', placeholder: 'provider\'s own', 'aria-label': `Model for ${FEATURE_NAME[k] || k}` }),
      fallback_provider: h('select', { class: 'input', 'aria-label': `Fallback provider for ${FEATURE_NAME[k] || k}` }, provOpts(j.fallback_provider, 'same provider')),
      fallback_model: h('input', { class: 'input', value: j.fallback_model || '', placeholder: 'no fallback', 'aria-label': `Fallback model for ${FEATURE_NAME[k] || k}` }),
      timeout_s: h('input', { class: 'input', type: 'number', min: 0, max: 600, value: j.timeout_s || 0, style: { width: '90px' }, 'aria-label': `Time-out for ${FEATURE_NAME[k] || k} (0: the general one)` }),
    }];
  }));
  const budget = h('input', { class: 'input', id: 'ai-budget', type: 'number', min: 0, step: '0.01', value: cfg.monthly_budget || 0 });
  const stopAt = h('input', { type: 'checkbox', checked: !!cfg.stop_at_budget });
  const prices = h('textarea', { class: 'input mono', id: 'ai-prices', rows: 3, placeholder: 'model-name  price-in  price-out',
    value: Object.entries(cfg.prices || {}).map(([m, p]) => `${m}  ${p.in}  ${p.out}`).join('\n') });
  const localProv = h('select', { class: 'input', id: 'ai-local', style: { maxWidth: '420px' } }, provOpts(cfg.local_provider, 'None: local-only records are skipped by AI'));
  const lo = cfg.local_only || {};
  const loRestricted = h('input', { type: 'checkbox', checked: lo.restricted !== false });
  const loCats = h('input', { class: 'input', id: 'ai-lo-cats', value: (lo.categories || []).join(', '), placeholder: 'for example HSG, GBX' });
  const loCust = h('input', { class: 'input', id: 'ai-lo-cust', value: (lo.customers || []).join(', '), placeholder: 'customer names as on the parts' });
  const folders = await get('/api/folders').then(r => r.folders).catch(() => []);
  const loFolders = h('select', { class: 'input', id: 'ai-lo-folders', multiple: true, size: Math.min(6, Math.max(3, folders.length)) },
    folders.map(f => h('option', { value: f.id, selected: (lo.folders || []).includes(f.id) }, f.name)));
  const list = v => v.split(',').map(x => x.trim()).filter(Boolean);
  const readPrices = () => Object.fromEntries(prices.value.split('\n').map(l => l.trim().split(/\s+/)).filter(p => p[0]).map(p => [p[0], { in: p[1] || 0, out: p[2] || 0 }]));
  const save = async () => {
    const r = await put('/api/ai/settings', {
      provider: provider.value, features: Object.fromEntries(Object.entries(feats).map(([k, c]) => [k, c.checked])),
      ...Object.fromEntries(Object.entries(sends).map(([k, c]) => [k, c.checked])), timeout_s: timeout.value, max_records: maxRec.value,
      jobs: Object.fromEntries(Object.entries(jobs).map(([k, j]) => [k, { model: j.model.value, fallback_provider: j.fallback_provider.value, fallback_model: j.fallback_model.value, timeout_s: Number(j.timeout_s.value) || 0 }])),
      monthly_budget: budget.value, stop_at_budget: stopAt.checked, prices: readPrices(), local_provider: localProv.value,
      local_only: { restricted: loRestricted.checked, categories: list(loCats.value), customers: list(loCust.value), folders: [...loFolders.selectedOptions].map(o => Number(o.value)) },
    });
    aiStatus(true);
    drawUsage(r.usage);
    return r;
  };
  const usageBox = h('div');
  const money = v => (v == null ? '—' : Number(v).toFixed(2));
  const drawUsage = u => {
    if (!u) return;
    const acc = Object.fromEntries((u.acceptance || []).map(a => [a.feature, a]));
    mount(usageBox, h('div', { class: 'col', style: { gap: '8px' } },
      h('div', { class: 'small' }, `${u.month}: spent ${money(u.spent)}`, u.budget ? ` of ${money(u.budget)} (${u.percent}%)` : ' (no budget set)',
        u.budget && u.percent >= 100 ? h('span', { class: 'sev-error' }, u.stop_at_budget ? ' · used up: AI extras are paused until next month' : ' · used up') : null),
      u.jobs.length || u.acceptance.length ? h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl ai-usage' },
        h('thead', null, h('tr', null, ['Job', 'Calls', 'Failed', 'Fallback', 'Tokens in', 'Tokens out', 'Average time', 'Cost', 'Accepted'].map(t => h('th', null, t)))),
        h('tbody', null, [...new Set([...u.jobs.map(j => j.job), ...Object.keys(acc)])].map(k => {
          const j = u.jobs.find(x => x.job === k) || {};
          const a = acc[k];
          return h('tr', null, h('td', null, FEATURE_NAME[k] || k), ...[j.calls, j.failed, j.fallbacks, j.prompt_tokens, j.completion_tokens].map(v => h('td', { class: 'num' }, v ?? 0)),
            h('td', { class: 'num' }, j.avg_ms ? `${(j.avg_ms / 1000).toFixed(1)} s` : '—'), h('td', { class: 'num' }, money(j.cost)),
            h('td', { class: 'num', title: a ? `${a.accepted} accepted (${a.edited} with changes), ${a.dismissed} dismissed` : 'Nothing decided yet' }, a && a.rate != null ? `${a.rate}%` : '—'));
        })))) : h('div', { class: 'empty' }, 'No AI calls this month.'),
      u.months.length > 1 ? h('div', { class: 'small muted' }, 'Earlier months: ', u.months.slice(1).map(m => `${m.month} ${money(m.cost)} (${m.calls} calls)`).join(' · ')) : null));
  };
  const reqBox = h('div');
  const drawRequests = rows => mount(reqBox, rows.length ? h('div', { style: { overflow: 'auto', maxHeight: '50vh' } }, h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['When', 'Who', 'Feature', 'About', 'Result', 'Decision', ''].map(t => h('th', null, t)))),
    h('tbody', null, rows.map(r => h('tr', null,
      h('td', { class: 'nowrap small', title: fmtDate(r.created_at) }, ago(r.created_at)), h('td', { class: 'small' }, r.user),
      h('td', { class: 'small' }, FEATURE_NAME[r.feature] || r.feature),
      h('td', { class: 'small' }, SUBJECT_LINK[r.subject] && r.subject_id ? h('a', { href: SUBJECT_LINK[r.subject](r.subject_id) }, `${r.subject.replace('_', ' ')} #${r.subject_id}`) : (r.subject === 'question' ? 'a question' : r.subject)),
      h('td', { class: 'small' }, h('span', { class: `chip st-${r.status}` }, r.status), r.status === 'failed' && r.message ? h('div', { class: 'small sev-error' }, r.message) : null),
      h('td', { class: 'small' }, r.decision || '—'),
      h('td', null, r.provider ? h('button', { class: 'btn sm ghost', onclick: () => showSent(r.id) }, 'What was sent') : h('span', { class: 'small muted', title: 'Answered by Kosha without asking the provider' }, 'nothing sent')))))))
    : h('div', { class: 'empty' }, 'No AI requests yet.'));
  mount(body, h('div', { class: 'col', style: { gap: '22px', maxWidth: '1100px' } },
    h('div', { class: 'banner info small' }, icon('info'), h('div', null,
      'Kosha works fully without AI. When you choose a provider and switch features on, people get ', h('b', null, 'suggestions'),
      ': a category or a cleaner description for a part, a draft change log, answers to questions with links to the records. Nothing is saved until a person accepts it, and every accepted suggestion is in the audit log. ',
      'For a fully local set-up, run a model on a server in your own network; see docs/ai.md.')),
    h('section', { class: 'col', style: { gap: '10px' } }, h('h2', null, 'Provider'),
      h('div', { class: 'field' }, h('label', { for: 'ai-provider' }, 'AI provider'), provider, provInfo,
        h('span', { class: 'hint' }, 'Providers are add-ins of kind "AI provider". Set their address, model and key under Admin › Add-ins.'))),
    h('section', { class: 'col', style: { gap: '6px' } }, h('h2', null, 'Features'),
      Object.entries(d.features).map(([k, label]) => h('label', { class: 'check' }, feats[k], label))),
    h('section', { class: 'col', style: { gap: '6px' } }, h('h2', null, 'What may be sent'),
      h('p', { class: 'small muted', style: { margin: 0 } }, 'Always sent when a feature is on: part numbers, descriptions, file names, change-order numbers, titles and reasons, the measured change lists, the engineer\'s note and the question asked, and for Ask Kosha the matching records the person may open. Also send:'),
      Object.entries(d.send_options).map(([k, label]) => h('label', { class: 'check' }, sends[k], label)),
      h('p', { class: 'small muted', style: { margin: 0 } }, 'File contents (drawings, models, documents) are never sent, except title-block text when you tick it above.')),
    h('section', { class: 'form-grid' },
      h('div', { class: 'field' }, h('label', { for: 'ai-timeout' }, 'Time-out per request (seconds)'), timeout),
      h('div', { class: 'field' }, h('label', { for: 'ai-max' }, 'Records sent with a question (at most)'), maxRec)),
    h('section', { class: 'col', style: { gap: '8px' } }, h('h2', null, 'A model for each job'),
      h('p', { class: 'small muted', style: { margin: 0 } }, 'Model names change often: test a new one, then switch here without a software update. Leave a model empty to use the one in the provider\'s settings. When the main model cannot be reached, the fallback answers; an answer that does not fit is asked for once more, then dropped.'),
      h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Job', 'Model', 'Fallback provider', 'Fallback model', 'Time-out (s)'].map(t => h('th', null, t)))),
        h('tbody', null, Object.entries(jobs).map(([k, j]) => h('tr', null, h('td', { class: 'small' }, FEATURE_NAME[k] || k), h('td', null, j.model), h('td', null, j.fallback_provider), h('td', null, j.fallback_model), h('td', null, j.timeout_s))))))),
    h('section', { class: 'col', style: { gap: '8px' } }, h('h2', null, 'Budget'),
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { for: 'ai-budget' }, 'Monthly budget (0: none)'), budget,
          h('span', { class: 'hint' }, 'In the currency of your prices. Administrators are told at 50%, 80% and 100%.')),
        h('div', { class: 'field' }, h('label', { for: 'ai-prices' }, 'Prices per million tokens'), prices,
          h('span', { class: 'hint' }, 'One model per line: name, price for what is sent, price for the answer.'))),
      h('label', { class: 'check' }, stopAt, 'Pause the AI extras when the budget is used up (everything else keeps working)')),
    h('section', { class: 'col', style: { gap: '8px' } }, h('h2', null, 'Local only'),
      h('p', { class: 'small muted', style: { margin: 0 } }, 'Records marked local only go only to an AI server inside the company (for example a model server on your own network). Without one, the AI assistant skips them.'),
      h('div', { class: 'field' }, h('label', { for: 'ai-local' }, 'AI server inside the company'), localProv),
      h('label', { class: 'check' }, loRestricted, 'Restricted parts are local only'),
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { for: 'ai-lo-cats' }, 'Categories (comma between them)'), loCats),
        h('div', { class: 'field' }, h('label', { for: 'ai-lo-cust' }, 'Customers (comma between them)'), loCust),
        h('div', { class: 'field' }, h('label', { for: 'ai-lo-folders' }, 'Folders'), loFolders, h('span', { class: 'hint' }, 'Hold Ctrl to choose more than one; subfolders are included.')))),
    h('div', { class: 'row wrap' },
      h('button', { class: 'btn primary', onclick: async () => { try { await save(); toast('AI settings saved', 'ok'); } catch (e) { fail(e); } } }, 'Save settings'),
      h('button', { class: 'btn', onclick: async () => { try { await save(); mount(result, working('Testing the provider…')); say(await post('/api/ai/test')); } catch (e) { fail(e); } } }, icon('play', 14), 'Test the provider')),
    result,
    h('section', { class: 'col', style: { gap: '10px' } }, h('h2', null, 'Use and cost this month'), usageBox),
    h('section', { class: 'col', style: { gap: '10px' } }, h('div', { class: 'row' }, h('h2', null, 'Recent requests'), h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm', onclick: async () => { try { drawRequests((await get('/api/ai/settings')).requests); } catch (e) { fail(e); } } }, icon('refresh', 14), 'Refresh')),
    h('p', { class: 'small muted', style: { margin: 0 } }, 'Who asked for what, and what became of it. Answers to questions stay private to the person who asked.'), reqBox)));
  drawRequests(d.requests);
  drawUsage(d.usage);
}

async function showSent(id) {
  try {
    const r = await get(`/api/ai/requests/${id}/sent`);
    dialog({ title: `What Kosha sent for request #${id}`, wide: true,
      body: h('div', { class: 'col' }, h('p', { class: 'small muted', style: { margin: 0 } }, `To ${r.provider}. This is the complete request.`),
        (r.sent.messages || []).map(m => h('div', null, h('div', { class: 'small', style: { fontWeight: 600 } }, m.role === 'system' ? 'Instructions' : m.role === 'user' ? 'Request' : m.role),
          h('pre', { class: 'ai-pre' }, m.content), (m.images || []).map(i => h('div', { class: 'small muted' }, `With one picture: ${i} (the title-block corner only).`)))),
        r.sent.local_only ? h('div', { class: 'small' }, 'Marked local only: sent to the AI server inside the company.') : null),
      actions: [{ label: 'Close' }] });
  } catch (e) { fail(e); }
}
