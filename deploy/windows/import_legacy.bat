@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM Bulk-imports a legacy folder or network share into Kosha.
REM   import_legacy.bat "\\fileserver\Drawings" "Legacy Drawings/File server"  [extra options]
REM Extra options: --dry-run   --metadata parts.xlsx   --state in_work   --types solidworks,cad2d,pdf,neutral3d,office
REM A report (Excel) is written to the current folder. Re-running is safe: already-imported files are skipped.
if "%~1"=="" (echo Usage: import_legacy.bat ^<source folder^> ^<Kosha folder^> [options] & exit /b 2)
set SRC=%~1
set DST=%~2
shift & shift
cd /d "%~dp0..\..\server"
..\venv\Scripts\python -m kosha.importer "%SRC%" --to "%DST%" %1 %2 %3 %4 %5 %6 %7 %8 %9
