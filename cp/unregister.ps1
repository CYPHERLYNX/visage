# unregister.ps1 - Removes the Visage Face Unlock credential provider.
# Run from an ELEVATED PowerShell, in the folder containing VisageCP.dll:
#   .\unregister.ps1
# Only Visage's own two registry keys are removed; built-in providers are untouched.
# ASCII-only.

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host "ERROR: this script must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as administrator, then run .\unregister.ps1 again."
    exit 1
}

$dll = Join-Path $PSScriptRoot "VisageCP.dll"
# Same Sysnative guard as register.ps1: from 32-bit PowerShell on 64-bit
# Windows, System32 redirects to SysWOW64, and the HKLM:\ checks below would
# look in the 32-bit registry view. Sysnative keeps us in the 64-bit view
# where the provider is actually registered.
$regsvr32 = Join-Path $env:SystemRoot "System32\regsvr32.exe"
if ((-not [Environment]::Is64BitProcess) -and [Environment]::Is64BitOperatingSystem) {
    $regsvr32 = Join-Path $env:SystemRoot "Sysnative\regsvr32.exe"
}

if ((Test-Path $dll) -and (Test-Path $regsvr32)) {
    Write-Host "Unregistering VisageCP.dll ..."
    & $regsvr32 /u /s $dll
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: regsvr32 /u exited with code $LASTEXITCODE; continuing with manual key removal." -ForegroundColor Yellow
    }
} else {
    Write-Host "VisageCP.dll or regsvr32.exe not found; removing registry keys directly."
}

$clsid = "{794AAC04-6CB6-4C77-A828-27C20D0F820D}"
$keys = @(
    "HKLM:\SOFTWARE\Classes\CLSID\$clsid",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$clsid"
)
foreach ($k in $keys) {
    if (Test-Path $k) {
        Remove-Item -Recurse -Force $k
        Write-Host "  removed: $k" -ForegroundColor Green
    } else {
        Write-Host "  already gone: $k"
    }
}

Write-Host ""
Write-Host "Visage Face Unlock is unregistered." -ForegroundColor Green
Write-Host "Lock the PC (Win+L) to confirm the tile is gone."
