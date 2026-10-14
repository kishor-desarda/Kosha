# Kosha admin guide

This guide is for the person who installs and runs Kosha. It covers the first day, the settings that matter, and
the regular checks that keep the data safe. Most settings are under **Admin** in the web client; each section
links to the longer page.

## Installing

- **Docker:** `docker run -p 8080:8080 -v kosha-data:/data ghcr.io/kishor-desarda/kosha:<version>`. The image
  of each release is listed in its release notes, with a software bill of materials (SBOM) and signed build
  provenance attached.
- **Hosting platform (for example Railway):** deploy the repository; it builds the `Dockerfile` and
  `railway.json` sets the health check (`/api/health`). Add a volume mounted at `/data`, or every redeploy
  starts with an empty database, and set `KOSHA_ADMIN_PASSWORD` (or read the first password in the log).
  Kosha listens on the platform's `PORT`; set `KOSHA_HTTPS=1` when it is reached through HTTPS.
- **Linux or Windows server:** the installers in `deploy/linux` and `deploy/windows` set Kosha up as a service.
  See the [README](../README.md#install-on-a-server).
- **PostgreSQL** instead of the built-in SQLite database for larger sites: set `KOSHA_DATABASE_URL`, and copy an
  existing installation with the copy tool. See the [README](../README.md#moving-to-postgresql).

All data lives in one data folder (`KOSHA_DATA_DIR`, `/data` in the image). Back it up.

## The first day

1. Sign in as `admin` and change the password. Turn on **two-step sign-in** under **Admin › Sign-in & sessions**.
2. **Admin › Packs:** load the industry pack closest to your work. It brings categories, number formats and the
   standard-parts library.
3. **Admin › Numbering & revisions:** check the part-number formats and the revision scheme.
4. **Admin › Users:** add people and choose their roles (viewer, engineer, checker, approver, admin). Roles stack.
   Vendor accounts see only the projects they are given. To sign in with company accounts, enable the LDAP or
   OpenID Connect add-in. See [directory sign-in](directory-sign-in.md).
5. **Admin › Access:** add access rules for folders, categories or customers that only some people may see. See
   [access](access.md).
6. **Admin › Approval routes:** who approves which changes, and who stands in when they are away.
7. **Admin › Import legacy data:** bring in the existing file share in batches. Start with a dry run, work through
   the quality queues, check the counts, and sign the batch off. **Undo batch** removes a batch that went wrong.
   See [legacy migration](legacy-migration.md).

## Connecting other systems

- **ERP:** enable the connector add-in for your ERP (ERPNext, Odoo, Dolibarr, or file exchange for any other),
  then set it up under **ERP sync › Settings** and **Mapping**. Transfers start as a dry run. See the
  [README](../README.md#connecting-an-erp) and [ERP, extended](erp-extended.md).
- **Notifications:** e-mail, Teams, Slack or Telegram under **Admin › Notifications**.
- **Programs:** API keys and signed webhooks under **Admin › Webhooks**. See
  [programs and events](programs-and-events.md) and [API rules](api-rules.md).
- **Add-ins:** **Admin › Add-ins** lists the installed add-ins and a catalogue grouped by kind (ERP, sign-in,
  notifications, AI providers, file types, storage copies, importers). An add-in runs only after you approve its
  permissions, and one that keeps failing is switched off on its own.

## AI assistance

Off until you switch it on under **Admin › AI assistant**. Choose a provider add-in, a model for each job with a
fallback, a monthly budget (admins are told at 50, 80 and 100 percent, and you can stop calls at the budget),
and which records must stay on a local model only. The usage table shows calls, cost and how often people
accepted the suggestions. See [AI assistance](ai.md).

## Keeping the data safe

- **Backups:** **Admin › Backups** makes verified backups and runs a monthly restore test. Keep a copy off the
  server.
- **Health checks:** stored files are read back against their fingerprints every month and repaired from a backup
  or a storage copy; a nightly check compares the database with the files. Problems reach admins as alerts. See
  [health checks](health-checks.md).
- **Storage copies:** with a storage add-in enabled, every stored file is also copied elsewhere, and a lost file
  is brought back from there. See [file previews and add-ins](file-previews.md).
- **Purging:** files are soft-deleted to the recycle bin; removing them for good needs two admins. See
  [file safety](file-safety.md).
- **Audit:** **Admin › Audit log** records every change in a tamper-evident chain. Approvers can run the
  **access review**. See [security](security.md).
- **Leaving:** a full data export with checksums is always available, so your data never depends on Kosha. See
  [export format](export-format.md).

## Upgrading

1. Read the release notes (the CHANGELOG section for that version).
2. Make a backup.
3. Install the new version (a new image tag, or the installer). Database changes run on start and never remove
   data. Add-ins declare which Kosha versions they work with; one that does not fit is shown as incompatible
   rather than started.
4. Open **Admin › Backups** and the health status to check that everything came up.

## Making a release (for maintainers)

1. Set `__version__` in `server/kosha/__init__.py` and add a `## [version]` section to `CHANGELOG.md`.
2. Run `python scripts/release_check.py` until it says the repository is ready.
3. Tag and push: `git tag v<version> && git push origin v<version>`. The release workflow runs the checks, the
   full CI and a dependency vulnerability scan, then builds the packages and the image, attaches the SBOMs and
   provenance, and publishes the GitHub release. A tag with `a`, `b` or `rc` is published as a pre-release and
   does not move the `latest` image.
