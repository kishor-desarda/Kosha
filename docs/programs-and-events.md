# Programs and events

Other programs (the company's AI, a script, a dashboard, the shop-floor screens) work with Kosha in two ways: they
call the API with an **API key**, and they are told what happens through **webhooks**. Open browser screens get
the same events over a live connection. The API rules (errors, idempotency, versions, paging) are in
[api-rules.md](api-rules.md).

## API keys

1. Admin › Users › **Add user**, kind *Service account (programs)*. A service account cannot sign in on a screen.
2. On its row, **API keys** › name the key after the program, tick what it may read and change, and optionally
   limit it to addresses (`10.0.5.20`, `10.0.5.0/24`) and an end date.
3. Copy the key (`ksk_…`). It is shown once; Kosha keeps only its SHA-256 fingerprint.

The program sends `Authorization: Bearer ksk_…` or `X-API-Key: ksk_…` (never in the address).

| Scope | Covers |
|---|---|
| `items:read` / `items:write` | Parts, OEM references, numbers, families |
| `files:read` / `files:write` | Files, folders, versions, uploads |
| `boms:read` / `boms:write` | Bills of materials |
| `changes:read` / `changes:write` | Change requests, change orders, deviations |
| `inputs:read` / `inputs:write` | Inputs library and projects |
| `discussions:read` / `discussions:write` | Discussions and comments |
| `search:read` | Search and reference data |
| `ai:read` / `ai:write` | AI assistance |

*Read* is `GET`; *write* is every other method except `DELETE`. **Programs propose, people decide:** a key acts at
most as an engineer, whatever the account's role. It can create parts, check files in, add inputs, comment and
draft change requests; releasing, approving and deleting always need a person. Administration, users and sign-in
are never open to keys. Each key shows when and from where it was last used; a revoked key stops at once and stays
listed.

## Webhooks

Admin › **Webhooks** › *Add webhook*: a name, the receiving address and the events it wants. The secret
(`whsec_…`) is shown once.

| Event | When |
|---|---|
| `item.created`, `item.updated` | A part was created or its properties changed |
| `file.checked_in`, `file.checked_out` | A new version was checked in, or a file taken for editing |
| `revision.released`, `revision.obsoleted` | A revision was released, or made obsolete or spares only |
| `bom.released` | A BOM was released |
| `ecr.raised` | A change request was raised |
| `eco.submitted`, `eco.approved`, `eco.implemented` | A change order was sent for approval, approved by everyone, implemented |
| `deviation.approved` | A deviation was approved |
| `comment.added` | A comment was added to a discussion |

Each event is a `POST` with a JSON body `{"id": "evt_…", "event": "item.created", "at": "…", "data": {…}}` and the
headers `X-Kosha-Event`, `X-Kosha-Event-Id`, `X-Kosha-Delivery` and

    X-Kosha-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>" with the secret>

Check the signature and that `t` is recent before trusting a call. Any `2xx` answer counts as received. Otherwise
Kosha tries again after 1, 5 and 30 minutes, 2 and 12 hours (six tries, 10-second time-out each), in the background:
the action behind the event never waits. The deliveries log shows every try; **Send again** replays one with the same
event id, so receivers can ignore duplicates.

## Live screens

Browsers keep one connection open to `GET /api/events/stream` (server-sent events). Only the event name and record
ids travel on it; the screen then asks for the record with the viewer's own permissions. When another person
changes what is on screen, a bar offers **Refresh**; the page is never redrawn under the user's hands.
