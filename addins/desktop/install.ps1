# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    Installs Kosha Desktop for the current Windows user. No administrator rights are needed.

.DESCRIPTION
    Copies Kosha Desktop to %LOCALAPPDATA%\Programs\Kosha Desktop, adds the Explorer right-click commands
    (Check in to Kosha, Check out from Kosha, Show in Kosha, Get latest version from Kosha) for this user only,
    starts the tray icon now and at every sign-in to Windows, and adds Kosha Desktop to Settings > Apps so it
    can be removed there. Running it again updates an existing installation.

    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Source "D:\Downloads\Kosha Desktop"

.PARAMETER Source
    The folder with Kosha.Desktop.exe (the output of dotnet publish). By default the folder of this script,
    then the usual build output folders next to it.

.PARAMETER Target
    Where to install. Default: %LOCALAPPDATA%\Programs\Kosha Desktop

.PARAMETER NoStart
    Do not start the tray icon now.
#>
[CmdletBinding()]
param(
    [string]$Source = '',
    [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\Kosha Desktop'),
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'

function Find-Source {
    $candidates = @(
        $Source,
        $PSScriptRoot,
        (Join-Path $PSScriptRoot 'Kosha.Desktop\bin\Release\net8.0-windows\win-x64\publish'),
        (Join-Path $PSScriptRoot 'Kosha.Desktop\bin\Release\net8.0-windows\win-x64'),
        (Join-Path $PSScriptRoot 'Kosha.Desktop\bin\Release\net8.0-windows')
    ) | Where-Object { $_ }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $c 'Kosha.Desktop.exe')) {
            return (Resolve-Path -LiteralPath $c).Path.TrimEnd('\')
        }
    }
    throw ("Kosha.Desktop.exe was not found. Build it first with`n" +
           "    dotnet publish addins\desktop\Kosha.Desktop -c Release -r win-x64 --self-contained false`n" +
           "or pass -Source with the folder that holds Kosha.Desktop.exe. Nothing was installed.")
}

$from = Find-Source
$exe = Join-Path $Target 'Kosha.Desktop.exe'
Write-Host "Installing Kosha Desktop from $from to $Target ..."

# A running copy is stopped so its files can be replaced. It keeps no unsaved work: settings are saved
# as soon as they change, and check-ins finish before its windows can be closed.
Get-Process -Name 'Kosha.Desktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

New-Item -ItemType Directory -Force -Path $Target | Out-Null
if ($from -ne (Resolve-Path -LiteralPath $Target).Path.TrimEnd('\')) {
    Copy-Item -Path (Join-Path $from '*') -Destination $Target -Recurse -Force
}
foreach ($name in 'uninstall.ps1', 'install.ps1') {
    $s = Join-Path $PSScriptRoot $name
    if ((Test-Path -LiteralPath $s) -and ($PSScriptRoot.TrimEnd('\') -ne (Resolve-Path -LiteralPath $Target).Path.TrimEnd('\'))) {
        Copy-Item -LiteralPath $s -Destination $Target -Force
    }
}

# The Explorer commands and the start-up entry are written by Kosha Desktop itself (HKEY_CURRENT_USER only).
$p = Start-Process -FilePath $exe -ArgumentList 'register' -Wait -PassThru
if ($p.ExitCode -ne 0) {
    throw ("Kosha Desktop could not add its Explorer commands (exit code $($p.ExitCode)). If Windows said that .NET is " +
           "missing, install the .NET 8 Desktop Runtime (x64) and run this script again.")
}

# An entry in Settings > Apps, for this user only.
$key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall\KoshaDesktop')
try {
    $version = (Get-Item -LiteralPath $exe).VersionInfo.ProductVersion
    $key.SetValue('DisplayName', 'Kosha Desktop')
    $key.SetValue('DisplayVersion', [string]$version)
    $key.SetValue('Publisher', 'Kosha contributors')
    $key.SetValue('DisplayIcon', "$exe,0")
    $key.SetValue('InstallLocation', $Target)
    $key.SetValue('UninstallString', "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Target\uninstall.ps1`"")
    $key.SetValue('NoModify', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
    $key.SetValue('NoRepair', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
}
finally {
    $key.Close()
}

if (-not $NoStart) {
    Start-Process -FilePath $exe -ArgumentList 'tray'
}
Write-Host ""
Write-Host "Kosha Desktop is installed. Right-click a file in your Kosha workspace folder to check it in or out"
Write-Host "(on Windows 11, choose 'Show more options' first). The Kosha icon in the notification area signs you in."
