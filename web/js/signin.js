// SPDX-License-Identifier: AGPL-3.0-or-later
// Sign-in page extras: "Sign in with ..." buttons for redirect providers (OpenID Connect) and the round trip back.
import { h, api } from './core.js';

const SSO_ERRORS = {
  expired: 'This sign-in has expired or was started in another browser window. Nothing was changed. Please try again.',
  unavailable: 'This way of signing in is not available right now. Sign in with your user name and password, or ask your Kosha administrator.',
  busy: 'Too many sign-ins were started from your network address. Wait a few minutes and try again.',
};

export function ssoErrorText(code) {
  return SSO_ERRORS[code] || SSO_ERRORS.unavailable;
}

// Fill `box` with a button per redirect provider and say whose passwords the form accepts.
export async function providerChoices(box, subtitle) {
  let r;
  try { r = await api('GET', '/api/auth/providers', undefined, { background: true }); } catch (e) { return; }
  if (r.directory_passwords && r.directory_passwords.length) {
    subtitle.textContent = `Use your ${r.directory_passwords.join(' or ')} user name and password, or the Kosha account your administrator gave you.`;
  }
  if (!r.redirect || !r.redirect.length) return;
  box.hidden = false;
  box.append(
    h('div', { class: 'sso' }, r.redirect.map(p => h('a', { class: 'btn', href: `/api/auth/sso/${encodeURIComponent(p.id)}/start` }, `Sign in with ${p.label}`))),
    h('div', { class: 'or' }, 'or use your user name and password'));
}

// Redeem the one-time ticket the server put in the address after a redirect sign-in.
export function finishSso(ticket) {
  return api('POST', '/api/auth/sso/finish', { ticket });
}
