# Teams notifier for Kosha

Licence: Apache-2.0. Posts Kosha notices to one Teams channel as adaptive cards: the title, the
text, who it is for, and an **Open in Kosha** button.

## Set up

1. In Teams, open the channel, choose **Workflows** and the template *Post to a channel when a
   webhook request is received*. Teams gives you an address; copy it. (Older *incoming webhook*
   addresses work too, where your organisation still allows them.)
2. In Kosha, open **Admin › Add-ins › Teams notifier › Settings**, paste the address and save.
3. In **Admin › Notifications**, set Kosha's own address (so the button links to the right page)
   and press **Send a test message** next to Teams.
4. Each user chooses in **My notifications** (the menu behind their initials) which notices go to Teams.

The address works like a password: anyone who has it can post to the channel. Kosha stores it
masked, never shows it again, and names only the server in error messages.

## How it behaves

- One notice is one card. Long texts are cut at 3,000 characters.
- Kosha's outbox delivers in the background and tries again later with growing waits when Teams
  is slow or refuses (HTTP 429 or 5xx); the action that caused the notice is never held up.
- Clear messages for the usual problems: a deleted workflow or webhook (404), a refused address
  (401/403), too many messages (429).

## Tests

```bash
pytest -q addins/teams/tests
```

`tests/fake_teams.py` stands in for both kinds of webhook; the tests run the SDK conformance kit
(`kosha_sdk.testing.check_notifier`) against it.
