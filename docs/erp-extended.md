# ERP: drawings, status, orders and retries

Kosha owns the engineering data and the ERP owns orders, customers, suppliers, stock and prices (specification 12.1).
This page covers what goes beyond sending items and BOMs.

## What Kosha sends

- **Item group and HSN code per category.** ERP sync › Mapping lists every category of the installed packs. Leave a box
  empty to use what the pack proposes. HSN codes have 4, 6 or 8 digits; the data steward sets them with accounts.
  A part's own HSN code (item master) wins over its category's.
- **OEM references and customer restriction.** Sent to the custom fields named in the ERPNext connection settings
  (for example `custom_kosha_oem_refs`, `custom_kosha_customer`). Leave a field name empty and nothing is sent for it.
- **Status.** *Spares only* sets the check field named in the settings (for example `custom_kosha_spares_only`), so
  planners do not use the part in new machines. *Obsolete* disables the ERP item. Kosha never switches an item back on;
  that stays a decision in the ERP.
- **The released drawing.** When a drawing is released, its stamped PDF (see [release outputs](release-outputs.md)) is
  attached to the ERP item as a private file named `<part number> Rev <x>.pdf`. The new file goes on first; then the
  PDFs of earlier revisions that Kosha attached come off. Files people attached by hand are never touched. Switch this
  off under Mapping if the ERP should not hold drawings. A connector that cannot attach files marks the job *skipped*.

## Retries

A transfer that fails is tried again by itself after 1, 4, 16 and 64 minutes. After the fifth failure it waits on the
ERP sync screen with the ERP's own message and a **Retry** button. Approvers and admins hear about the first failure
and the last one, not about every retry. Releases never wait for the ERP.

## What comes in from the ERP

The Kosha app inside ERPNext sends signed calls when:

| In ERPNext | In Kosha |
| --- | --- |
| A sales order is submitted | Its project with the standard folders ([transmittals](transmittals.md)) |
| A purchase order, RFQ or subcontracting order is submitted | ERP sync › Orders, with the released revision of each part at that moment; approvers are told |
| A purchase order is cancelled | The order shows as cancelled and no package can be made for it |
| A customer or supplier is added or changed | Kept by name for pickers, restrictions and vendor packages |
| An item price changes | Stock and cost are read again for the parts Kosha sent |

**Make vendor package** on an order sends the released drawings of its parts, as released when the order came in, to
the supplier through a transmittal: a share link that ends, is watermarked with the supplier's name and can be
withdrawn. If a newer revision is released while the transmittal is open, its sender is warned.

## Nightly check

When the connection is live (not a dry run), Kosha compares every released item and BOM with the ERP once a night,
in the background. Differences show on Admin › Backups › Health and in ERP sync › Reconciliation, where most can be
sent again in one click. Switch the nightly check off under Mapping.

## For connector authors

`kosha_sdk.erp.ErpItem` carries `hsn`, `status` (`""`, `spares_only` or `obsolete`), `oem_refs` and `customer`.
`ErpConnector.attach_file(code, revision, filename, content)` is optional; raise `ErpNotSupported` if the ERP has no
attachments. `ErpEvent` kinds: `purchase_order`, `rfq`, `subcontracting_order` (detail `supplier|code,code|cancelled`),
`customer`, `supplier` (detail `disabled` when switched off) and `price_list`.
