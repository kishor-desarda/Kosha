# API rules for programs

Everything the browser does goes through the same API, with the same permission checks and audit trail. Programs
should follow these rules (the server's own description is at `/api/v1/openapi.json`, readable at `/api/docs`).

- **Versioned path.** Use `/api/v1/...`, for example `GET /api/v1/items`. It reaches the same operations as
  `/api/...`, which the browser uses.
- **Request IDs.** Every answer has an `X-Request-ID` header. Send your own (8 to 64 letters, digits, `.`, `_`,
  `:` or `-`) to follow a job through the server log; otherwise Kosha makes one.
- **One error format.** Errors are JSON:
  `{"error": "plain message", "code": "machine_code", "request_id": "...", ...details}`. The message can be shown
  to a person as it is.
- **No lost updates.** Items, files, change orders, deviations, BOMs and projects carry `row_version`. Send the
  version you read as `If-Match: "3"` with an update; if someone changed the record in between, the answer is
  `412 changed_meanwhile`, naming who and when, and nothing is saved. Without `If-Match` the update is applied.
- **Safe retries.** Send `Idempotency-Key: <your unique key>` with a POST. If the same request arrives again (for
  example after a network drop), the first answer is returned with `Idempotent-Replayed: true` and nothing is done
  twice. The same key with a different request is refused (`422 idempotency_key_reused`). A request that failed
  does not keep its key. Keys belong to the account and are kept for a day.
- **Cursor paging.** `GET /api/v1/items?cursor=&limit=500` returns the first page and `next_cursor`; pass that as
  `cursor` for the next page, until `next_cursor` is `null`. Items can be filtered by `category`, `state`,
  `item_type`, `family` and `q`. The audit log and change orders page the same way.
- **Limits.** Each account may make `api_rate_limit_per_minute` calls a minute (Admin settings, default 1200;
  0 switches the limit off). Past it the answer is `429 rate_limited` with `Retry-After` in seconds.
