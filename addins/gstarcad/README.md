# Kosha add-in for GStarCAD

Licence: Apache-2.0. A **Kosha palette** inside GStarCAD, on the same shared CAD core as the
SolidWorks add-in. Type `KOSHA` to open it.

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

## Build

GStarCAD's .NET interface follows AutoCAD's. You need GStarCAD's .NET API assemblies
(`gmap.dll` and `gmdb.dll`, in the GStarCAD program folder or its SDK).

```bat
REM GStarCAD 2024 / 2025 (.NET Framework 4.8)
dotnet build addins\gstarcad\Kosha.GstarCad -c Release -p:GstarCadSdkDir="C:\Program Files\Gstarsoft\GstarCAD2025"

REM newer GStarCAD versions that load .NET 8 add-ins
dotnet build addins\gstarcad\Kosha.GstarCad -c Release -p:GstarNet8=true -p:GstarCadSdkDir="..."
```

All GStarCAD namespaces are named in one file, `GstarCadApi.cs`. If your SDK version uses
different namespaces, change them there.

## Install

1. Copy the build output folder to the PC, for example `C:\Program Files\Kosha\GStarCAD`.
2. In GStarCAD, run `NETLOAD` and choose `Kosha.GstarCad.dll` to try it.
3. To load it every time, add the DLL to the startup suite (`APPLOAD` › *Startup Suite*), or ask
   your CAD administrator to register it for demand loading.
4. Type `KOSHA`, enter the server address, your local workspace folder (for example `C:\Kosha`)
   and your Kosha user name and password.

## Status

This is a starter add-in. The shared core it uses is built and tested in CI; the GStarCAD-specific
classes (`GstarCadHost`, `GstarCadPlugin`) need a GStarCAD installation to build and have to be
piloted on one PC first. Contributions from GStarCAD users are very welcome: xref check-in,
title-block mapping settings, and a demand-loading installer.
