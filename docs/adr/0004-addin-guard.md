# 4. Every call into an add-in goes through one guard

Status: accepted

**Situation.** Add-ins come from many authors and talk to systems Kosha does not control. A slow or
broken add-in must never lose data or stop Kosha.

**Decision.** The registry (`server/kosha/addins.py`) runs every add-in call with a time-out and a
broad catch, counts unexpected failures and switches an add-in off after repeated failures (default 5),
telling the admin why. Expected, explained failures (`AddinError`) pass through without counting.
Add-ins get no database connection; they receive settings and a context from the server. Third-party
add-ins stay off until an admin approves the permissions in their manifest.

**Consequences.** Add-in authors must be idempotent and fail politely; the SDK's conformance tests
check this. Calls run in a thread pool; a timed-out call returns control to Kosha while its thread
finishes in the background.
