@echo off
REM SPDX-License-Identifier: Apache-2.0
REM Registers the Kosha add-in with SolidWorks. Run as Administrator.
set DLL=%~dp0Kosha.SolidWorks\bin\Release\net48\Kosha.SolidWorks.dll
if not exist "%DLL%" set DLL=%~dp0Kosha.SolidWorks.dll
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe" /codebase "%DLL%"
if errorlevel 1 (echo Registration failed. Run this file as Administrator. & pause & exit /b 1)
echo Kosha add-in registered. Start SolidWorks and open the Kosha tab in the Task Pane.
pause
