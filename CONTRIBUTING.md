# Contributing to Kosha

Thank you for helping. Every kind of contribution counts, and most of it is not code.

| You are | A good first step |
| --- | --- |
| Someone using Kosha | Report what confused you, with a screenshot; suggest clearer words for a message |
| A CAD or ERP specialist | Test an add-in with your CAD or ERP version and report the result; write a connector Kosha lacks |
| A developer | Pick an issue labelled `good first issue` or `help wanted` and say you are taking it |
| A translator | Translation will open once the screen text moves to text files (planned for 1.0) |
| A standards expert | Add or check rows in the standard-parts tables in `packs/` |
| A company using Kosha | Share what you changed, publish your add-ins, donate test files you are allowed to share |

## Set up a development copy

```bash
git clone https://github.com/kosha-plm/kosha.git && cd kosha
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt pytest ruff
pip install -e sdk/python
cd server && KOSHA_DATA_DIR=../demo-data python -m kosha --demo    # http://localhost:8080
```

Run the tests before you start, so you know they were green:

```bash
cd server && pytest && python tests/test_api.py && python tests/test_import.py && cd ..
pytest sdk/python/tests addins/erpnext/tests
ruff check server sdk addins
python scripts/spdx_headers.py
```

## Making a change

1. **Talk first for anything big.** Open an issue or a discussion before a large change or a new
   dependency, so nobody spends a week on something that cannot be merged.
2. **Fork, branch, change.** Keep pull requests small (as a guide, under about 400 changed lines).
   Follow the development guide: reuse before you write, nothing vendor-specific in the core,
   every change with its tests, plain words on screen.
3. **Sign off every commit** with `git commit -s`. The `Signed-off-by` line certifies the
   [Developer Certificate of Origin](https://developercertificate.org/): that you wrote the change
   or otherwise have the right to contribute it under the licence of the folder it touches.
   Kosha has no contributor licence agreement.
4. **Write a clear commit message** in the Conventional Commits style, for example
   `fix(bom): keep line order when importing indented numbers`.
5. **Open a pull request** with the template filled in: what changed, why, the issue it closes,
   screenshots for screen changes, and how you tested it.
6. **Review.** A maintainer answers within three working days. Questions are about the code,
   never about you.

## Rules that are checked automatically

- Every source file starts with an SPDX licence line (`python scripts/spdx_headers.py --fix` adds it).
  The core is AGPL-3.0-or-later; `sdk/` and `addins/` are Apache-2.0; `packs/` is CC BY 4.0.
- No real company, customer, supplier or person names in code, tests or demo data. Use the
  invented *Demo Engineering* company and generic parts.
- No secrets in the repository.
- Dependencies must have a licence allowed for the folder they are used in (development guide, 2.3).

## Data contributions (packs, standard parts)

Contribute only data you may share under CC BY 4.0: your own measurements, public facts, or data
its owner releases. Record the source standard and edition for every row. Never copy from paid
standards beyond the facts needed (sizes, designations), and never from catalogue services that
forbid redistribution.

## Conduct and security

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). Security problems are
reported privately, as described in [SECURITY.md](SECURITY.md), never in public issues.
