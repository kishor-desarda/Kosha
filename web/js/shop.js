// SPDX-License-Identifier: AGPL-3.0-or-later
// Shop view (specification 5.7): the released drawing of a part, large and read-only, by part number or QR code.
import { S, h, mount, icon, get, fail, fileUrl, stateChip, dialog } from './core.js';
import { t } from './locale.js';

const canScan = () => 'BarcodeDetector' in window && !!navigator.mediaDevices?.getUserMedia;

// what a scanned QR code means: Kosha's own label (…#/shop?pn=MX-100-020) or a bare part number
export function partFromCode(text) {
  const s = String(text || '').trim();
  const m = s.match(/[#?&]pn=([^&#]+)/) || s.match(/#\/shop\?pn=([^&#]+)/);
  if (m) { try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; } }
  return s.split(/\s+/)[0] || '';
}

function drawingCard(d) {
  const pic = d.preview_kind
    ? h('img', { class: 'shop-drawing', src: fileUrl(d.id, 'preview', d.version), alt: `${d.filename}, revision ${d.revision}` })
    : h('div', { class: 'empty' }, icon('file', 40), h('div', null, 'No picture of this file. Open the PDF instead.'));
  return h('section', { class: 'panel shop-card' },
    h('div', { class: 'panel-h' }, h('h2', null, d.filename), h('span', { class: 'spacer' }),
      h('span', { class: 'shop-rev' }, `Rev ${d.revision}`), stateChip('released')),
    d.note ? h('div', { class: 'banner warn', role: 'note' }, icon('warn'), h('div', null, d.note)) : null,
    h('div', { class: 'panel-b' }, pic),
    d.pdf_output_id ? h('div', { class: 'panel-b' }, h('a', { class: 'btn', target: '_blank', rel: 'noopener',
      href: `/api/outputs/${d.pdf_output_id}/file?inline=1&access_token=${encodeURIComponent(S.token)}` }, icon('external', 16), 'Open the stamped PDF to print')) : null);
}

export async function renderShop(container, _m, params, { setTitle }) {
  setTitle(t('shop.title', 'Shop view'));
  const input = h('input', { class: 'input shop-input', id: 'shop-pn', type: 'search', autocomplete: 'off', value: params.pn || '',
    placeholder: t('shop.placeholder', 'Part number, for example MX-100-020'), 'aria-label': 'Part number' });
  const out = h('div', { id: 'shop-result', class: 'col', style: { gap: '16px' } });
  let stopCamera = null;

  const show = async pn => {
    pn = (pn || '').trim();
    if (!pn) { mount(out, h('div', { class: 'empty' }, t('shop.empty', 'Type a part number or scan the QR code on the job card.'))); return; }
    if (location.hash !== `#/shop?pn=${encodeURIComponent(pn)}`) history.replaceState(null, '', `#/shop?pn=${encodeURIComponent(pn)}`);
    mount(out, h('div', { class: 'muted' }, 'Looking…'));
    try {
      const r = await get(`/api/shop?pn=${encodeURIComponent(pn)}`);
      const it = r.item;
      setTitle(`${t('shop.title', 'Shop view')}: ${it.part_number}`);
      mount(out,
        h('div', { class: 'shop-head' }, h('div', { class: 'shop-pn mono' }, it.part_number), h('div', { class: 'shop-desc' }, it.description),
          h('div', { class: 'row' }, stateChip(it.state), it.material ? h('span', { class: 'muted' }, it.material) : null)),
        it.warning ? h('div', { class: 'banner err', role: 'alert' }, icon('warn'), h('div', null, it.warning)) : null,
        r.drawings.length ? r.drawings.map(drawingCard)
          : h('div', { class: 'banner warn' }, icon('warn'), h('div', null, 'This part has no released drawing yet. Do not make it from an unreleased print; ask the drawing office.')));
    } catch (e) {
      mount(out, h('div', { class: 'banner err', role: 'alert' }, icon('warn'), h('div', null, e.message || String(e))));
    }
  };

  const scan = async btn => {
    if (stopCamera) { stopCamera(); return; }
    const video = h('video', { class: 'shop-camera', playsinline: '', muted: '' });
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }); } catch (e) {
      fail(new Error('The camera could not be opened. Allow camera use for Kosha in the browser, or type the part number.')); return;
    }
    video.srcObject = stream; await video.play().catch(() => {});
    const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
    let on = true;
    const dlg = dialog({ title: t('shop.scan', 'Scan QR code'), body: h('div', { class: 'col' }, video, h('p', { class: 'muted' }, 'Hold the QR code on the job card in front of the camera.')),
      actions: [{ label: 'Close' }], onClose: () => stopCamera && stopCamera() });
    stopCamera = () => { on = false; stream.getTracks().forEach(tr => tr.stop()); stopCamera = null; btn.focus(); };
    const tick = async () => {
      if (!on) return;
      try {
        const codes = await detector.detect(video);
        if (codes.length) { const pn = partFromCode(codes[0].rawValue); stopCamera(); dlg?.close?.(); input.value = pn; show(pn); return; }
      } catch (e) { /* the next frame */ }
      setTimeout(tick, 250);
    };
    tick();
  };

  const scanBtn = h('button', { class: 'btn lg', id: 'shop-scan', disabled: !canScan(),
    title: canScan() ? 'Open the camera and read the QR code on the job card' : 'This browser cannot read QR codes with the camera. Type the part number instead.',
    onclick: e => scan(e.currentTarget) }, icon('camera', 18), t('shop.scan', 'Scan QR code'));
  input.addEventListener('keydown', e => { if (e.key === 'Enter') show(input.value); });

  mount(container, h('div', { class: 'page-pad shop' },
    h('div', { class: 'shop-bar row' }, input,
      h('button', { class: 'btn primary lg', onclick: () => show(input.value) }, icon('search', 18), t('shop.show', 'Show drawing')),
      scanBtn,
      h('button', { class: 'btn ghost lg', title: 'Fill the screen (Esc to go back)', onclick: () => document.documentElement.requestFullscreen?.().catch(() => {}) },
        icon('external', 18), t('shop.full', 'Full screen'))),
    out));
  show(params.pn || '');
  if (!params.pn) input.focus();
  return () => { if (stopCamera) stopCamera(); };
}

// on the item page: a QR label that opens this part's shop view
export function qrLabelDialog(item) {
  const src = `/api/items/${item.id}/qr.svg?access_token=${encodeURIComponent(S.token)}`;
  dialog({
    title: `QR label for ${item.part_number}`,
    body: h('div', { class: 'col', style: { gap: '10px', alignItems: 'center' } },
      h('img', { id: 'qr-label', src, alt: `QR code that opens the shop view of ${item.part_number}`, style: { width: '240px' } }),
      h('p', { class: 'muted', style: { margin: 0 } }, 'Print it on the job card or the nameplate. Scanning it opens the released drawing in the shop view.')),
    actions: [{ label: 'Close' }, { label: 'Open for printing', primary: true, onClick: () => { window.open(src, '_blank', 'noopener'); return false; } }],
  });
}
