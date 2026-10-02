# make_test_vault.ps1 - Creates a TEST vault.dat for credential-provider testing.
# Encrypts {"version":1,"username":...,"domain":...,"password":...} with DPAPI
# LocalMachine scope, exactly like the real enrollment flow, and writes it to
# C:\ProgramData\Mien\vault.dat (backing up any existing file first).
#
#   .\make_test_vault.ps1 -Username "Fahad" -Password "test-password-123"
#   .\make_test_vault.ps1 -Username "DOMAIN\Fahad" -Password "..."
#
# WARNING: this writes a DPAPI blob any local process can unprotect. Use only
# on a test VM, never with a real password. ASCII-only.

param(
    [Parameter(Mandatory=$true)][string]$Username,
    [string]$Domain = "",
    [Parameter(Mandatory=$true)][string]$Password
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security

$dir = "C:\ProgramData\Mien"
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }

$vault = Join-Path $dir "vault.dat"
if (Test-Path $vault) {
    $bak = "$vault.bak"
    Copy-Item $vault $bak -Force
    Write-Host "Backed up existing vault to $bak"
}

$obj = @{ version = 1; username = $Username; domain = $Domain; password = $Password }
$json = ($obj | ConvertTo-Json -Compress)
$plain = [Text.Encoding]::UTF8.GetBytes($json)
$blob = [Security.Cryptography.ProtectedData]::Protect(
    $plain, $null, [Security.Cryptography.DataProtectionScope]::LocalMachine)
[IO.File]::WriteAllBytes($vault, $blob)

# Lock the ACL down the way the installer must: SYSTEM + Administrators only.
$acl = Get-Acl $vault
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
foreach ($who in @("NT AUTHORITY\SYSTEM", "BUILTIN\Administrators")) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $who, "FullControl", "Allow")
    $acl.AddAccessRule($rule)
}
Set-Acl $vault $acl

Write-Host "Wrote test vault: $vault (user=$Username domain=$Domain)"
Write-Host "The password is inside a DPAPI blob; plaintext never hits disk."
