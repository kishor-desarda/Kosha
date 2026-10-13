// SPDX-License-Identifier: AGPL-3.0-or-later
// Themes of the web client: Same as my computer (the default), Light, Dark and High contrast.
// The colours themselves are the variables in app.css; this module only picks which set applies (data-theme on
// <html>), saves the choice with the user's account and remembers it in this browser for theme-boot.js.
import { S, h, post, store, dialog, fail } from './core.js';
import { lookSection } from './workbench.js';

export const THEMES = [
  ['system', 'Same as my computer', 'Light or dark, following the setting of your computer. Changes when it does.'],
  ['light', 'Light', 'Dark text on light backgrounds.'],
  ['dark', 'Dark', 'Light text on dark backgrounds. Easier on the eyes in a dim room.'],
  ['contrast', 'High contrast', 'Black text on white with strong lines, for the clearest reading.'],
];
const KNOWN = new Set(THEMES.map(t => t[0]));

export const themeLabel = key => (THEMES.find(t => t[0] === key) || THEMES[0])[1];

// The theme in force for the signed-in user: their own choice, else the installation's default.
export function currentTheme() {
  const own = S.user && S.user.ui_theme;
  if (KNOWN.has(own)) return own;
  return KNOWN.has(S.defaultTheme) ? S.defaultTheme : 'system';
}

export function applyTheme(theme) {
  const t = KNOWN.has(theme) ? theme : 'system';
  document.documentElement.setAttribute('data-theme', t);
  store('theme', t);       // read by theme-boot.js on the next visit, before the page is drawn
  return t;
}

// Account menu › Theme: the choice applies at once and is saved with the account.
export function themeDialog() {
  const chosen = currentTheme();
  const opts = THEMES.map(([key, label, hint]) => {
    const input = h('input', { type: 'radio', name: 'theme', value: key, checked: key === chosen });
    input.addEventListener('change', async () => {
      if (!input.checked) return;
      applyTheme(key);
      const before = S.user.ui_theme;
      S.user.ui_theme = key;
      try { await post('/api/auth/theme', { theme: key }); } catch (e) {
        S.user.ui_theme = before;
        fail(new Error(`The theme is changed in this browser, but could not be saved with your account: ${e.message} Try again later.`));
      }
    });
    return h('label', { class: 'check' }, input, h('span', null, h('b', null, label), h('small', null, hint)));
  });
  dialog({ title: 'Theme', body: [
    h('div', { class: 'theme-opts', role: 'radiogroup', 'aria-label': 'Theme' }, opts),
    lookSection(),
    h('p', { class: 'muted small', style: { margin: 0 } }, 'Your choices are saved with your account, so they follow you to other computers and browsers.')],
  actions: [{ label: 'Done', primary: true }] });
}
