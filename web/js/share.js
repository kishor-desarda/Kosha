// SPDX-License-Identifier: AGPL-3.0-or-later
// Share links: making one (file page), the list of one's links (#/shares), and the page an outside recipient opens
// without an account (#/share/<token>).
import { h, mount, get, post, toast, fail, dialog, confirmDlg, ago, fmtSize, icon } from './core.js';

export function shareDialog(docs) {
  const who = h('input', { class: 'input', id: 'sh-who', placeholder: 'Ravi Kumar, Example Castings' });
  const days = h('input', { class: 'input', id: 'sh-days', type: 'number', min: 1, max: 90, value: 14, style: { maxWidth: '100px' } });
  const pw = h('input', { class: 'input', id: 'sh-pw', type: 'text', autocomplete: 'off', placeholder: 'Optional, at least 8 characters' });
  const viewOnly = h('input', { type: 'checkbox' });
  const note = h('input', { class: 'input', id: 'sh-note', placeholder: 'For the quotation of the mixer housing' });
  dialog({ title: `Share ${docs.length === 1 ? docs[0].filename : docs.length + ' files'} outside the company`, body: [
    h('p', { style: { marginTop: 0 } }, 'The recipient opens the link without a Kosha account. PDFs carry their name and the date as a watermark. You can withdraw the link at any time; every opening and download is recorded.'),
    h('div', { class: 'field' }, h('label', { for: 'sh-who' }, 'For (person and company)'), who),
    h('div', { class: 'row wrap' },
      h('div', { class: 'field' }, h('label', { for: 'sh-days' }, 'Works for (days)'), days),
      h('div', { class: 'field', style: { flex: 1 } }, h('label', { for: 'sh-pw' }, 'Password'), pw,
        h('span', { class: 'hint' }, 'Send it another way than the link, for example by phone.'))),
    h('label', { class: 'check' }, viewOnly, 'View only (no download)'),
    h('div', { class: 'field' }, h('label', { for: 'sh-note' }, 'Note for the recipient (optional)'), note)],
  actions: [{ label: 'Cancel' }, { label: 'Make link', primary: true, onClick: async () => {
    const r = await post('/api/shares', { document_ids: docs.map(d => d.id), recipient: who.value, days: Number(days.value), password: pw.value, view_only: viewOnly.checked, note: note.value });
    const url = `${location.origin}/#/share/${r.token}`;
    const box = h('input', { class: 'input mono', readonly: true, value: url, 'aria-label': 'Share link' });
    dialog({ title: 'Share link', body: [
      h('p', { style: { marginTop: 0 } }, `Send this link to ${r.recipient}. It works until ${r.expires_on}. Kosha shows it only now; your links are listed under My share links.`),
      h('div', { class: 'row' }, box, h('button', { class: 'btn', onclick: async () => { box.select(); try { await navigator.clipboard.writeText(url); toast('Copied', 'ok'); } catch (e) { toast('Select the link and copy it with Ctrl+C.'); } } }, 'Copy'))],
    actions: [{ label: 'Done', primary: true }] });
  } }] });
}

const STATE = { active: ['Active', 'st-released'], expired: ['Ended', 'st-obsolete'], withdrawn: ['Withdrawn', 'st-obsolete'] };

export async function renderShares(container, _m, _p, { setTitle }) {
  setTitle('My share links');
  const draw = async () => {
    const r = await get('/api/shares');
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Share links'),
        h('div', { class: 'sub' }, 'Released files shared with people outside the company. Make a link from a released file\'s page. Admins see every link.'))),
      h('div', { class: 'panel', style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['For', 'Files', 'Made', 'Ends', 'Opened', 'Downloads', 'State', ''].map(t => h('th', null, t)))),
        h('tbody', null, r.shares.length ? r.shares.map(s => {
          const [label, cls] = STATE[s.state];
          return h('tr', null, h('td', null, s.recipient, s.note ? h('div', { class: 'muted small' }, s.note) : null),
            h('td', { class: 'small' }, s.files.map(f => `${f.filename} (rev ${f.revision})`).join(', ')),
            h('td', { class: 'small' }, `${ago(s.created_at)} by ${s.created_by_name}`), h('td', null, s.expires_on),
            h('td', null, s.opened_count), h('td', null, s.view_only ? 'View only' : s.download_count),
            h('td', null, h('span', { class: `chip ${cls}` }, label), s.password ? h('span', { class: 'muted small' }, ' · password') : null),
            h('td', null, s.state !== 'active' ? null : h('button', { class: 'btn sm danger', onclick: async () => {
              if (!await confirmDlg('Withdraw link', `${s.recipient} can no longer open these files. Files already downloaded stay with them.`, 'Withdraw', true)) return;
              try { await post(`/api/shares/${s.id}/withdraw`); draw(); } catch (e) { fail(e); }
            } }, 'Withdraw')));
        }) : h('tr', null, h('td', { colspan: 8 }, h('div', { class: 'empty' }, 'No share links yet.'))))))));
  };
  await draw();
}

// The page a recipient outside the company opens: no sign-in, no Kosha menus.
export async function renderShareOutside(root, token) {
  document.title = 'Shared files — Kosha';
  let key = '';
  const page = h('div', { class: 'page-pad', style: { maxWidth: '920px', margin: '0 auto' } });
  mount(root, h('div', { class: 'share-page' }, page));
  const q = () => (key ? `?key=${encodeURIComponent(key)}` : '');
  const show = async () => {
    let r;
    try { r = await get(`/api/share/${token}${q()}`); } catch (e) {
      mount(page, h('div', { class: 'page-head' }, h('h1', null, 'Shared files')), h('div', { class: 'banner err' }, icon('warn'), h('span', null, e.message)));
      return;
    }
    if (r.password) {
      const pw = h('input', { class: 'input', type: 'password', id: 'share-pw', autocomplete: 'off' });
      const err = h('div', { class: 'banner err', hidden: true });
      const form = h('form', { class: 'col', style: { gap: '12px', maxWidth: '380px' } },
        h('h1', null, 'Shared files'), h('p', { class: 'muted' }, `For ${r.recipient}. Type the password the sender gave you.`), err,
        h('div', { class: 'field' }, h('label', { for: 'share-pw' }, 'Password'), pw), h('button', { class: 'btn primary', type: 'submit' }, 'Open'));
      form.addEventListener('submit', async e => {
        e.preventDefault();
        try { key = (await post(`/api/share/${token}/unlock`, { password: pw.value })).key; show(); } catch (ex) { err.hidden = false; mount(err, icon('warn'), h('span', null, ex.message)); }
      });
      mount(page, form);
      setTimeout(() => pw.focus(), 50);
      return;
    }
    mount(page,
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, r.company ? `Files from ${r.company}` : 'Shared files'),
        h('div', { class: 'sub' }, `Shared by ${r.from} with ${r.recipient}. The link works until ${r.expires_on}.${r.view_only ? ' View only.' : ''}`))),
      r.note ? h('div', { class: 'banner info' }, icon('info'), h('span', null, r.note)) : null,
      h('div', { class: 'panel', style: { overflow: 'auto', marginTop: '12px' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['File', 'Part', 'Rev', 'Size', ''].map(t => h('th', null, t)))),
        h('tbody', null, r.files.map(f => h('tr', null,
          h('td', null, f.filename, f.superseded ? h('div', { class: 'sev-warning small' }, 'A newer revision exists. Ask the sender before using this one.') : null),
          h('td', { class: 'mono' }, f.part_number || ''), h('td', null, f.revision), h('td', null, fmtSize(f.size)),
          h('td', { class: 'nowrap' }, f.preview ? h('a', { class: 'btn sm', href: `/api/share/${token}/files/${f.id}/preview${q()}`, target: '_blank', rel: 'noopener' }, 'View') : null,
            r.view_only ? null : h('a', { class: 'btn sm', href: `/api/share/${token}/files/${f.id}/download${q()}` }, 'Download'))))))),
      h('p', { class: 'muted small', style: { marginTop: '16px' } }, 'Files are watermarked with your name and the date. Opening and downloads are recorded.'));
  };
  await show();
}
