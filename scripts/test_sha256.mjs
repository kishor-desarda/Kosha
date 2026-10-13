// SPDX-License-Identifier: AGPL-3.0-or-later
// The browser's piece-by-piece SHA-256 (web/js/sha256.js) against Node's, for every padding case and odd slicing.
import { createHash, randomBytes } from 'node:crypto';
import { Sha256 } from '../web/js/sha256.js';

let failures = 0;
const sizes = [0, 1, 3, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129, 1000, 4096, 100003];
for (const n of sizes) {
  const data = randomBytes(n);
  const want = createHash('sha256').update(data).digest('hex');
  for (const step of [1, 7, 64, 65, 1000, n || 1]) {
    const s = new Sha256();
    for (let i = 0; i < n; i += step) s.update(new Uint8Array(data.subarray(i, i + step)));
    const got = s.hex();
    if (got !== want) { failures++; console.log(`FAIL size ${n} step ${step}: ${got} != ${want}`); }
  }
}
if (new Sha256().update(new TextEncoder().encode('abc')).hex() !== 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad') failures++;
if (failures) process.exit(1);
console.log(`ok: SHA-256 matches for ${sizes.length} sizes, sliced six ways`);
