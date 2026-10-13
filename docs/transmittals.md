# Transmittals, vendor packages, Pack and Go and projects from sales orders

How released drawings leave the company with a record, and how a booked sales order becomes a project
(specification 6.2, 6.8 and 6.9).

## Send to vendor

On a released file's page, under **Changes**, **Send to vendor** makes:

- a **vendor package** (supplier, job work) or a **transmittal** (customer, consultant), numbered TR-26-0001;
- the files: the chosen ones and, unless unticked, the part's other released files of the same revision (DXF of
  plates, STEP model);
- a **transmittal sheet** (PDF) listing the number, recipient, purpose, order, sender, date and every file with its
  revision, version and SHA-256 fingerprint;
- a **share link** (see `security.md`) that ends on a date (30 days by default), can need a password, can be view
  only, carries the recipient's name as a watermark and can be withdrawn. The recipient's page also offers the PDF
  stamped at release (see `release-outputs.md`).

When a newer revision of a sent file is released while the transmittal is still open, its sender is told and the
recipient's page says *Superseded — ask for the new drawing*; the stamped PDF they download carries the same red
band. **Close** a transmittal when the order is done: the link stops working and the record is kept.

**Transmittals** (account menu) lists what you sent; approvers see everything. API: `GET/POST /api/transmittals`,
`GET /api/transmittals/{id}`, `GET /api/transmittals/{id}/sheet`, `POST /api/transmittals/{id}/close`.

## Pack and Go

On a BOM, **Pack and Go** downloads every file of every part in its tree as one ZIP, with *Transmittal list.csv*
(revision, version, state and fingerprint of each file). Tick *Released files only* for a set that can leave the
company; use Send to vendor when the files go outside, so the record is kept. Each file counts towards the
bulk-download alarm. API: `GET /api/boms/{id}/pack-and-go?released=1`.

## Projects from sales orders

When the ERPNext connector is set up, submitting a sales order in ERPNext sends Kosha a signed notice and Kosha
makes the project: code PRJ-26-041, the customer, the delivery date, and the folders
*Projects/PRJ-26-041 Customer/* with *GA and layout*, *Design*, *Customer documents*, *Purchase* and *Manuals* (the
setting `project_folder_set` changes the list). The same sales order never makes a second project. Without the
connector, **Projects › From a sales order** does the same by hand. API: `POST /api/projects/from-sales-order`.
