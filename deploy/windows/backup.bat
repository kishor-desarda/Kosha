@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM Backs up the Kosha database and files while it is running.
REM Usage: backup.bat D:\Backups\Kosha
if "%~1"=="" (echo Usage: backup.bat ^<backup folder^> & exit /b 2)
cd /d "%~dp0..\..\server"
..\venv\Scripts\python -m kosha.backup "%~1"
