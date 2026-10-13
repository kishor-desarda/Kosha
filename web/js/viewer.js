// SPDX-License-Identifier: AGPL-3.0-or-later
// Embedded viewer: DXF/DWG drawings (SVG with pan, zoom, measure, revision overlay),
// 3D models (STL from the SolidWorks add-in, WebGL), PDFs and images.
import { S, h, mount, clear, icon, get, fileUrl, downloadDoc, stateChip, fail, toast } from './core.js';

let current = null;

export async function openViewer(docOrId, opts = {}) {
  const id = typeof docOrId === 'object' ? docOrId.id : docOrId;
  let det;
  try { det = await get(`/api/documents/${id}`); } catch (e) { fail(e); return; }
  close();
  const versions = det.versions;
  let ver = opts.version || det.version;
  const stage = h('div', { class: 'stage' });
  const band = h('div', { class: 'superseded-band', role: 'status', hidden: true });
  const tools = h('div', { class: 'row' });
  const verSel = h('select', { 'aria-label': 'Version' }, versions.map(v => h('option', { value: v.version, selected: v.version === ver }, `v${v.version} · rev ${v.revision} · ${new Date(v.created_at).toLocaleDateString()}`)));
  verSel.addEventListener('change', () => { ver = Number(verSel.value); show(); });
  const root = h('div', { class: 'viewer', role: 'dialog', 'aria-modal': 'true', 'aria-label': `Viewer: ${det.filename}` },
    h('div', { class: 'vh' },
      h('button', { class: 'btn', onclick: close, 'aria-label': 'Close viewer' }, icon('back', 16), 'Close'),
      h('div', { style: { minWidth: 0 } }, h('div', { class: 'ellipsis', style: { fontWeight: 600 } }, det.filename),
        h('div', { class: 'small sub' }, det.part_number ? `${det.part_number} · ` : '', det.description || det.type_label)),
      stateChip(det.state), h('span', { class: 'chip rev' }, `Rev ${det.revision}`),
      h('span', { class: 'spacer' }), tools, verSel,
      h('button', { class: 'btn', onclick: () => downloadDoc(det.id, ver) }, icon('download', 16), 'Download'),
      h('a', { class: 'btn', href: `#/doc/${det.id}`, onclick: close }, icon('info', 16), 'Details')),
    band, stage);
  document.body.appendChild(root);
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  current = { root, onKey, dispose: null };
  root.querySelector('button').focus();

  function show() {
    if (current.dispose) { current.dispose(); current.dispose = null; }
    clear(stage); clear(tools);
    const v = versions.find(x => x.version === ver) || versions[0];
    const kind = v.viewer_kind;
    stage.className = 'stage';
    band.hidden = !v.superseded_by;
    band.textContent = v.superseded_by ? `Superseded — use Rev ${v.superseded_by}` : '';
    if (kind === 'svg') current.dispose = svgViewer(stage, tools, det, v, versions);
    else if (kind === 'stl') current.dispose = stlViewer(stage, tools, det, v);
    else if (kind === 'pdf') stage.appendChild(h('iframe', { src: fileUrl(det.id, 'viewer', v.version), title: det.filename }));
    else if (kind) current.dispose = imageViewer(stage, tools, fileUrl(det.id, 'viewer', v.version), det.filename);
    else {
      stage.appendChild(h('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '12px', padding: '30px', textAlign: 'center' } },
        icon('file', 48), h('h2', null, 'No preview for this file'),
        h('p', { class: 'note', style: { maxWidth: '560px', lineHeight: 1.5 } }, v.meta.preview_note || `Kosha stores and versions ${det.type_label} files, but cannot draw them in the browser.`),
        h('button', { class: 'btn primary', onclick: () => downloadDoc(det.id, v.version) }, icon('download'), 'Download to open in CAD')));
    }
  }
  show();
}

function close() {
  if (!current) return;
  if (current.dispose) current.dispose();
  document.removeEventListener('keydown', current.onKey);
  current.root.remove();
  current = null;
}

// ------------------------------------------------------------------ SVG drawings
function svgViewer(stage, tools, det, v, versions) {
  stage.classList.add('paper');
  const wrap = h('div', { class: 'svgwrap' });
  const hud = h('div', { class: 'hud' }, 'Loading drawing…');
  stage.append(wrap, hud);
  let vb = null, base = null, layers = [], measuring = false, pts = [], mGroup = null, compareWith = null;
  let changes = null, zoneLayer = null;   // measured change list while two versions are overlaid
  const units = { mm: 'mm', in: 'in', cm: 'cm', m: 'm' };
  let unit = 'mm';

  const btn = (ic, label, fn, pressed) => {
    const b = h('button', { class: 'btn', title: label, 'aria-pressed': pressed === undefined ? null : String(pressed) }, icon(ic, 16), label);
    b.addEventListener('click', () => fn(b));
    return b;
  };
  const measureBtn = btn('measure', 'Measure', b => {
    measuring = !measuring; pts = []; b.setAttribute('aria-pressed', String(measuring));
    wrap.classList.toggle('measuring', measuring);
    hud.textContent = measuring ? 'Click two points to measure. Esc or Measure again to stop.' : hudText();
    if (!measuring && mGroup) mGroup.remove();
  }, false);
  const cmpSel = h('select', { 'aria-label': 'Compare with version' }, h('option', { value: '' }, 'Compare with…'),
    versions.filter(x => x.version !== v.version && x.viewer_kind === 'svg').map(x => h('option', { value: x.version }, `v${x.version} (rev ${x.revision})`)));
  cmpSel.addEventListener('change', () => { compareWith = cmpSel.value ? Number(cmpSel.value) : null; load(); });
  tools.append(btn('fit', 'Fit', fit), btn('zoomIn', '', () => zoom(0.7)), btn('zoomOut', '', () => zoom(1.4)), measureBtn, cmpSel.options.length > 1 ? cmpSel : null);
  tools.querySelectorAll('button').forEach(b => { if (!b.textContent) b.setAttribute('aria-label', b.title || 'Zoom'); });

  async function fetchSvg(version) {
    const r = await fetch(fileUrl(det.id, 'viewer', version));
    if (!r.ok) throw new Error('Could not load the drawing');
    const doc = new DOMParser().parseFromString(await r.text(), 'image/svg+xml');
    const svg = doc.documentElement;
    if (svg.querySelector('script')) svg.querySelectorAll('script').forEach(s => s.remove());
    return document.importNode(svg, true);
  }

  async function load() {
    try {
      clear(wrap);
      const main = await fetchSvg(v.version);
      unit = units[main.getAttribute('data-units')] || 'mm';
      layers = [main];
      let box = main.viewBox.baseVal;
      base = { x: box.x, y: box.y, w: box.width, hgt: box.height };
      if (compareWith) {
        const old = await fetchSvg(compareWith);
        const ob = old.viewBox.baseVal;
        const x0 = Math.min(base.x, ob.x), y0 = Math.min(base.y, ob.y);
        const x1 = Math.max(base.x + base.w, ob.x + ob.width), y1 = Math.max(base.y + base.hgt, ob.y + ob.height);
        base = { x: x0, y: y0, w: x1 - x0, hgt: y1 - y0 };
        old.classList.add('overlay-old');
        main.classList.add('overlay-new');
        old.style.mixBlendMode = 'multiply';
        main.style.mixBlendMode = 'multiply';
        layers = [old, main];
      }
      wrap.classList.toggle('overlay', !!compareWith);
      for (const s of layers) { s.setAttribute('preserveAspectRatio', 'xMidYMid meet'); wrap.appendChild(s); }
      if (compareWith) {
        stage.querySelector('.legend')?.remove();
        stage.appendChild(h('div', { class: 'legend' }, h('b', null, 'Revision overlay'),
          h('span', null, h('span', { class: 'sw-old' }, '■ '), `v${compareWith} (older)`),
          h('span', null, h('span', { class: 'sw-new' }, '■ '), `v${v.version} (this)`),
          h('span', { class: 'sub' }, 'Lines in both look purple')));
      } else stage.querySelector('.legend')?.remove();
      fit();
      hud.textContent = hudText();
      zoneLayer = null;
      if (changes) { changes.remove(); changes = null; }
      if (compareWith) {
        const want = compareWith;
        import('./diffview.js').then(m => { if (compareWith === want && !changes) changes = m.changesPanel(stage, det.id, want, v.version, zoomToZone); });
      }
    } catch (e) { hud.textContent = e.message; }
  }
  const hudText = () => `${det.filename} · v${v.version} · units ${unit} · scroll to zoom, drag to pan`;

  function apply() {
    for (const s of layers) s.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.hgt}`);
  }
  function fit() {
    if (!base) return;
    const r = wrap.getBoundingClientRect();
    const aspect = r.width / Math.max(r.height, 1);
    vb = { ...base };
    if (vb.w / vb.hgt < aspect) { const nw = vb.hgt * aspect; vb.x -= (nw - vb.w) / 2; vb.w = nw; }
    else { const nh = vb.w / aspect; vb.y -= (nh - vb.hgt) / 2; vb.hgt = nh; }
    apply();
  }
  function zoomToZone(z) {   // z = [min x, min y, max x, max y] in drawing units; the SVG has y pointing down
    if (!z || !base) return;
    const NS = 'http://www.w3.org/2000/svg';
    const r = wrap.getBoundingClientRect(), aspect = r.width / Math.max(r.height, 1);
    const w = Math.max(z[2] - z[0], base.w / 400), hgt = Math.max(z[3] - z[1], base.hgt / 400);
    vb = { x: z[0] - w, y: -z[3] - hgt, w: w * 3, hgt: hgt * 3 };
    if (vb.w / vb.hgt < aspect) { const nw = vb.hgt * aspect; vb.x -= (nw - vb.w) / 2; vb.w = nw; }
    else { const nh = vb.w / aspect; vb.y -= (nh - vb.hgt) / 2; vb.hgt = nh; }
    if (!zoneLayer) {   // drawn behind the overlaid drawings so their lines stay visible
      zoneLayer = document.createElementNS(NS, 'svg');
      zoneLayer.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      wrap.insertBefore(zoneLayer, wrap.firstChild);
      layers.unshift(zoneLayer);
    }
    zoneLayer.replaceChildren();
    const rect = document.createElementNS(NS, 'rect');
    for (const [k, val] of Object.entries({ x: z[0], y: -z[3], width: z[2] - z[0], height: z[3] - z[1] })) rect.setAttribute(k, val);
    rect.setAttribute('vector-effect', 'non-scaling-stroke');
    Object.assign(rect.style, { fill: 'var(--warn-bg)', stroke: 'var(--warn)', strokeWidth: '2' });
    zoneLayer.appendChild(rect);
    apply();
  }
  function toUser(cx, cy) {
    const r = wrap.getBoundingClientRect();
    return { x: vb.x + (cx - r.left) / r.width * vb.w, y: vb.y + (cy - r.top) / r.height * vb.hgt };
  }
  function zoom(f, cx, cy) {
    if (!vb) return;
    const r = wrap.getBoundingClientRect();
    const p = cx === undefined ? { x: vb.x + vb.w / 2, y: vb.y + vb.hgt / 2 } : toUser(cx, cy);
    const nw = vb.w * f, nh = vb.hgt * f;
    if (nw < base.w / 2000 || nw > base.w * 20) return;
    const fx = cx === undefined ? 0.5 : (cx - r.left) / r.width, fy = cy === undefined ? 0.5 : (cy - r.top) / r.height;
    vb = { x: p.x - fx * nw, y: p.y - fy * nh, w: nw, hgt: nh };
    apply();
  }
  const onWheel = e => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.18 : 1 / 1.18, e.clientX, e.clientY); };
  let drag = null;
  const onDown = e => {
    if (measuring) {
      const p = toUser(e.clientX, e.clientY);
      pts.push(p);
      drawMeasure();
      if (pts.length === 2) {
        const dx = pts[1].x - pts[0].x, dy = -(pts[1].y - pts[0].y);
        const d = Math.hypot(dx, dy);
        hud.textContent = `Distance ${d.toFixed(2)} ${unit}   ΔX ${dx.toFixed(2)}   ΔY ${dy.toFixed(2)}   Angle ${(Math.atan2(dy, dx) * 180 / Math.PI).toFixed(1)}°`;
        pts = [];
      } else hud.textContent = 'Click the second point…';
      return;
    }
    drag = { x: e.clientX, y: e.clientY, vb: { ...vb } };
    wrap.style.cursor = 'grabbing';
  };
  const onMove = e => {
    if (!drag) return;
    const r = wrap.getBoundingClientRect();
    vb.x = drag.vb.x - (e.clientX - drag.x) / r.width * vb.w;
    vb.y = drag.vb.y - (e.clientY - drag.y) / r.height * vb.hgt;
    apply();
  };
  const onUp = () => { drag = null; wrap.style.cursor = ''; };
  function drawMeasure() {
    const svg = layers[layers.length - 1];
    if (mGroup) mGroup.remove();
    const NS = 'http://www.w3.org/2000/svg';
    mGroup = document.createElementNS(NS, 'g');
    const r = vb.w / 160;
    for (const p of pts) {
      const c = document.createElementNS(NS, 'circle');
      c.setAttribute('cx', p.x); c.setAttribute('cy', p.y); c.setAttribute('r', r); c.setAttribute('class', 'measure-pt');
      mGroup.appendChild(c);
    }
    svg.appendChild(mGroup);
  }
  const onKey = e => { if (e.key === 'Escape' && measuring) { e.stopPropagation(); measureBtn.click(); } };
  wrap.addEventListener('wheel', onWheel, { passive: false });
  wrap.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  document.addEventListener('keydown', onKey, true);
  const ro = new ResizeObserver(() => { if (base) fit(); });
  ro.observe(wrap);
  load();
  return () => {
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
    document.removeEventListener('keydown', onKey, true);
    ro.disconnect();
  };
}

// ------------------------------------------------------------------ images
function imageViewer(stage, tools, src, name) {
  stage.classList.add('paper');
  let s = 1, x = 0, y = 0, drag = null;
  const img = h('img', { src, alt: name, style: { position: 'absolute', left: '50%', top: '50%', maxWidth: '90%', maxHeight: '90%', transformOrigin: 'center', userSelect: 'none' }, draggable: 'false' });
  const set = () => { img.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(${s})`; };
  set();
  stage.appendChild(img);
  stage.addEventListener('wheel', e => { e.preventDefault(); s = Math.max(0.2, Math.min(12, s * (e.deltaY > 0 ? 0.87 : 1.15))); set(); }, { passive: false });
  stage.addEventListener('mousedown', e => { drag = { x: e.clientX - x, y: e.clientY - y }; });
  const mv = e => { if (drag) { x = e.clientX - drag.x; y = e.clientY - drag.y; set(); } };
  const up = () => { drag = null; };
  window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up);
  tools.append(h('button', { class: 'btn', onclick: () => { s = 1; x = y = 0; set(); } }, icon('fit', 16), 'Fit'));
  return () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
}

// ------------------------------------------------------------------ 3D (WebGL)
function parseSTL(buf) {
  const dv = new DataView(buf);
  let tris = 0, pos;
  const isBinary = buf.byteLength >= 84 && 84 + dv.getUint32(80, true) * 50 === buf.byteLength;
  if (isBinary) {
    tris = dv.getUint32(80, true);
    pos = new Float32Array(tris * 9);
    for (let i = 0; i < tris; i++) {
      const o = 84 + i * 50 + 12;
      for (let k = 0; k < 9; k++) pos[i * 9 + k] = dv.getFloat32(o + k * 4, true);
    }
  } else {
    const text = new TextDecoder().decode(buf);
    const nums = [];
    const re = /vertex\s+([-+\deE.]+)\s+([-+\deE.]+)\s+([-+\deE.]+)/g;
    let m;
    while ((m = re.exec(text))) nums.push(+m[1], +m[2], +m[3]);
    pos = new Float32Array(nums);
    tris = Math.floor(nums.length / 9);
  }
  const nrm = new Float32Array(pos.length);
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < tris; i++) {
    const o = i * 9;
    const ax = pos[o], ay = pos[o + 1], az = pos[o + 2];
    const ux = pos[o + 3] - ax, uy = pos[o + 4] - ay, uz = pos[o + 5] - az;
    const vx = pos[o + 6] - ax, vy = pos[o + 7] - ay, vz = pos[o + 8] - az;
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
    for (let k = 0; k < 3; k++) {
      nrm[o + k * 3] = nx; nrm[o + k * 3 + 1] = ny; nrm[o + k * 3 + 2] = nz;
      for (let c = 0; c < 3; c++) { const val = pos[o + k * 3 + c]; if (val < min[c]) min[c] = val; if (val > max[c]) max[c] = val; }
    }
  }
  return { pos, nrm, tris, min, max };
}

const M4 = {
  persp(fov, a, n, f) { const t = 1 / Math.tan(fov / 2); return [t / a, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) / (n - f), -1, 0, 0, 2 * f * n / (n - f), 0]; },
  look(e, c, u) {
    let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2]; let l = Math.hypot(zx, zy, zz); zx /= l; zy /= l; zz /= l;
    let xx = u[1] * zz - u[2] * zy, xy = u[2] * zx - u[0] * zz, xz = u[0] * zy - u[1] * zx; l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    return [xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0, -(xx * e[0] + xy * e[1] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1];
  },
  mul(a, b) { const o = new Array(16); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k]; o[i * 4 + j] = s; } return o; },
};

// A colour variable from app.css as WebGL wants it: [r, g, b] from 0 to 1 (6-digit hex values only).
function cssRgb(el, name, fallback) {
  const m = getComputedStyle(el).getPropertyValue(name).trim().match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  return m ? m.slice(1).map(x => parseInt(x, 16) / 255) : fallback;
}

function stlViewer(stage, tools, det, v) {
  stage.classList.add('model');
  const modelRgb = cssRgb(stage, '--viewer-model', [0.62, 0.66, 0.70]);
  const edgeRgb = cssRgb(stage, '--viewer-accent', [0.95, 0.45, 0.23]);
  const canvas = h('canvas', { 'aria-label': `3D view of ${det.filename}` });
  const hud = h('div', { class: 'hud' }, 'Loading model…');
  stage.append(canvas, hud);
  const gl = canvas.getContext('webgl', { antialias: true }) || canvas.getContext('experimental-webgl');
  if (!gl) {
    hud.textContent = 'This browser cannot show 3D (WebGL is off). Showing the preview image instead.';
    canvas.remove();
    stage.appendChild(h('img', { src: fileUrl(det.id, 'preview', v.version), style: { position: 'absolute', inset: '10%', width: '80%', height: '80%', objectFit: 'contain' }, alt: '' }));
    return null;
  }
  const vs = `attribute vec3 p; attribute vec3 n; uniform mat4 mvp; uniform mat4 mv; uniform mat3 nm; varying vec3 vn; varying vec3 vp;
    void main(){ vn = nm * n; vp = (mv * vec4(p,1.0)).xyz; gl_Position = mvp * vec4(p,1.0); }`;
  const fs = `precision mediump float; varying vec3 vn; varying vec3 vp; uniform vec3 col; uniform float uFlat;
    void main(){ if (uFlat > 0.5) { gl_FragColor = vec4(col,1.0); return; }
      vec3 N = normalize(vn); if (!gl_FrontFacing) N = -N; vec3 L = normalize(vec3(0.35,0.55,1.0)); vec3 V = normalize(-vp);
      float d = max(dot(N,L),0.0); float s = pow(max(dot(N, normalize(L+V)),0.0), 48.0) * 0.35;
      float rim = pow(1.0 - max(dot(N,V),0.0), 3.0) * 0.18;
      gl_FragColor = vec4(col * (0.30 + 0.70*d) + s + rim, 1.0); }`;
  const sh = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(prog);
  gl.useProgram(prog);
  const loc = { p: gl.getAttribLocation(prog, 'p'), n: gl.getAttribLocation(prog, 'n'), mvp: gl.getUniformLocation(prog, 'mvp'), mv: gl.getUniformLocation(prog, 'mv'), nm: gl.getUniformLocation(prog, 'nm'), col: gl.getUniformLocation(prog, 'col'), flat: gl.getUniformLocation(prog, 'uFlat') };
  let mesh = null, pBuf, nBuf, eBuf = null, eCount = 0;
  let wire = false;
  const cam = { yaw: -0.8, pitch: 0.5, dist: 1, target: [0, 0, 0], radius: 1 };
  let raf = 0;
  const draw = () => {
    raf = 0;
    if (!mesh) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth * dpr, hh = canvas.clientHeight * dpr;
    if (canvas.width !== w || canvas.height !== hh) { canvas.width = w; canvas.height = hh; }
    gl.viewport(0, 0, w, hh);
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    const cp = Math.cos(cam.pitch);
    const eye = [cam.target[0] + cam.dist * cp * Math.cos(cam.yaw), cam.target[1] + cam.dist * cp * Math.sin(cam.yaw), cam.target[2] + cam.dist * Math.sin(cam.pitch)];
    const view = M4.look(eye, cam.target, [0, 0, 1]);
    const proj = M4.persp(35 * Math.PI / 180, w / Math.max(hh, 1), cam.dist / 200, cam.dist * 20 + cam.radius * 4);
    gl.uniformMatrix4fv(loc.mvp, false, new Float32Array(M4.mul(proj, view)));
    gl.uniformMatrix4fv(loc.mv, false, new Float32Array(view));
    gl.uniformMatrix3fv(loc.nm, false, new Float32Array([view[0], view[1], view[2], view[4], view[5], view[6], view[8], view[9], view[10]]));
    gl.bindBuffer(gl.ARRAY_BUFFER, pBuf); gl.enableVertexAttribArray(loc.p); gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, nBuf); gl.enableVertexAttribArray(loc.n); gl.vertexAttribPointer(loc.n, 3, gl.FLOAT, false, 0, 0);
    gl.uniform1f(loc.flat, 0);
    gl.uniform3f(loc.col, ...modelRgb);
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
    gl.drawArrays(gl.TRIANGLES, 0, mesh.tris * 3);
    gl.disable(gl.POLYGON_OFFSET_FILL);
    if (wire && eBuf) {
      gl.bindBuffer(gl.ARRAY_BUFFER, eBuf); gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, 0, 0);
      gl.disableVertexAttribArray(loc.n);
      gl.uniform1f(loc.flat, 1); gl.uniform3f(loc.col, ...edgeRgb);
      gl.drawArrays(gl.LINES, 0, eCount);
    }
  };
  const redraw = () => { if (!raf) raf = requestAnimationFrame(draw); };
  const view = (yaw, pitch) => { cam.yaw = yaw; cam.pitch = pitch; cam.target = mesh ? mesh.center.slice() : cam.target; cam.dist = cam.radius * 2.8; redraw(); };
  const b = (label, fn, extra = {}) => { const x = h('button', { class: 'btn', ...extra }, label); x.addEventListener('click', () => fn(x)); return x; };
  tools.append(
    b('Iso', () => view(-0.8, 0.5)), b('Front', () => view(-Math.PI / 2, 0)), b('Top', () => view(-Math.PI / 2, 1.5699)), b('Right', () => view(0, 0)),
    b('Edges', x => {
      wire = !wire; x.setAttribute('aria-pressed', String(wire));
      if (wire && !eBuf && mesh) {
        const e = new Float32Array(mesh.tris * 18);
        for (let i = 0; i < mesh.tris; i++) {
          const o = i * 9, q = i * 18, P = mesh.pos;
          e.set([P[o], P[o + 1], P[o + 2], P[o + 3], P[o + 4], P[o + 5], P[o + 3], P[o + 4], P[o + 5], P[o + 6], P[o + 7], P[o + 8], P[o + 6], P[o + 7], P[o + 8], P[o], P[o + 1], P[o + 2]], q);
        }
        eBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, eBuf); gl.bufferData(gl.ARRAY_BUFFER, e, gl.STATIC_DRAW); eCount = mesh.tris * 6;
      }
      redraw();
    }, { 'aria-pressed': 'false' }));
  let drag = null;
  const down = e => { drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey, yaw: cam.yaw, pitch: cam.pitch, t: cam.target.slice() }; canvas.style.cursor = 'grabbing'; };
  const move = e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (drag.pan) {
      const s = cam.dist / canvas.clientHeight * 0.9;
      const right = [-Math.sin(cam.yaw), Math.cos(cam.yaw), 0];
      const up = [-Math.sin(cam.pitch) * Math.cos(cam.yaw), -Math.sin(cam.pitch) * Math.sin(cam.yaw), Math.cos(cam.pitch)];
      for (let k = 0; k < 3; k++) cam.target[k] = drag.t[k] - right[k] * dx * s + up[k] * dy * s;
    } else {
      cam.yaw = drag.yaw - dx * 0.008;
      cam.pitch = Math.max(-1.56, Math.min(1.56, drag.pitch + dy * 0.008));
    }
    redraw();
  };
  const up = () => { drag = null; canvas.style.cursor = ''; };
  canvas.addEventListener('mousedown', down);
  canvas.addEventListener('contextmenu', e => e.preventDefault());
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  canvas.addEventListener('wheel', e => { e.preventDefault(); cam.dist = Math.max(cam.radius * 0.05, Math.min(cam.radius * 30, cam.dist * (e.deltaY > 0 ? 1.12 : 0.89))); redraw(); }, { passive: false });
  const ro = new ResizeObserver(redraw); ro.observe(canvas);

  fetch(fileUrl(det.id, 'viewer', v.version)).then(r => { if (!r.ok) throw new Error('Could not load the model'); return r.arrayBuffer(); }).then(buf => {
    mesh = parseSTL(buf);
    if (!mesh.tris) throw new Error('The model has no triangles');
    pBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, pBuf); gl.bufferData(gl.ARRAY_BUFFER, mesh.pos, gl.STATIC_DRAW);
    nBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, nBuf); gl.bufferData(gl.ARRAY_BUFFER, mesh.nrm, gl.STATIC_DRAW);
    mesh.center = [0, 1, 2].map(k => (mesh.min[k] + mesh.max[k]) / 2);
    const size = [0, 1, 2].map(k => mesh.max[k] - mesh.min[k]);
    cam.radius = Math.hypot(...size) / 2 || 1;
    view(-0.8, 0.5);
    hud.textContent = `Size X ${size[0].toFixed(1)} × Y ${size[1].toFixed(1)} × Z ${size[2].toFixed(1)} mm · ${mesh.tris.toLocaleString()} triangles · drag to rotate, right-drag to pan, scroll to zoom`;
  }).catch(e => { hud.textContent = e.message; toast(e.message, 'err'); });

  return () => {
    window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up);
    ro.disconnect(); if (raf) cancelAnimationFrame(raf);
    const ext = gl.getExtension('WEBGL_lose_context'); if (ext) ext.loseContext();
  };
}

export { close as closeViewer };
