<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/kosha-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/brand/kosha-logo-primary.svg">
    <img src="docs/brand/kosha-logo-primary.svg" alt="Kosha: open-source PLM" width="400">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/kishor-desarda/Kosha/actions/workflows/ci.yml"><img src="https://github.com/kishor-desarda/Kosha/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-AGPL--3.0--or--later-262522" alt="Licence: AGPL-3.0-or-later"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-262522" alt="Python 3.10 or newer">
  <img src="https://img.shields.io/badge/status-1.0%20release%20candidate-EF5A28" alt="Status: 1.0 release candidate">
</p>

# Kosha: open-source PLM

**Open-source product lifecycle management (PLM) for engineering companies.**
Drawings, 3D models, revisions, bills of materials, change orders and ERP sync in one place,
built for design offices that work in SolidWorks, AutoCAD, GStarCAD and other CAD programs.
Kosha is a self-hosted PLM and PDM (product data management) system: one small Linux server,
a browser client, CAD and Office add-ins, and no per-user fees.

*Kosha* (कोश) means a treasury, a place where valuable things are kept safe.

![Explorer in Guided mode](docs/screenshots/02-explorer-guided.png)

> **Status: 1.0 release candidate (1.0.0rc1).** Every area of the specification works end to end and is tested;
> expect small changes from pilot feedback before 1.0. Try it with the demo data, and tell us what you think.

## Why Kosha

- **Never lose a file.** Every version is kept once by its SHA-256 fingerprint, written safely
  to disk before Kosha says "checked in", and backed up with a check of every copy.
- **Easy for everyone.** A Windows-Explorer-like screen, a Guided mode for occasional users and an
  Expert mode with keyboard shortcuts. Every greyed-out button says why.
- **Open and connectable.** A documented REST API (every server describes itself at `/api/docs` and
  `/api/openapi.json`) and an add-in framework: ERP connectors, CAD add-ins, file types and industry
  packs plug in without touching the core.
- **Free to run.** Runs on one small Linux server. No per-user fees, no cloud lock-in.

## What it does today

| Area | What Kosha does |
| --- | --- |
| **Revision control** | Check-out locks a file; check-in creates a version. Revisions run A, B, C… (skipping I, O, Q, S, X, Z as ASME Y14.35 recommends). Lifecycle *In work → In review → Released → Obsolete*, with role checks. Released revisions are locked; **Revise** starts the next one. Everything is in the audit log. |
| **All engineering data** | Folders by product line hold SolidWorks, DWG, DXF, PDF, STEP, STL, Office files and images. Files link to **items** (the part master) by part number, read from the file name, the DWG/DXF title block or SolidWorks custom properties. |
| **Engineering BOMs** | Built from CAD assembly structure at check-in, imported from a BOM table (indented numbers create sub-assemblies), or edited by hand. Validation (unreleased parts, missing material, duplicates, loops), comparison between revisions, Excel export, copy forward on revise. |
| **Manufacturing BOMs** | How the workshop builds each assembly: parts issued at each operation of a process plan (work centres, set-up and run times, instructions), sub-assemblies built with their parent, parts added for manufacturing. Checked against the engineering BOM down to every part, released after it, carried to the next revision, and sent to the ERP with its operations when the ERP builds from manufacturing BOMs. See [manufacturing BOMs](docs/manufacturing-bom.md). |
| **Options and variants** | Configurable products: options and choices (motor power, voltage, seal), one BOM with every alternative and a rule on each line that is not always used (`MOTOR in (5K5, 7K5) and VOLTAGE = 415`), constraints between options, the BOM of any combination, a check of every combination, and variants saved for orders with their BOM kept as it was; machines can be recorded from a variant. See [options and variants](docs/options-and-variants.md). |
| **ERP sync** | On release, items and BOMs are queued and sent to the ERP through a connector add-in (ERPNext, Odoo and Dolibarr today, and a file-exchange connector that writes CSV/JSON files for any other ERP): children first, unchanged BOMs not sent again, dry-run by default, failures kept with the ERP's own message and a **Retry** button. Stock and cost are read back. **Reconciliation** compares released items and BOMs with the ERP and lists the differences in plain words, with **Send again** and an Excel export. |
| **Notifications** | Change orders waiting for approval, failed ERP transfers and (for those who ask) new releases reach people by e-mail or in Teams, Slack or Telegram. Each person chooses under **My notifications**; an outbox delivers in the background and retries, so a broken mail server never holds up work. |
| **Change orders** | ECOs collect the affected files, start new revisions, route to named approvers, and **Implement** releases everything and triggers the ERP push. |
| **Duplicate finder** | Four checks across legacy data: identical files, same part number, similar geometry (DXF/DWG shape signatures; 3D size and volume) and similar descriptions ("BLT HEX M16 X 60 8.8" = "HEX BOLT M16X60 GR8.8"). Review side by side, choose a standard part, export to Excel. |
| **Legacy import** | Imports a file share with a dry run and an Excel report, skips copies and junk files, keeps legacy revisions, and can be re-run safely. |
| **Viewer** | DXF/DWG drawn in the browser with pan, zoom, measure and red/blue revision overlay; 3D models in a WebGL viewer; PDFs and images. |
| **Sign-in** | Local accounts with paused sign-in after repeated failures, or company accounts through **LDAP / Active Directory** and **OpenID Connect** (Keycloak and other providers) add-ins. Directory groups map to Kosha roles; the local `admin` keeps working when the directory is down. See [directory sign-in](docs/directory-sign-in.md). |
| **AI assistance (optional)** | Off until an admin switches it on. Suggests categories, materials and standard parts, drafts change logs from the measured changes, and answers questions from Kosha's own records with links (**Ask Kosha**), only from what the asking person may see. Nothing is saved until a person accepts it. Works with any OpenAI-compatible server, including local runners, through an add-in. See [AI assistance](docs/ai.md). |
| **Themes** | Light, Dark, High contrast or *Same as my computer*, chosen by each person; text meets WCAG AA contrast in every theme. See [themes](docs/themes.md). |
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
git clone https://github.com/kishor-desarda/Kosha.git kosha && cd kosha
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
`/var/lib/kosha` and settings in `/etc/kosha/kosha.env`, starts a hardened systemd service, a
nightly backup timer and a monthly restore test, and prints the first admin password. Running it again upgrades Kosha, after
taking and checking a backup first. Put HTTPS in front of it with Caddy or nginx
(`deploy/linux/Caddyfile.example`).

Then sign in as `admin`, change the password, add users under **Admin › Users**, and check the
part-number pattern under **Admin › Numbering & revisions**.

**Backups:** `python -m kosha.backup <folder>` copies the database with SQLite's online backup,
runs an integrity check on the copy, and copies new file versions, checking each against its
fingerprint. Keep a second copy away from the server (3-2-1 rule) and test a restore every month:
`python -m kosha.backup --verify <folder>` restores the backup into a temporary folder and reads back
every file (the Linux installer runs it monthly). `python -m kosha.restore <folder> <new data folder>`
restores into an empty folder and checks it. **Admin › Backups** shows the last backup, the last restore
test, disk space and versions.
On PostgreSQL the same command saves the database with `pg_dump` and reads the copy back to check it.

**Leaving Kosha:** `python -m kosha.export <file.zip>` writes every table and every file version with
checksums ([format](docs/export-format.md)); `--verify <file.zip>` checks an export.

**Security:** failed sign-ins pause sign-in for that user name (5 failures, 15 minutes) or address;
passwords need 10 characters and may not be common ones; sessions end after 30 idle minutes or 12 hours
(**Admin › Sign-in & sessions**), on sign-out, on a password change and with **Sign out everywhere**.
Set `KOSHA_HTTPS=1` behind HTTPS, and `KOSHA_CLAMD` to have every upload scanned by ClamAV (uploads are
refused while the scanner can't be reached). See `server/kosha.env.example`.

### Moving to PostgreSQL

Kosha keeps its records in **SQLite** by default: nothing to install, right for a small team. For
company-wide use, run it on **PostgreSQL** 12 or newer (16 recommended). The stored file versions stay
in the data folder either way; only the records move.

1. Create a database owned by a Kosha user (the `C.UTF-8` locale keeps SQLite's sort order):
   ```bash
   sudo -u postgres createuser --pwprompt kosha
   sudo -u postgres createdb --owner kosha --template template0 --encoding UTF8 --locale C.UTF-8 kosha
   ```
2. Stop Kosha and install the PostgreSQL driver, and `pg_dump` for the backups:
   ```bash
   sudo systemctl stop kosha
   sudo apt-get install postgresql-client && sudo /opt/kosha/venv/bin/pip install "psycopg[binary]"
   ```
3. Copy the records (the SQLite file is only read):
   ```bash
   cd /opt/kosha/server && sudo -u kosha /opt/kosha/venv/bin/python -m kosha.dbcopy \
       --from /var/lib/kosha/kosha.db --to postgresql://kosha:PASSWORD@localhost:5432/kosha
   ```
   `kosha.dbcopy` creates the tables, copies the rows of linked tables in one transaction per group,
   continues the id counters, and then checks that every table has the same row count and checksum
   on both sides. It refuses to copy into a database that already holds data, and if anything fails it
   empties what it filled.
4. Add `KOSHA_DATABASE_URL=postgresql://kosha:PASSWORD@localhost:5432/kosha` to `/etc/kosha/kosha.env`
   (letters and digits in the password, or URL-encode it) and `sudo systemctl start kosha`. Keep the
   SQLite file until you have checked Kosha on PostgreSQL. Later runs of `deploy/linux/install.sh` keep
   the driver and `pg_dump` installed.

A new installation can start on PostgreSQL directly: do steps 1, 2 and 4.

With Docker, `deploy/docker-compose.postgres.yml` runs Kosha with PostgreSQL 16 in a named volume.

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

The official **Odoo connector** (`addins/odoo`) works the same way through Odoo's external API; choose
**Odoo** under *ERP system* on the same page and follow its [README](addins/odoo/README.md). Other
ERPs plug into the same `ErpConnector` interface (see *Add-ins* below).

**ERP sync › Reconciliation** reads the ERP and lists what differs from Kosha's released items and
BOMs: items missing in the ERP, description, unit, revision or material differences, missing, extra or
changed BOM lines, and items the ERP marks as coming from Kosha that Kosha no longer has. The check
only reads; nothing changes in the ERP until someone presses **Send again**, which queues the usual,
idempotent transfer.

### Notifications

Under **Admin › Notifications**, set Kosha's own address (for links), the mail server (STARTTLS,
SSL/TLS or none; **Send a test e-mail**), and test the chat channels. Teams, Slack and Telegram are
notifier add-ins: fill in their webhook address or bot token under **Admin › Add-ins**. Each person
then picks channels per kind of notice under **My notifications** in the menu behind their initials.
The delivery log shows every notice, its attempts and errors, with **Send again**.

## CAD add-ins

| Add-in | Folder | State |
| --- | --- | --- |
| SolidWorks 2021 and later | `addins/solidworks` | Written against the SolidWorks API; build and pilot on one PC first |
| AutoCAD 2021 and later | `addins/autocad` | Preview on the shared CAD core and DWG code (.NET 8 for 2025+, .NET Framework 4.8 before); autoloader bundle included; build against your AutoCAD assemblies, not built in CI |
| GStarCAD | `addins/gstarcad` | Starter add-in on the shared CAD core and DWG code; build against your GStarCAD SDK, not built in CI |
| Kosha Desktop (Office files, PDFs, images) | `addins/desktop` | Windows tray program with Explorer right-click commands; logic tested in CI, program compiled in CI; see [docs/desktop.md](docs/desktop.md) |
| Word, Excel, PowerPoint | `web/office-addin` | Task pane served by Kosha: insert part data, check the document in; tested in a browser in CI; see [docs/office-addin.md](docs/office-addin.md) |
| Shared CAD core (`Kosha.Client`, `Kosha.CadCore`, `Kosha.CadCore.UI`, `Kosha.CadCore.Dwg`) | `sdk/dotnet` | Used by every CAD add-in; the DWG code is shared by AutoCAD and GStarCAD |

Each designer keeps a local workspace folder (for example `C:\Kosha`) that mirrors Kosha's folders.
The add-ins check files in and out, upload properties, previews, 3D meshes and assembly structure,
and warn about duplicates. Kosha Desktop and the CAD add-ins share one sign-in and one workspace
folder per Windows user.

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
server/kosha/        Python server (Starlette; SQLite or PostgreSQL): REST API, lifecycle, BOMs, ERP queue, add-ins
server/kosha/cad/    DXF reader and SVG renderer, DWG conversion, SolidWorks preview reader, STL tools
server/tests/        unit tests (pytest) and end-to-end suites with a simulated ERPNext
web/                 browser client (plain JavaScript modules, no build step)
sdk/python/          add-in SDK (Apache-2.0): manifest, base classes, safe HTTP, conformance tests
sdk/dotnet/          shared C# libraries for CAD add-ins (Apache-2.0)
addins/              official add-ins (Apache-2.0): ERPNext, Odoo, Teams, Slack, Telegram, SolidWorks, AutoCAD,
                     GStarCAD, Kosha Desktop
web/office-addin/    the Office add-in (task pane for Word, Excel and PowerPoint), served by Kosha
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
cd .. && pytest sdk/python/tests addins/erpnext/tests addins/odoo/tests addins/teams/tests addins/slack/tests addins/telegram/tests
python scripts/smoke_ui.py   # browser smoke test (needs Playwright)
python scripts/test_office_taskpane.py       # Office task pane, in test mode and a simulated Word
dotnet test sdk/dotnet/Kosha.CadCore.Tests   # shared CAD core (needs the .NET 8 SDK)
dotnet test addins/desktop/Kosha.Desktop.Core.Tests   # Kosha Desktop logic
```

To run the same tests on PostgreSQL, install `psycopg[binary]` and set `KOSHA_TEST_DATABASE_URL` to a
server where the user may create databases (for example `postgresql://kosha:secret@localhost:5432/postgres`):
every test server then gets its own new database. The two script suites run on one with
`python tests/harness.py run tests/test_api.py`.

The same suites run on every pull request in GitHub Actions.

## Roles

- **Viewer:** browse, view and download.
- **Engineer:** add files, check out and in, submit, revise, build BOMs, raise change orders.
- **Approver:** everything an engineer can do, plus release, obsolete, approve change orders, release BOMs and push to the ERP.
- **Admin:** everything, plus users, settings, add-ins and the audit log.

## Roadmap

The full specification and the development guide are maintained alongside the project. In short:

- **1.0 release candidate (now):** everything in the table above and every area of the specification, each
  covered by tests, with an acceptance suite that walks through the specification's acceptance scenarios. See the
  [changelog](CHANGELOG.md), the [user guide](docs/user-guide.md) and the [admin guide](docs/admin-guide.md).
- **1.0:** pilots at real design offices, then hardening: the newer features proven on PostgreSQL in daily
  use, the Dolibarr connector checked against live Dolibarr versions, the AI prompts tuned on real models,
  and the AutoCAD and GStarCAD add-ins out of preview.
- **Beyond 1.0:** manufacturing BOMs and process plans, products with options and variants, and quality
  (nonconformance, corrective actions, FMEA); see the [roadmap](docs/roadmap.md).
- **Later:** more ERP connectors and CAD add-ins, AI features that use embeddings (similar-part search),
  and a documentation site.

## Frequently asked questions

**What is Kosha?**
Kosha is a free, open-source product lifecycle management (PLM) and product data management (PDM)
system for engineering and manufacturing companies. It keeps CAD files, drawings and documents under
revision control, manages engineering bills of materials (BOMs) and engineering change orders (ECOs),
and sends released items and BOMs to an ERP.

**Who is it for?**
Design offices and small to mid-sized manufacturers that keep drawings on a
file share today and want revision control, releases and BOMs without a per-user licence.

**Which CAD programs and file types does it support?**
SolidWorks (parts, assemblies, drawings and custom properties), AutoCAD and GStarCAD (DWG and DXF with
title-block reading), STEP and STL models, PDFs, Office files and images. DXF and DWG drawings and 3D
models are viewed in the browser.

**Which ERP systems does it connect to?**
ERPNext, Odoo and Dolibarr through official connector add-ins, and any other ERP through the file-exchange
connector (CSV or JSON files in a shared folder). More ERPs plug into the same connector interface.

**Is it really free? What is the licence?**
Yes. The server and web client are AGPL-3.0-or-later; the SDKs and official add-ins are Apache-2.0, so
you can write your own add-ins under any licence; packs and documentation are CC BY 4.0.

**Where does my data live?**
On your own server. Files are stored once by their SHA-256 fingerprint, records in SQLite or PostgreSQL,
with verified backups and a documented [export format](docs/export-format.md) so you can always leave.

**Can people sign in with their company accounts?**
Yes. The LDAP add-in works with Active Directory and OpenLDAP, the OpenID Connect add-in with Keycloak and other
standard providers. Directory groups decide each person's role.

**Does Kosha need AI or the cloud?**
No. AI assistance is optional and off by default. When it is on, it can use a model running on your own
server, and the admin chooses exactly what may be sent.

**Is there an API?**
Yes. Every Kosha server describes its REST API at `/api/docs` (OpenAPI at `/api/openapi.json`), and the
Python SDK builds add-ins that react to Kosha events.

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

"Kosha" and the Kosha logo are covered by the [trademark policy](TRADEMARKS.md); logo files and colours are in
[docs/brand](docs/brand/README.md).
