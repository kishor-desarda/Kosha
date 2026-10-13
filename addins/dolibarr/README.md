# Dolibarr connector for Kosha

Licence: Apache-2.0. Sends released items and BOMs from Kosha to [Dolibarr ERP/CRM](https://www.dolibarr.org),
reads stock and cost back, and reads products and BOMs back for Kosha's reconciliation check. It talks to
Dolibarr's REST API (`<address>/api/index.php`, authenticated with the `DOLAPIKEY` header), so nothing has to be
installed inside Dolibarr.

| Kosha | Dolibarr |
| --- | --- |
| Item (part number) | Product (`/products`), reference `ref` = part number, type product, on sale and on purchase |
| Description | Product label (and description, when the product is created) |
| Make or buy | *Nature of product*: manufactured (`finished` = 1) or raw material (0), when the product is created |
| Unit | Unit (`fk_unit`) from Setup › Dictionaries › Units, only with **Send units** ticked |
| Weight | Weight in kg (`weight`, `weight_units` = 0) |
| Revision, material | Optional complementary attributes (extra fields) you name in the settings |
| Released BOM | BOM of the Bills of Materials module (`/boms`), ref `DE-300-000-B`, label `Rev B`, one line per part |
| Stock, cost | `/products/{id}/stock` per warehouse; weighted average price (`pmp`) or cost price (`cost_price`) |

## How it behaves

- **Idempotent.** Kosha looks a product up by reference before it creates one (also when it is no longer on sale
  or purchase), and changes only the fields that differ. Sending the same item twice changes nothing.
  Extra fields Kosha does not manage are sent back unchanged, so none is cleared.
- **BOMs.** When the lines (part and quantity) match the BOM Dolibarr uses now (the product's default BOM, else
  the newest validated one), nothing is sent ("unchanged"). Otherwise Kosha creates a new BOM as a draft, adds
  the lines, validates it and makes it the product's default BOM. The old BOM is kept, so manufacturing orders
  already planned are not touched; disable old BOMs in Dolibarr when you no longer need them.
- **Stopped half-way?** A BOM takes several calls. If Kosha stops between them (a crash, a lost answer), the next
  send finds the draft it started for that revision (same ref), corrects its lines and validates it, instead of
  adding a second BOM.
- **Dry run** is handled by Kosha: while it is on, the connector is never called.
- **Creating calls are sent once.** Reads are retried after a network hiccup; calls that create a record are not,
  so a lost answer can never create a second product, BOM or BOM line.
- **Errors keep Dolibarr's own words**, for example `Dolibarr refused the request: Bad Request: ...`.
- Dolibarr cannot sign calls to Kosha, so this connector does not accept calls from Dolibarr. Stock and cost are
  read when Kosha asks (ERP sync › Send waiting now, or an item's *Stock & cost* refresh).

## Set up

1. In Dolibarr, switch on the modules **API REST**, **Products**, **Stocks** and **Bills of Materials**
   (Home › Setup › Modules/Applications).
2. Create (or choose) an integration user with the rights to read, create and change products and BOMs, and to
   read stock. On the user's card, generate an **API key** and copy it.
3. Optional, recommended: add two complementary attributes on products (Products › Setup › Complementary
   attributes), for example `kosha_revision` and `kosha_material`, type *String*. With the revision attribute
   set, Kosha can also find products in Dolibarr that it created but no longer has.
4. Optional: to send units, switch on *Use units* in Products › Setup, tick **Send units** in Kosha, and map
   Kosha's units to the codes of Dolibarr's unit dictionary under **ERP sync › Connection settings › Unit
   mapping**, for example `Nos=P`, `EA=P`, `KG=KG`, `M=M`, `MM=MM`. Kosha matches a unit by its code, short
   label or label, ignoring case; use the codes, so the reconciliation compares like with like.
5. In Kosha, choose **Dolibarr** as the ERP system on the same page, fill in the address and API key, press
   **Test connection**, then untick **Dry run** and tick **Connect**.

| Setting | Meaning |
| --- | --- |
| Dolibarr address | The address you sign in at, e.g. `https://erp.example.com/dolibarr` (Kosha adds `/api/index.php`) |
| API key | The integration user's key; stored masked |
| Revision / material extra field | The codes of the complementary attributes from step 3 (`options_` is added by Kosha) |
| Send units | Fill the product's and BOM lines' unit; only when Dolibarr's *Use units* option is on |
| Validate new BOMs | Validate each new BOM so it can be used at once; off leaves it as a draft for someone to check |
| Make each new BOM the product's default BOM | Sets the product's *Default BOM* |
| Cost read back into Kosha | Weighted average price (`pmp`) or cost price |

## API details this connector relies on

The connector follows Dolibarr's documented REST API (the API explorer of a Dolibarr 17 to 20 installation).
These points could not be checked against every Dolibarr version; if one does not hold on yours, please report it:

- **Validating a BOM** is done with `PUT /boms/{id}` and `{"status": 1}`. The API has no separate *validate* call
  that we know of; this sets the status without asking Dolibarr's numbering module for a new ref, which is why
  Kosha gives the BOM its own ref.
- **BOM lines** use `GET/POST /boms/{id}/lines` and `DELETE /boms/{id}/lines/{lineid}` (added in Dolibarr 17, we
  believe). When `GET /boms/{id}/lines` is missing, Kosha reads the lines that come with `GET /boms/{id}`.
- **Default BOM**: `PUT /products/{id}` with `fk_default_bom` (the product's *Default BOM*, Dolibarr 13 or later).
- **Lists** answer 404 when nothing matches on older versions and `[]` on newer ones; both are treated as empty.
  Kosha finds products with `sqlfilters=(t.ref:=:'...') or (...)`, and the products it created with
  `(ef.<attribute>:!=:'')`. Part numbers with a quote or a bracket are looked up one by one with
  `GET /products/ref/{ref}`.
- **Stock**: `GET /products/{id}/stock` answers `{"stock_warehouses": {"<warehouse id>": {"real": ...}}}`, or 404
  when the product has no stock (read as zero); warehouse names come from `GET /warehouses/{id}` (`ref`).
- **Units**: `GET /setup/dictionary/units` with `id`, `code`, `short_label` and `label`.
- **Version and user** for *Test connection*: `GET /status` (`dolibarr_version`) and `GET /users/info` (`login`).

## Tests

```bash
pytest -q addins/dolibarr/tests
```

`tests/fake_dolibarr.py` is a small in-memory Dolibarr that implements only the calls the connector makes, the way
Dolibarr answers them (numbers as text, 404 for empty lists, lines only on draft BOMs); the tests run the SDK
conformance kit (`kosha_sdk.testing.check_erp_connector`) against it. No test calls a real Dolibarr.
