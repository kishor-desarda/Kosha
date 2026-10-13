# Language, locale and help

## Search the way people talk

The search box, Ctrl+K and the search page understand short forms and Hinglish typed in Roman script:

| Typed | Searched for |
| --- | --- |
| `brg hsg 80` | (brg or bearing) and (hsg or housing) and 80 |
| `32 teeth wala gear ka drawing dikhao` | (32T, Z32, 32 teeth ...) and (gear or GER); files first |
| `SS316 flange DN100` | SS316 and flange and DN100 |

Words such as *wala, ka, dikhao, chahiye, show, find* describe the request and are left out. *drawing*, *file* and
*pdf* ask for files. A tooth count is found however it is written (32T, Z32, 32 teeth, 32 dant). The search page says
how it read the words ("Searched for: ...").

The India localisation pack adds words through two dictionaries: `search_short_forms` (code: the short form; name:
what it stands for, several separated by `|`) and `search_fillers`. Built-in words are in `server/kosha/searchwords.py`.

## Locale: dates, numbers and money

Admin › Users › *Dates, numbers and money as in* sets the company's locale:

| Locale | Date | Time | Money | Summaries |
| --- | --- | --- | --- | --- |
| India (default) | 10-Oct-2026 | 14:05 IST | ₹1,25,000.00 | ₹2.4 lakh, ₹1.2 crore |
| United Kingdom | 10-Oct-2026 | 14:05 | £125,000.00 | £125K |
| United States | Oct 10, 2026 | 2:05 PM | $125,000.00 | $125K |
| Germany | 10.10.2026 | 14:05 | 125.000,00 € | 125K € |

## Language: the words on screen

Each person chooses the language in the account menu › Language. Screen text lives in translation files in
`web/locales`: `en.json` is complete; `hi.json` (Hindi) covers the main screens, and anything not yet translated stays
in English. A new language is a new file with the same keys; no code changes. The test
`server/tests/test_language_api.py` checks that every key used in the client exists in English and that translations
keep the same `{placeholders}`.

In the code, `t('nav.home', 'Home')` gives the text in the person's language (English, then the given text, if a
translation is missing).

## Help on every screen

**F1** or the **?** button at the top opens one screen of help for the screen in front of you, in your language
(`help.<screen>` in the translation files). Ctrl+K goes anywhere; `/` jumps to search.
