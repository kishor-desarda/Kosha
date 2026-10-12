@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM Kosha - set up on a Windows PC to try it or develop it. Production servers run Linux (see deploy\linux).
REM Needs Python 3.10+ from python.org (tick "Add python.exe to PATH").
setlocal
cd /d "%~dp0..\.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
echo Creating Python environment...
%PY% -m venv venv || goto :err
call venv\Scripts\python -m pip install --upgrade pip
call venv\Scripts\pip install -r server\requirements.txt || goto :err
if not exist server\kosha.env copy server\kosha.env.example server\kosha.env >nul
echo.
echo Installed. Edit server\kosha.env (data folder, admin password, DWG converter),
echo then start Kosha with deploy\windows\run_server.bat
echo To try it with demo data first: deploy\windows\run_demo.bat
exit /b 0
:err
echo Setup failed. Check that Python 3.10 or newer is installed and on PATH.
exit /b 1
