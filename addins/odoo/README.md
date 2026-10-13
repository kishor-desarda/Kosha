# Odoo connector for Kosha

Licence: Apache-2.0. Sends released items and BOMs from Kosha to Odoo, reads stock and cost back,
and reads items and BOMs back for Kosha's reconciliation check. It talks to Odoo's external
JSON-RPC API (`/jsonrpc`), so nothing has to be installed inside Odoo.

| Kosha | Odoo |
| --- | --- |
| Item (part number) | Product variant (`product.product`), internal reference `default_code` = part number |
| Description | Product name |
| Unit | Unit of measure (`uom.uom`), by name after Kosha's unit mapping |
| Weight | Weight |
| Revision, material | Optional custom fields you name in the settings (Odoo has none of its own) |
| Released BOM | Bill of materials (`mrp.bom` with `mrp.bom.line`), reference "Rev B" |
| Stock, cost | `stock.quant` in internal locations, product cost (`standard_price`) |

## How it behaves

- **Idempotent.** Kosha looks a product up by internal reference (archived ones included) before it
  creates one, and writes only the fields that changed. Sending the same item twice changes nothing.
- **BOMs.** When the lines (part and quantity) match the BOM Odoo uses now, nothing is sent
  ("unchanged"). Otherwise a new BOM is added and put first; the old one is kept, so manufacturing
  orders already planned are not touched. Archive old BOMs in Odoo when you no longer need them.
- **Dry run** is handled by Kosha: while it is on, the connector is never called.
- **Creating calls are sent once.** Reads are retried after a network hiccup; calls that create a
  record are not, so a lost answer can never create a second product or BOM.

## Set up

1. In Odoo, create (or choose) an integration user with the *Inventory / User* and
   *Manufacturing / User* rights. Sign in as that user, open **Preferences › Account Security ›
   New API Key**, and copy the key.
2. Optional, recommended: add two custom fields of type *Text* on *Product Variant* (or *Product*),
   for example `x_kosha_revision` and `x_kosha_material`. With the revision field set, Kosha can
   also find products in Odoo that it created but no longer has.
3. Check the unit names: Odoo calls pieces *Units*. In Kosha open **ERP sync › Connection settings ›
   Unit mapping** and map Kosha's units, for example `Nos=Units`, `EA=Units`, `KG=kg`, `M=m`, `MM=mm`.
4. In Kosha, choose **Odoo** as the ERP system on the same page, fill in the address, database,
   login and API key, press **Test connection**, then untick **Dry run** and tick **Connect**.

| Setting | Meaning |
| --- | --- |
| Odoo address | The start page of Odoo, e.g. `https://erp.example.com` |
| Database | The database name shown on Odoo's sign-in page |
| User (login) and API key | The integration user; the key is stored masked |
| Company number | Only for databases with more than one company |
| How new products are set up | *Odoo 15 to 17* (stock-tracked product), *Odoo 18 or later* (goods with *Track inventory*), or consumable |
| Revision / material custom field | The technical names of the custom fields from step 2 |

## Tests

```bash
pytest -q addins/odoo/tests
```

`tests/fake_odoo.py` is a small in-memory Odoo that implements only the calls the connector makes;
the tests run the SDK conformance kit (`kosha_sdk.testing.check_erp_connector`) against it.
