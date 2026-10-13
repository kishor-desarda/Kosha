# Kosha documentation

- [`../README.md`](../README.md): what Kosha is, how to try and install it.
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
- [`programs-and-events.md`](programs-and-events.md): API keys for programs, webhooks, and live updates on open screens.
- [`ai.md`](ai.md): optional AI assistance (classification, change-log drafts, Ask Kosha): what is sent, privacy, running it fully local.

The full specification and development guide will be published here as the documentation site
(MkDocs) is set up.
