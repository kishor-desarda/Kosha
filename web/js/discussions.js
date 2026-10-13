// SPDX-License-Identifier: AGPL-3.0-or-later
// Discussions: comment threads on any record, with replies, @mentions (they notify people), resolve and reopen,
// editing your own comment for 15 minutes and removing your own comment ("comment removed").
//
//   import { discussionPanel } from './discussions.js';
//   page.appendChild(discussionPanel('document', 12));     // item | document | change_order | deviation | bom
import { h, mount, clear, icon, get, post, patch, del, toast, fail, ago, fmtDate, confirmDlg, S } from './core.js';

const MENTION = /(^|[^\w@.\\])@([A-Za-z0-9_][\w.\-\\]*)/g;
let usersPromise = null;

export function activeUsers() {
  if (!usersPromise) usersPromise = get('/api/users').then(r => r.users.filter(u => u.active)).catch(() => { usersPromise = null; return []; });
  return usersPromise;
}

// ------------------------------------------------------------------ @mention input
// Wraps a textarea: typing "@" followed by letters lists matching people; Enter, Tab or a click inserts "@username ".
export function mentionInput(textarea) {
  const list = h('div', { class: 'mention-list', role: 'listbox', hidden: true });
  const wrap = h('div', { class: 'mention-wrap' }, textarea, list);
  let matches = [];
  let active = 0;
  let start = -1;
  const close = () => { list.hidden = true; matches = []; start = -1; };
  const pick = u => {
    if (start < 0) return;
    const pos = textarea.selectionStart;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(pos);
    const ins = `@${u.username} `;
    textarea.value = before + ins + after;
    const caret = before.length + ins.length;
    textarea.setSelectionRange(caret, caret);
    textarea.focus();
    textarea.dispatchEvent(new Event('input'));
    close();
  };
  const draw = () => {
    mount(list, matches.map((u, i) => h('button', {
      type: 'button', role: 'option', class: i === active ? 'on' : '', 'aria-selected': String(i === active),
      onmousedown: e => { e.preventDefault(); pick(u); },
    }, h('b', null, `@${u.username}`), h('span', { class: 'muted' }, u.full_name))));
    list.hidden = !matches.length;
  };
  textarea.addEventListener('input', async () => {
    const upto = textarea.value.slice(0, textarea.selectionStart);
    const m = upto.match(/(^|[^\w@.\\])@([\w.\-\\]*)$/);
    if (!m) { close(); return; }
    start = upto.length - m[2].length - 1;
    const q = m[2].toLowerCase();
    const users = await activeUsers();
    matches = users.filter(u => u.username.toLowerCase().startsWith(q) || u.full_name.toLowerCase().split(/\s+/).some(p => p.startsWith(q)))
      .filter(u => u.id !== S.user?.id).slice(0, 6);
    active = 0;
    draw();
  });
  textarea.addEventListener('keydown', e => {
    if (list.hidden || !matches.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % matches.length; draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + matches.length) % matches.length; draw(); }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(matches[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
  });
  textarea.addEventListener('blur', () => setTimeout(close, 150));
  return wrap;
}

// Comment text with known @mentions highlighted (text nodes only: nothing in a comment is ever run as HTML).
function bodyNodes(text, mentions) {
  const known = new Set((mentions || []).map(m => m.toLowerCase()));
  const out = [];
  let last = 0;
  for (const m of text.matchAll(MENTION)) {
    const name = m[2].replace(/[.-]+$/, '');
    if (!known.has(name.toLowerCase())) continue;
    const at = m.index + m[1].length;
    out.push(text.slice(last, at), h('span', { class: 'mention' }, `@${name}`));
    last = at + 1 + name.length;
  }
  out.push(text.slice(last));
  return out;
}

function threadFromHash() {
  const q = (location.hash.split('?')[1] || '');
  const t = new URLSearchParams(q).get('thread');
  return t ? Number(t) : null;
}

// ------------------------------------------------------------------ panel
export function discussionPanel(entityType, entityId, opts = {}) {
  const root = h('section', { class: 'panel disc', 'aria-label': 'Discussion' });
  const drafts = new Map();          // unsent text survives redraws: 'new', 'title', reply:<thread id>, edit:<comment id>
  const expanded = new Set();        // resolved threads the user opened
  let editing = null;
  let showOlder = false;
  let showForm = false;
  let data = null;
  let loadedAt = 0;
  const focus = threadFromHash();
  if (focus) expanded.add(focus);
  const base = `/api/discussions?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}`;

  const load = async () => {
    try { data = await get(base); loadedAt = Date.now(); draw(); } catch (e) {
      if (e.code === 'aborted') return;
      mount(root, h('div', { class: 'panel-h' }, h('h2', null, 'Discussion')), h('div', { class: 'panel-b' },
        h('div', { class: 'banner err small' }, icon('warn'), h('div', null, `Discussion could not be loaded: ${e.message}`))));
    }
  };
  const run = async (fn, okMsg) => {
    try { data = await fn(); if (okMsg) toast(okMsg, 'ok'); draw(); return true; } catch (e) { fail(e); return false; }
  };

  const textBox = (key, placeholder, label) => {
    const t = h('textarea', { class: 'input', placeholder, 'aria-label': label, rows: 3 });
    t.value = drafts.get(key) || '';
    t.addEventListener('input', () => drafts.set(key, t.value));
    return t;
  };

  const newThreadForm = () => {
    const title = h('input', { class: 'input', placeholder: 'Topic (optional), e.g. Hub seat fretting', 'aria-label': 'Topic', maxlength: 200 });
    title.value = drafts.get('title') || '';
    title.addEventListener('input', () => drafts.set('title', title.value));
    const body = textBox('new', 'Write your question or note. Type @ to mention someone; they get a notification.', 'New discussion');
    const send = h('button', { class: 'btn primary sm', type: 'button' }, 'Post');
    send.addEventListener('click', async () => {
      if (!body.value.trim()) { toast('Write something first.', 'err'); body.focus(); return; }
      send.disabled = true;
      const ok = await run(() => post('/api/discussions', { entity_type: entityType, entity_id: Number(entityId), title: title.value, body: body.value }), 'Posted');
      send.disabled = false;
      if (ok) { drafts.delete('new'); drafts.delete('title'); showForm = false; draw(); }
    });
    return h('div', { class: 'disc-new col' },
      data.current_revision ? h('div', { class: 'small muted' }, `This discussion will be about revision ${data.current_revision}.`) : null,
      title, mentionInput(body),
      h('div', { class: 'row' }, send, h('button', { class: 'btn ghost sm', type: 'button', onclick: () => { showForm = false; draw(); } }, 'Cancel')));
  };

  const commentEl = (t, c) => {
    const mine = c.user_id === S.user?.id;
    const tools = [];
    if (mine && !c.removed) {
      tools.push(h('button', { class: 'btn ghost sm', type: 'button', disabled: !c.can_edit.ok, title: c.can_edit.ok ? `You can edit this until ${fmtDate(c.editable_until)}` : c.can_edit.why,
        onclick: () => { editing = c.id; drafts.set(`edit:${c.id}`, c.body); draw(); } }, icon('edit', 14), 'Edit'));
      tools.push(h('button', { class: 'btn ghost sm', type: 'button', disabled: !c.can_remove.ok, title: c.can_remove.ok ? 'Remove your comment (it shows as "comment removed")' : c.can_remove.why,
        onclick: async () => {
          if (await confirmDlg('Remove comment', 'Remove your comment? It will show as "comment removed" for everyone.', 'Remove', true)) {
            run(() => del(`/api/discussions/comments/${c.id}`), 'Comment removed');
          }
        } }, icon('trash', 14), 'Remove'));
    }
    let body;
    if (editing === c.id) {
      const t2 = textBox(`edit:${c.id}`, '', 'Edit comment');
      const save = h('button', { class: 'btn primary sm', type: 'button' }, 'Save');
      save.addEventListener('click', async () => {
        save.disabled = true;
        const ok = await run(() => patch(`/api/discussions/comments/${c.id}`, { body: t2.value }), 'Comment updated');
        save.disabled = false;
        if (ok) { editing = null; drafts.delete(`edit:${c.id}`); draw(); }
      });
      body = h('div', { class: 'col' }, mentionInput(t2), h('div', { class: 'row' }, save,
        h('button', { class: 'btn ghost sm', type: 'button', onclick: () => { editing = null; drafts.delete(`edit:${c.id}`); draw(); } }, 'Cancel')));
    } else {
      body = c.removed ? h('div', { class: 'disc-body removed' }, 'comment removed') : h('div', { class: 'disc-body' }, bodyNodes(c.body, c.mentions));
    }
    return h('div', { class: 'disc-c' },
      h('div', { class: 'disc-meta' }, h('b', null, c.author), h('span', { class: 'muted', title: fmtDate(c.created_at) }, ago(c.created_at)),
        c.edited_at && !c.removed ? h('span', { class: 'muted', title: `Edited ${fmtDate(c.edited_at)}` }, '(edited)') : null,
        h('span', { class: 'spacer' }), tools),
      body);
  };

  const threadEl = t => {
    const resolved = t.status === 'resolved';
    const open = !resolved || expanded.has(t.id);
    const first = t.comments[0];
    const heading = t.title || (first && !first.removed ? first.body.split('\n')[0].slice(0, 90) : 'Discussion');
    const toggle = resolved ? h('button', { class: 'btn ghost sm', type: 'button', 'aria-expanded': String(open),
      onclick: () => { open ? expanded.delete(t.id) : expanded.add(t.id); draw(); } }, icon(open ? 'chevD' : 'chevR', 14), open ? 'Hide' : `Show ${t.comments.length} comment${t.comments.length === 1 ? '' : 's'}`) : null;
    const statusBtn = h('button', { class: 'btn sm', type: 'button', disabled: !t.can_resolve.ok, title: t.can_resolve.ok ? '' : t.can_resolve.why,
      onclick: () => run(() => post(`/api/discussions/${t.id}/${resolved ? 'reopen' : 'resolve'}`), resolved ? 'Reopened' : 'Marked as resolved') },
    icon(resolved ? 'refresh' : 'check', 14), resolved ? 'Reopen' : 'Resolve');
    let footer = null;
    if (open && !resolved) {
      const r = textBox(`reply:${t.id}`, 'Reply… (type @ to mention someone)', 'Reply');
      const send = h('button', { class: 'btn sm', type: 'button' }, 'Reply');
      send.addEventListener('click', async () => {
        if (!r.value.trim()) { toast('Write a reply first.', 'err'); r.focus(); return; }
        send.disabled = true;
        const ok = await run(() => post(`/api/discussions/${t.id}/comments`, { body: r.value }));
        send.disabled = false;
        if (ok) { drafts.delete(`reply:${t.id}`); draw(); }
      });
      footer = h('div', { class: 'disc-reply col' }, mentionInput(r), h('div', null, send));
    } else if (open && resolved) {
      footer = h('div', { class: 'small muted disc-reply' }, `Resolved by ${t.resolved_by_name || '?'} ${ago(t.resolved_at)}. Reopen it to reply.`);
    }
    return h('article', { class: `disc-t ${resolved ? 'resolved' : ''} ${focus === t.id ? 'focus' : ''}`, id: `thread-${t.id}` },
      h('div', { class: 'disc-th' },
        h('span', { class: `chip ${resolved ? 'st-resolved' : 'st-open'}` }, resolved ? 'Resolved' : 'Open'),
        t.revision ? h('span', { class: 'chip type', title: 'Revision this discussion is about' }, `Rev ${t.revision}`) : null,
        h('b', { class: 'ellipsis', style: { flex: 1, minWidth: 0 } }, heading),
        h('span', { class: 'small muted nowrap' }, `${t.author} · ${ago(t.created_at)}`), toggle, statusBtn),
      open ? h('div', { class: 'disc-cs' }, t.comments.map(c => commentEl(t, c))) : null,
      footer);
  };

  const draw = () => {
    if (!data) return;
    const all = data.threads;
    const cur = data.current_revision;
    const current = cur ? all.filter(t => !t.revision || t.revision === cur) : all;
    const older = cur ? all.filter(t => t.revision && t.revision !== cur) : [];
    const startBtn = h('button', { class: 'btn sm', type: 'button', disabled: showForm, title: showForm ? 'The form is open below.' : '',
      onclick: () => { showForm = true; draw(); const ta = root.querySelector('.disc-new textarea'); if (ta) ta.focus(); } }, icon('plus', 14), 'Start a discussion');
    mount(root,
      h('div', { class: 'panel-h' }, h('h2', null, 'Discussion'),
        data.open ? h('span', { class: 'chip st-open' }, `${data.open} open`) : null, h('span', { class: 'spacer' }), startBtn),
      h('div', { class: 'panel-b col disc-list' },
        showForm ? newThreadForm() : null,
        current.length ? current.map(threadEl) : (showForm ? null : h('div', { class: 'empty small' },
          older.length ? `No discussions about revision ${cur} yet.` : 'No discussions yet. Ask a question or leave a note for your colleagues.')),
        older.length ? h('div', { class: 'col' },
          h('button', { class: 'btn ghost sm', type: 'button', 'aria-expanded': String(showOlder), style: { alignSelf: 'flex-start' },
            onclick: () => { showOlder = !showOlder; draw(); } }, icon(showOlder ? 'chevD' : 'chevR', 14), `Earlier revisions (${older.length})`),
          showOlder ? older.map(threadEl) : null) : null));
    if (focus && !root.dataset.scrolled) {
      const el = root.querySelector(`#thread-${focus}`);
      if (el) { root.dataset.scrolled = '1'; setTimeout(() => el.scrollIntoView({ block: 'center' }), 60); }
    }
  };

  // Called when the page around the panel is drawn again; keeps unsent text. Reloads when the data is old.
  root.refresh = (force = false) => { if (force || Date.now() - loadedAt > 30000) load(); };
  mount(root, h('div', { class: 'panel-h' }, h('h2', null, 'Discussion')), h('div', { class: 'panel-b muted small' }, 'Loading…'));
  if (opts.preload !== false) load();
  void clear;
  return root;
}
