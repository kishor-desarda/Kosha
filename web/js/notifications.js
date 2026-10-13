// SPDX-License-Identifier: AGPL-3.0-or-later
// The bell in the top bar: unread count, the latest notifications, mark as read.
import { S, h, mount, icon, get, post, fail, ago, fmtDate } from './core.js';

let timer = null;
let current = null;    // the bell of the current shell (rebuilt after each sign-in)

async function refreshCount() {
  if (!current || !S.token) return;
  try { current.setCount((await get('/api/notifications/count')).unread); } catch (e) { /* offline: keep the last count */ }
}
window.addEventListener('kosha:changed', () => setTimeout(refreshCount, 400));
window.addEventListener('hashchange', () => { if (current) current.close(); });
document.addEventListener('mousedown', e => {
  if (current && current.panel && !current.panel.contains(e.target) && !current.btn.contains(e.target)) current.close();
});

export function notificationBell() {
  const badge = h('span', { class: 'badge bell-badge', hidden: true });
  const btn = h('button', { class: 'btn ghost icon bell', type: 'button', 'aria-label': 'Notifications', title: 'Notifications',
    'aria-haspopup': 'true', 'aria-expanded': 'false' }, icon('bell', 20), badge);
  const me = { btn, panel: null, unread: 0 };
  me.setCount = n => {
    me.unread = n || 0;
    badge.hidden = !me.unread;
    badge.textContent = me.unread > 99 ? '99+' : String(me.unread);
    btn.setAttribute('aria-label', me.unread ? `Notifications, ${me.unread} unread` : 'Notifications');
  };
  me.close = () => {
    if (!me.panel) return;
    me.panel.remove(); me.panel = null;
    btn.setAttribute('aria-expanded', 'false');
  };
  const draw = r => {
    me.setCount(r.unread);
    const markAll = h('button', { class: 'btn ghost sm', type: 'button', disabled: !r.unread, title: r.unread ? '' : 'Nothing unread.',
      onclick: async () => { try { draw({ ...(await post('/api/notifications/read-all')), notifications: r.notifications.map(n => ({ ...n, read: true })) }); } catch (e) { fail(e); } } },
    icon('check', 14), 'Mark all as read');
    mount(me.panel,
      h('div', { class: 'notif-h' }, h('b', null, 'Notifications'), h('span', { class: 'spacer' }), markAll),
      h('div', { class: 'notif-list' }, r.notifications.length ? r.notifications.map(n => h('a', {
        class: `notif ${n.read ? '' : 'unread'}`, href: n.link || '#/home', title: fmtDate(n.created_at),
        onclick: async () => { if (!n.read) { try { me.setCount((await post(`/api/notifications/${n.id}/read`)).unread); } catch (e) { /* the link still opens */ } } me.close(); },
      }, h('span', { class: 'dot', 'aria-hidden': 'true' }), h('div', { style: { minWidth: 0, flex: 1 } },
        h('div', { class: 'notif-t' }, n.title), h('div', { class: 'small muted' }, `${n.actor_name ? `${n.actor_name} · ` : ''}${ago(n.created_at)}`),
        n.read ? null : h('span', { class: 'sr-only' }, 'unread'))))
        : h('div', { class: 'empty small' }, 'No notifications yet. Mentions, replies and approvals waiting for you show up here.')));
  };
  const open = async () => {
    me.panel = h('div', { class: 'notif-panel', role: 'dialog', 'aria-label': 'Notifications' }, h('div', { class: 'empty small' }, 'Loading…'));
    me.panel.addEventListener('keydown', e => { if (e.key === 'Escape') { me.close(); btn.focus(); } });
    document.body.appendChild(me.panel);
    const rect = btn.getBoundingClientRect();
    me.panel.style.top = `${rect.bottom + 6}px`;
    me.panel.style.right = `${Math.max(8, innerWidth - rect.right)}px`;
    btn.setAttribute('aria-expanded', 'true');
    try { draw(await get('/api/notifications?limit=30')); } catch (e) { me.close(); fail(e); }
  };
  btn.addEventListener('click', () => (me.panel ? me.close() : open()));
  btn.addEventListener('keydown', e => { if (e.key === 'Escape') me.close(); });
  if (current) current.close();
  current = me;
  clearInterval(timer);
  timer = setInterval(refreshCount, 45000);
  refreshCount();
  return btn;
}
