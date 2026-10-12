# Kosha add-in SDK (Python)

Licence: Apache-2.0. Build server add-ins for [Kosha](../../README.md) without depending on the
Kosha server itself.

```bash
pip install kosha-sdk            # or, in a checkout: pip install -e sdk/python
kosha-sdk new erp-connector my-erp --publisher com.example
kosha-sdk check my-erp
```

What is inside:

| Module | Gives you |
| --- | --- |
| `kosha_sdk.manifest` | Reads and checks `kosha-addin.yaml` |
| `kosha_sdk.context` | `AddinContext` (settings, logger, work folder, safe HTTP) and `Health` |
| `kosha_sdk.http` | HTTP client with time-outs, limited retries for temporary failures, idempotency keys |
| `kosha_sdk.erp` | `ErpConnector` base class and the data it exchanges |
| `kosha_sdk.events` | `@on_event("revision.released")` hooks |
| `kosha_sdk.testing` | Conformance checks: `check_manifest`, `check_erp_connector` |

See section 14 of the development guide for the rules every add-in follows.
