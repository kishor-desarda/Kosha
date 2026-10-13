// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs in <head> as a plain script, before the page is drawn: puts back the theme remembered from the last visit,
// so a dark page never flashes white while the app loads. The app (theme.js) keeps the remembered value up to date.
// No imports or exports: this file is not a module. Without storage (private window, blocked site data) the page
// follows the computer's light or dark setting.
(function () {
  var theme = 'system';
  try {
    var saved = JSON.parse(window.localStorage.getItem('kosha.theme'));
    if (saved === 'light' || saved === 'dark' || saved === 'contrast') theme = saved;
  } catch (e) { /* no storage: follow the computer */ }
  document.documentElement.setAttribute('data-theme', theme);
  try {         // accent colour and text size (workbench.js keeps them up to date)
    var look = JSON.parse(window.localStorage.getItem('kosha.look')) || {};
    if (/^(teal|green|purple|orange)$/.test(look.accent || '')) document.documentElement.setAttribute('data-accent', look.accent);
    if (look.text === 90 || look.text === 115 || look.text === 130) document.documentElement.setAttribute('data-text', String(look.text));
  } catch (e) { /* no storage: the defaults */ }
})();
