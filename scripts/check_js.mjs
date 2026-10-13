// SPDX-License-Identifier: AGPL-3.0-or-later
// Syntax check for the browser code, with the right parser for each file.
//
//   node scripts/check_js.mjs
//
// `node --check file.js` parses a .js file as CommonJS first and quietly accepts some files with ES-module
// syntax errors, so each file is copied to a temporary .mjs (web/js: ES modules) or .cjs (Office add-in:
// classic scripts) and checked there. Exits 1 and names every file with a syntax error.
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const groups = [
  { dir: join(root, 'web', 'js'), ext: '.mjs' },
  { dir: join(root, 'web', 'office-addin'), ext: '.cjs' },
];

const tmp = mkdtempSync(join(tmpdir(), 'kosha-js-'));
let checked = 0;
const bad = [];
try {
  for (const { dir, ext } of groups) {
    for (const name of readdirSync(dir).filter(n => n.endsWith('.js')).sort()) {
      const src = join(dir, name);
      const copy = join(tmp, name.replace(/\.js$/, ext));
      copyFileSync(src, copy);
      const r = spawnSync(process.execPath, ['--check', copy], { encoding: 'utf8' });
      checked += 1;
      if (r.status !== 0) {
        const where = relative(root, src);
        bad.push(where);
        const msg = (r.stderr || '').split(copy).join(where).trim();
        console.error(`::error file=${where}::syntax error\n${msg}\n`);
      }
    }
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
if (bad.length) {
  console.error(`${bad.length} of ${checked} files have syntax errors: ${bad.join(', ')}`);
  process.exit(1);
}
console.log(`${checked} files checked, no syntax errors`);
