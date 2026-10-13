// SPDX-License-Identifier: AGPL-3.0-or-later
// "New part number" dialog: number series and spec codes, with fields built from each scheme and a live preview.
import { h, mount, icon, get, post, toast, fail, dialog, debounce } from './core.js';

const changed = () => window.dispatchEvent(new Event('kosha:changed'));

/** A form control for one field definition from the server ({name, label, type, unit, choices, min, max, hint}). */
export function fieldControl(f, value, onChange) {
  const id = `nf-${f.name}`;
  let input;
  if (f.choices) {
    input = h('select', { class: 'input', id, onchange: () => onChange(input.value) },
      h('option', { value: '' }, f.type === 'choice' ? 'Choose…' : 'Choose a value…'),
      f.choices.map(c => h('option', { value: c.value, selected: String(value ?? '') === String(c.value) }, c.label)));
  } else {
    input = h('input', { class: `input ${f.type === 'text' ? 'mono' : ''}`, id, value: value ?? '', autocomplete: 'off',
      inputmode: f.type === 'int' ? 'numeric' : f.type === 'decimal' ? 'decimal' : null,
      oninput: () => onChange(input.value) });
  }
  const range = f.min !== undefined && f.max !== undefined ? `From ${f.min} to ${f.max}${f.unit ? ' ' + f.unit : ''}.`
    : f.min !== undefined ? `At least ${f.min}${f.unit ? ' ' + f.unit : ''}.` : f.max !== undefined ? `At most ${f.max}${f.unit ? ' ' + f.unit : ''}.` : '';
  const hint = [f.hint, range].filter(Boolean).join(' ');
  const err = h('span', { class: 'hint', role: 'alert', hidden: true, style: { color: 'var(--err)' } });
  const el = h('div', { class: 'field' }, h('label', { for: id }, f.label, f.unit && !f.choices ? ` (${f.unit})` : ''), input,
    hint ? h('span', { class: 'hint' }, hint) : null, err);
  return { el, input, setError: msg => { err.hidden = !msg; err.textContent = msg || ''; input.setAttribute('aria-invalid', msg ? 'true' : 'false'); } };
}

/**
 * Open the dialog. options: { scheme: 'spec:spur-gear', onDone(result) }.
 * Resolves when the dialog closes, with the allocation result or null.
 */
export async function newPartNumberDialog({ scheme: preselect, onDone } = {}) {
  let data;
  try { data = await get('/api/numbering/schemes'); } catch (e) { fail(e); return null; }
  const schemes = data.schemes;
  const values = {};
  const descs = {};
  const touched = new Set();      // "scheme/field": show a field's error only after the user has used it
  let current = schemes.find(s => s.id === preselect) || schemes.find(s => s.available !== false) || schemes[0];
  let last = null;
  let seq = 0;
  let result = null;

  const list = h('div', { class: 'col', role: 'radiogroup', 'aria-label': 'Number scheme', style: { gap: '2px', flex: '0 0 230px', maxHeight: '58vh', overflow: 'auto', paddingRight: '6px', borderRight: '1px solid var(--line-2)' } });
  const right = h('div', { class: 'col', style: { gap: '12px', flex: '1 1 320px', minWidth: 0 } });
  const preview = h('div', { class: 'panel', style: { background: 'var(--panel-2)' }, 'aria-live': 'polite' });
  const createBtn = h('button', { class: 'btn primary', type: 'button' }, icon('plus', 16), 'Create part number');
  const why = h('span', { class: 'small muted', style: { flex: 1 } });
  const foot = h('div', { class: 'row', style: { gap: '10px', marginTop: '4px' } }, why, createBtn);
  let controls = {};

  const groups = [['sequence', 'Numbers in a series'], ['spec', 'Spec codes (same values, same part)']];
  const drawList = () => mount(list, groups.map(([kind, title]) => {
    const items = schemes.filter(s => s.kind === kind);
    if (!items.length) return null;
    return [h('div', { class: 'tree-sec' }, title), items.map(s => h('button', {
      class: `tree-node ${s.id === current.id ? 'active' : ''}`, role: 'radio', 'aria-checked': String(s.id === current.id),
      style: { border: 0, background: s.id === current.id ? null : 'transparent', textAlign: 'left', width: '100%', height: 'auto', minHeight: '32px', whiteSpace: 'normal', padding: '6px 8px' },
      title: s.available === false ? s.why : s.hint || '',
      onclick: () => { current = s; drawList(); drawForm(); },
    }, h('span', { style: { flex: 1 } }, s.label, s.kind === 'spec' ? h('span', { class: 'muted small' }, ` · ${s.category_name}`) : null),
    s.available === false ? h('span', { class: 'cnt' }, 'not set up') : null))];
  }));
  if (!schemes.some(s => s.kind === 'spec')) {
    list.appendChild(h('div', { class: 'small muted', style: { padding: '8px' } }, 'No spec codes yet. An admin can install the General machinery pack under Admin › Packs.'));
  }

  const setWhy = (text, enabled) => {
    createBtn.disabled = !enabled;
    createBtn.title = enabled ? '' : text;
    why.textContent = enabled ? '' : text;
  };

  const drawPreview = r => {
    last = r;
    for (const [name, c] of Object.entries(controls)) c.setError(r && r.errors && touched.has(`${current.id}/${name}`) ? r.errors[name] : '');
    if (!r) { mount(preview); return; }
    if (!r.ok) {
      const filled = Object.values(values[current.id] || {}).some(v => String(v ?? '').trim());
      mount(preview, h('div', { class: 'panel-b' }, h('div', { class: 'small muted' }, 'Part number'),
        h('div', { class: 'mono', style: { fontSize: '20px', color: 'var(--muted)' } }, '—'),
        filled && r.errors && r.errors._ ? h('div', { class: 'small', style: { marginTop: '6px', color: 'var(--err)' } }, r.errors._) : null));
      setWhy(filled ? 'Fix the values marked in red first.' : 'Fill in the fields to get a part number.', false);
      return;
    }
    const ex = r.existing;
    mount(preview, h('div', { class: 'panel-b col', style: { gap: '6px' } },
      h('div', { class: 'small muted' }, r.provisional ? 'Next free number' : 'Spec code (part number)'),
      h('div', { class: 'mono', style: { fontSize: '22px', fontWeight: 700, wordBreak: 'break-all' } }, r.part_number),
      r.description ? h('div', null, r.description) : null,
      ex ? h('div', { class: 'banner ok small' }, icon('check'), h('div', null, h('a', { href: `#/item/${ex.id}` }, ex.part_number), ` already exists (${ex.description || 'no description'}). Kosha uses it instead of making a duplicate.`))
        : h('div', { class: 'small muted' }, r.message),
      !ex && r.near && r.near.length ? h('div', { class: 'col', style: { gap: '4px' } },
        h('div', { class: 'small' }, h('b', null, 'Similar parts that differ in one value'), ' (reuse one if it does the job):'),
        r.near.map(n => h('div', { class: 'small' }, h('a', { href: `#/item/${n.id}` }, n.part_number), ` ${n.description || ''} · ${n.differs}`))) : null));
    if (!data.can_allocate) setWhy('You need the engineer role to create part numbers.', false);
    else if (current.available === false) setWhy(current.why, false);
    else setWhy('', true);
    createBtn.lastChild.textContent = ex ? 'Use the existing part' : current.creates_item === false ? 'Reserve this number' : 'Create part number';
  };

  const runPreview = debounce(async () => {
    const mine = ++seq;
    try {
      const r = await post('/api/numbering/preview', { scheme: current.id, values: values[current.id] || {}, description: descs[current.id] || '' });
      if (mine === seq) drawPreview(r);
    } catch (e) { if (mine === seq) { drawPreview(null); fail(e); } }
  }, 200);

  const drawForm = () => {
    controls = {};
    const vals = values[current.id] = values[current.id] || {};
    const fields = current.fields.map(f => {
      const c = fieldControl(f, vals[f.name], v => { vals[f.name] = v; touched.add(`${current.id}/${f.name}`); runPreview(); });
      controls[f.name] = c;
      return c.el;
    });
    const desc = current.kind === 'sequence' ? h('input', { class: 'input', id: 'nf-desc', value: descs[current.id] || '', placeholder: 'e.g. AGITATOR SHAFT',
      oninput: e => { descs[current.id] = e.target.value; } }) : null;
    mount(right,
      h('div', null, h('h3', null, current.label), current.hint ? h('div', { class: 'small muted', style: { marginTop: '4px', lineHeight: 1.45 } }, current.hint) : null,
        current.kind === 'spec' ? h('div', { class: 'small muted mono', style: { marginTop: '4px' } }, current.code) : h('div', { class: 'small muted mono', style: { marginTop: '4px' } }, current.pattern)),
      current.available === false ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, current.why)) : null,
      fields.length ? h('div', { class: 'form-grid', style: { gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' } }, fields) : null,
      desc ? h('div', { class: 'field' }, h('label', { for: 'nf-desc' }, current.creates_item === false ? 'What is it for?' : 'Description'), desc,
        h('span', { class: 'hint' }, current.creates_item === false ? 'Kept with the number in the list of numbers handed out.' : 'Shown with the part everywhere in Kosha. You can change it later.')) : null,
      preview, foot);
    runPreview();
  };

  createBtn.addEventListener('click', async () => {
    if (!last || !last.ok) return;
    createBtn.disabled = true;
    try {
      const r = await post('/api/numbering/allocate', { scheme: current.id, values: values[current.id] || {}, description: descs[current.id] || '' });
      result = r;
      toast(r.message, 'ok');
      changed();
      showDone(r);
      if (onDone) onDone(r);
    } catch (e) {
      if (e.data && e.data.errors) { Object.keys(e.data.errors).forEach(k => touched.add(`${current.id}/${k}`)); drawPreview({ ok: false, errors: e.data.errors }); }
      fail(e);
      createBtn.disabled = false;
    }
  });

  const body = h('div', { class: 'row wrap', style: { alignItems: 'stretch', gap: '18px' } }, list, right);
  const showDone = r => {
    const copy = h('button', { class: 'btn', onclick: async () => { try { await navigator.clipboard.writeText(r.part_number); toast('Copied', 'ok'); } catch (e) { toast('Copy did not work in this browser; select the number instead.', 'err'); } } }, icon('copy', 16), 'Copy number');
    mount(body, h('div', { class: 'col', style: { gap: '14px', width: '100%' } },
      h('div', { class: 'banner ok' }, icon('check'), h('div', null, r.message)),
      r.warning ? h('div', { class: 'banner small' }, icon('warn'), h('div', null, r.warning)) : null,
      h('div', { class: 'mono', style: { fontSize: '26px', fontWeight: 700, userSelect: 'all' } }, r.part_number),
      r.item ? h('div', null, r.item.description) : null,
      h('div', { class: 'row wrap' }, copy,
        r.item ? h('a', { class: 'btn primary', href: `#/item/${r.item.id}`, onclick: () => dlg.close() }, icon('external', 16), 'Open the part') : null,
        h('button', { class: 'btn', onclick: () => { mount(body, list, right); drawList(); drawForm(); } }, icon('plus', 16), 'Make another'))));
  };

  let dlg;
  const done = new Promise(resolve => {
    dlg = dialog({ title: 'New part number', wide: true, body, actions: [{ label: 'Close' }], onClose: () => resolve(result) });
  });
  drawList();
  drawForm();
  return done;
}
