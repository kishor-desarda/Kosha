# Changelog

All notable changes to Kosha are listed here, newest first. Versions follow [semantic versioning](https://semver.org/): a release candidate (`rc`) is feature-complete and waits for pilot feedback before the final release.
The release workflow publishes the section for each tag as that release's notes.

## [Unreleased]

### Backlog

- [ ] **SolidWorks: rebuild missing 3D previews and viewer meshes:** add an admin-triggered, resumable batch operation (similar to Refresh Missing BOM) that finds existing `.SLDPRT` and `.SLDASM` versions missing preview/viewer assets, uses a dedicated Windows worker with SolidWorks installed to open files programmatically and export STL meshes and optional PNG previews, then attaches the generated assets to the existing version records without check-in, new revisions, version increments, or changes to lifecycle state or original CAD files. Include per-file progress, failures, and a retryable report.

- [ ] **SolidWorks plugin: revise file:** add a **Revise** action so users can start a new revision of a released document directly from the SolidWorks add-in.
- [ ] **Explorer: All in-work files view:** add a view listing every document currently in `in_work`, across all folders, with useful metadata and navigation to each document and its containing folder.
- [ ] **Items: Parts and assemblies not used in any BOM:** add a view listing parts and assemblies that are not referenced as components by any BOM, based on actual BOM relationships.
- [ ] **Recycle Bin: Select All:** add a button to select every item when purging the recycle bin.
- [ ] **Items: Select All:** add a button to select every purged item when permanently deleting items.
- [ ] **Delete unreleased revision:** allow an unreleased revision to be deleted independently, subject to dependency and lifecycle safeguards.
- [ ] **SolidWorks plugin: check out file:** add a check-out action to the SolidWorks plugin so a user can check out a document directly from the CAD workflow.
- [ ] **Open in SolidWorks:** download a part into its correct folder and open it in SolidWorks. For assemblies, download the complete recursive BOM, including parts nested in subassemblies.
- [ ] **Pack and Go backup:** export selected items with their complete revision histories, associated files and the relevant database records into a self-contained backup.
- [ ] **Pack and Go restore:** inspect conflicts before restoring and allow the user to rename or overwrite conflicting records.
- [ ] **Deletion-log tamper protection:** add exclusive file locking, shutdown backups, startup comparison and tamper detection. This is deliberately deferred from the current round.
- [ ] **Permanent-deletion verification and hardening:** revisit post-delete verification of database records and content blobs; distinguish expected orphaned import reports from unexpected leftovers; use pre-deletion snapshots to verify blob cleanup; log cleanup failures and add regression coverage for interrupted or partial cleanup.

## [1.0.0rc1]

The first release candidate for 1.0: every area of the specification is built and covered by tests, including an
acceptance suite that walks through the specification's acceptance scenarios end to end.

### Added
- **SolidWorks legacy import:** read native custom properties (`PartNo`, `Part Number`, Description, Material and Revision) and assembly component trees; preserve repeated instances and respect suppression and BOM-exclusion flags, with filename fallback when the component-tree stream is unavailable.
- **Permanent deletion:** permanently remove eligible unreleased items, their exclusively owned documents and versions, and unshared BOM descendants; retain shared dependencies and blobs, protect released or ERP-linked records, and record outcomes in an independent deletion log with startup recovery.

- **Quality:** nonconformance reports (NCR-YY-NNNN) with photos and a disposition (use as is, rework, repair,
  scrap, return to supplier; use as is and repair raise a draft deviation); corrective and preventive actions
  (CAPA-YY-NNNN) with containment, root cause, actions with owners and due dates, a change request when the design
  must change, and a four-eyes check that it worked; design and process FMEA with RPN, a company RPN limit,
  severity 9 and 10 always flagged, scores after the action, and Excel export. A new **Quality** screen and an
  extra *quality* role for inspectors.
- **Options and variants:** configurable products with options and choices, rules on BOM lines at any level
  in a small readable language, constraints between options, the BOM of any combination, a check of every
  combination (lines never used, choices that change nothing, empty assemblies), and variants saved for orders with
  their BOM kept as it was; machines can be recorded from a variant.
- **Manufacturing BOMs and process plans:** how the workshop builds each assembly, made from the engineering BOM:
  operations with work centres, set-up and run times and instructions; each part at the operation that uses it;
  sub-assemblies built with their parent; parts added for manufacturing. Checks against the engineering BOM down
  to every part, release after the engineering BOM, carry-over to the next revision, an Excel export for the
  workshop, the `mbom.released` event, and an ERP setting to send manufacturing BOMs with their operations
  (operations in ERPNext when switched on).

- **Part numbers:** number formats from the specification, bulk reservation, voiding with a reason, and near
  matches shown before a new number is taken.
- **Items:** product families, spare-part flags, OEM cross-references and customer part numbers.
- **Access:** roles that stack, access rules by folder, category and customer, vendor accounts, and a regular
  access review for admins.
- **Lifecycle:** checks before review and release, "spares only" and superseded revisions.
- **Changes:** change requests, interchangeability, effectivity, approval routes, stand-ins and reminders.
- **API:** a versioned path, request IDs, one error format, `If-Match`, `Idempotency-Key`, cursor paging and
  limits; API keys for programs, signed webhooks and live updates on open screens.
- **Files:** resumable uploads, byte-range downloads, blocked program files, a recycle bin and a two-admin purge;
  previews for text, tables, archives, e-mails, Word, Excel, PowerPoint, STEP and video.
- **Security:** two-step sign-in, new-place alerts, a bulk-download alarm, an audit chain, personal-data
  export and erasure, watermarked PDF downloads, share links for outside people and encrypted secrets.
- **Health:** a monthly file fingerprint check with repair, a nightly consistency check and alerts to admins;
  drafts kept in the browser.
- **Release outputs:** a stamped PDF and a release sheet at each release, an "uncontrolled when printed" line
  and a superseded band.
- **BOMs:** latest released, in work, as of a date and as-built views, machine builds and a cost roll-up with
  what-if.
- **Transmittals:** vendor packages with a transmittal sheet and a superseded warning, Pack and Go, and
  projects from ERP sales orders.
- **ERP:** drawings attached to ERP items, HSN, OEM and status sent out, orders and customers read in,
  automatic retries, a nightly check and a field-mapping screen.
- **Workbench:** Ctrl+K, favourites, saved views, pinned pages, Undo, accent colour and text size.
- **Language:** Hinglish search, Indian number and date formats, translation files with Hindi, and F1 help.
- **Shop floor and learning:** a shop view with QR labels, a first-login tour and a practice folder.
- **Legacy migration:** import batches with quality queues, a review grid, an Excel round-trip, ZIP unpacking,
  search by old number, a count check, a sign-off and Undo batch.
- **AI (optional):** a model per job with a fallback, retries, a cost log and a monthly budget, local-only data,
  a drawing check, duplicate explanations, title-block reading and voice input.
- **Add-ins:** file-type, storage-copy and importer add-ins, and an add-in catalogue grouped by kind.
- **Releases:** a release workflow that checks readiness, runs the full CI, scans dependencies for known
  vulnerabilities, builds the packages and the server image with a software bill of materials (SBOM) and signed
  build provenance, and publishes a GitHub release. User and admin guides.


### Fixed

- **Permanent deletion:** remove item-specific legacy import records and source references, including imported part rows with no document link; remove historical audit entries for draft BOMs deleted before their parent item, without touching later audit history if a BOM ID is reused.
- **Container image on hosting platforms:** the image no longer declares a `VOLUME` (some platforms, such as
  Railway, refuse to build it), listens on the platform's `PORT` when `KOSHA_PORT` is not set, and hands a
  root-owned data volume to the `kosha` user before starting without root. `railway.json` sets the health check.

### Changed

- The server version is now `1.0.0rc1`. Add-ins that declare `kosha: ">=0.5,<2.0"` keep working.

## [0.5.0]

The preview: revision control, engineering BOMs, change orders, ERP connectors (ERPNext, Odoo, Dolibarr and file
exchange), the duplicate finder, legacy import, viewers, PostgreSQL, directory sign-in, the inputs library,
standard parts, industry packs, themes, optional AI assistance, the Office add-in, Kosha Desktop and the CAD
add-ins in preview.
