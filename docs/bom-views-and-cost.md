# BOM views, machines and cost

One BOM screen with a view selector, the installed base of delivered machines, and the cost roll-up
(specification 6.10, 6.12 and 4.2). Open it from a BOM with **Views and cost**, or from **BOMs › Machines**.

## Views

| View | Shows |
| --- | --- |
| Latest released | Every level is the newest released BOM: what production should build today. |
| In work | The BOM still in work, line by line against the released one: *Added*, *Quantity changed* (old → new), *Removed*. Lower levels also show their BOMs in work. |
| As of a date | Every level as it was released on that day. BOMs released later are left out. Each BOM records when it was released; BOMs released before this was added count from their last change. |
| As built | The BOM one delivered machine was built with, by serial number, with the deviations limited to that serial. |

API: `GET /api/items/{id}/bom/view?view=released|in_work|as_of|as_built&date=YYYY-MM-DD&serial=MX26-0042&cost=1`.

## Machines (the installed base)

**Record a machine** takes the machine's top assembly, the customer, site and sales order, and a serial number: type
the one on the nameplate, or leave it empty for the next number of the product family (*Machine serial number*
series, for example MX26-0042). The machine's latest released BOM, every level, is copied into the record, so it
never changes when the design moves on; a machine can only be recorded once its BOM is released. Approved deviations
whose serial list names the machine show on its as-built BOM.

Engineers record and edit machines (customer, site, order, commissioning date, note). An approver can **withdraw** a
machine recorded by mistake, with a reason; the record is kept and listed again with `?withdrawn=1`.

## Cost roll-up

Tick **Show cost** on any view:

- A part's own cost is its ERP rate (read from the ERP; Kosha never changes it).
- A part without one gets an **estimate**: weight × (material rate + process rate), with the per-kg rates an admin
  sets under **Rates**. Estimates are marked, and their sum is shown apart.
- Assemblies made in-house are the sum of their lines; a bought assembly is its own price and its lines are not
  counted again.
- Lines with neither are listed, so nobody mistakes a low total for a real one.
- The **top cost drivers** are listed first, and **Excel** exports the costed BOM for quotations.

**What-if** compares any two views (two revisions, a date, two machines, a machine against the BOM in work) and lists
the parts that make the difference, biggest first.

API: `GET /api/items/{id}/bom/cost/export`, `POST /api/boms/what-if` with `{a: {item_id, view, date, serial}, b: …}`,
`GET/PUT /api/cost/rates`, `GET/POST /api/machines`, `GET/PATCH /api/machines/{id}`,
`POST /api/machines/{id}/withdraw`.
