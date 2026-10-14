# Kosha documentation

- [`../README.md`](../README.md): what Kosha is, how to try and install it.
- [`user-guide.md`](user-guide.md): the everyday jobs, from adding files to release, for engineers, checkers and approvers.
- [`admin-guide.md`](admin-guide.md): installing, the first day, connecting other systems, keeping data safe, upgrading and releasing.
- [`../CHANGELOG.md`](../CHANGELOG.md): what changed in each release.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md): how to contribute.
- [`adr/`](adr/): architecture decision records, one page per decision.
- [`../sdk/python/README.md`](../sdk/python/README.md): building server add-ins.
- [`../addins/`](../addins/): each official add-in has its own README.
- ERP connectors: [ERPNext](../addins/erpnext/), [Odoo](../addins/odoo/README.md),
  [Dolibarr](../addins/dolibarr/README.md), and [file exchange](../addins/file-exchange/README.md) (CSV and JSON
  files in a shared folder, for any ERP without a supported API; its README documents the file layout).
- [`desktop.md`](desktop.md): Kosha Desktop, Explorer commands and a tray icon for Office files, PDFs and images.
- [`office-addin.md`](office-addin.md): the Kosha task pane in Word, Excel and PowerPoint.
- [`brand/`](brand/README.md): the Kosha logo, icons and colours.
- [`../llms.txt`](../llms.txt): a short guide to the project for AI assistants and search tools.
- [`themes.md`](themes.md): Light, Dark, High contrast and "Same as my computer"; choosing one, and colours for developers.
- [`directory-sign-in.md`](directory-sign-in.md): signing in with Active Directory, LDAP or an OpenID Connect provider such as Keycloak.
- [`changes.md`](changes.md): change requests, change orders, approval routes and stand-ins.
- [`api-rules.md`](api-rules.md): the rules for programs that use the API (errors, idempotency, versions, paging, limits).
- [`security.md`](security.md): two-step sign-in, new-place alerts, the bulk-download alarm, the audit chain, personal data, watermarks, share links, encrypted secrets.
- [`file-safety.md`](file-safety.md): resumable uploads, blocked program files, the recycle bin, purging and clean-up.
- [`health-checks.md`](health-checks.md): stored files read back and repaired, the nightly consistency check, alerts to admins, drafts kept in the browser.
- [`release-outputs.md`](release-outputs.md): the stamped PDF and release sheet made at each release, printed and superseded marks.
- [`manufacturing-bom.md`](manufacturing-bom.md): manufacturing BOMs and process plans: parts by operation, built with the parent, checks against the engineering BOM, release and the ERP.
- [`roadmap.md`](roadmap.md): the steps beyond 1.0 (manufacturing BOMs, options and variants, quality) and their state.
- [`bom-views-and-cost.md`](bom-views-and-cost.md): latest released, in work, as of a date and as-built BOMs, machines, cost roll-up and what-if.
- [`transmittals.md`](transmittals.md): vendor packages and transmittals, Pack and Go, projects from ERP sales orders.
- [`language-and-locale.md`](language-and-locale.md): Hinglish search, ₹ lakh and Indian dates, screen languages, F1 help.
- [`shop-floor-and-learning.md`](shop-floor-and-learning.md): shop view and QR labels, the first-login tour, the practice folder.
- [`legacy-migration.md`](legacy-migration.md): import batches, quality queues and the review grid, Excel round-trip, old paths and numbers, count check, sample audit, sign-off and Undo batch.
- [`workbench.md`](workbench.md): Ctrl+K, favourites, saved views, pinned pages, Undo, accent colour and text size.
- [`erp-extended.md`](erp-extended.md): HSN, OEM and status to the ERP, drawings attached to ERP items, orders and customers in, retries and the nightly check.
- [`programs-and-events.md`](programs-and-events.md): API keys for programs, webhooks, and live updates on open screens.
- [`ai.md`](ai.md): optional AI assistance (classification, change-log drafts, Ask Kosha): what is sent, privacy, running it fully local.
- [`file-previews.md`](file-previews.md): previews of text, tables, archives, e-mails, Office files, STEP and video; file-type, storage and importer add-ins.

The full specification and development guide will be published here as the documentation site
(MkDocs) is set up.
