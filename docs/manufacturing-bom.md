# Manufacturing BOMs and process plans

The engineering BOM says what a machine is made of, the way the designer structured it. The **manufacturing BOM**
says how the workshop builds it: which parts are issued at which operation, which sub-assemblies are built in one
go with their parent, and what the workshop adds that the drawings do not list (paint, grease, thread-locker). The
**process plan** (routing) is the list of numbered operations, each at a work centre, with set-up and run times and
instructions.

Open it from an engineering BOM with **Manufacturing BOM**, or from **BOMs › Manufacturing BOMs**.

## Making one

**Manufacturing BOM** on an engineering BOM makes the manufacturing BOM of the same revision from it: the same
parts, not yet at any operation. When an earlier revision already has one, its operations, each part's operation and
note, the sub-assemblies built with their parent and the parts added for manufacturing come over, so a new design
revision only needs the differences.

Engineers make and change manufacturing BOMs; approvers release them.

## The process plan

**Add operation** adds the next number (10, 20, 30 …). Each operation has a work centre (Kosha suggests the ones
already used), what happens, set-up and run minutes, and instructions for the workshop. The totals show above the
table. An operation that still has parts cannot be removed; move its parts first.

## Parts

Choose for each part the operation that uses it. **Add part** adds something the design does not list; Kosha marks
it *Added*. A sub-assembly the workshop builds together with its parent can be **built with parent**: its line is
replaced by its own parts, issued at the same operation. **Build separately** puts it back as one line.
**Rebuild from engineering BOM** takes the parts from the engineering BOM again after it changed, keeping each
part's operation and note, the sub-assemblies built with their parent and the added parts.

## Checks

Kosha compares what both BOMs finally use, down to the bought and made parts, so a sub-assembly built with its
parent counts the same as one built separately.

| Check | Severity |
| --- | --- |
| A part of the engineering BOM is not issued, or fewer are issued than it needs | Error: stops the release |
| More are issued than the engineering BOM needs (a scrap allowance, or a mistake) | Warning |
| A part is not issued at any operation; an operation has no work centre | Warning |
| No process plan yet | Warning |
| A part was added for manufacturing; an operation has no run time or no parts | Information |
| A newer engineering revision has been released since | Warning, on the released one |

## Release and the ERP

An approver presses **Release** once the engineering BOM of the same revision is released and there are no errors.
The manufacturing BOM is then locked; the next engineering revision gets its own. Add-ins and webhooks hear
`mbom.released`.

Under **ERP sync › Connection settings**, *ERPNext builds from* chooses what the ERP gets:

- **Engineering BOMs** (the default): released engineering BOMs, as before.
- **Manufacturing BOMs (with operations)**: an assembly that has a manufacturing BOM goes to the ERP as its released
  manufacturing BOM, each part with its operation, and with the process plan. Sub-assemblies nobody planned still go
  as designed. The reconciliation then compares the ERP with the manufacturing BOM, and **Send again** sends it.

Each connector stores what its ERP can: the ERPNext connector sends the operations when *Send the operations of
manufacturing BOMs* is ticked (each operation's description must exist in ERPNext as an Operation and its work
centre as a Workstation); otherwise only the parts go.

**Excel** exports the parts by operation and the process plan (operations with the parts issued at each and the
total times) for the workshop.

API: `GET/POST /api/mboms`, `GET /api/mboms/{id}`, `PUT /api/mboms/{id}/plan` (`ops` and/or `lines`),
`POST /api/mboms/{id}/flatten` (`item_id`, `on`), `POST /api/mboms/{id}/rebuild`, `POST /api/mboms/{id}/release`,
`GET /api/mboms/{id}/export`.
