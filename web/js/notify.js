// SPDX-License-Identifier: AGPL-3.0-or-later
// Notifications: each person's choice of channels ("My notifications") and the admin's mail server, channels and
// delivery log. Notices are delivered in the background by the outbox; nothing here waits for another system
// except the explicit test buttons.
import { S, h, mount, icon, get, post, put, toast, fail, dialog, ago, fmtDate } from './core.js';

const STATUS = { pending: 'Waiting', sending: 'Sending', sent: 'Sent', failed: 'Gave up' };

/** "My notifications": which kinds of notice reach me on which channel. */
export async function myNotifications() {
  let d;
  try { d = await get('/api/me/notifications'); } catch (e) { fail(e); return; }
  const boxes = {};
  const notReady = d.channels.filter(c => !c.ready);
  const head = h('tr', null, h('th', null, 'Tell me about'), d.channels.map(c => h('th', { style: { textAlign: 'center' }, title: c.ready ? '' : c.why }, c.name)));
  const rows = d.kinds.map(k => h('tr', null,
    h('td', null, h('div', null, k.label), h('div', { class: 'small muted' }, `For ${k.audience}`)),
    d.channels.map(c => {
      const on = (d.prefs[k.id] || []).includes(c.id);
      const why = !c.ready ? `${c.name} cannot be used yet: ${c.why}` : c.id === 'email' && !d.email ? 'Your account has no e-mail address.' : '';
      const box = h('input', { type: 'checkbox', checked: on, disabled: !!why && !on, title: why || `${k.label}: ${c.name}`,
        'aria-label': `${k.label} by ${c.name}` });
      (boxes[k.id] = boxes[k.id] || {})[c.id] = box;
      return h('td', { style: { textAlign: 'center' } }, box);
    })));
  dialog({
    title: 'My notifications', wide: true,
    body: h('div', { class: 'col', style: { gap: '12px' } },
      h('p', { class: 'muted', style: { margin: 0 } }, 'Choose how Kosha tells you about things that need you. Chat channels post to a shared team channel and name you there.'),
      h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' }, h('thead', null, head), h('tbody', null, rows))),
      d.email ? h('div', { class: 'small muted' }, `E-mail goes to ${d.email}.`)
        : h('div', { class: 'banner small' }, icon('warn'), h('div', null, 'Your account has no e-mail address, so e-mail cannot reach you. Ask your admin to add one.')),
      notReady.length ? h('div', { class: 'small muted' }, notReady.map(c => h('div', null, `${c.name}: ${c.why}`))) : null),
    actions: [{ label: 'Cancel' }, { label: 'Save', primary: true, onClick: async () => {
      const prefs = Object.fromEntries(Object.entries(boxes).map(([kind, byChannel]) => [kind, Object.entries(byChannel).filter(([, b]) => b.checked).map(([c]) => c)]));
      await put('/api/me/notifications', { prefs });
      toast('Your notification choices are saved', 'ok');
    } }],
  });
}

/** Admin › Notifications: Kosha's address, the mail server, channels and the delivery log. */
export async function notifyAdminTab(body) {
  const s = await get('/api/notify/settings');
  const m = s.smtp;
  const f = {
    host: h('input', { class: 'input', id: 'smtp-host', value: m.host || '', placeholder: 'mail.example.com' }),
    port: h('input', { class: 'input', id: 'smtp-port', type: 'number', value: m.port }),
    security: h('select', { class: 'input', id: 'smtp-security' }, Object.entries(s.security).map(([k, l]) => h('option', { value: k, selected: m.security === k }, l))),
    username: h('input', { class: 'input', id: 'smtp-user', value: m.username || '', autocomplete: 'off' }),
    password: h('input', { class: 'input', id: 'smtp-pass', type: 'password', value: m.password || '', autocomplete: 'new-password' }),
    from_address: h('input', { class: 'input', id: 'smtp-from', value: m.from_address || '', placeholder: 'kosha@example.com' }),
    from_name: h('input', { class: 'input', id: 'smtp-name', value: m.from_name || '' }),
    timeout_s: h('input', { class: 'input', id: 'smtp-timeout', type: 'number', value: m.timeout_s }),
  };
  const enabled = h('input', { type: 'checkbox', checked: !!m.enabled });
  const verify = h('input', { type: 'checkbox', checked: m.verify_tls !== false });
  const base = h('input', { class: 'input', id: 'kosha-url', value: s.base_url || '', placeholder: 'https://plm.example.com' });
  const testTo = h('input', { class: 'input', id: 'smtp-test-to', value: S.user.email || '', placeholder: 'you@example.com', style: { maxWidth: '280px' } });
  const result = h('div');
  const L = { host: 'Mail server', port: 'Port', security: 'Security', username: 'User name', password: 'Password', from_address: 'Sender address', from_name: 'Sender name', timeout_s: 'Time-out (seconds)' };
  const say = (r) => mount(result, h('div', { class: `banner ${r.ok ? 'ok' : 'err'} small` }, icon(r.ok ? 'check' : 'warn'), h('div', null, r.message)));
  const save = async () => {
    const smtp = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()]));
    smtp.enabled = enabled.checked; smtp.verify_tls = verify.checked;
    await put('/api/notify/settings', { base_url: base.value.trim(), smtp });
  };
  const channelsBox = h('div');
  const logBox = h('div');
  mount(body, h('div', { class: 'col', style: { gap: '22px', maxWidth: '1100px' } },
    h('div', { class: 'banner info small' }, icon('info'), h('div', null,
      'Kosha tells people about change orders waiting for their approval, failed ERP transfers and, if they ask for it, new releases. ',
      'Each person chooses the channels under ', h('b', null, 'My notifications'), ' (the menu behind their initials). ',
      'Notices are sent in the background and retried when a channel is down, so a broken mail server never holds up anyone\'s work.')),
    h('section', { class: 'col', style: { gap: '12px' } }, h('h2', null, "Kosha's address"),
      h('div', { class: 'field', style: { maxWidth: '520px' } }, h('label', { for: 'kosha-url' }, 'Address people use to open Kosha'), base,
        h('span', { class: 'hint' }, 'Used for the links in e-mails and chat messages. Without it, messages carry no link.'))),
    h('section', { class: 'col', style: { gap: '12px' } }, h('h2', null, 'E-mail'),
      h('label', { class: 'check' }, enabled, 'Send e-mail'),
      h('div', { class: 'form-grid' }, Object.entries(f).map(([k, el]) => h('div', { class: 'field' }, h('label', { for: el.id }, L[k]), el,
        k === 'password' ? h('span', { class: 'hint' }, 'Stored masked; leave as it is to keep it.') : null))),
      h('label', { class: 'check' }, verify, 'Verify the mail server\'s certificate'),
      h('div', { class: 'row wrap' },
        h('button', { class: 'btn primary', onclick: async () => { try { await save(); toast('Notification settings saved', 'ok'); drawChannels(); } catch (e) { fail(e); } } }, 'Save settings'),
        h('button', { class: 'btn', onclick: async () => { try { await save(); say(await post('/api/notify/email/check')); } catch (e) { fail(e); } } }, 'Check connection'),
        h('span', { class: 'spacer' }),
        h('label', { for: 'smtp-test-to', class: 'small' }, 'Test to'), testTo,
        h('button', { class: 'btn', onclick: async () => { try { await save(); say(await post('/api/notify/email/test', { to: testTo.value.trim() })); } catch (e) { fail(e); } } }, icon('play', 14), 'Send a test e-mail')),
      result),
    h('section', { class: 'col', style: { gap: '12px' } }, h('h2', null, 'Channels'), channelsBox),
    h('section', { class: 'col', style: { gap: '12px' } }, h('h2', null, 'Delivery log'), logBox)));

  async function drawChannels() {
    const d = await get('/api/notify/settings');
    const out = h('div');
    mount(channelsBox, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Channel', 'State', ''].map(x => h('th', null, x)))),
      h('tbody', null, d.channels.map(c => h('tr', null,
        h('td', null, h('b', null, c.name), h('div', { class: 'small muted' }, c.per_person ? 'One message to each person' : 'One post per notice in a shared channel')),
        h('td', { class: 'small' }, c.ready ? h('span', { class: 'chip st-enabled' }, 'Ready') : h('span', { class: 'sev-warning' }, c.why)),
        h('td', { class: 'nowrap' }, h('div', { class: 'row' },
          h('button', { class: 'btn sm', disabled: !c.ready, title: c.ready ? `Send a test message through ${c.name} now` : c.why,
            onclick: async () => { try { const r = await post(`/api/notify/channels/${encodeURIComponent(c.id)}/test`, { to: testTo.value.trim() }); mount(out, h('div', { class: `banner ${r.ok ? 'ok' : 'err'} small` }, icon(r.ok ? 'check' : 'warn'), h('div', null, `${c.name}: ${r.message}`))); } catch (e) { fail(e); } } },
          icon('play', 14), 'Send a test message'),
          c.source === 'addin' ? h('a', { class: 'btn sm ghost', href: '#/admin?tab=addins' }, icon('settings', 14), 'Settings') : null))))),
    ), out);
  }

  let status = '';
  async function drawLog() {
    const d = await get(`/api/notify/outbox${status ? `?status=${status}` : ''}`);
    const chips = h('div', { class: 'row wrap filter-chips', role: 'group', 'aria-label': 'Show only' },
      h('button', { class: 'chip', 'aria-pressed': String(!status), onclick: () => { status = ''; drawLog(); } }, 'All'),
      Object.entries(STATUS).filter(([k]) => k !== 'sending').map(([k, l]) => h('button', { class: 'chip', 'aria-pressed': String(status === k), onclick: () => { status = status === k ? '' : k; drawLog(); } }, `${l} ${d.counts[k] || 0}`)),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm', disabled: !d.counts.pending, title: d.counts.pending ? 'Deliver every notice that is due now' : 'Nothing is waiting.',
        onclick: async () => { try { const r = await post('/api/notify/process'); toast(`Tried ${r.processed} notice(s)`, 'ok'); drawLog(); } catch (e) { fail(e); } } }, icon('play', 14), 'Deliver waiting now'));
    mount(logBox, chips, d.rows.length ? h('div', { style: { overflow: 'auto', maxHeight: '60vh' } }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['When', 'Notice', 'Channel', 'To', 'Status', ''].map(x => h('th', null, x)))),
      h('tbody', null, d.rows.map(r => h('tr', null,
        h('td', { class: 'nowrap small', title: fmtDate(r.created_at) }, ago(r.created_at)),
        h('td', null, h('div', null, r.title), h('div', { class: 'small muted' }, r.kind),
          r.last_error ? h('div', { class: 'small sev-error' }, r.last_error) : null),
        h('td', { class: 'small' }, r.channel_name),
        h('td', { class: 'small' }, r.address || r.recipients.join(', ') || '—'),
        h('td', { class: 'nowrap' }, h('span', { class: `chip st-${r.status}` }, STATUS[r.status] || r.status),
          h('div', { class: 'small muted' }, r.status === 'pending' && r.attempts ? `${r.attempts} failed, next try ${fmtDate(r.next_attempt_at)}` : r.attempts ? `${r.attempts} attempt(s)` : '')),
        h('td', null, r.status === 'failed' || (r.status === 'pending' && r.attempts)
          ? h('button', { class: 'btn sm', onclick: async () => { try { const x = await post(`/api/notify/outbox/${r.id}/retry`); toast(x.row.status === 'sent' ? 'Sent' : `Still failing: ${x.row.last_error}`, x.row.status === 'sent' ? 'ok' : 'err'); drawLog(); } catch (e) { fail(e); } } }, icon('refresh', 14), 'Send again')
          : null)))))) : h('div', { class: 'empty' }, 'No notices yet.'));
  }
  await Promise.all([drawChannels(), drawLog()]);
}
