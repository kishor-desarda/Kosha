@echo off
REM SPDX-License-Identifier: Apache-2.0
REM Removes the Kosha add-in from SolidWorks. Run as Administrator.
set DLL=%~dp0Kosha.SolidWorks\bin\Release\net48\Kosha.SolidWorks.dll
if not exist "%DLL%" set DLL=%~dp0Kosha.SolidWorks.dll
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe" /unregister "%DLL%"
echo Kosha add-in removed.
pause
