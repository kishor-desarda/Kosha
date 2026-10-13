# Kosha Desktop

Licence: Apache-2.0. A small Windows program for the files that are not CAD files: Office
documents, PDFs, images, text files. It adds four commands to the Explorer right-click menu and
a Kosha icon to the notification area. Full guide: [`docs/desktop.md`](../../docs/desktop.md).

| Right-click command | What it does |
| --- | --- |
| **Check in to Kosha** | Adds a new file to Kosha, or checks in a new version of a file checked out to you. Asks for a comment. |
| **Check out from Kosha** | Locks the file in Kosha for you and makes it writable. If your copy is not the latest version, it is replaced with the latest one first (your copy is kept). |
| **Show in Kosha** | Opens the file's page in Kosha in your browser. |
| **Get latest version from Kosha** | Replaces your copy with the latest version, after checking it against its fingerprint. Your copy is kept. |

The **Kosha icon** signs you in, shows and changes your workspace folder, opens Kosha in the
browser, and opens the **sync status** window: the files checked out to you, whether the copy on
this PC has changed, and what Kosha Desktop did recently (with every failure in plain words).

Kosha Desktop uses the same settings file as the CAD add-ins (`%APPDATA%\Kosha\settings.json`):
one sign-in and one workspace folder for all of them. A file's Kosha folder is the sub-folder it
sits in, for example `C:\Kosha\Products\Pumps\Pump manual.docx` belongs in *Kosha / Products /
Pumps*. Check-ins go through the same `/api/addin/checkin` endpoint as the CAD add-ins, without CAD
data; they are stored with the source *Kosha Desktop*.

**Nothing is lost:** a checked-in file becomes read-only until you check it out; every replaced
file is first copied to `%LOCALAPPDATA%\Kosha\replaced-files`; a failed download or a file that is
open in another program leaves your file as it was.

## Projects

| Folder | What it is | Built in CI |
| --- | --- | --- |
| `Kosha.Desktop.Core` | all the logic: command line, Explorer verbs, check-in/out, get latest, sync status, activity log (`netstandard2.0`) | yes, with tests |
| `Kosha.Desktop.Core.Tests` | NUnit tests against the in-memory Kosha server shared with the CAD core tests | yes |
| `Kosha.Desktop` | the Windows program: tray icon and windows (WinForms, `net8.0-windows`) | compiled only |

## Build and install

On Windows with the .NET SDK 8:

```bat
dotnet publish addins\desktop\Kosha.Desktop -c Release -r win-x64 --self-contained false
powershell -NoProfile -ExecutionPolicy Bypass -File addins\desktop\install.ps1
```

`install.ps1` finds the published files, copies them to `%LOCALAPPDATA%\Programs\Kosha Desktop`,
runs `Kosha.Desktop.exe register` (the Explorer commands under
`HKEY_CURRENT_USER\Software\Classes\*\shell\Kosha...` and a start-up entry, for this user only),
adds Kosha Desktop to *Settings › Apps*, and starts the tray icon. No administrator rights are
needed. The PC needs the .NET 8 Desktop Runtime (x64); publish with `--self-contained true` to
include it instead.

To hand it to other people, copy the `publish` folder together with `install.ps1` and
`uninstall.ps1`, and run `install.ps1` from there.

Remove it with *Settings › Apps › Kosha Desktop › Uninstall*, or `uninstall.ps1`. Your settings,
your workspace files and the kept copies stay.

## Command line

```text
Kosha.Desktop.exe                         the tray icon
Kosha.Desktop.exe checkin  "<file>"       also: checkout, show, latest, undo
Kosha.Desktop.exe register | unregister   add or remove the Explorer commands for this user
```

## Status

The logic is built and tested in CI on Linux; the Windows program is compiled there but has not
been run in CI. Try it on one PC first. Known gaps: one file at a time (selecting several files
starts one window per file); no automatic background sync.
