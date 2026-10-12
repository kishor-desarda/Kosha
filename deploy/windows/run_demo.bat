@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM Starts Kosha with the Demo Engineering data set: users, folders, drawings, a BOM and change orders.
REM Demo users: asha / daniel / sofia / tom, password demo-kosha-1 (the admin password is in kosha.env)
REM Uses a separate data folder so it never touches real data.
cd /d "%~dp0..\..\server"
set KOSHA_DATA_DIR=%~dp0..\..\demo-data
..\venv\Scripts\python -m kosha --demo %*
