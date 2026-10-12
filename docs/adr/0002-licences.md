# 2. AGPL for the core, Apache-2.0 for SDKs and add-ins, CC BY for data

Status: accepted

**Situation.** Kosha should stay open when others run it as a service, yet companies must be able
to write and keep their own add-ins, and connectors must be easy to combine with ERPs under various
licences.

**Options.** (a) Everything permissive (MIT/Apache). (b) Everything GPL/AGPL. (c) AGPL core with a
permissive SDK and add-ins.

**Decision.** (c). The server and web client are AGPL-3.0-or-later. `sdk/` and `addins/` are
Apache-2.0. Packs, standard-parts data and documentation are CC BY 4.0. Copyleft tools that Kosha
calls (ClamAV, LibreDWG) run as separate programs; closed freeware (ODA File Converter) is never bundled.

**Consequences.** Improvements to the core flow back; add-ins can be private or commercial. Each
file carries an SPDX header and CI checks it.
