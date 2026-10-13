// SPDX-License-Identifier: AGPL-3.0-or-later
// Words and formats on screen (specification 5.1). t('key') reads the person's language from web/locales/<code>.json
// and falls back to English, then to the text given in the call, so an untranslated string is never blank.
// fmtDay, fmtWhen, fmtMoney and fmtShortMoney follow the company's locale: in India 10-Oct-2026, 14:05 IST,
// ₹1,25,000 and ₹2.4 lakh.
import { S, get } from './core.js';

let words = {};
let english = {};
let locale = { code: 'en-IN', date: 'DD-Mon-YYYY', time_zone: 'Asia/Kolkata', hour12: false, number: 'en-IN', currency: 'INR', big_numbers: 'lakh' };
let loaded = null;

async function words_of(code) {
  const r = await fetch(`/static/locales/${encodeURIComponent(code)}.json`, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`No translation file for ${code}`);
  return r.json();
}

// Called once after sign-in, before the first page is drawn.
export function loadLocale() {
  loaded = (async () => {
    try { english = await words_of('en'); } catch (e) { english = {}; }
    try { const r = await get('/api/locale'); locale = r.locale; S.locales = r; } catch (e) { /* keep India */ }
    await setLanguage(currentLanguage(), false);
  })();
  return loaded;
}

export function currentLanguage() {
  const own = S.wb && S.wb.prefs && S.wb.prefs.language;
  return own || (navigator.language || 'en').slice(0, 2);
}

export async function setLanguage(code, remember = true) {
  if (!code || code === 'en') words = {};
  else {
    try { words = await words_of(code); } catch (e) { words = {}; code = 'en'; }
  }
  document.documentElement.lang = code || 'en';
  if (remember) {
    const { savePrefs } = await import('./workbench.js');
    await savePrefs({ language: code });
  }
}

export function t(key, fallback = '', vars = null) {
  let s = words[key] || english[key] || fallback || key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
}
export const hasText = key => !!(words[key] || english[key]);
export const localeInfo = () => locale;

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function parts(d) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: locale.time_zone, year: 'numeric', month: 'numeric',
    day: 'numeric', hour: 'numeric', minute: 'numeric', hourCycle: 'h23' }).formatToParts(d).map(x => [x.type, x.value]));
  return { y: p.year, m: Number(p.month), d: String(p.day).padStart(2, '0'), H: Number(p.hour), M: p.minute };
}

// 10-Oct-2026 (India and UK), Oct 10, 2026 (US), 10.10.2026 (Germany)
export function fmtDay(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = parts(d);
  if (locale.date === 'Mon DD, YYYY') return `${MON[p.m - 1]} ${p.d}, ${p.y}`;
  if (locale.date === 'DD.MM.YYYY') return `${p.d}.${String(p.m).padStart(2, '0')}.${p.y}`;
  return `${p.d}-${MON[p.m - 1]}-${p.y}`;
}

export function fmtWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = parts(d);
  const time = locale.hour12 ? `${((p.H + 11) % 12) + 1}:${p.M} ${p.H < 12 ? 'AM' : 'PM'}` : `${String(p.H).padStart(2, '0')}:${p.M}`;
  return `${fmtDay(iso)} ${time}`;
}

export function fmtMoney(n, currency = '') {
  if (n == null || n === '' || isNaN(Number(n))) return '—';
  const cur = currency || locale.currency;
  try {
    return new Intl.NumberFormat(locale.number, { style: 'currency', currency: cur, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(n));
  } catch (e) { return `${Number(n).toFixed(2)} ${cur}`; }
}

// Summaries: ₹2.4 lakh, ₹1.2 crore in India; elsewhere 240K, 1.2M.
export function fmtShortMoney(n, currency = '') {
  if (n == null || isNaN(Number(n))) return '—';
  const v = Number(n);
  const cur = currency || locale.currency;
  const sym = (() => { try { return new Intl.NumberFormat(locale.number, { style: 'currency', currency: cur, maximumFractionDigits: 0 }).formatToParts(0).find(x => x.type === 'currency').value; } catch (e) { return cur + ' '; } })();
  const one = x => (Math.round(x * 10) / 10).toLocaleString(locale.number);
  if (locale.big_numbers === 'lakh') {
    if (Math.abs(v) >= 1e7) return `${sym}${one(v / 1e7)} crore`;
    if (Math.abs(v) >= 1e5) return `${sym}${one(v / 1e5)} lakh`;
  } else {
    if (Math.abs(v) >= 1e6) return `${sym}${one(v / 1e6)}M`;
    if (Math.abs(v) >= 1e3) return `${sym}${one(v / 1e3)}K`;
  }
  return `${sym}${Math.round(v).toLocaleString(locale.number)}`;
}
