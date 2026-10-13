# SPDX-License-Identifier: AGPL-3.0-or-later
"""QR codes for job cards and nameplates (specification 5.7): a QR code that opens a part in the shop view.

A small, self-contained encoder (ISO/IEC 18004): byte mode, error correction level M (15 % of the code can be dirty
or torn), versions 1 to 10 (up to 213 bytes, plenty for a link), with the mask chosen by the standard's penalty rules.
No outside library is needed. ``svg(text)`` gives a scalable picture for printing.
"""
from __future__ import annotations

# data codewords and error-correction layout at level M: (ec codewords per block, [(blocks, data codewords per block)])
_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
_EC_LEVEL_M = 0          # format bits of level M


class QrTooLong(ValueError):
    pass


# ---------------------------------------------------------------- Reed-Solomon over GF(256), polynomial 0x11D --

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(degree: int) -> list[int]:
    g = [1]
    for i in range(degree):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        g = nxt
    return g


def _ec(data: list[int], degree: int) -> list[int]:
    gen = _generator(degree)
    rem = list(data) + [0] * degree
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], coef)
    return rem[len(data):]


# ------------------------------------------------------------------------------------------- encoding --

def _codewords(data: bytes) -> tuple[int, list[int]]:
    for version in range(1, 11):
        ec_len, groups = _M[version]
        capacity = sum(n * k for n, k in groups)
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * len(data) <= capacity * 8:
            break
    else:
        raise QrTooLong(f"Too long for a QR code here ({len(data)} bytes; at most 213).")
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, capacity * 8 - len(bits)))
    put(0, (-len(bits)) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    # split into blocks, add error correction, interleave
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            blocks.append(words[pos:pos + k])
            pos += k
    ecs = [_ec(b, ec_len) for b in blocks]
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec_len):
        out += [e[i] for e in ecs]
    return version, out


def _bch_format(mask: int) -> int:
    data = (_EC_LEVEL_M << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _bch_version(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


class _Grid:
    def __init__(self, version: int):
        self.size = 17 + 4 * version
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]
        self.version = version

    def set(self, x: int, y: int, dark: bool) -> None:
        self.mod[y][x] = dark
        self.fixed[y][x] = True

    def functions(self) -> None:
        n = self.size
        for i in range(n):                                  # timing patterns
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):     # finders with their separators
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        d = max(abs(dx), abs(dy))
                        self.set(x, y, d not in (2, 4))
        pos = _ALIGN[self.version]
        last = len(pos) - 1
        for i, ay in enumerate(pos):                        # alignment patterns, not over the finders
            for j, ax in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)                                 # reserve; written for real after masking
        if self.version >= 7:
            bits = _bch_version(self.version)
            for i in range(18):
                b = (bits >> i) & 1 == 1
                a, c = n - 11 + i % 3, i // 3
                self.set(a, c, b)
                self.set(c, a, b)

    def format_bits(self, mask: int) -> None:
        n = self.size
        bits = _bch_format(mask)
        for i in range(0, 6):
            self.set(8, i, (bits >> i) & 1 == 1)
        self.set(8, 7, (bits >> 6) & 1 == 1)
        self.set(8, 8, (bits >> 7) & 1 == 1)
        self.set(7, 8, (bits >> 8) & 1 == 1)
        for i in range(9, 15):
            self.set(14 - i, 8, (bits >> i) & 1 == 1)
        for i in range(0, 8):
            self.set(n - 1 - i, 8, (bits >> i) & 1 == 1)
        for i in range(8, 15):
            self.set(8, n - 15 + i, (bits >> i) & 1 == 1)
        self.set(8, n - 8, True)                            # the dark module

    def data(self, words: list[int]) -> None:
        n = self.size
        i = 0
        total = len(words) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fixed[y][x] and i < total:
                        self.mod[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def masked(self, mask: int) -> list[list[bool]]:
        fn = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
              lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
              lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
              lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)[mask]
        return [[self.mod[y][x] ^ (not self.fixed[y][x] and fn(x, y)) for x in range(self.size)]
                for y in range(self.size)]


def _penalty(m: list[list[bool]]) -> int:
    n = len(m)
    score = 0
    for lines in (m, [list(col) for col in zip(*m)]):
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, v
            if run >= 5:
                score += run - 2
            s = "".join("1" if v else "0" for v in line)
            for pat in ("10111010000", "00001011101"):
                start = s.find(pat)
                while start != -1:
                    score += 40
                    start = s.find(pat, start + 1)
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(v for row in m for v in row)
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def matrix(text: str, mask: int | None = None) -> list[list[bool]]:
    """The modules of the QR code for ``text`` (True = dark), without the quiet zone."""
    version, words = _codewords(text.encode("utf-8"))
    best = None
    for m in ([mask] if mask is not None else range(8)):
        g = _Grid(version)
        g.functions()
        g.data(words)
        g.format_bits(m)
        out = g.masked(m)
        p = _penalty(out)
        if best is None or p < best[0]:
            best = (p, out)
    return best[1]


def svg(text: str, module: int = 8, label: str = "") -> str:
    """An SVG picture of the QR code with its quiet zone (and an optional caption under it)."""
    m = matrix(text)
    n = len(m)
    quiet = 4
    side = (n + 2 * quiet) * module
    extra = module * 4 if label else 0
    path = "".join(f"M{(x + quiet) * module},{(y + quiet) * module}h{module}v{module}h-{module}z"
                   for y in range(n) for x in range(n) if m[y][x])
    caption = ""
    if label:
        from xml.sax.saxutils import escape
        caption = (f'<text x="{side / 2}" y="{side + module * 2.5}" text-anchor="middle" font-family="monospace" '
                   f'font-size="{module * 2.2}">{escape(label)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side + extra}" width="{side}" '
            f'height="{side + extra}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/>{caption}</svg>')
