# register.ps1 - Registers the Mien Face Unlock credential provider.
# Run from an ELEVATED PowerShell, in the folder containing MienCP.dll:
#   .\register.ps1
# ASCII-only.

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host "ERROR: this script must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as administrator, then run .\register.ps1 again."
    exit 1
}

$dll = Join-Path $PSScriptRoot "MienCP.dll"
if (-not (Test-Path $dll)) {
    Write-Host "ERROR: MienCP.dll not found next to this script: $dll" -ForegroundColor Red
    Write-Host "Build the Release|x64 configuration first (see BUILD.md)."
    exit 1
}

# 64-bit OS needs the 64-bit regsvr32. From 32-bit PowerShell on 64-bit
# Windows, System32 silently redirects to SysWOW64 (the 32-bit regsvr32),
# which would register the DLL in the 32-bit view where LogonUI never looks.
# Sysnative bypasses the redirect so we always get the 64-bit regsvr32.
$regsvr32 = Join-Path $env:SystemRoot "System32\regsvr32.exe"
if ((-not [Environment]::Is64BitProcess) -and [Environment]::Is64BitOperatingSystem) {
    $regsvr32 = Join-Path $env:SystemRoot "Sysnative\regsvr32.exe"
}
if (-not (Test-Path $regsvr32)) {
    Write-Host "ERROR: regsvr32.exe not found at $regsvr32" -ForegroundColor Red
    exit 1
}

Write-Host "Registering MienCP.dll ..."
& $regsvr32 /s $dll
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: regsvr32 failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# Verify both keys landed.
$clsid = "{794AAC04-6CB6-4C77-A828-27C20D0F820D}"
$comKey = "HKLM:\SOFTWARE\Classes\CLSID\$clsid\InprocServer32"
$cpKey  = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$clsid"

$ok = $true
foreach ($k in @($comKey, $cpKey)) {
    if (Test-Path $k) { Write-Host "  OK: $k" -ForegroundColor Green }
    else { Write-Host "  MISSING: $k" -ForegroundColor Red; $ok = $false }
}

if (-not $ok) {
    Write-Host "ERROR: registration incomplete." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Mien Face Unlock is registered." -ForegroundColor Green
Write-Host "Lock the PC (Win+L) to see the new tile. Your password/PIN tiles are untouched."
Write-Host "If anything looks wrong, run .\unregister.ps1 from an elevated prompt."
