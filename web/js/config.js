// SPDX-License-Identifier: AGPL-3.0-or-later
// Configurable products (#/products, #/config/<item>): options and choices, rules on BOM lines, constraints,
// configuring one combination, checking every combination; saved variants (#/variant/<id>).
import { h, mount, clear, icon, get, post, put, toast, fail, dialog, ago, can, fmtDate, tabs, S } from './core.js';
import { itemPicker } from './bom.js';

export async function renderProducts(container, _m, _p, { setTitle }) {
  setTitle('Configurable products');
  const [p, v] = await Promise.all([get('/api/products'), get('/api/variants')]);
  mount(container, h('div', { class: 'page-pad' },
    h('div', { class: 'page-head' }, h('div', { style: { flex: 1 } }, h('h1', null, 'Configurable products'),
      h('div', { class: 'sub' }, 'One product with options (motor power, voltage, seal), one BOM with every alternative, and a rule on each line that is not always used. Variants are the combinations saved for orders.')),
    h('a', { class: 'btn', href: '#/boms' }, 'Engineering BOMs'),
    can('engineer') ? h('button', { class: 'btn primary', onclick: newProduct }, icon('plus', 16), 'Make a product configurable') : null),
    h('div', { class: 'panel', style: { overflow: 'auto', marginBottom: '16px' } }, h('div', { class: 'panel-h' }, h('h2', null, 'Products')),
      p.products.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Product', 'Description', 'Options', 'Line rules', 'Variants'].map(t => h('th', null, t)))),
        h('tbody', null, p.products.map(x => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/config/${x.item_id}`; } },
          h('td', { class: 'mono' }, x.part_number), h('td', null, x.description), h('td', { class: 'num' }, x.options), h('td', { class: 'num' }, x.rules), h('td', { class: 'num' }, x.variants)))))
        : h('div', { class: 'empty' }, 'No configurable products yet. Open a product\'s BOM and press "Options and variants", or make one configurable here.')),
    h('div', { class: 'panel', style: { overflow: 'auto' } }, h('div', { class: 'panel-h' }, h('h2', null, 'Variants')), variantTable(v.variants))));
}

function variantTable(list) {
  return list.length ? h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Variant', 'Name', 'Product', 'Customer', 'Order', 'Saved'].map(t => h('th', null, t)))),
    h('tbody', null, list.map(x => h('tr', { class: `clickable ${x.withdrawn_at ? 'muted' : ''}`, onclick: () => { location.hash = `#/variant/${x.id}`; } },
      h('td', { class: 'mono nowrap' }, x.number, x.withdrawn_at ? h('span', { class: 'chip st-obsolete', style: { marginLeft: '6px' } }, 'Withdrawn') : null),
      h('td', null, x.name), h('td', { class: 'mono' }, x.part_number || ''), h('td', null, x.customer || '—'), h('td', null, x.order_ref || '—'), h('td', { class: 'muted' }, ago(x.created_at))))))
    : h('div', { class: 'empty' }, 'No variants saved yet.');
}

function newProduct() {
  let picked = null;
  const label = h('div', { class: 'small' }, 'Pick the product (its top assembly). Its BOM should list every alternative: all motors, all seals.');
  dialog({ title: 'Make a product configurable', body: [label, itemPicker(it => { picked = it; label.textContent = `Selected: ${it.part_number} ${it.description}`; }, 'Type the product part number')],
    actions: [{ label: 'Cancel' }, { label: 'Next: options', primary: true, onClick: () => {
      if (!picked) { toast('Pick the product first', 'err'); return false; }
      location.hash = `#/config/${picked.id}?tab=options`;
    } }] });
}

export async function renderConfig(container, [id], params, { setTitle }) {
  let tab = params.tab || 'configure';
  let source = params.source || 'released';
  let d;
  const choice = {};
  const body = h('div');
  const load = async data => {
    d = data || await get(`/api/items/${id}/config?source=${source}`);
    source = d.source;
    setTitle(`Options ${d.item.part_number}`);
    if (!d.options.length && tab === 'configure') tab = 'options';
    draw();
  };
  const engineer = can('engineer');

  const draw = () => {
    const sel = tabs([['configure', 'Configure'], ['options', `Options (${d.options.length})`], ['rules', 'Line rules'], ['check', 'Check every combination'], ['variants', `Variants (${d.variants.filter(v => !v.withdrawn_at).length})`]], tab, k => { tab = k; draw(); });
    const srcSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Which BOM' }, [['released', 'Released BOM'], ['in_work', 'BOM in work']].map(([k, l]) =>
      h('button', { 'aria-pressed': String(source === k), onclick: () => { source = k; load().catch(fail); } }, l)));
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } }, h('div', { class: 'small muted' }, 'Options and variants'),
        h('h1', null, h('a', { href: `#/item/${d.item.id}`, class: 'mono', style: { color: 'inherit', textDecoration: 'none' } }, d.item.part_number), ' ', h('span', { style: { fontWeight: 400 } }, d.item.description)),
        h('div', { class: 'sub' }, d.structure.revision ? `${source === 'released' ? 'Released' : 'In work'} BOM revision ${d.structure.revision}` : d.structure.empty_why)),
      srcSeg, d.structure.bom_id ? h('a', { class: 'btn', href: `#/bom/${d.structure.bom_id}` }, icon('bom', 16), 'Open BOM') : null),
      d.rule_problems.length ? h('div', { class: 'banner err', style: { marginBottom: '12px' } }, icon('warn'), h('div', null,
        `${d.rule_problems.length} line rule(s) no longer read: `, d.rule_problems.map(p => `${p.part_number} (${p.message})`).join('; '))) : null,
      h('div', { class: 'panel' }, sel, body)));
    clear(body);
    ({ configure: drawConfigure, options: drawOptions, rules: drawRules, check: drawCheck, variants: drawVariants })[tab]();
  };

  // ---- configure one combination
  const drawConfigure = () => {
    if (!d.options.length) { mount(body, h('div', { class: 'empty' }, 'No options yet. ', engineer ? 'Add them under Options.' : 'An engineer sets them up.')); return; }
    for (const o of d.options) if (!choice[o.code]) choice[o.code] = o.choices[0].code;
    const out = h('div', { style: { marginTop: '14px' } });
    const run = async () => {
      try {
        const c = await post(`/api/items/${id}/configure`, { choice, source });
        mount(out,
          c.allowed ? h('div', { class: 'banner ok small', style: { marginBottom: '10px' } }, icon('check'), h('div', null, `${c.summary}: ${c.rows.length} rows.`))
            : h('div', { class: 'banner err small', style: { marginBottom: '10px' } }, icon('warn'), h('div', null, 'This combination is not allowed. ', c.blocked_by.join(' '))),
          rowsTable(c.rows),
          c.allowed && engineer && source === 'released' ? h('div', { class: 'row', style: { marginTop: '12px' } }, h('button', { class: 'btn primary', onclick: () => saveVariant(c) }, icon('check', 16), 'Save as variant for an order')) : null,
          c.allowed && source !== 'released' ? h('p', { class: 'muted small' }, 'Variants are saved from the released BOM; switch to Released BOM to save one.') : null);
      } catch (e) { mount(out, h('div', { class: 'banner err small' }, icon('warn'), h('div', null, e.message))); }
    };
    mount(body, h('div', { class: 'panel-b' },
      h('div', { class: 'form-grid' }, d.options.map(o => h('div', { class: 'field' }, h('label', { for: `cf-${o.code}` }, `${o.name} (${o.code})`),
        h('select', { class: 'input', id: `cf-${o.code}`, onchange: e => { choice[o.code] = e.target.value; run(); } },
          o.choices.map(c => h('option', { value: c.code, selected: choice[o.code] === c.code }, `${c.label}${c.label !== c.code ? ` (${c.code})` : ''}`)))))),
      out));
    run();
  };

  const saveVariant = c => {
    const name = h('input', { class: 'input', id: 'sv-n', value: c.summary.slice(0, 120) });
    const cust = h('input', { class: 'input', id: 'sv-c' });
    const order = h('input', { class: 'input', id: 'sv-o', placeholder: 'e.g. SO-0042' });
    dialog({ title: 'Save as variant', body: [h('p', { class: 'small', style: { marginTop: 0 } }, `${c.summary}. The variant keeps this BOM as it is now, even when the design moves on.`),
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'sv-n' }, 'Name'), name), h('div', { class: 'field' }, h('label', { for: 'sv-c' }, 'Customer'), cust),
        h('div', { class: 'field' }, h('label', { for: 'sv-o' }, 'Sales order'), order))],
    actions: [{ label: 'Cancel' }, { label: 'Save variant', primary: true, onClick: async () => {
      const r = await post(`/api/items/${id}/variants`, { choice: c.choice, name: name.value, customer: cust.value, order_ref: order.value });
      toast(`Variant ${r.variant.number} saved`, 'ok');
      location.hash = `#/variant/${r.variant.id}`;
    } }] });
  };

  // ---- options and constraints
  const drawOptions = () => {
    const opts = d.options.map(o => ({ code: o.code, name: o.name, choices: o.choices.map(c => ({ ...c })) }));
    const cons = d.constraints.map(c => ({ if: c.if_rule, then: c.then_rule, message: c.message }));
    const ro = !engineer;
    const optList = h('div', { class: 'col', style: { gap: '12px' } });
    const drawOpts = () => mount(optList, opts.map((o, i) => h('div', { class: 'panel', style: { padding: '10px 12px' } },
      h('div', { class: 'row wrap' },
        h('input', { class: 'input mono', value: o.code, disabled: ro, 'aria-label': 'Option code', placeholder: 'MOTOR', style: { maxWidth: '140px' }, oninput: e => { o.code = e.target.value.toUpperCase(); } }),
        h('input', { class: 'input', value: o.name, disabled: ro, 'aria-label': 'Option name', placeholder: 'Motor power', style: { maxWidth: '260px' }, oninput: e => { o.name = e.target.value; } }),
        h('span', { class: 'spacer' }),
        ro ? null : h('button', { class: 'btn ghost sm', 'aria-label': `Remove option ${o.code}`, onclick: () => { opts.splice(i, 1); drawOpts(); } }, icon('trash', 14))),
      h('div', { class: 'row wrap', style: { marginTop: '8px', gap: '6px' } }, o.choices.map((c, j) => h('span', { class: 'row', style: { gap: '4px', border: '1px solid var(--line)', borderRadius: '6px', padding: '4px 6px' } },
        h('input', { class: 'input mono', value: c.code, disabled: ro, 'aria-label': `Choice code of ${o.code}`, placeholder: '7K5', style: { width: '90px' }, oninput: e => { c.code = e.target.value.toUpperCase(); } }),
        h('input', { class: 'input', value: c.label, disabled: ro, 'aria-label': `Choice name of ${o.code}`, placeholder: '7.5 kW', style: { width: '120px' }, oninput: e => { c.label = e.target.value; } }),
        ro ? null : h('button', { class: 'btn ghost sm', 'aria-label': `Remove choice ${c.code}`, onclick: () => { o.choices.splice(j, 1); drawOpts(); } }, icon('x', 12)))),
      ro ? null : h('button', { class: 'btn sm', onclick: () => { o.choices.push({ code: '', label: '' }); drawOpts(); } }, icon('plus', 12), 'Choice')))));
    drawOpts();
    const consList = h('div', { class: 'col', style: { gap: '6px' } });
    const drawCons = () => mount(consList, cons.length ? cons.map((c, i) => h('div', { class: 'row wrap' }, h('span', { class: 'small' }, 'If'),
      h('input', { class: 'input mono', value: c.if, disabled: ro, 'aria-label': 'If', placeholder: 'MOTOR = 11K', style: { maxWidth: '220px' }, oninput: e => { c.if = e.target.value; } }), h('span', { class: 'small' }, 'then'),
      h('input', { class: 'input mono', value: c.then, disabled: ro, 'aria-label': 'Then', placeholder: 'VOLTAGE = 415', style: { maxWidth: '220px' }, oninput: e => { c.then = e.target.value; } }),
      h('input', { class: 'input', value: c.message, disabled: ro, 'aria-label': 'Reason shown', placeholder: 'The 11 kW motor runs on 415 V only.', style: { flex: 1, minWidth: '200px' }, oninput: e => { c.message = e.target.value; } }),
      ro ? null : h('button', { class: 'btn ghost sm', 'aria-label': 'Remove constraint', onclick: () => { cons.splice(i, 1); drawCons(); } }, icon('trash', 14)))) : h('div', { class: 'muted small' }, 'No constraints: every combination is allowed.'));
    drawCons();
    mount(body, h('div', { class: 'panel-b col', style: { gap: '16px', maxWidth: '1000px' } },
      ro ? h('div', { class: 'banner info small' }, icon('info'), h('div', null, 'Only engineers change options.')) : null,
      h('div', null, h('h3', null, 'Options'), h('p', { class: 'muted small' }, 'Each option has a short code used in rules (MOTOR) and a name for people (Motor power), and at least two choices.'), optList,
        ro ? null : h('button', { class: 'btn sm', style: { marginTop: '8px' }, onclick: () => { opts.push({ code: '', name: '', choices: [{ code: '', label: '' }, { code: '', label: '' }] }); drawOpts(); } }, icon('plus', 14), 'Add option')),
      h('div', null, h('h3', null, 'Constraints'), h('p', { class: 'muted small' }, 'Combinations that are not allowed, with the reason shown to whoever chooses.'), consList,
        ro ? null : h('button', { class: 'btn sm', style: { marginTop: '8px' }, onclick: () => { cons.push({ if: '', then: '', message: '' }); drawCons(); } }, icon('plus', 14), 'Add constraint')),
      ro ? null : h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: async () => {
        try {
          let r = await put(`/api/items/${id}/config/options`, { options: opts });
          r = await put(`/api/items/${id}/config/constraints`, { constraints: cons.filter(c => c.if || c.then) });
          toast('Options saved', 'ok'); load(r);
        } catch (e) { fail(e); }
      } }, icon('check', 16), 'Save options and constraints'))));
  };

  // ---- rules on lines
  const drawRules = () => {
    const rows = d.structure.rows;
    if (!rows.length) { mount(body, h('div', { class: 'empty' }, d.structure.empty_why || 'The BOM is empty.')); return; }
    const changed = new Map();
    const codes = d.options.map(o => `${o.code}: ${o.choices.map(c => c.code).join(', ')}`).join(' · ');
    const saveBtn = h('button', { class: 'btn primary sm', disabled: true, title: 'Change a rule first', onclick: async () => {
      try { const r = await put(`/api/items/${id}/config/rules`, { rules: [...changed.values()] }); toast('Rules saved', 'ok'); load(r); } catch (e) { fail(e); }
    } }, icon('check', 14), 'Save rules');
    mount(body, h('div', { class: 'panel-b' },
      h('p', { class: 'small', style: { marginTop: 0 } }, 'Leave the rule empty for lines that are always used. Write rules like ', h('code', null, 'MOTOR = 7K5'), ', ', h('code', null, 'MOTOR in (5K5, 7K5) and VOLTAGE = 415'), ' or ', h('code', null, 'not SEAL = PTFE'), '.'),
      d.options.length ? h('p', { class: 'muted small' }, `Options: ${codes}`) : h('div', { class: 'banner small' }, icon('warn'), h('div', null, 'Set up the options first; rules choose between them.')),
      h('div', { class: 'row', style: { justifyContent: 'flex-end', marginBottom: '8px' } }, engineer ? saveBtn : null),
      h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl bom-grid' },
        h('thead', null, h('tr', null, ['Level', 'Part number', 'Description', 'Qty', 'Used when'].map(t => h('th', null, t)))),
        h('tbody', null, rows.map(r => h('tr', null,
          h('td', { class: 'lvl', style: { paddingLeft: `${8 + (r.level - 1) * 18}px` } }, `L${r.level}`),
          h('td', { class: 'mono nowrap' }, r.part_number), h('td', null, r.description), h('td', { class: 'num' }, r.qty),
          h('td', { style: { minWidth: '280px' } }, engineer && d.options.length ? h('input', { class: 'input mono', value: r.rule, placeholder: 'always', 'aria-label': `Rule for ${r.part_number}`,
            oninput: e => { changed.set(`${r.parent_item_id}/${r.item_id}`, { parent_item_id: r.parent_item_id, child_item_id: r.item_id, rule: e.target.value }); saveBtn.disabled = false; saveBtn.title = ''; } })
            : h('span', { class: 'mono small' }, r.rule || h('span', { class: 'muted' }, 'always'))))))))));
  };

  // ---- check every combination
  const drawCheck = () => {
    const out = h('div');
    mount(body, h('div', { class: 'panel-b' },
      h('p', { class: 'small', style: { marginTop: 0 } }, 'Kosha builds the BOM of every allowed combination and reports lines no combination uses, choices that change nothing, and assemblies left empty.'),
      h('button', { class: 'btn primary', disabled: !d.options.length, title: d.options.length ? '' : 'Set up the options first', onclick: async () => {
        try {
          const r = await post(`/api/items/${id}/config/check`, { source });
          mount(out, h('p', null, `${r.combinations} combinations: ${r.allowed} allowed, ${r.blocked} not allowed by the constraints, ${r.distinct_boms} different BOMs.`),
            r.issues.length ? r.issues.map(i => h('div', { class: 'issue' }, h('span', { class: `sev-${i.severity}` }, icon(i.severity === 'info' ? 'info' : 'warn', 16)),
              h('div', null, i.part_number ? h('b', { class: 'mono' }, i.part_number, ' ') : null, i.message)))
              : h('div', { class: 'banner ok small' }, icon('check'), h('div', null, 'No problems: every line is used, every choice matters, no assembly is left empty.')));
        } catch (e) { mount(out, h('div', { class: 'banner err small' }, icon('warn'), h('div', null, e.message))); }
      } }, icon('check', 16), 'Check every combination'), out));
  };

  const drawVariants = () => mount(body, h('div', { style: { overflow: 'auto' } }, variantTable(d.variants)));

  await load();
}

function rowsTable(rows) {
  return rows.length ? h('div', { style: { overflow: 'auto' } }, h('table', { class: 'tbl bom-grid' },
    h('thead', null, h('tr', null, ['Level', 'Part number', 'Description', 'Qty', 'Total qty', 'Rev', 'Used when'].map(t => h('th', null, t)))),
    h('tbody', null, rows.map(r => h('tr', null,
      h('td', { class: 'lvl', style: { paddingLeft: `${8 + (r.level - 1) * 18}px` } }, `L${r.level}`),
      h('td', { class: 'mono nowrap' }, h('a', { href: `#/item/${r.item_id}` }, r.part_number)), h('td', null, r.description),
      h('td', { class: 'num' }, r.qty), h('td', { class: 'num muted' }, r.ext_qty), h('td', null, r.revision),
      h('td', { class: 'mono small' }, r.rule || h('span', { class: 'muted' }, 'always')))))))
    : h('div', { class: 'empty' }, 'No lines.');
}

export async function renderVariant(container, [id], _p, { setTitle }) {
  const load = async () => {
    const v = (await get(`/api/variants/${id}`)).variant;
    setTitle(`Variant ${v.number}`);
    mount(container, h('div', { class: 'page-pad' },
      h('div', { class: 'page-head' }, h('div', { style: { flex: 1, minWidth: '300px' } }, h('div', { class: 'small muted' }, 'Variant'),
        h('h1', null, h('span', { class: 'mono' }, v.number), ' ', h('span', { style: { fontWeight: 400 } }, v.name),
          v.withdrawn_at ? h('span', { class: 'chip st-obsolete', style: { marginLeft: '8px' } }, 'Withdrawn') : null),
        h('div', { class: 'sub' }, h('a', { href: `#/config/${v.item_id}?tab=variants` }, `${v.part_number} ${v.description}`), ` · BOM revision ${v.revision} · saved ${fmtDate(v.created_at)}${v.created_by_name ? ` by ${v.created_by_name}` : ''}`)),
      h('a', { class: 'btn', href: `/api/variants/${v.id}/export?access_token=${encodeURIComponent(S.token)}` }, icon('download', 16), 'Excel'),
      can('engineer') && !v.withdrawn_at ? h('button', { class: 'btn', onclick: () => recordMachine(v) }, icon('plus', 16), 'Record a machine') : null,
      can('approver') && !v.withdrawn_at ? h('button', { class: 'btn', onclick: () => withdraw(v) }, 'Withdraw') : null),
      v.withdrawn_at ? h('div', { class: 'banner small', style: { marginBottom: '12px' } }, icon('warn'), h('div', null, `Withdrawn ${fmtDate(v.withdrawn_at)}: ${v.withdrawn_reason}. No new machines can be recorded from it.`)) : null,
      h('div', { class: 'row wrap', style: { alignItems: 'flex-start', gap: '16px' } },
        h('div', { class: 'panel', style: { flex: '1 1 640px', minWidth: 0 } }, h('div', { class: 'panel-h' }, h('h2', null, 'BOM of this variant'), h('span', { class: 'muted small' }, 'Kept as saved; it does not change when the design moves on.')), rowsTable(v.lines)),
        h('aside', { class: 'panel', style: { flex: '0 1 320px', minWidth: '260px' } }, h('div', { class: 'panel-h' }, h('h2', null, 'Choices')),
          h('div', { class: 'panel-b col', style: { gap: '6px' } }, h('div', null, v.summary),
            Object.entries(v.choice).map(([k, c]) => h('div', { class: 'small mono' }, `${k} = ${c}`)),
            v.customer ? h('div', { class: 'small' }, `Customer: ${v.customer}`) : null, v.order_ref ? h('div', { class: 'small' }, `Order: ${v.order_ref}`) : null,
            v.note ? h('div', { class: 'small' }, v.note) : null,
            h('h3', { style: { margin: '8px 0 0' } }, 'Machines built'),
            v.machines.length ? v.machines.map(m => h('div', { class: 'small' }, h('a', { href: `#/bom-view/${v.item_id}?view=as_built&serial=${encodeURIComponent(m.serial)}` }, m.serial), m.customer ? ` · ${m.customer}` : ''))
              : h('div', { class: 'muted small' }, 'None yet.'))))));
  };
  const recordMachine = v => {
    const serial = h('input', { class: 'input mono', id: 'rm-s', placeholder: 'MX26-0042' });
    const cust = h('input', { class: 'input', id: 'rm-c', value: v.customer });
    const site = h('input', { class: 'input', id: 'rm-site' });
    dialog({ title: `Record a machine built as ${v.number}`, body: [h('p', { class: 'small', style: { marginTop: 0 } }, 'The machine\'s as-built BOM is this variant\'s BOM.'),
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('label', { for: 'rm-s' }, 'Serial number (from the nameplate)'), serial),
        h('div', { class: 'field' }, h('label', { for: 'rm-c' }, 'Customer'), cust), h('div', { class: 'field' }, h('label', { for: 'rm-site' }, 'Site'), site))],
    actions: [{ label: 'Cancel' }, { label: 'Record machine', primary: true, onClick: async () => {
      if (!serial.value.trim()) { toast('Type the serial number from the nameplate', 'err'); return false; }
      await post('/api/machines', { variant_id: v.id, serial: serial.value.trim(), customer: cust.value, site: site.value, order_ref: v.order_ref });
      toast('Machine recorded', 'ok'); load();
    } }] });
  };
  const withdraw = v => {
    const reason = h('input', { class: 'input', id: 'wv-r', placeholder: 'e.g. Order cancelled' });
    dialog({ title: `Withdraw ${v.number}`, body: [h('p', { class: 'small', style: { marginTop: 0 } }, 'The variant is kept for the record, but no new machines can be recorded from it.'), h('div', { class: 'field' }, h('label', { for: 'wv-r' }, 'Reason'), reason)],
      actions: [{ label: 'Cancel' }, { label: 'Withdraw', danger: true, onClick: async () => { await post(`/api/variants/${v.id}/withdraw`, { reason: reason.value }); load(); } }] });
  };
  await load();
}
