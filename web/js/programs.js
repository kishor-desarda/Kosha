// SPDX-License-Identifier: AGPL-3.0-or-later
// Programs and events: API keys of service accounts (Admin › Users) and webhooks (Admin › Webhooks).
import { h, mount, get, post, patch, del, toast, fail, dialog, fmtDate, ago, confirmDlg } from './core.js';

function shownOnce(title, what, value, note) {
  const box = h('input', { class: 'input mono', readonly: true, value, 'aria-label': what });
  dialog({ title, body: [h('p', { style: { marginTop: 0 } }, `Copy the ${what} now. Kosha keeps only a fingerprint and cannot show it again.`),
    h('div', { class: 'row' }, box, h('button', { class: 'btn', onclick: async () => {
      box.select();
      try { await navigator.clipboard.writeText(value); toast('Copied', 'ok'); } catch (e) { toast('Select the text and copy it with Ctrl+C.'); }
    } }, 'Copy')),
    note ? h('p', { class: 'muted small' }, note) : null],
  actions: [{ label: 'I have copied it', primary: true }] });
  setTimeout(() => box.select(), 50);
}

// ------------------------------------------------------------------ API keys
export async function apiKeysDialog(u) {
  const [meta, r] = await Promise.all([get('/api/api-scopes'), get(`/api/users/${u.id}/api-keys`)]);
  const list = h('div');
  const draw = (keys) => mount(list, keys.length ? h('table', { class: 'tbl' },
    h('thead', null, h('tr', null, ['Name', 'Key starts with', 'Scopes', 'Limits', 'Last used', 'Status', ''].map(t => h('th', null, t)))),
    h('tbody', null, keys.map(k => h('tr', null, h('td', null, k.name), h('td', { class: 'mono' }, `${k.key_prefix}…`),
      h('td', { class: 'small' }, k.scopes.join(', ')),
      h('td', { class: 'small' }, [k.allowed_ips ? `From ${k.allowed_ips}` : 'Any address', k.expires_on ? `, until ${k.expires_on}` : ''].join('')),
      h('td', { class: 'small' }, k.last_used_at ? `${ago(k.last_used_at)} from ${k.last_used_ip}` : 'Never'),
      h('td', null, h('span', { class: `chip ${k.active ? 'st-released' : 'st-obsolete'}` }, k.revoked_at ? `Revoked by ${k.revoked_by_name || 'admin'}` : k.active ? 'Active' : 'Expired')),
      h('td', null, k.revoked_at ? null : h('button', { class: 'btn sm danger', onclick: async () => {
        if (!await confirmDlg('Revoke API key', `The program using "${k.name}" loses access at once. The key stays listed here.`, 'Revoke', true)) return;
        try { await post(`/api/api-keys/${k.id}/revoke`); draw((await get(`/api/users/${u.id}/api-keys`)).keys); toast('Key revoked', 'ok'); } catch (e) { fail(e); }
      } }, 'Revoke')))))) : h('p', { class: 'muted' }, 'No keys yet.'));
  draw(r.keys);
  const name = h('input', { class: 'input', placeholder: 'Drawing check robot', 'aria-label': 'Key name' });
  const ips = h('input', { class: 'input', placeholder: '10.0.5.20 or 10.0.5.0/24', 'aria-label': 'Allowed addresses' });
  const until = h('input', { class: 'input', type: 'date', 'aria-label': 'End date' });
  const boxes = Object.entries(meta.areas).map(([area, label]) => {
    const rd = h('input', { type: 'checkbox', value: `${area}:read` });
    const wr = h('input', { type: 'checkbox', value: `${area}:write` });
    wr.addEventListener('change', () => { if (wr.checked) rd.checked = true; });
    return { area, label, rd, wr };
  });
  dialog({ title: `API keys of ${u.full_name}`, wide: true, body: [
    h('p', { class: 'muted small', style: { marginTop: 0 } }, 'Programs send the key as "Authorization: Bearer ksk_…" or "X-API-Key". A program acts at most as an engineer: it can create and change, but releasing, approving and deleting always need a person.'),
    list,
    h('h3', null, 'New key'),
    h('div', { class: 'field' }, h('label', null, 'Name (the program that uses it)'), name),
    h('div', { class: 'field' }, h('label', null, 'What the key may do'),
      h('table', { class: 'tbl' }, h('tbody', null, boxes.map(b => h('tr', null, h('td', null, b.label),
        h('td', null, h('label', { class: 'check' }, b.rd, 'Read')), h('td', null, h('label', { class: 'check' }, b.wr, 'Change'))))))),
    h('div', { class: 'row wrap' },
      h('div', { class: 'field', style: { flex: 1 } }, h('label', null, 'Only from these addresses (optional)'), ips),
      h('div', { class: 'field' }, h('label', null, 'Valid until (optional)'), until))],
  actions: [{ label: 'Close' }, { label: 'Make key', primary: true, onClick: async () => {
    const scopes = boxes.flatMap(b => [b.rd, b.wr]).filter(x => x.checked).map(x => x.value);
    const res = await post(`/api/users/${u.id}/api-keys`, { name: name.value, scopes, allowed_ips: ips.value, expires_on: until.value || null });
    draw((await get(`/api/users/${u.id}/api-keys`)).keys);
    name.value = ''; ips.value = ''; until.value = '';
    boxes.forEach(b => { b.rd.checked = false; b.wr.checked = false; });
    shownOnce('New API key', 'key', res.key.key, `Give it to the program "${res.key.name}". Revoke it here when the program is retired.`);
    return false;
  } }] });
}

// ------------------------------------------------------------------ webhooks
const STATUS = { pending: ['Waiting', 'st-in_review'], sent: ['Sent', 'st-released'], failed: ['Failed', 'st-obsolete'], cancelled: ['Cancelled', 'st-obsolete'] };

function hookDialog(meta, hook, done) {
  const name = h('input', { class: 'input', value: hook ? hook.name : '', placeholder: 'Shop-floor screens', 'aria-label': 'Name' });
  const url = h('input', { class: 'input', value: hook ? hook.url : '', placeholder: 'https://example.invalid/kosha-events', 'aria-label': 'Address' });
  const chosen = new Set(hook ? hook.events : []);
  const boxes = Object.entries(meta.events).map(([ev, label]) => {
    const c = h('input', { type: 'checkbox', value: ev, checked: chosen.has(ev) });
    return h('label', { class: 'check', title: ev }, c, label);
  });
  dialog({ title: hook ? `Change webhook ${hook.name}` : 'Add webhook', body: [
    h('div', { class: 'field' }, h('label', null, 'Name'), name),
    h('div', { class: 'field' }, h('label', null, 'Address that receives the events'), url,
      h('span', { class: 'hint' }, 'Kosha sends each event as a JSON POST signed with the webhook secret (header X-Kosha-Signature).')),
    h('div', { class: 'field' }, h('label', null, 'Events'), h('div', { style: { display: 'flex', flexDirection: 'column', gap: '4px' } }, boxes))],
  actions: [{ label: 'Cancel' }, { label: hook ? 'Save' : 'Add webhook', primary: true, onClick: async () => {
    const events = boxes.map(b => b.querySelector('input')).filter(c => c.checked).map(c => c.value);
    if (hook) { await patch(`/api/webhooks/${hook.id}`, { name: name.value, url: url.value, events }); toast('Saved', 'ok'); } else {
      const r = await post('/api/webhooks', { name: name.value, url: url.value, events });
      shownOnce('Webhook secret', 'secret', r.webhook.secret, 'The receiving program checks each call with it: HMAC-SHA256 of "<t>.<body>", compared with v1 in X-Kosha-Signature.');
    }
    done();
  } }] });
}

export async function webhooksSection(body) {
  const draw = async () => {
    const r = await get('/api/webhooks');
    mount(body,
      h('div', { class: 'row', style: { marginBottom: '12px' } },
        h('p', { class: 'muted small', style: { flex: 1, margin: 0 } }, 'Other programs (a dashboard, the shop-floor screens, the company AI) can be told when something happens in Kosha. Calls that fail are tried again after 1, 5, 30 minutes, 2 and 12 hours.'),
        h('button', { class: 'btn primary', onclick: () => hookDialog(r, null, draw) }, 'Add webhook')),
      r.webhooks.length ? h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Name', 'Address', 'Events', 'Sent', 'Waiting', 'Failed', 'Status', ''].map(t => h('th', null, t)))),
        h('tbody', null, r.webhooks.map(w => h('tr', null, h('td', null, w.name), h('td', { class: 'mono small' }, w.url),
          h('td', { class: 'small' }, w.events.join(', ')), h('td', null, w.counts.sent), h('td', null, w.counts.pending), h('td', null, w.counts.failed),
          h('td', null, h('span', { class: `chip ${w.active ? 'st-released' : 'st-obsolete'}` }, w.active ? 'On' : 'Paused')),
          h('td', { class: 'nowrap' }, h('div', { class: 'row' },
            h('button', { class: 'btn sm', onclick: () => hookDialog(r, w, draw) }, 'Change'),
            h('button', { class: 'btn sm', onclick: async () => { try { await patch(`/api/webhooks/${w.id}`, { active: !w.active }); draw(); } catch (e) { fail(e); } } }, w.active ? 'Pause' : 'Resume'),
            h('button', { class: 'btn sm danger', onclick: async () => {
              if (!await confirmDlg('Remove webhook', `Kosha stops sending events to ${w.url}. Its delivery log is kept.`, 'Remove', true)) return;
              try { await del(`/api/webhooks/${w.id}`); draw(); } catch (e) { fail(e); }
            } }, 'Remove'))))))) : h('p', { class: 'muted' }, 'No webhooks yet.'),
      h('div', { class: 'row', style: { margin: '18px 0 8px' } }, h('h2', { style: { flex: 1, margin: 0 } }, 'Deliveries'),
        h('button', { class: 'btn sm', title: 'Send the deliveries that are due now instead of waiting for the background worker', onclick: async () => {
          try { const p = await post('/api/webhooks/process'); toast(`${p.processed} tried`, 'ok'); draw(); } catch (e) { fail(e); }
        } }, 'Send due now')),
      r.deliveries.length ? h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['When', 'Webhook', 'Event', 'Status', 'Tries', 'Answer', ''].map(t => h('th', null, t)))),
        h('tbody', null, r.deliveries.map(d => {
          const [label, cls] = STATUS[d.status] || [d.status, ''];
          return h('tr', null, h('td', { class: 'small', title: d.created_at }, fmtDate(d.created_at)), h('td', null, d.webhook),
            h('td', { class: 'mono small' }, d.event), h('td', null, h('span', { class: `chip ${cls}` }, label)),
            h('td', null, d.attempts), h('td', { class: 'small' }, d.last_error || (d.last_status ? `HTTP ${d.last_status}` : ''),
              d.status === 'pending' && d.attempts ? h('div', { class: 'muted' }, `Next try ${fmtDate(d.next_at)}`) : null),
            h('td', null, d.status === 'pending' ? null : h('button', { class: 'btn sm', title: 'Send this event to the webhook again', onclick: async () => {
              try { await post(`/api/webhooks/deliveries/${d.id}/replay`); toast('Queued again', 'ok'); draw(); } catch (e) { fail(e); }
            } }, 'Send again')));
        }))) : h('p', { class: 'muted' }, 'Nothing sent yet.'));
  };
  await draw();
}
