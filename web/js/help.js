// SPDX-License-Identifier: AGPL-3.0-or-later
// What-is-this help (specification 5.6): F1 or the ? button opens one screen of help for the screen in front of the
// person, in their language (help.<screen> in web/locales). Account menu › Language chooses that language.
import { S, h, dialog, toast, fail } from './core.js';
import { t, hasText, setLanguage, currentLanguage } from './locale.js';

const SCREEN = { home: 'nav.home', kosha: 'nav.explorer', inputs: 'nav.inputs', boms: 'nav.boms', changes: 'nav.changes',
  deviations: 'nav.deviations', erp: 'nav.erp', common: 'nav.duplicates', library: 'nav.library', admin: 'nav.admin' };
const SCREEN_EN = { search: 'Search', shop: 'Shop view' };

export function helpDialog(key) {
  const screen = SCREEN[key] ? t(SCREEN[key]) : (SCREEN_EN[key] || 'This screen');
  const text = hasText(`help.${key}`) ? t(`help.${key}`) : t('help.none');
  dialog({ title: t('help.title', 'Help: {screen}', { screen }),
    body: text.split('\n\n').map(p => h('p', { style: { margin: 0, lineHeight: 1.55 } }, p)).concat(
      h('p', { class: 'muted small', style: { margin: 0 } }, 'Ctrl+K: go anywhere · F1: this help · /: search')),
    actions: [{ label: 'Close', primary: true }] });
}

export function languageDialog() {
  const r = S.locales || { languages: [{ code: 'en', name: 'English', covered: 100 }], locale: { name: 'India' } };
  const chosen = currentLanguage();
  const opts = r.languages.map(l => {
    const input = h('input', { type: 'radio', name: 'language', value: l.code, checked: l.code === chosen || (chosen.slice(0, 2) === l.code) });
    input.addEventListener('change', async () => {
      if (!input.checked) return;
      try { await setLanguage(l.code); toast(t('language.saved', 'Language changed.'), 'ok'); setTimeout(() => location.reload(), 600); } catch (e) { fail(e); }
    });
    return h('label', { class: 'check' }, input, h('span', null, h('b', null, l.name),
      l.covered < 100 ? h('small', null, `${l.covered}% of the screen text is translated; the rest stays in English.`) : null));
  });
  dialog({ title: t('language.title', 'Language'), body: [
    h('div', { class: 'theme-opts', role: 'radiogroup', 'aria-label': 'Language' }, opts),
    h('p', { class: 'muted small', style: { margin: 0 } }, t('language.hint', '', { locale: r.locale.name }))],
  actions: [{ label: 'Done', primary: true }] });
}
