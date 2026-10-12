# Kosha add-in for SolidWorks

Licence: Apache-2.0. Adds a **Kosha** tab to the SolidWorks Task Pane so designers work with Kosha
without leaving SolidWorks. Written against the SolidWorks 2021 API; later versions keep these calls.

The add-in is a thin host: `SolidWorksHost.cs` answers the questions of the shared CAD core
(`sdk/dotnet/Kosha.CadCore`), and `SwAddin.cs` registers the pane. The pane, the check-in and
check-out logic and the API client are shared with every other Kosha CAD add-in and tested in CI.

## What it does

- **Check out, check in, undo check-out, get latest** for the active part, assembly or drawing.
- With every check-in it sends the custom properties (PartNo, Description, Material, weight, Finish,
  Vendor), a PNG of the current view, a coarse STL for the 3D viewer and duplicate checks, and for
  assemblies the **component list with quantities**, which the server turns into the engineering BOM.
  Suppressed, envelope and "exclude from BOM" components are skipped.
- **Components first:** new or checked-out parts and sub-assemblies are checked in before the
  assembly, deepest level first.
- **Next number** reserves the next free part number for a prefix (for example `MX-100-`) and writes
  it to the `PartNo` property.
- **Similar-part warning** before a new part is added, from its size, volume and area.
- **Nothing is lost:** checked-in files become read-only until checked out; *Get latest version*
  verifies the download against its fingerprint and keeps your previous local file in
  `%LOCALAPPDATA%\Kosha\replaced-files`.

## Local workspace

Each designer has a local folder that mirrors Kosha, for example `C:\Kosha`. A file's Kosha folder
comes from where it sits under that folder:

    C:\Kosha\Products\Mixers\MX-100-020 Agitator Shaft.SLDPRT  →  Kosha folder  Products / Mixers

Missing Kosha folders are created on the first check-in.

## Build

On a PC with SolidWorks and the .NET SDK 8 (or Visual Studio 2022):

```bat
dotnet build addins\solidworks\Kosha.SolidWorks -c Release
```

If SolidWorks is not in `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS`, add
`-p:SolidWorksDir="D:\Path\To\SOLIDWORKS"`.

## Install on each designer PC

1. Copy the `bin\Release\net48` folder to the PC, for example to `C:\Program Files\Kosha\SolidWorks`,
   with `register.bat` beside it.
2. Right-click `register.bat` › **Run as administrator**.
3. Start SolidWorks and open the **Kosha** tab in the Task Pane.
4. Enter the server address (for example `https://plm.example.com`), your local workspace folder,
   and your Kosha user name and password.

Run `unregister.bat` as administrator to remove it. Settings are kept per user in
`%APPDATA%\Kosha\settings.json`; the sign-in token is protected with Windows DPAPI.

## Status

Pilot it on one designer PC before rolling it out: the SolidWorks-specific part has been reviewed but
cannot be built or run in the project's CI, which has no SolidWorks.
