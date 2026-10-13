// SPDX-License-Identifier: AGPL-3.0-or-later
// First-login tour (specification 5.6): five short steps in a corner card. It never blocks the page: people can
// follow along, skip a step or close it, and start it again from the account menu.
import { S, h, icon, get, fail } from './core.js';
import { t } from './locale.js';
import { savePrefs } from './workbench.js';

const STEPS = [
  { key: 'find', title: 'Find a drawing', text: 'Type a part number, a description or a file name in the search box at the top (or press /). Ctrl K jumps to any screen or part.',
    go: practice => (practice ? `#/files/${practice}` : '#/files'), goLabel: 'Open the practice folder' },
  { key: 'preview', title: 'Preview it', text: 'Click a file to see its picture, revision and history. Nothing is downloaded and nothing changes.' },
  { key: 'checkout', title: 'Check it out', text: 'Press Check out before you change a file. Others then see that you are working on it, so two people never overwrite each other.' },
  { key: 'checkin', title: 'Check it in', text: 'When you are done, press Check in and choose the changed file. Kosha keeps every version, so an earlier one can always be opened again.' },
  { key: 'approval', title: 'Send for approval', text: 'Press Submit for review. An approver checks it and releases it; only released revisions reach the shop floor and the ERP.' },
];

let card = null;

export function closeTour(done = true) {
  if (card) { card.remove(); card = null; }
  if (done && S.wb && !S.wb.prefs.tour_done) savePrefs({ tour_done: true }).catch(() => {});
}

export async function startTour() {
  closeTour(false);
  let practice = null;
  try { practice = (await get('/api/practice')).folder_id; } catch (e) { /* the tour works without it */ }
  let i = 0;
  card = h('aside', { class: 'tour', id: 'tour', role: 'complementary', 'aria-label': t('tour.name', 'Tour of Kosha') });
  const draw = () => {
    const s = STEPS[i];
    card.replaceChildren(
      h('div', { class: 'row' }, h('span', { class: 'tour-step' }, `${t('tour.step', 'Step')} ${i + 1} / ${STEPS.length}`), h('span', { class: 'spacer' }),
        h('button', { class: 'btn ghost icon sm', id: 'tour-close', 'aria-label': t('tour.close', 'Close the tour'), title: t('tour.close', 'Close the tour'), onclick: () => closeTour(true) }, icon('x', 14))),
      h('h2', null, t(`tour.${s.key}.title`, s.title)),
      h('p', null, t(`tour.${s.key}.text`, s.text)),
      i === 0 && practice ? h('p', { class: 'small muted' }, t('tour.practice', 'Try everything in the practice folder, Training — safe to try. It is never sent to the ERP and is emptied every night.')) : null,
      h('div', { class: 'row' },
        s.go ? h('button', { class: 'btn sm', onclick: () => { location.hash = s.go(practice); } }, t(`tour.${s.key}.go`, s.goLabel)) : null,
        h('span', { class: 'spacer' }),
        i > 0 ? h('button', { class: 'btn ghost sm', onclick: () => { i--; draw(); } }, t('tour.back', 'Back')) : null,
        i < STEPS.length - 1
          ? h('button', { class: 'btn primary sm', id: 'tour-next', onclick: () => { i++; draw(); } }, t('tour.next', 'Next'))
          : h('button', { class: 'btn primary sm', id: 'tour-done', onclick: () => closeTour(true) }, t('tour.done', 'Done'))));
  };
  draw();
  document.body.appendChild(card);
}

// once, for someone who has never finished or closed it (vendors and other outside accounts get no tour)
export function maybeStartTour() {
  if (!S.wb || S.wb.prefs.tour_done || !S.user || S.user.kind === 'vendor') return;
  startTour().catch(fail);
}
