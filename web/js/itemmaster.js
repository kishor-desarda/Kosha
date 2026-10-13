// SPDX-License-Identifier: AGPL-3.0-or-later
// Item page sections for the item master: product family, spare flags, customer part, OEM cross-references and
// allowing a locked customer part to be reused.
import { h, icon, get, post, patch, del, toast, fail, dialog, confirmDlg, can, fmtDate } from './core.js';

const DISABLED_WHY = 'Only an approver can change who a part belongs to and whether it may be reused.';

export function itemBadges(r) {
  const it = r.item;
  const out = [];
  if (it.family) out.push(h('span', { class: 'chip std', title: r.family ? r.family.name : '' }, `Family ${it.family}`));
  if (it.spare) out.push(h('span', { class: 'chip std' }, 'Spare'));
  if (it.wear_part) out.push(h('span', { class: 'chip std' }, 'Wear part'));
  if (it.recommended_spare) out.push(h('span', { class: 'chip std' }, 'Recommended spare'));
  if (it.restricted) out.push(h('span', { class: 'chip out', title: `Restricted to ${it.customer}` }, 'Restricted'));
  if (it.no_reuse) out.push(h('span', { class: 'chip out', title: 'Needs an admin to be used in another customer\'s assembly' }, 'Do not reuse'));
  return out;
}

export function masterPanel(r, reload) {
  const it = r.item;
  const editable = can('engineer');
  const guarded = can('approver');
  const f = {};
  const check = (key, label, allowed = editable, why = 'Viewers cannot change parts.') => {
    f[key] = h('input', { type: 'checkbox', id: `im-${key}`, checked: !!it[key], disabled: !allowed });
    return h('label', { class: 'check', title: allowed ? '' : why }, f[key], label);
  };
  const text = (key, label, extra = {}, allowed = editable, why = 'Viewers cannot change parts.') => {
    f[key] = h('input', { class: 'input', id: `im-${key}`, value: it[key] || '', disabled: !allowed, title: allowed ? '' : why, ...extra });
    return h('div', { class: 'field' }, h('label', { for: f[key].id }, label), f[key]);
  };
  f.family = h('select', { class: 'input', id: 'im-family', disabled: !editable },
    h('option', { value: '' }, r.families.length ? '— none —' : 'No families yet (Admin › Packs)'),
    r.families.map(x => h('option', { value: x.code, selected: it.family === x.code }, `${x.code} · ${x.name}`)));
  return h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, 'Family, spares and customer')),
    h('div', { class: 'panel-b col', style: { gap: '12px' } },
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { for: 'im-family' }, 'Product family'), f.family),
        text('hsn', 'HSN code', { placeholder: 'For example 84833000', inputmode: 'numeric' }),
        text('customer', 'Customer', { placeholder: 'Only for a part made for one customer' }, guarded, DISABLED_WHY),
        text('customer_part_number', 'Customer\'s part number')),
      h('div', { class: 'row', style: { flexWrap: 'wrap', gap: '4px 18px' } },
        check('spare', 'Sold as a spare'), check('wear_part', 'Wear part'), check('recommended_spare', 'Recommended spare'),
        check('restricted', 'Restricted to the customer\'s team', guarded, DISABLED_WHY),
        check('no_reuse', 'Do not reuse for other customers', guarded, DISABLED_WHY)),
      editable ? h('div', null, h('button', { class: 'btn primary', onclick: async () => {
        const body = {};
        for (const [k, el] of Object.entries(f)) {
          const v = el.type === 'checkbox' ? el.checked : el.value;
          if (el.disabled) continue;
          body[k] = v;
        }
        try { await patch(`/api/items/${it.id}`, body); toast('Saved', 'ok'); reload(); } catch (e) { fail(e); }
      } }, 'Save')) : null,
      r.reuse_allowed.length ? h('div', { class: 'small' }, h('b', null, 'Reuse allowed in: '),
        r.reuse_allowed.map(a => h('div', null, h('a', { href: `#/item/${a.parent_item_id}` }, a.parent_part_number),
          ` by ${a.approved_by_name}, ${fmtDate(a.approved_at)}: ${a.reason}`))) : null,
      it.no_reuse && can('data_steward') ? h('div', null, h('button', { class: 'btn sm', onclick: () => allowReuse(it, reload) }, 'Allow reuse in an assembly…')) : null));
}

function allowReuse(it, reload) {
  const parent = h('input', { class: 'input mono', id: 'ar-parent', placeholder: 'Assembly part number' });
  const reason = h('textarea', { class: 'input', id: 'ar-reason', rows: 3, placeholder: 'Why this part may be used there (kept in the audit log)' });
  dialog({
    title: `Allow ${it.part_number} in another assembly`,
    body: h('div', { class: 'col', style: { gap: '12px' } },
      h('p', { style: { margin: 0 } }, `${it.part_number} is made for ${it.customer || 'one customer'} and locked against reuse.`),
      h('div', { class: 'field' }, h('label', { for: 'ar-parent' }, 'Assembly'), parent),
      h('div', { class: 'field' }, h('label', { for: 'ar-reason' }, 'Reason'), reason)),
    actions: [{ label: 'Cancel' }, { label: 'Allow', primary: true, onClick: async () => {
      const x = await post(`/api/items/${it.id}/allow-reuse`, { parent_part_number: parent.value, reason: reason.value });
      toast(x.message, 'ok'); reload();
    } }],
  });
}

export function oemPanel(r, reload) {
  const it = r.item;
  const refs = r.oem_refs;
  return h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', null, `OEM cross-references (${refs.length})`), h('span', { class: 'spacer' }),
    can('engineer') ? h('button', { class: 'btn sm', onclick: () => addRef(it, reload).catch(fail) }, icon('plus', 14), 'Add') : null),
  refs.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Maker', 'Model', 'OEM part number', 'Fit', ''].map(x => h('th', null, x)))),
    h('tbody', null, refs.map(x => h('tr', null,
      h('td', { title: x.oem_name || '' }, x.oem), h('td', null, x.oem_model || '—'), h('td', { class: 'mono' }, x.oem_part_number || '—'),
      h('td', null, { direct: 'Direct', modified: 'With modification' }[x.fit] || '—'),
      h('td', { class: 'num' }, can('engineer') ? h('button', { class: 'btn ghost sm', 'aria-label': `Remove ${x.oem} ${x.oem_part_number}`, onclick: async () => {
        if (!await confirmDlg('Remove cross-reference', `Remove ${x.oem} ${x.oem_part_number || x.oem_model} from ${it.part_number}? It stays in the audit log.`, 'Remove', true)) return;
        try { await del(`/api/oem-refs/${x.id}`); reload(); } catch (e) { fail(e); }
      } }, icon('x', 14)) : null)))))
    : h('div', { class: 'empty' }, 'No OEM numbers. Add the other maker\'s part number so people typing it find this part.'));
}

async function addRef(it, reload) {
  const o = await get('/api/oems');
  const oem = h('select', { class: 'input', id: 'or-oem' }, o.oems.length ? o.oems.map(x => h('option', { value: x.code }, `${x.code} · ${x.name}`))
    : h('option', { value: '' }, 'No makers yet'));
  const newCode = h('input', { class: 'input mono', id: 'or-code', placeholder: 'ABC', maxlength: 4 });
  const newName = h('input', { class: 'input', id: 'or-name', placeholder: 'Maker\'s name' });
  const model = h('input', { class: 'input', id: 'or-model' });
  const pn = h('input', { class: 'input mono', id: 'or-pn' });
  const opts = (id, m) => h('select', { class: 'input', id }, h('option', { value: '' }, '—'), Object.entries(m).map(([v, l]) => h('option', { value: v }, l)));
  const source = opts('or-source', o.sources);
  const fit = opts('or-fit', o.fits);
  const fld = (id, label, el) => h('div', { class: 'field' }, h('label', { for: id }, label), el);
  dialog({
    title: `OEM number for ${it.part_number}`,
    body: h('div', { class: 'col', style: { gap: '12px' } },
      h('div', { class: 'form-grid' }, fld('or-oem', 'Maker', oem), fld('or-model', 'Their machine model', model),
        fld('or-pn', 'Their part number', pn), fld('or-source', 'How the design came about', source), fld('or-fit', 'Interchangeable', fit)),
      o.can_add ? h('details', null, h('summary', null, 'Maker not in the list? Add it'),
        h('div', { class: 'form-grid', style: { marginTop: '8px' } }, fld('or-code', 'Code (2 to 4 letters)', newCode), fld('or-name', 'Name', newName)))
        : h('p', { class: 'small muted', style: { margin: 0 } }, 'Maker not in the list? Ask an approver to add it.')),
    actions: [{ label: 'Cancel' }, { label: 'Add', primary: true, onClick: async () => {
      let code = oem.value;
      if (newCode.value.trim()) code = (await post('/api/oems', { code: newCode.value, name: newName.value })).oem.code;
      await post(`/api/items/${it.id}/oem-refs`, { oem: code, oem_model: model.value, oem_part_number: pn.value, source: source.value, fit: fit.value });
      toast('Added', 'ok'); reload();
    } }],
  });
}
