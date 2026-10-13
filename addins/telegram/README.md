# Telegram notifier for Kosha

Licence: Apache-2.0. A Telegram bot posts Kosha notices to a group chat (or a topic in a group):
the title in bold, the text, who it is for, and a link to the page in Kosha.

## Set up

1. In Telegram, create a bot with the BotFather chat and copy the token it gives you.
2. Add the bot to your team's group. Find the group's chat id (group ids start with `-100`); for a
   group with topics, also note the topic number.
3. In Kosha, open **Admin › Add-ins › Telegram notifier › Settings**, paste the token and the chat
   id, and press **Test**: it answers with the bot's name.
4. In **Admin › Notifications**, set Kosha's own address (so links point to the right page) and press
   **Send a test message** next to Telegram.
5. Each user chooses in **My notifications** (the menu behind their initials) which notices go to Telegram.

The token works like a password. It is part of the address of every call to Telegram, so Kosha
never shows or logs it; error messages name only the Telegram server. If you run your own Bot API
server, set its address under *Telegram API address*.

## How it behaves

- Messages use Telegram's HTML subset; everything else is escaped. Long texts are cut so the message
  stays within Telegram's 4,096 characters.
- Kosha's outbox delivers in the background and tries again later when Telegram is slow or refuses
  (HTTP 429 or 5xx); the action that caused the notice is never held up.
- Plain messages for the usual problems: wrong token, chat not found, bot removed from the group.

## Tests

```bash
pytest -q addins/telegram/tests
```

`tests/fake_telegram.py` stands in for the Bot API (`getMe`, `sendMessage`); the tests run the SDK
conformance kit (`kosha_sdk.testing.check_notifier`) against it.
