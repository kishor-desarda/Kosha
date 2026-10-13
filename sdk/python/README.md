# Kosha add-in SDK (Python)

Licence: Apache-2.0. Build server add-ins for [Kosha](../../README.md) without depending on the
Kosha server itself.

```bash
pip install kosha-sdk            # or, in a checkout: pip install -e sdk/python
kosha-sdk new erp-connector my-erp --publisher com.example
kosha-sdk new notifier my-chat --publisher com.example
kosha-sdk new sign-in my-directory --publisher com.example
kosha-sdk check my-erp
```

What is inside:

| Module | Gives you |
| --- | --- |
| `kosha_sdk.manifest` | Reads and checks `kosha-addin.yaml` |
| `kosha_sdk.context` | `AddinContext` (settings, logger, work folder, safe HTTP) and `Health` |
| `kosha_sdk.http` | HTTP client with time-outs, limited retries for temporary failures, idempotency keys |
| `kosha_sdk.erp` | `ErpConnector` base class and the data it exchanges |
| `kosha_sdk.notify` | `Notifier` base class, `Notice` and `Recipient` |
| `kosha_sdk.signin` | `PasswordProvider` and `RedirectProvider` base classes, `Identity`, `RedirectStart`, `SignInError` |
| `kosha_sdk.events` | `@on_event("revision.released")` hooks |
| `kosha_sdk.testing` | Conformance checks: `check_manifest`, `check_erp_connector`, `check_notifier`, `check_password_provider`, `check_redirect_provider` |

See section 14 of the development guide for the rules every add-in follows.

## ERP connectors

Required: `health`, `upsert_item`, `upsert_bom`, `stock_and_cost`. Optional: `handle_webhook`
(signed calls from the ERP) and the read-back methods Kosha's **reconciliation** check uses:

| Method | Returns |
| --- | --- |
| `fetch_items(codes)` | `{code: ErpItemRecord}` for the codes the ERP has (at most 100 per call); leave out the others |
| `fetch_bom(code)` | `ErpBomRecord` of the BOM the ERP uses by default, or `None` |
| `list_items_from_kosha()` | codes of every item the ERP marks as created by Kosha |

They only read. A field of `ErpItemRecord` set to `None` means "the ERP does not keep this", and Kosha
does not compare it. A connector that leaves a method out inherits a default that raises
`ErpNotSupported`; Kosha then shows that message instead of the check. `check_erp_connector` tests
the read-back methods when a connector offers them; pass `uom=` with a unit your ERP knows.

## Notifiers

A notifier delivers one `Notice` (title, plain-text body, link, severity, kind, recipients, key) to
its channel: a chat webhook, a messenger bot, a pager. Kosha's outbox decides who gets what, retries
with growing waits, and never lets a failing notifier hold up the action behind the notice.

```python
from kosha_sdk import Health
from kosha_sdk.notify import Notice, Notifier, NotifyError


class MyChat(Notifier):
    def health(self) -> Health: ...
    def send(self, notice: Notice) -> None:        # raise NotifyError("plain words") when it fails
        ...
```

`notice.text()` and `notice.html()` give ready-made plain and escaped HTML versions; `notice.key` is
the same when a notice is sent again, for channels that can drop repeats. Run `check_notifier` against
a test channel before publishing.

## Sign-in providers

A sign-in add-in (kind `sign-in`, permission `users:sign-in`) answers one question: who is this person?
Kosha keeps everything around it: throttling of failed sign-ins, sessions, linking a directory account to a
Kosha user (by the directory's stable id, never silently by name), turning groups into roles, the audit
log, and the local `admin` account that works when the directory is down. See
[`docs/directory-sign-in.md`](../../docs/directory-sign-in.md) for how admins set it up.

```python
from kosha_sdk import Health
from kosha_sdk.signin import Identity, PasswordProvider, RedirectProvider, RedirectStart, SignInError


class MyDirectory(PasswordProvider):             # user name and password typed into Kosha's sign-in form
    def health(self) -> Health: ...
    def authenticate(self, username, password) -> Identity | None:
        ...                                      # None: unknown user or wrong password (never tell which)


class MySso(RedirectProvider):                   # a "Sign in with ..." button
    def health(self) -> Health: ...
    def start(self, redirect_uri, state) -> RedirectStart:
        ...                                      # the provider's address, with `state`; `saved` stays on the server
    def finish(self, redirect_uri, params, saved) -> Identity:
        ...                                      # check everything; raise SignInError("plain words") if not right
```

`Identity(external_id, username, full_name, email, groups)`: `external_id` must be stable and never
reused (a GUID, an entry UUID, issuer and subject). Refuse empty passwords (an LDAP bind without one is
anonymous and "succeeds"). Raise `SignInError` when the directory cannot answer; Kosha then tries the next
way of signing in, shows the message to admins and never switches the add-in off for it. `label()` (the
`label` setting by default) names the provider on the sign-in page. Kosha creates and checks `state`,
binds the round trip to the browser and keeps `RedirectStart.saved` (nonce, PKCE verifier) on the server.

Test with `check_password_provider(provider, user, password)` against a test account, or
`check_redirect_provider(provider, browser)` where `browser(url)` signs in at a test provider and returns
the parameters it sends back. The official `addins/ldap` and `addins/oidc` are complete examples.

## Events

`revision.released`, `bom.released`, `change.implemented`, `item.created`, `change.submitted` and
`erp.job_failed`. Handlers run in the background after the change is committed and must be idempotent.
