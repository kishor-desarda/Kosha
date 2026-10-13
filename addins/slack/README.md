# Slack notifier for Kosha

Licence: Apache-2.0. Posts Kosha notices to one Slack channel through an incoming webhook: the
title, the text, who it is for, and an **Open in Kosha** button.

## Set up

1. In Slack, create an app for your workspace (or use one you have), switch on **Incoming
   Webhooks**, add a webhook for the channel and copy its address.
2. In Kosha, open **Admin › Add-ins › Slack notifier › Settings**, paste the address and save.
3. In **Admin › Notifications**, set Kosha's own address (so the button links to the right page)
   and press **Send a test message** next to Slack.
4. Each user chooses in **My notifications** (the menu behind their initials) which notices go to Slack.

The address works like a password: anyone who has it can post to the channel. Kosha stores it
masked, never shows it again, and names only the server in error messages.

## How it behaves

- Text is escaped the way Slack expects, so `<`, `>` and `&` in part descriptions show as typed.
- Kosha's outbox delivers in the background and tries again later when Slack is slow or refuses
  (HTTP 429 or 5xx); the action that caused the notice is never held up.
- Slack's error codes are turned into plain words: `invalid_token`, `no_service`,
  `channel_is_archived`, `action_prohibited` and others.

## Tests

```bash
pytest -q addins/slack/tests
```

`tests/fake_slack.py` answers like a Slack webhook; the tests run the SDK conformance kit
(`kosha_sdk.testing.check_notifier`) against it.
