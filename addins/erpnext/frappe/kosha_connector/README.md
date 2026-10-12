# Kosha Connector for ERPNext

Licence: Apache-2.0. The ERPNext half of Kosha's ERPNext add-in. Kosha's server sends released items
and BOMs to ERPNext through ERPNext's REST API; this small Frappe app adds the ERPNext-side comforts:

- **Kosha fields** on Item and BOM: Kosha revision, material, and a link to the item in Kosha.
- **View in Kosha** button on the Item form.
- **Change notices to Kosha**: when stock moves, an item changes or a BOM is cancelled in ERPNext,
  Kosha is told (signed with a shared secret, sent after the database commit, retried by
  Frappe's queue), so stock and cost shown in Kosha stay current.

## Install

```bash
bench get-app https://github.com/kosha-plm/kosha-erpnext-connector    # published from addins/erpnext/frappe in the Kosha repository
bench --site your.site install-app kosha_connector
```

Then open **Kosha Settings** in ERPNext, enter Kosha's address (for example `https://plm.example.com`)
and a long random **webhook secret**, and tick **Enabled**. Put the same secret in Kosha under
**ERP sync › Connection settings › Webhook secret**.

Uninstalling the app removes its fields but never touches items, BOMs or stock.
