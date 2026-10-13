# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    Removes Kosha Desktop for the current Windows user. No administrator rights are needed.

.DESCRIPTION
    Stops Kosha Desktop, removes its Explorer right-click commands, its start-up entry and its entry in
    Settings > Apps, and deletes the program folder. Your settings (%APPDATA%\Kosha), the files in your
    workspace and the copies Kosha kept for you (%LOCALAPPDATA%\Kosha\replaced-files) are left in place.

    powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1
#>
[CmdletBinding()]
param(
    [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\Kosha Desktop')
)
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $env:TEMP     # never stand inside the folder that is about to be removed

Get-Process -Name 'Kosha.Desktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

$exe = Join-Path $Target 'Kosha.Desktop.exe'
if (Test-Path -LiteralPath $exe) {
    try { Start-Process -FilePath $exe -ArgumentList 'unregister' -Wait } catch { }
}

# Remove the keys here as well, in case Kosha Desktop could not start. The .NET registry API reads '*' as the
# literal key name (all file types). Do not use Remove-Item for these keys: there '*' would be a wildcard.
$hkcu = [Microsoft.Win32.Registry]::CurrentUser
foreach ($verb in 'KoshaCheckIn', 'KoshaCheckOut', 'KoshaShow', 'KoshaGetLatest') {
    $hkcu.DeleteSubKeyTree("Software\Classes\*\shell\$verb", $false)
}
$run = $hkcu.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Run', $true)
if ($run) {
    $run.DeleteValue('Kosha Desktop', $false)
    $run.Close()
}
$hkcu.DeleteSubKeyTree('Software\Microsoft\Windows\CurrentVersion\Uninstall\KoshaDesktop', $false)

if (Test-Path -LiteralPath $Target) {
    Remove-Item -LiteralPath $Target -Recurse -Force
}
Write-Host "Kosha Desktop was removed."
Write-Host "Kept: your settings in $env:APPDATA\Kosha and the copies Kosha kept for you in $env:LOCALAPPDATA\Kosha\replaced-files."
