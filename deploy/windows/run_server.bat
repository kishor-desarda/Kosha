@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM Starts the Kosha server. Open http://localhost:8080 in Edge, Chrome or Firefox.
cd /d "%~dp0..\..\server"
..\venv\Scripts\python -m kosha %*
