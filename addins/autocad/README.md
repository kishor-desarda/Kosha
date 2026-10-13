# Kosha add-in for AutoCAD

Licence: Apache-2.0. A **Kosha palette** inside AutoCAD 2021 and later (and the AutoCAD-based
toolsets), on the same shared CAD core as the SolidWorks and GStarCAD add-ins. Type `KOSHA` to open it.

## What it does

- **Check out, check in, undo check-out, get latest** for the active drawing.
- Reads the part number, description and material from the drawing's custom properties
  (`DWGPROPS`) and from title-block attributes with tags such as `PART_NO`, `DESCRIPTION`,
  `MATERIAL` and `REV`; falls back to the file name (`CP-80-014 Inlet Flange.dwg`).
- **Next number** writes the new part number to the drawing properties and to a `PART_NO`
  title-block attribute when the drawing has one.
- Previews and duplicate checks are made on the Kosha server from the DWG itself (configure a DWG
  converter on the server, see the main README).
- **Nothing is lost:** files become read-only after check-in; *Get latest version* verifies the
  download and keeps your previous local file in `%LOCALAPPDATA%\Kosha\replaced-files`.

## How it is built

The add-in has three small files of its own:

| File | What it does |
| --- | --- |
| `AutoCadApi.cs` | the only place that names AutoCAD's namespaces (`Autodesk.AutoCAD.*`), as global usings |
| `AutoCadHost.cs` | `AutoCadHost`, the AutoCAD answers for the shared CAD core (`ICadHost`) |
| `AutoCadPlugin.cs` | `IExtensionApplication` and the `KOSHA` command |

Everything else is shared. The DWG logic (title-block attributes, drawing properties, `DBMOD`,
save, close, open) and the palette are written once in `sdk/dotnet/Kosha.CadCore.Dwg` and compiled
into both this add-in and the GStarCAD add-in; the aliases in `AutoCadApi.cs` and `GstarCadApi.cs`
make the same source compile against either program. The rules that need no CAD program (which
tags are read, which property is written to which tag) are in `Kosha.CadCore` and tested in CI.

## Build

You need the .NET SDK 8 (or Visual Studio 2022) and AutoCAD's .NET assemblies `AcCoreMgd.dll`,
`AcDbMgd.dll` and `AcMgd.dll`: in the `inc` folder of the ObjectARX SDK for your AutoCAD version,
or in the AutoCAD program folder.

```bat
REM AutoCAD 2025 and later (.NET 8, the default)
dotnet build addins\autocad\Kosha.AutoCad -c Release -p:AutoCadDir="C:\Program Files\Autodesk\AutoCAD 2025"

REM AutoCAD 2021 to 2024 (.NET Framework 4.8)
dotnet build addins\autocad\Kosha.AutoCad -c Release -p:AutoCadNet8=false -p:AutoCadDir="C:\ObjectARX 2024\inc"
```

The output is in `Kosha.AutoCad\bin\Release\net8.0-windows` and `Kosha.AutoCad\bin\Release\net48`.
AutoCAD's own assemblies are referenced but never copied (`Private=false`).

## Install

### Try it: NETLOAD

In AutoCAD, run `NETLOAD`, choose `Kosha.AutoCad.dll` from the build output, then type `KOSHA`.
Enter the server address, your local workspace folder (for example `C:\Kosha`) and your Kosha user
name and password.

### Every day: the autoloader bundle

AutoCAD loads every bundle in its `ApplicationPlugins` folders at start-up. The bundle description,
`Kosha.bundle\PackageContents.xml`, is in this folder; it picks the .NET 8 build for AutoCAD 2025
and later and the .NET Framework build for AutoCAD 2021 to 2024.

```bat
set B=%APPDATA%\Autodesk\ApplicationPlugins\Kosha.bundle
xcopy /E /I /Y addins\autocad\Kosha.bundle "%B%"
xcopy /E /I /Y addins\autocad\Kosha.AutoCad\bin\Release\net8.0-windows "%B%\Contents\net8"
xcopy /E /I /Y addins\autocad\Kosha.AutoCad\bin\Release\net48 "%B%\Contents\net48"
```

Leave out the line for a build you did not make. Start AutoCAD and type `KOSHA`. To install it for
every user of the PC, copy the bundle to `%PROGRAMDATA%\Autodesk\ApplicationPlugins` instead (needs
administrator rights). If AutoCAD asks whether to load `Kosha.AutoCad.dll`, choose *Always load*,
or add the bundle folder to the trusted locations (*Options › Files › Trusted Locations*).

To remove it, delete the `Kosha.bundle` folder. Settings are kept per user in
`%APPDATA%\Kosha\settings.json`, shared with the other Kosha add-ins; the sign-in token is
protected with Windows DPAPI.

## Status

Not built in CI. The project's CI runs on Linux without AutoCAD, so this add-in and the shared DWG
code in `sdk/dotnet/Kosha.CadCore.Dwg` are only compiled on a PC with the AutoCAD assemblies. The
shared task pane, the check-in logic and the title-block rules it uses are built and tested in CI.
Pilot it on one designer PC before rolling it out.

Known gaps, and good first contributions:

- External references are not checked in with the drawing; check them in on their own.
- If a drawing was opened while it was read-only (checked in), AutoCAD keeps it read-only after
  *Check out*: close and open it again before editing.
- `DBMOD` stays set after Kosha saves the drawing, so AutoCAD may still ask to save when you close it.
