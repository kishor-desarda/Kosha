# Roadmap: from 1.0 towards the big PLM systems

Kosha 1.0 covers what most design offices use a PLM for: safe files with revisions, releases, engineering BOMs,
change requests and orders, deviations, ERP sync, viewing, transmittals, machines and cost. Measured against the
large commercial PLM systems, these areas were missing. They are built one at a time, each with tests, and marked
here when merged.

| Step | Area | What it brings | State |
| --- | --- | --- | --- |
| 1 | Manufacturing BOMs and process plans | How the workshop builds each assembly: parts by operation, sub-assemblies built with their parent, parts added for manufacturing, operations with work centres and times, checked against the engineering BOM, sent to the ERP with operations. See [manufacturing BOMs](manufacturing-bom.md). | In progress |
| 2 | Products with options and variants | One configurable product with options (motor power, voltage, material), rules on BOM lines, checks on every combination, saved variants with their resolved BOM for an order. | Planned |
| 3 | Quality | Nonconformance reports with disposition, corrective and preventive actions with root cause and verification, FMEA with risk numbers. | Planned |
| Later | Compliance | Materials and substances against RoHS, REACH and similar rules. | Later |
| Later | More CAD programs | Creo, NX, CATIA, Inventor and electronics CAD add-ins. | Later |
| Later | Plans and requirements | Project schedules and milestones; requirements traced to parts. | Later |
| Later | Large companies | Several sites sharing data; validation for regulated industries. | Later |
