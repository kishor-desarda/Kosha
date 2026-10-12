# Kosha

**Open-source product lifecycle management (PLM) for engineering companies.**
Drawings, 3D models, revisions, bills of materials, change orders and ERP sync in one place,
built for design offices that work in SolidWorks, AutoCAD, GStarCAD and other CAD programs.

*Kosha* (कोश) means a treasury, a place where valuable things are kept safe.

![Explorer in Guided mode](docs/screenshots/02-explorer-guided.png)

> **Status: early preview (0.5.0.dev).** The core works end to end and is tested, but expect
> changes before 1.0. Try it with the demo data, and tell us what you think.

## Why Kosha

- **Never lose a file.** Every version is kept once by its SHA-256 fingerprint, written safely
  to disk before Kosha says "checked in", and backed up with a check of every copy.
- **Easy for everyone.** A Windows-Explorer-like screen, a Guided mode for occasional users and an
  Expert mode with keyboard shortcuts. Every greyed-out button says why.
- **Open and connectable.** A documented REST API and an add-in framework: ERP connectors, CAD
  add-ins, file types and industry packs plug in without touching the core.
- **Free to run.** Runs on one small Linux server. No per-user fees, no cloud lock-in.

## What it does today

| Area | What Kosha does |
| --- | --- |
| **Revision control** | Check-out locks a file; check-in creates a version. Revisions run A, B, C… (skipping I, O, Q, S, X, Z as ASME Y14.35 recommends). Lifecycle *In work → In review → Released → Obsolete*, with role checks. Released revisions are locked; **Revise** starts the next one. Everything is in the audit log. |
| **All engineering data** | Folders by product line hold SolidWorks, DWG, DXF, PDF, STEP, STL, Office files and images. Files link to **items** (the part master) by part number, read from the file name, the DWG/DXF title block or SolidWorks custom properties. |
| **Engineering BOMs** | Built from CAD assembly structure at check-in, imported from a BOM table (indented numbers create sub-assemblies), or edited by hand. Validation (unreleased parts, missing material, duplicates, loops), comparison between revisions, Excel export, copy forward on revise. |
| **ERP sync** | On release, items and BOMs are queued and sent to the ERP through a connector add-in (ERPNext today): children first, unchanged BOMs not sent again, dry-run by default, failures kept with the ERP's own message and a **Retry** button. Stock and cost are read back. |
| **Change orders** | ECOs collect the affected files, start new revisions, route to named approvers, and **Implement** releases everything and triggers the ERP push. |
| **Duplicate finder** | Four checks across legacy data: identical files, same part number, similar geometry (DXF/DWG shape signatures; 3D size and volume) and similar descriptions ("BLT HEX M16 X 60 8.8" = "HEX BOLT M16X60 GR8.8"). Review side by side, choose a standard part, export to Excel. |
| **Legacy import** | Imports a file share with a dry run and an Excel report, skips copies and junk files, keeps legacy revisions, and can be re-run safely. |
| **Viewer** | DXF/DWG drawn in the browser with pan, zoom, measure and red/blue revision overlay; 3D models in a WebGL viewer; PDFs and images. |
| **Add-ins** | Manifest, permissions approved by the admin, automatic switch-off of failing add-ins, settings forms generated from the manifest, Python SDK with conformance tests. |

<details>
<summary>More screens</summary>

| | |
| --- | --- |
| ![Sign in](docs/screenshots/00-sign-in.png) | ![Home](docs/screenshots/01-home-guided.png) |
| ![Right-click menu](docs/screenshots/03-right-click-menu.png) | ![Expert mode](docs/screenshots/04-explorer-expert.png) |
| ![Engineering BOM](docs/screenshots/07-engineering-bom.png) | ![Change order](docs/screenshots/08-change-order.png) |
| ![Duplicate finder](docs/screenshots/09-duplicates.png) | ![ERP sync](docs/screenshots/10-erp-sync.png) |
| ![Add-ins](docs/screenshots/11-admin-addins.png) | |

Regenerate them with `python scripts/screenshots.py` (needs Playwright).
</details>

## Try it in two minutes

You need Python 3.10 or newer.

```bash
git clone https://github.com/kosha-plm/kosha.git && cd kosha
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt
cd server && KOSHA_DATA_DIR=../demo-data python -m kosha --demo
```

Open <http://localhost:8080>. The demo company, *Demo Engineering*, makes mixers, conveyors
and pumps; every name in it is invented. Demo users all have the password `demo-kosha-1`:

| User | Role | Starts in |
| --- | --- | --- |
| `asha` | engineer | Guided mode |
| `daniel`, `sofia` | approver | Expert mode |
| `tom` | viewer | Guided mode |

The `admin` password is printed when the database is created (set it with `KOSHA_ADMIN_PASSWORD`).
On Windows, `deploy\windows\install_server.bat` and `deploy\windows\run_demo.bat` do the same.
With Docker: `docker compose up` and open port 8080.

## Install on a server

Kosha's recommended server is **Ubuntu 24.04 LTS**.

```bash
sudo deploy/linux/install.sh
```

The script creates a `kosha` service user, installs Kosha in `/opt/kosha`, keeps data in
`/var/lib/kosha` and settings in `/etc/kosha/kosha.env`, starts a hardened systemd service and a
nightly backup timer, and prints the first admin password. Running it again upgrades Kosha, after
taking and checking a backup first. Put HTTPS in front of it with Caddy or nginx
(`deploy/linux/Caddyfile.example`).

Then sign in as `admin`, change the password, add users under **Admin › Users**, and check the
part-number pattern under **Admin › Numbering & revisions**.

**Backups:** `python -m kosha.backup <folder>` copies the database with SQLite's online backup,
runs an integrity check on the copy, and copies new file versions, checking each against its
fingerprint. Keep a second copy away from the server (3-2-1 rule) and test a restore every month.

### DWG previews (AutoCAD and GStarCAD files)

DWG is a closed format, so Kosha converts it to DXF on the server. Install **LibreDWG**
(open source) and set `KOSHA_DWG2DXF`, or download the free **ODA File Converter** yourself and
set `KOSHA_ODA_CONVERTER`. Then press **Admin › CAD conversion › Regenerate previews**. Without a
converter, DWG files are still stored, versioned and released; they just have no preview.

### Importing legacy data

Use **Admin › Import legacy data**, or `python -m kosha.importer <source> --to "Legacy Drawings/Old share" --dry-run`
for very large imports. Run a dry run first, read the Excel report, then import and open
**Duplicates** to find common parts.

### Connecting an ERP

Kosha talks to ERPs through connector add-ins. The official **ERPNext connector** is switched on
by default:

1. In ERPNext, create an integration user, give it permission to create Items and BOMs and read
   Bins, and generate an API key and secret.
2. Optionally install the small Frappe app in `addins/erpnext/frappe/kosha_connector`, which adds
   Kosha fields and a *View in Kosha* button to ERPNext.
3. In Kosha, open **ERP sync › Connection settings**, fill in the address, key and secret, press
   **Test connection**, then untick **Dry run** and tick **Connect**.

Other ERPs plug into the same `ErpConnector` interface (see *Add-ins* below).

## CAD add-ins

| Add-in | Folder | State |
| --- | --- | --- |
| SolidWorks 2021 and later | `addins/solidworks` | Written against the SolidWorks API; build and pilot on one PC first |
| GStarCAD | `addins/gstarcad` | Starter add-in on the shared CAD core; build against your GStarCAD SDK |
| Shared CAD core (`Kosha.Client`, `Kosha.CadCore`) | `sdk/dotnet` | Used by every CAD add-in |

Each designer keeps a local workspace folder (for example `C:\Kosha`) that mirrors Kosha's folders.
The add-ins check files in and out, upload properties, previews, 3D meshes and assembly structure,
and warn about duplicates.

## Add-ins

An add-in is a folder with a `kosha-addin.yaml` manifest and code that implements one of Kosha's
interfaces: ERP connector, file type, AI provider, sign-in, notifier, storage, importer, CAD
add-in, Office add-in, web extension or industry pack. Official add-ins live in `addins/`;
others are installed as Python packages or put in a folder listed in `KOSHA_ADDIN_DIR`, and stay
off until an admin has reviewed the permissions they ask for.

```bash
pip install -e sdk/python
kosha-sdk new erp-connector my-erp --publisher com.example
kosha-sdk check my-erp
```

Every call into an add-in runs through a guard with a time-out. An add-in that keeps failing is
switched off automatically and the admin is told why; Kosha carries on without it.

## Project layout

```text
server/kosha/        Python server (Starlette + SQLite): REST API, lifecycle, BOMs, ERP queue, add-in loader
server/kosha/cad/    DXF reader and SVG renderer, DWG conversion, SolidWorks preview reader, STL tools
server/tests/        unit tests (pytest) and end-to-end suites with a simulated ERPNext
web/                 browser client (plain JavaScript modules, no build step)
sdk/python/          add-in SDK (Apache-2.0): manifest, base classes, safe HTTP, conformance tests
sdk/dotnet/          shared C# libraries for CAD add-ins (Apache-2.0)
addins/              official add-ins (Apache-2.0): ERPNext, SolidWorks, GStarCAD
packs/               industry packs and standard-parts data (CC BY 4.0)
deploy/              Linux service, backup timer, Caddy example, Windows scripts for trying Kosha
docs/                documentation and decision records
```

## Tests

```bash
cd server
pytest                       # unit tests: add-in guard, file store, backups
python tests/test_api.py     # main workflows end to end, with a simulated ERPNext
python tests/test_import.py  # legacy import of a messy folder tree and all duplicate checks
cd .. && pytest sdk/python/tests addins/erpnext/tests
python scripts/smoke_ui.py   # browser smoke test (needs Playwright)
dotnet test sdk/dotnet/Kosha.CadCore.Tests   # shared CAD core (needs the .NET 8 SDK)
```

The same suites run on every pull request in GitHub Actions.

## Roles

- **Viewer:** browse, view and download.
- **Engineer:** add files, check out and in, submit, revise, build BOMs, raise change orders.
- **Approver:** everything an engineer can do, plus release, obsolete, approve change orders, release BOMs and push to the ERP.
- **Admin:** everything, plus users, settings, add-ins and the audit log.

## Roadmap

The full specification and the development guide are maintained alongside the project. In short:

- **0.5 preview:** this release, plus the remaining open-source groundwork.
- **1.0:** PostgreSQL, directory sign-in (LDAP / Active Directory, OpenID Connect), inputs library,
  measured change logs, deviations, copy design, standard-parts library, AutoCAD add-in, themes
  including dark mode.
- **1.1 and later:** AI assistance that stays optional (classification, change-log drafts, ask-Kosha),
  more ERP connectors, Office add-in, industry packs.

## Contributing

Contributions of every kind are welcome: bug reports, translations, test files you may share,
standard-parts data, add-ins and code. Read [CONTRIBUTING.md](CONTRIBUTING.md); commits are signed
off under the Developer Certificate of Origin (`git commit -s`). Please report security issues
privately as described in [SECURITY.md](SECURITY.md).

## Licence

- The Kosha server and web client: **GNU AGPL-3.0-or-later** ([LICENSE](LICENSE)).
- The SDKs and the official add-ins: **Apache-2.0** ([LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)),
  so companies can build and keep their own add-ins under any licence.
- Packs, standard-parts data and documentation: **CC BY 4.0**.

"Kosha" and the Kosha logo are covered by the [trademark policy](TRADEMARKS.md).
