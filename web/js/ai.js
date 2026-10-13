// SPDX-License-Identifier: AGPL-3.0-or-later
// AI assistance (optional): suggestion panels for parts and files, the AI draft in the change-log editor,
// the "Ask Kosha" screen and Admin › AI. When AI is off, every function here adds nothing to the screen
// (Ask Kosha and Admin › AI say plainly that it is off). Suggestions are never saved without a click.
import { S, h, mount, icon, get, post, put, toast, fail, dialog, ago, fmtDate, can } from './core.js';

const OFF = { on: false, features: { classify: false, changelog: false, ask: false }, provider: '', why: '' };
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
    h('div', { class: 'row' }, btn, h('span', { class: 'spacer' }), h('a', { class: 'btn ghost sm', href: `#/search?q=${encodeURIComponent(params.q || '')}` }, icon('search', 14), 'Search instead')));
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
const FEATURE_NAME = { classify: 'Classification', changelog: 'Change-log draft', ask: 'Ask Kosha' };
const SUBJECT_LINK = { item: id => `#/item/${id}`, document: id => `#/doc/${id}`, change_order: id => `#/change/${id}` };

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
  const save = async () => {
    const r = await put('/api/ai/settings', {
      provider: provider.value, features: Object.fromEntries(Object.entries(feats).map(([k, c]) => [k, c.checked])),
      ...Object.fromEntries(Object.entries(sends).map(([k, c]) => [k, c.checked])), timeout_s: timeout.value, max_records: maxRec.value,
    });
    aiStatus(true);
    return r;
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
    h('div', { class: 'row wrap' },
      h('button', { class: 'btn primary', onclick: async () => { try { await save(); toast('AI settings saved', 'ok'); } catch (e) { fail(e); } } }, 'Save settings'),
      h('button', { class: 'btn', onclick: async () => { try { await save(); mount(result, working('Testing the provider…')); say(await post('/api/ai/test')); } catch (e) { fail(e); } } }, icon('play', 14), 'Test the provider')),
    result,
    h('section', { class: 'col', style: { gap: '10px' } }, h('div', { class: 'row' }, h('h2', null, 'Recent requests'), h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm', onclick: async () => { try { drawRequests((await get('/api/ai/settings')).requests); } catch (e) { fail(e); } } }, icon('refresh', 14), 'Refresh')),
    h('p', { class: 'small muted', style: { margin: 0 } }, 'Who asked for what, and what became of it. Answers to questions stay private to the person who asked.'), reqBox)));
  drawRequests(d.requests);
}

async function showSent(id) {
  try {
    const r = await get(`/api/ai/requests/${id}/sent`);
    dialog({ title: `What Kosha sent for request #${id}`, wide: true,
      body: h('div', { class: 'col' }, h('p', { class: 'small muted', style: { margin: 0 } }, `To ${r.provider}. This is the complete request.`),
        (r.sent.messages || []).map(m => h('div', null, h('div', { class: 'small', style: { fontWeight: 600 } }, m.role === 'system' ? 'Instructions' : m.role === 'user' ? 'Request' : m.role),
          h('pre', { class: 'ai-pre' }, m.content)))),
      actions: [{ label: 'Close' }] });
  } catch (e) { fail(e); }
}
