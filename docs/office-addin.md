# Kosha in Word, Excel and PowerPoint

The Kosha Office add-in is a task pane in Word, Excel and PowerPoint. It lets people

- **sign in** to Kosha with their Kosha user name and password;
- **find parts** by part number, description or material and **insert** the part number,
  description, revision and material: in Word at the cursor, in Excel into the selected cells as
  one row (as text, so `0012` stays `0012`), in PowerPoint into the selected text box;
- **check in this document**: choose the Kosha folder, see whether the file is new, checked out to
  you or to someone else, add a comment, and check it in with a progress bar. A file that is
  already in Kosha is checked out first, with one click in the pane;
- **open in Kosha** the document's page, or a part's page, in the browser.

Checking in only reads the document. Office hands it over in slices of 4 MB, exactly as it is
open (saved or not); the pane joins them, checks the length and uploads them to
`/api/addin/checkin`, the same endpoint the CAD add-ins use. The document in Office is never
changed. If anything fails on the way, the pane says so and nothing is checked in.

## What it needs

- Kosha reachable over **HTTPS** with a certificate the PCs trust. Office does not load add-ins
  from plain `http://` addresses.
- Microsoft 365 apps or Office 2021 and later (Windows or Mac), or Office on the web. Office 2016
  and 2019 that still show add-ins with Internet Explorer are not supported.
- Nothing to install on the server: Kosha already serves the pane at
  `https://<your-kosha-server>/static/office-addin/taskpane.html`, with its script, styles and icons.

## Install (for the Kosha administrator)

1. Copy `web/office-addin/manifest.xml` and replace every `https://kosha.example.com` in it with
   your Kosha server's address. In PowerShell:

   ```powershell
   (Get-Content manifest.xml) -replace 'https://kosha.example.com', 'https://plm.yourcompany.local' |
       Set-Content kosha-office.xml -Encoding utf8
   ```

   If you run more than one Kosha server, give each manifest its own `<Id>` (a new GUID).
2. Deploy the edited manifest:
   - **Microsoft 365 admin center › Settings › Integrated apps › Upload custom apps**, for the users
     or groups who need it. Office shows a **Kosha** button on the Home tab within a day or so.
   - To try it first, share a folder, put the manifest in it, and add the folder in Word under
     *File › Options › Trust Center › Trust Center Settings › Trusted Add-in Catalogs*. After a
     restart, the *Shared Folder* tab of *Home › Add-ins* (or *Insert › Get Add-ins*) shows Kosha.
3. Tell users to press **Kosha** on the Home tab and sign in.

The pane keeps the sign-in in the Office web view's storage on that PC until the Kosha token
expires (`KOSHA_TOKEN_HOURS`, 12 hours by default) or the user signs out.

## Content-Security-Policy

Every script of the pane is in a file of its own (`taskpane.js`, `commands.js`); there is no
inline script or style, so a strict Content-Security-Policy works. Office requires `office.js`
from Microsoft, so the policy for `/static/office-addin/` must allow it:

```text
script-src 'self' https://appsforoffice.microsoft.com
```

Office also shows the pane inside a frame, so do not send `X-Frame-Options: DENY` or
`frame-ancestors 'none'` for `/static/office-addin/`.

## Test mode

Open `https://<your-kosha-server>/static/office-addin/taskpane.html` in an ordinary browser. The
pane sees that Office is not there and says so: **Insert** writes into a test text box at the
bottom, and **Check in** sends a file you choose. Everything else, including the server calls, is
the same as in Office. This is how the automated test runs it:

```bash
python scripts/test_office_taskpane.py
```

It starts a Kosha server with the demo data, then in Chromium (1) signs in, searches, inserts,
checks a file in, checks it out and checks in version 2 in test mode, and (2) does the same in a
simulated Word: a small stand-in for `office.js` hands over a 250 kB document in three slices, and
the test checks that Kosha stored every byte and that the file handle was closed. CI runs it in the
`ui` job.

## Files

| File | What it is |
| --- | --- |
| `web/office-addin/manifest.xml` | the Office add-in manifest (XML), edited by the administrator |
| `web/office-addin/taskpane.html`, `taskpane.js`, `taskpane.css` | the pane |
| `web/office-addin/commands.html`, `commands.js` | the function file Office asks for (the button only opens the pane) |
| `web/office-addin/assets/` | icons |

## Known gaps

- Not yet tried in every Office version: the pane is tested in Chromium with a stand-in for
  Office, not inside Word, Excel or PowerPoint, in CI. Pilot it with a few people first.
- The document's file name comes from Office; a document that was never saved is called
  `Document.docx` (or `.xlsx`, `.pptx`) until you type a name.
- No "get latest version" in the pane: open the file from Kosha, or use
  [Kosha Desktop](desktop.md) for files in the workspace folder.
