// SPDX-License-Identifier: AGPL-3.0-or-later
// Transmittals and vendor packages: send released files to a supplier or customer (dialog), and the record of what
// went to whom (#/transmittals).
import { S, h, mount, icon, get, post, toast, fail, dialog, confirmDlg, fmtDate, can } from './core.js';

export function sendDialog(docs, { projectId } = {}) {
  const kind = h('select', { class: 'input', id: 'tr-kind' }, h('option', { value: 'vendor_package' }, 'Vendor package (supplier, job work)'), h('option', { value: 'transmittal' }, 'Transmittal (customer, consultant)'));
  const who = h('input', { class: 'input', id: 'tr-who', placeholder: 'Person and company, for example Ravi, Example Cutting' });
  const purpose = h('input', { class: 'input', id: 'tr-purpose', placeholder: 'Quotation, manufacture, approval …' });
  const order = h('input', { class: 'input', id: 'tr-order', placeholder: 'PO-26-0107' });
  const days = h('input', { class: 'input', id: 'tr-days', type: 'number', min: 1, max: 90, value: 30, style: { maxWidth: '100px' } });
  const pw = h('input', { class: 'input', id: 'tr-pw', autocomplete: 'off', placeholder: 'Optional, at least 8 characters' });
  const related = h('input', { type: 'checkbox', checked: true });
  const viewOnly = h('input', { type: 'checkbox' });
  dialog({ title: `Send ${docs.length === 1 ? docs[0].filename : docs.length + ' files'}`, body: [
    h('p', { style: { marginTop: 0 } }, 'Kosha records which revision goes to whom and why, makes a numbered transmittal sheet, and sends a link that ends on a date. PDFs carry the stamp from the release and the recipient\'s name. If a newer revision is released while this is open, you are told and their page says so.'),
    h('div', { class: 'form-grid' },
      h('div', { class: 'field' }, h('label', { for: 'tr-kind' }, 'Kind'), kind),
      h('div', { class: 'field' }, h('label', { for: 'tr-who' }, 'To'), who),
      h('div', { class: 'field' }, h('label', { for: 'tr-purpose' }, 'Purpose'), purpose),
      h('div', { class: 'field' }, h('label', { for: 'tr-order' }, 'Order number (optional)'), order),
      h('div', { class: 'field' }, h('label', { for: 'tr-days' }, 'Link works for (days)'), days),
      h('div', { class: 'field' }, h('label', { for: 'tr-pw' }, 'Password'), pw)),
    h('label', { class: 'check' }, related, 'Add the part\'s other released files of the same revision (DXF, STEP …)'),
    h('label', { class: 'check' }, viewOnly, 'View only (no download)')],
  actions: [{ label: 'Cancel' }, { label: 'Send', primary: true, onClick: async () => {
    const r = await post('/api/transmittals', { kind: kind.value, document_ids: docs.map(d => d.id), recipient: who.value, purpose: purpose.value, order_ref: order.value,
      days: Number(days.value), password: pw.value, view_only: viewOnly.checked, include_related: related.checked, project_id: projectId || undefined });
    const t = r.transmittal;
    const url = `${location.origin}/#/share/${t.token}`;
    const box = h('input', { class: 'input mono', readonly: true, value: url, 'aria-label': 'Link' });
    dialog({ title: `${t.kind_label} ${t.number}`, body: [
      h('p', { style: { marginTop: 0 } }, `${t.files.length} file(s) for ${t.recipient}. Send them this link; it works until ${t.expires_on}. Kosha shows it only now.`),
      h('div', { class: 'row' }, box, h('button', { class: 'btn', onclick: async () => { box.select(); try { await navigator.clipboard.writeText(url); toast('Copied', 'ok'); } catch (e) { toast('Select the link and copy it with Ctrl+C.'); } } }, 'Copy')),
      h('p', null, h('a', { href: `/api/transmittals/${t.id}/sheet?access_token=${encodeURIComponent(S.token)}` }, 'Download the transmittal sheet'), ' · ', h('a', { href: '#/transmittals' }, 'All transmittals'))],
    actions: [{ label: 'Done', primary: true }] });
  } }] });
}

const LINK = { active: ['Link active', 'st-released'], ended: ['Link ended', 'st-obsolete'], withdrawn: ['Link withdrawn', 'st-obsolete'], none: ['No link', 'st-obsolete'] };

export async function renderTransmittals(container, _m, params, { setTitle }) {
  setTitle('Transmittals');
  const q = h('input', { class: 'input', type: 'search', value: params.q || '', placeholder: 'Number, recipient, order or purpose', 'aria-label': 'Find transmittals', style: { maxWidth: '360px' } });
  const tbody = h('tbody');
  const draw = async () => {
    const r = await get(`/api/transmittals?q=${encodeURIComponent(q.value)}`);
    mount(tbody, r.transmittals.length ? r.transmittals.map(t => h('tr', null,
      h('td', { class: 'mono nowrap' }, t.number, h('div', { class: 'small muted' }, t.kind_label)),
      h('td', null, t.recipient, t.purpose ? h('div', { class: 'small muted' }, t.purpose) : null),
      h('td', null, t.order_ref || (t.project_code ? h('span', { class: 'mono' }, t.project_code) : '—')),
      h('td', { class: 'small' }, t.files.map(f => h('div', null, `${f.filename} (rev ${f.revision})`)),
        t.superseded.map(x => h('div', { class: 'sev-warning' }, `${x.filename}: rev ${x.new_revision} released since`))),
      h('td', { class: 'nowrap small' }, `${fmtDate(t.created_at, false)} by ${t.created_by_name}`),
      h('td', null, h('span', { class: `chip ${LINK[t.link][1]}` }, LINK[t.link][0]), h('div', { class: 'small muted' }, `${t.opened} opened · ${t.downloads} downloads`)),
      h('td', { class: 'nowrap' },
        h('a', { class: 'btn sm', href: `/api/transmittals/${t.id}/sheet?access_token=${encodeURIComponent(S.token)}` }, 'Sheet'),
        t.status === 'open' && can('engineer') ? h('button', { class: 'btn sm ghost', onclick: async () => {
          if (!await confirmDlg(`Close ${t.number}`, 'The order is done: the link stops working and no more superseded warnings are sent. The record is kept.', 'Close')) return;
          try { await post(`/api/transmittals/${t.id}/close`); draw(); } catch (e) { fail(e); }
        } }, 'Close') : h('span', { class: 'small muted' }, t.status === 'closed' ? ' Closed' : ''))))
      : h('tr', null, h('td', { colspan: 7 }, h('div', { class: 'empty' }, q.value ? 'Nothing matches.' : 'Nothing sent yet. Send released files from a file\'s page with "Send to vendor".'))));
  };
  let t;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => draw().catch(fail), 250); });
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Transmittals'),
      h('div', { class: 'sub' }, 'Which revisions went to which supplier or customer, when and why. You see what you sent; approvers see everything.'))),
    h('div', { style: { marginBottom: '12px' } }, q),
    h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Number', 'To', 'Order', 'Files', 'Sent', 'Link', ''].map(x => h('th', null, x)))), tbody))));
  await draw();
}
