# Kosha: notes for contributors and coding agents

Kosha is an open-source PLM (AGPL-3.0 core, Apache-2.0 SDK and add-ins, CC BY 4.0 packs).
Read `README.md` first; the development rules below are the short form of the development guide.

## Layout

- `server/kosha/` Python server (Starlette + SQLite). One module per area (`lifecycle.py`, `boms.py`, `changes.py`, `erp.py` ...).
  - `api/` one module per area; every module is imported automatically (`api/__init__.py`), so a new feature adds a new file.
  - `migrations/` versioned database changes: add `m<YYYYMMDD>_<HHMM>_<area>_<what>.py` with `SQL = "..."` and/or `def up(conn)`. Never edit a released one; never drop data.
  - `addins.py` add-in registry and guard; `store.py` content-addressed file store; `backup.py` verified backups.
- `web/` browser client: plain JavaScript modules, no build step, `h()` helper from `core.js`, colours only through CSS variables in `app.css`.
- `sdk/python/kosha_sdk` (Apache-2.0) add-in SDK; `sdk/dotnet` shared C# CAD core with NUnit tests.
- `addins/<name>/` official add-ins, each with `kosha-addin.yaml`.
- `packs/` industry packs and standard-parts data (YAML/CSV, CC BY 4.0).

## Commands

```bash
cd server && pytest -q                    # unit tests
cd server && python tests/test_api.py     # end-to-end API workflows (simulated ERPNext)
cd server && python tests/test_import.py  # legacy import and duplicate checks
pytest -q sdk/python/tests addins/erpnext/tests
python scripts/smoke_ui.py                # browser smoke test (Playwright)
python scripts/test_office_taskpane.py    # Office task pane (web/office-addin) in test mode and a simulated Word
ruff check server sdk addins scripts
python scripts/spdx_headers.py            # every source file needs an SPDX header (--fix adds it)
```

CI (`.github/workflows/ci.yml`) runs all of these plus `dotnet test sdk/dotnet/Kosha.CadCore.Tests` and
`dotnet test addins/desktop/Kosha.Desktop.Core.Tests`. The AutoCAD and GStarCAD add-ins (and the DWG code they share in
`sdk/dotnet/Kosha.CadCore.Dwg`, compiled into both) need the CAD programs' assemblies and are not built in CI.

## Tests

- Unit tests: `server/tests/test_<area>.py` with pytest.
- End-to-end: use the fixtures `kosha` (empty installation) and `demo_kosha` (Demo Engineering data) from
  `server/tests/conftest.py`; each test module gets its own server process, data folder and port
  (`tests/harness.py`: `kosha.client()` signs in as admin, `kosha.add_user(name, role)` adds and signs in a user,
  `client.call(method, path, body=..., files=..., expect=...)`).
- In this development container run pytest through the wrapper that exposes the system packages:
  `/tmp/claude-0/-home-claude/820060bb-82fe-5378-951b-69c74aabfd08/scratchpad/tools/pytest.sh` (CI uses plain `pytest`).

## Rules

- **No data loss.** Writes go through `store.put_*` (fsync + atomic move) and `db.tx()`; business records are soft-deleted; every outside call (ERP, AI, e-mail) goes through a queue or the add-in guard with a time-out; jobs are idempotent.
- **Product-agnostic.** No real company, customer, supplier or person names anywhere. Demo data belongs to the invented *Demo Engineering* (mixers, conveyors, pumps; users asha, daniel, sofia, tom). Nothing vendor-specific in the core: ERPs, CAD programs, AI providers and notifiers live in add-ins behind interfaces.
- **Plain words on screen.** Buttons say what they do; every disabled control says why; errors say what happened, whether work is safe, and what to do next.
- **Every change brings tests** at the right level, and the full suite stays green.
- **Licences:** SPDX header on every file; only dependencies allowed by the development guide (permissive, or copyleft as separate programs).
- **Commits:** Conventional Commits style, signed off (`git commit -s`).
