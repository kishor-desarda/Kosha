# Kosha Desktop

Kosha Desktop brings Kosha to Windows Explorer for the files that are not CAD files: Office
documents, PDFs, images, text files. CAD files are better checked in from the CAD add-ins, which
also send properties, previews and assembly structure.

## Everyday use

Right-click a file in your **workspace folder** (for example `C:\Kosha`). On Windows 11, choose
*Show more options* first.

| Command | What happens |
| --- | --- |
| **Check in to Kosha** | A small window shows what Kosha knows about the file, asks for a comment, and checks it in. A new file is added as the first revision; a file checked out to you gets a new version. Afterwards the file is read-only until you check it out again (tick *Keep it checked out* to go on editing). |
| **Check out from Kosha** | Locks the file in Kosha for you and makes it writable. If your copy is not the latest version, Kosha Desktop asks, then replaces it with the latest one first, so you never edit an old version by mistake. |
| **Show in Kosha** | Opens the file's page in Kosha in your browser. |
| **Get latest version from Kosha** | Replaces your copy with the latest version, after asking. |

The sub-folder a file sits in decides its Kosha folder:
`C:\Kosha\Products\Pumps\Pump manual.docx` belongs in *Kosha / Products / Pumps*, and a missing
Kosha folder is created on the first check-in. Files directly in `C:\Kosha`, or outside it, cannot
be checked in: Kosha Desktop says so and changes nothing.

Kosha checks in the file as it is saved on disk, so save it in its program first. Files that are
open in Word or Excel can be checked in; to *replace* a file (get latest), close it first.

### The Kosha icon

The Kosha icon in the notification area (next to the clock) has:

- **Show sync status**: the files checked out to you, and for each whether the copy on this PC
  has changed (*check it in when you are done*), is the same as in Kosha, or is not on this PC;
  buttons to check in, undo the check-out, show it in Kosha or in Explorer. Below it, what Kosha
  Desktop did recently, including every failure with its reason.
- **Open Kosha in the browser**, **Open workspace folder**, **Change workspace folder…**
- **Sign in… / Sign out**.

Kosha Desktop shares its settings (`%APPDATA%\Kosha\settings.json`) with the Kosha CAD add-ins:
one sign-in and one workspace folder for all of them. The sign-in token is protected with Windows
DPAPI, so another Windows user or PC cannot read it.

## Nothing is lost

- Before a file is replaced, the old copy is kept in `%LOCALAPPDATA%\Kosha\replaced-files`.
- A download is checked against its SHA-256 fingerprint before anything is replaced; the new file
  is written next to the old one and swapped in, so a crash leaves the old or the new file, never
  half of one. If the swap fails (the file is open somewhere), the old file stays as it was.
- A file already in Kosha cannot be checked in without checking it out first, so nobody's changes
  are overwritten by an old copy.

## Install (no administrator rights)

Build once on a PC with the .NET SDK 8, then give people the `publish` folder with `install.ps1`
and `uninstall.ps1`:

```bat
dotnet publish addins\desktop\Kosha.Desktop -c Release -r win-x64 --self-contained false
powershell -NoProfile -ExecutionPolicy Bypass -File addins\desktop\install.ps1
```

`install.ps1` copies Kosha Desktop to `%LOCALAPPDATA%\Programs\Kosha Desktop`, adds the four
Explorer commands under `HKEY_CURRENT_USER\Software\Classes\*\shell\Kosha...` and a start-up entry
under `HKEY_CURRENT_USER\...\Run` for the current user only, adds *Kosha Desktop* to
*Settings › Apps*, and starts the Kosha icon. The PC needs the .NET 8 Desktop Runtime (x64), or
publish with `--self-contained true`.

Remove it with *Settings › Apps › Kosha Desktop › Uninstall*, or run `uninstall.ps1`. Settings,
workspace files and kept copies stay on the PC.

## For developers

| Folder | What it is |
| --- | --- |
| `addins/desktop/Kosha.Desktop.Core` | all the logic, `netstandard2.0`: command line, the Explorer registry entries, check-in/out of plain files, get latest, the sync status list, the activity log shared by all Kosha Desktop processes |
| `addins/desktop/Kosha.Desktop.Core.Tests` | NUnit tests with real files in a temporary workspace and the in-memory Kosha server from `sdk/dotnet/Kosha.CadCore.Tests/Fakes.cs` |
| `addins/desktop/Kosha.Desktop` | the Windows program (WinForms): tray icon, sign-in, the verb window, the sync status window, registry writes |

```bash
dotnet test addins/desktop/Kosha.Desktop.Core.Tests     # runs on Linux too
dotnet build addins/desktop/Kosha.Desktop               # compiles on Linux (EnableWindowsTargeting); runs on Windows
```

Every Explorer command starts `Kosha.Desktop.exe <verb> "<file>"` in a process of its own; the
processes and the tray share the settings file and the activity log
(`%LOCALAPPDATA%\Kosha\desktop-activity.json`). Check-ins use the add-in endpoint
`/api/addin/checkin` without CAD metadata and are stored with the source *Kosha Desktop*.

## Known gaps

- One file at a time: selecting several files opens one window per file.
- No background sync: Kosha Desktop acts when you ask it to.
- The Windows program is compiled in CI but not run there; pilot it on one PC first.
