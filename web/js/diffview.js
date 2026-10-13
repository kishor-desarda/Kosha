// SPDX-License-Identifier: AGPL-3.0-or-later
// Measured change list: the "Changes" side list in the viewer overlay, and the change-log editor
// shown on the document page and in a change order.
import { h, mount, icon, get, post, put, toast, fail, ago } from './core.js';

const SEV = {
  major: ['Major', 'var(--err)', 'var(--err-bg)'],
  minor: ['Minor', 'var(--warn)', 'var(--warn-bg)'],
  info: ['Info', 'var(--obs)', 'var(--obs-bg)'],
};

export function sevChip(s) {
  const [label, ink, bg] = SEV[s] || SEV.info;
  return h('span', { class: 'chip', style: { color: ink, background: bg, flex: 'none' } }, label);
}

// One line per change; changes with a zone are buttons that call onPick(change).
export function changeList(changes, onPick) {
  if (!changes.length) return h('div', { class: 'muted small', style: { padding: '6px 0' } }, 'No measured differences.');
  return h('ul', { style: { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '2px' } },
    changes.map(c => {
      const body = [sevChip(c.severity), h('span', { style: { flex: 1, minWidth: 0, overflowWrap: 'anywhere' } }, c.text)];
      const zoned = onPick && c.zone;
      return h('li', null, zoned
        ? h('button', { class: 'btn ghost sm', style: { width: '100%', justifyContent: 'flex-start', whiteSpace: 'normal', textAlign: 'left', fontWeight: 400, minHeight: '32px', gap: '8px' },
          title: 'Zoom to this change', onclick: e => { e.currentTarget.closest('ul').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', 'false')); e.currentTarget.setAttribute('aria-pressed', 'true'); onPick(c); } }, body)
        : h('div', { class: 'row small', style: { padding: '6px 9px', alignItems: 'flex-start', gap: '8px' }, title: onPick ? 'This change has no position on the drawing' : null }, body));
    }));
}

function notesBlock(notes) {
  return notes && notes.length ? h('div', { class: 'col', style: { gap: '4px' } },
    notes.map(n => h('div', { class: 'small muted row', style: { alignItems: 'flex-start' } }, icon('info', 14), h('span', null, n)))) : null;
}

// ------------------------------------------------------------------ viewer side list
export function changesPanel(stage, docId, fromVersion, toVersion, onZone) {
  const list = h('div', { style: { overflow: 'auto', flex: 1, padding: '6px 8px 10px' } }, h('div', { class: 'small muted' }, 'Measuring the differences…'));
  const title = h('b', null, 'Changes');
  const panel = h('aside', {
    'aria-label': 'Measured changes', style: {
      position: 'absolute', left: '14px', top: '14px', bottom: '64px', width: 'min(340px, 42%)', display: 'flex', flexDirection: 'column',
      background: 'var(--panel)', color: 'var(--ink)', border: '1px solid var(--line)', borderRadius: '8px', boxShadow: 'var(--shadow)', zIndex: 2,
    },
  },
  h('div', { class: 'row', style: { padding: '8px 8px 6px 12px', borderBottom: '1px solid var(--line-2)' } }, title,
    h('span', { class: 'small muted' }, `v${fromVersion} → v${toVersion}`), h('span', { class: 'spacer' }),
    h('button', { class: 'btn ghost sm', 'aria-label': 'Hide the change list', title: 'Hide the change list', onclick: () => panel.remove() }, icon('x', 14))),
  list);
  ['mousedown', 'wheel'].forEach(ev => panel.addEventListener(ev, e => e.stopPropagation(), { passive: true }));
  stage.appendChild(panel);
  get(`/api/documents/${docId}/changes?from=v${fromVersion}&to=v${toVersion}`).then(r => {
    if (!panel.isConnected) return;
    title.textContent = `Changes (${r.total})`;
    mount(list, r.changes.some(c => c.zone) ? h('div', { class: 'small muted', style: { margin: '2px 0 6px' } }, 'Click a change to zoom to it.') : null,
      changeList(r.changes, c => onZone(c.zone)), notesBlock(r.notes));
  }).catch(e => { if (panel.isConnected) mount(list, h('div', { class: 'small', style: { color: 'var(--err)' } }, e.message)); });
  return { remove: () => panel.remove() };
}

// ------------------------------------------------------------------ change-log editor
export function changeLogTab(c, d) {
  const box = h('div');
  c.appendChild(box);
  editor(box, { base: `/api/documents/${d.id}/changelog`, revision: d.revision, perDocument: false });
}

export function changeLogSection(section, co) {
  const box = h('div', { class: 'panel-b' });
  mount(section, h('div', { class: 'panel-h' }, h('h2', null, 'Change log')), box);
  editor(box, { base: `/api/changes/${co.id}/changelog`, perDocument: true });
}

async function editor(box, opts) {
  let rev = opts.revision || '';
  const load = async () => {
    mount(box, h('div', { class: 'muted small' }, 'Measuring the changes…'));
    let r;
    try { r = await get(`${opts.base}${rev && !opts.perDocument ? `?revision=${encodeURIComponent(rev)}` : ''}`); } catch (e) { mount(box, h('div', { class: 'banner err small' }, icon('warn'), h('div', null, e.message))); return; }
    draw(r);
  };

  const uid = opts.base.replace(/\W+/g, '-');
  const draw = r => {
    const ro = !r.can_edit.ok;
    const fields = {};
    const start = r.saved ? r.saved.sections : r.draft;
    const note = h('textarea', { class: 'input', id: `cl-note${uid}`, rows: 2, disabled: ro, placeholder: 'In one or two sentences: why is this change needed?', value: r.saved ? r.saved.note : '' });
    const sections = r.sections.map(s => {
      const ta = h('textarea', { class: 'input', id: `cl-${s.key}${uid}`, disabled: ro, rows: s.key === 'what' ? 8 : 4, value: start[s.key] || '' });
      fields[s.key] = ta;
      return h('div', { class: 'field' }, h('label', { for: ta.id }, s.label), ta);
    });
    const fill = sec => { for (const [k, ta] of Object.entries(fields)) ta.value = sec[k] || ''; };
    const buildDraft = async btn => {
      btn.disabled = true;
      try {
        const x = await post(`${opts.base}/draft`, { note: note.value, revision: rev || undefined });
        fill(x.draft); toast('Draft built from the measured changes and your note. Edit it, then save.', 'ok');
      } catch (e) { fail(e); } finally { btn.disabled = false; }
    };
    const save = async btn => {
      btn.disabled = true;
      try {
        const sec = {}; for (const [k, ta] of Object.entries(fields)) sec[k] = ta.value;
        const x = await put(opts.base, { note: note.value, sections: sec, revision: rev || undefined });
        toast('Change log saved', 'ok'); draw(x);
      } catch (e) { fail(e); } finally { btn.disabled = false; }
    };
    const why = r.can_edit.why;
    const draftBtn = h('button', { class: 'btn', disabled: ro, title: ro ? why : 'Rebuild the text from the measured changes and your note (replaces the text below)' }, icon('sparkle', 16), 'Build draft');
    draftBtn.addEventListener('click', () => buildDraft(draftBtn));
    const saveBtn = h('button', { class: 'btn primary', disabled: ro, title: ro ? why : 'Save this change log' }, icon('check', 16), 'Save change log');
    saveBtn.addEventListener('click', () => save(saveBtn));

    const status = r.saved
      ? h('div', { class: 'small muted' }, `Saved by ${r.saved.updated_by} ${ago(r.saved.updated_at)}${r.saved.source === 'edited' ? ' (edited by hand)' : ''}.`)
      : h('div', { class: 'small muted' }, 'Not saved yet. The text below is a draft built from the measured changes; nothing is invented.');
    const revSel = !opts.perDocument && r.revisions && r.revisions.length > 1
      ? h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Revision', onchange: e => { rev = e.target.value; load(); } },
        r.revisions.map(x => h('option', { value: x, selected: x === r.revision }, `Revision ${x}`)))
      : null;

    const facts = h('div', { class: 'col', style: { flex: '1 1 300px', minWidth: 0, gap: '12px' } },
      h('h3', null, 'Measured changes'),
      r.input.groups.map(g => h('div', { class: 'col', style: { gap: '6px' } },
        opts.perDocument || g.from ? h('div', { class: 'small' }, opts.perDocument ? h('a', { href: `#/doc/${g.document.id}` }, g.document.filename) : null,
          g.from && g.to ? h('span', { class: 'muted' }, `${opts.perDocument ? ' · ' : ''}${g.from.label} → ${g.to.label}`) : null) : null,
        changeList(g.changes || []), notesBlock(g.notes))));

    mount(box,
      h('div', { class: 'row wrap', style: { marginBottom: '12px' } }, revSel, status, h('span', { class: 'spacer' }), draftBtn, saveBtn),
      ro ? h('p', { class: 'muted small', style: { margin: '0 0 10px' } }, why) : null,
      r.saved && r.saved.stale ? h('div', { class: 'banner info small', style: { marginBottom: '12px' } }, icon('info'),
        h('div', null, 'The measured changes are different from when this change log was saved (a new version was checked in). Press ', h('b', null, 'Build draft'), ' to update the text, or keep yours.')) : null,
      h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '24px' } }, facts,
        h('div', { class: 'col', style: { flex: '2 1 440px', minWidth: 0, gap: '12px' } },
          h('div', { class: 'field' }, h('label', { for: note.id }, 'Your short note'), note,
            h('span', { class: 'hint' }, 'Used for the "Why" section when you press Build draft.')),
          sections)));
  };
  await load();
}
