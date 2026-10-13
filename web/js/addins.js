// SPDX-License-Identifier: AGPL-3.0-or-later
// Add-ins: list, approve and enable, switch off, settings forms built from each add-in's manifest.
import { S, h, mount, icon, get, post, put, toast, fail, dialog, confirmDlg } from './core.js';

const STATUS = {
  enabled: 'On', disabled: 'Off', awaiting_approval: 'Waiting for approval', incompatible: 'Wrong Kosha version',
  invalid: 'Manifest problem', desktop: "Runs on users' PCs",
};
const SOURCE = { bundled: 'Official', local: 'Local folder', installed: 'Installed package' };

/** A form built from a manifest's settings: returns { el, values() }. Secrets are never shown. */
export function schemaForm(fields, values, editable) {
  const inputs = {};
  const el = h('div', { class: 'form-grid' }, fields.map(f => {
    const id = `set-${f.key}`;
    if (f.type === 'bool') {
      inputs[f.key] = h('input', { type: 'checkbox', id, checked: !!values[f.key], disabled: !editable });
      return h('label', { class: 'check', style: { gridColumn: '1 / -1' } }, inputs[f.key], f.label);
    }
    if (f.type === 'choice') {
      inputs[f.key] = h('select', { class: 'input', id, disabled: !editable },
        f.choices.map(c => h('option', { value: c, selected: values[f.key] === c }, c)));
    } else {
      inputs[f.key] = h('input', { class: 'input', id, disabled: !editable, autocomplete: 'off',
        type: f.type === 'secret' ? 'password' : f.type === 'int' ? 'number' : 'text', value: values[f.key] ?? '' });
    }
    return h('div', { class: 'field' }, h('label', { for: id }, f.label, f.required ? ' *' : ''), inputs[f.key],
      f.hint ? h('span', { class: 'hint' }, f.hint) : null);
  }));
  const valuesOf = () => Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.type === 'checkbox' ? i.checked : i.value.trim()]));
  return { el, values: valuesOf };
}

let view = 'list';

async function catalogueView() {
  const c = await get('/api/addins/catalogue');
  return h('div', { class: 'col', id: 'addin-catalogue', style: { gap: '14px' } },
    c.groups.map(g => h('section', { class: 'col', style: { gap: '6px' } },
      h('div', { class: 'row' }, h('h3', { style: { margin: 0 } }, g.label), h('span', { class: 'small muted' }, g.description)),
      g.addins.length ? h('ul', { class: 'check-list' }, g.addins.map(a => h('li', { class: 'row' },
        h('span', { class: `chip st-${a.status}` }, STATUS[a.status] || a.status), h('b', null, a.name),
        h('span', { class: 'small muted' }, `${a.version} · ${SOURCE[a.source] || a.source} · runs on ${a.where.join(', ') || '—'}`))))
        : h('div', { class: 'small muted' }, 'None installed.', g.template ? [' Start one with the SDK: ', h('code', null, g.template)] : ' See the SDK guide to build one.'))),
    c.broken.length ? h('div', { class: 'small sev-error' }, `${c.broken.length} add-in folder(s) could not be read: `, c.broken.map(b => `${b.name} (${b.message})`).join('; ')) : null);
}

export async function addinsTab(body) {
  const draw = async () => {
    const seg = h('div', { class: 'seg' }, [['list', 'Installed'], ['catalogue', 'By kind']].map(([k, l]) =>
      h('button', { 'aria-pressed': String(view === k), onclick: () => { view = k; draw(); } }, l)));
    if (view === 'catalogue') {
      mount(body, h('div', { class: 'row', style: { marginBottom: '10px' } }, seg), await catalogueView());
      return;
    }
    const { addins } = await get('/api/addins');
    const rows = addins.map(a => h('tr', null,
      h('td', null, h('b', null, a.name), h('div', { class: 'small muted', style: { maxWidth: '460px' } }, a.description),
        a.message ? h('div', { class: `small ${['invalid', 'incompatible', 'disabled'].includes(a.status) ? 'sev-error' : 'muted'}` }, a.message) : null),
      h('td', { class: 'small' }, a.kinds.join(', ')),
      h('td', { class: 'mono small' }, a.version),
      h('td', { class: 'small' }, SOURCE[a.source] || a.source, a.official ? h('div', { class: 'muted' }, a.licence) : h('div', { class: 'muted' }, `${a.publisher} · ${a.licence}`)),
      h('td', null, h('span', { class: `chip st-${a.status}` }, STATUS[a.status] || a.status)),
      h('td', { class: 'nowrap' },
        a.status === 'awaiting_approval' || a.status === 'disabled'
          ? h('button', { class: 'btn sm primary', onclick: () => enable(a, draw) }, 'Review and switch on') : null,
        a.status === 'enabled' ? h('button', { class: 'btn sm', onclick: () => disable(a, draw) }, 'Switch off') : null,
        a.has_settings && a.status !== 'invalid' ? h('button', { class: 'btn sm ghost', onclick: () => settings(a, draw) }, icon('settings', 14), 'Settings') : null)));
    mount(body,
      h('div', { class: 'banner info small', style: { marginBottom: '14px' } }, icon('info'), h('div', null,
        'Add-ins connect Kosha to other systems and add file types, packs and screens. Official add-ins ship with Kosha. ',
        'Others wait until you have reviewed what they may do. An add-in that keeps failing is switched off automatically; Kosha carries on without it.')),
      h('div', { class: 'row', style: { marginBottom: '10px' } }, seg, h('span', { class: 'spacer' }),
        h('button', { class: 'btn sm', onclick: async () => { try { await post('/api/addins/rescan'); toast('Add-ins re-read', 'ok'); draw(); } catch (e) { fail(e); } } }, icon('refresh', 14), 'Look for new add-ins')),
      h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, ['Add-in', 'Kind', 'Version', 'Source', 'Status', ''].map(t => h('th', null, t)))),
        h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: 6 }, h('div', { class: 'empty' }, 'No add-ins found.')))))));
  };
  await draw();
}

function enable(a, draw) {
  dialog({
    title: `Switch on ${a.name}?`,
    body: h('div', null,
      h('p', { style: { margin: 0 } }, `${a.name} ${a.version} from ${a.publisher} (${a.licence}) will be able to:`),
      h('ul', { class: 'perm-list' }, a.permissions.length ? a.permissions.map(p => h('li', null, p.label)) : h('li', null, 'nothing beyond its own settings')),
      a.official ? null : h('div', { class: 'banner small', style: { marginTop: '12px' } }, icon('warn'), h('div', null, 'This add-in is not part of the official Kosha release. Switch it on only if you trust where it came from.'))),
    actions: [{ label: 'Cancel' }, { label: 'Allow and switch on', primary: true, onClick: async () => {
      try { await post(`/api/addins/${encodeURIComponent(a.id)}/enable`); toast(`${a.name} is on`, 'ok'); draw(); } catch (e) { fail(e); return false; }
    } }],
  });
}

async function disable(a, draw) {
  if (!await confirmDlg(`Switch off ${a.name}?`, 'Its settings and the data it created are kept. You can switch it on again at any time.', 'Switch off')) return;
  try { await post(`/api/addins/${encodeURIComponent(a.id)}/disable`); toast(`${a.name} is off`, 'ok'); draw(); } catch (e) { fail(e); }
}

async function settings(a, draw) {
  const d = await get(`/api/addins/${encodeURIComponent(a.id)}`);
  const form = schemaForm(d.settings_schema, d.settings, S.user.role === 'admin');
  const result = h('div');
  dialog({
    title: `${a.name} settings`, wide: true,
    body: h('div', { class: 'col', style: { gap: '14px' } },
      d.help ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, d.help)) : null, form.el, result),
    actions: [
      { label: 'Close' },
      { label: 'Test', onClick: async () => {
        try { await put(`/api/addins/${encodeURIComponent(a.id)}/settings`, form.values()); const r = await post(`/api/addins/${encodeURIComponent(a.id)}/health`);
          mount(result, h('div', { class: `banner ${r.ok ? 'ok' : 'err'} small` }, icon(r.ok ? 'check' : 'warn'), h('div', null, r.message))); } catch (e) { fail(e); }
        return false;
      } },
      { label: 'Save', primary: true, onClick: async () => { try { await put(`/api/addins/${encodeURIComponent(a.id)}/settings`, form.values()); toast('Settings saved', 'ok'); draw(); } catch (e) { fail(e); return false; } } },
    ],
  });
}
