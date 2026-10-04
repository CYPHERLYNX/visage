#Requires -Version 5.1
param(
    [switch]$Force   # skip interactive confirmations (used by Visage-Setup.exe's uninstaller)
)
<#
.SYNOPSIS
    Visage Uninstall - cleanly removes face unlock from Windows.

.DESCRIPTION
    1. Unregisters the Visage Credential Provider sign-in tile
       (via ..\cp\unregister.ps1 when present).
    2. Stops any running Visage processes.
    3. Asks for confirmation, then deletes C:\ProgramData\Visage
       (enrolled faces, settings, models) and verifies it is gone.
    4. Optionally removes the Python packages Visage installed.

    Must run as Administrator. If it is not, it asks to relaunch elevated.
    Leaves the Visage program folder itself (this repo / the release zip
    contents) in place - delete that yourself if you want it gone too.

    Exit codes: 0 = uninstalled (or nothing to do), 1 = a step failed.
#>

$script:InstallerDir = $PSScriptRoot
$script:ProjectRoot  = Split-Path -Parent $script:InstallerDir
$script:EngineDir    = Join-Path $script:ProjectRoot "engine"
$script:CpDir        = Join-Path $script:ProjectRoot "cp"
$script:ReqFile      = Join-Path $script:EngineDir "requirements.txt"
$script:UnregisterCp = Join-Path $script:CpDir "unregister.ps1"
$script:DataDir      = "C:\ProgramData\Visage"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Best-effort python finder (same contract as setup.ps1). Returns @(exe, pre...)
# or $null. Never throws.
function Find-Python {
    foreach ($c in @(@{ Exe = "python"; Pre = @() }, @{ Exe = "py"; Pre = @("-3") })) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $verOut = & $c.Exe @($c.Pre) @("--version") 2>&1
        if ($LASTEXITCODE -ne 0) { continue }
        if (($verOut -join " ") -match 'Python\s+(\d+)\.(\d+)') {
            if ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10)) {
                return @($c.Exe) + $c.Pre
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------- 0. admin

if (-not (Test-IsAdmin)) {
    Write-Host ""
    Write-Host "Visage Uninstall needs to run as Administrator."
    Write-Host "Requesting elevation now - approve the UAC prompt to continue."
    $argList = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process powershell.exe -ArgumentList $argList -Verb RunAs
    exit 0
}

Write-Host ""
Write-Host "================================================================"
Write-Host "  Visage Uninstall"
Write-Host "================================================================"

$failed = $false

# ------------------------------------------------- 1. unregister the CP tile

Write-Host ""
Write-Host "[1/4] Unregistering the Visage sign-in tile"
Write-Host ("-" * 64)
if (Test-Path $script:UnregisterCp) {
    Write-Host "Running: cp\unregister.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:UnregisterCp
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host "[ OK ] Sign-in tile unregistered."
        Write-Host "       (If the tile still shows, reboot once - Windows loads"
        Write-Host "        credential providers at boot.)"
    } else {
        Write-Host "[FAILED] cp\unregister.ps1 exited with code $code."
        Write-Host "       The tile may still appear on the lock screen."
        Write-Host "       Re-run this uninstaller after a reboot. If the tile still"
        Write-Host "       will not go away, see cp\TEST_PLAN.md for the manual"
        Write-Host "       registry cleanup steps (Safe Mode recovery)."
        $failed = $true
    }
} else {
    Write-Host "[SKIP] ..\cp\unregister.ps1 not found - nothing to unregister."
    Write-Host "       (Either the tile was never registered, or cp\ was removed.)"
}

# ------------------------------------------------- 1b. Start menu shortcut

Write-Host ""
Write-Host "Removing the Start menu shortcut"
Write-Host ("-" * 64)
$lnkPath = Join-Path ([Environment]::GetFolderPath("CommonStartMenu")) "Programs\Visage Manager.lnk"
if (Test-Path $lnkPath) {
    try {
        Remove-Item -Force -Path $lnkPath -ErrorAction Stop
        Write-Host "[ OK ] Start menu shortcut removed."
    } catch {
        Write-Host "[FAILED] Could not remove the Start menu shortcut: $($_.Exception.Message)"
        $failed = $true
    }
} else {
    Write-Host "[ OK ] No Start menu shortcut found."
}

# ------------------------------------------------- 2. stop running processes

Write-Host ""
Write-Host "[2/4] Stopping running Visage processes"
Write-Host ("-" * 64)
$procs = Get-Process -Name "Visage*" -ErrorAction SilentlyContinue
# The SYSTEM-spawned engine runs as python.exe/pythonw.exe, not "Visage*".
# Identify it by its unmistakable module command line.
$engineProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" `
    -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'engine\.visage_engine' }
foreach ($ep in $engineProcs) {
    try { Stop-Process -Id $ep.ProcessId -Force -ErrorAction Stop } catch { }
}
if ($procs) {
    $names = ($procs | Select-Object -ExpandProperty ProcessName -Unique) -join ", "
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    $still = Get-Process -Name "Visage*" -ErrorAction SilentlyContinue
    if ($still) {
        Write-Host "[FAILED] Could not stop: $names"
        Write-Host "       Close Visage Manager yourself, then re-run this uninstaller."
        $failed = $true
    } else {
        Write-Host "[ OK ] Stopped: $names"
    }
} else {
    Write-Host "[ OK ] No Visage processes running."
}

# ------------------------------------------------- 3. delete the data folder

Write-Host ""
Write-Host "[3/4] Deleting Visage data"
Write-Host ("-" * 64)
if (-not (Test-Path $script:DataDir)) {
    Write-Host "[ OK ] C:\ProgramData\Visage does not exist - nothing to delete."
} else {
    Write-Host "This permanently deletes:"
    Write-Host "  C:\ProgramData\Visage\"
    Write-Host "    - all enrolled faces (identities\)"
    Write-Host "    - settings (config.json)"
    Write-Host "    - downloaded face models (models\)"
    Write-Host ""
    $answer = "YES"
    if (-not $Force) {
        $answer = Read-Host 'Type YES (all caps) to confirm deletion'
    }
    if ($answer -ne "YES") {
        Write-Host ""
        Write-Host "Uninstall cancelled. Nothing was deleted."
        exit 0
    }
    try {
        Remove-Item -Recurse -Force -Path $script:DataDir -ErrorAction Stop
        if (Test-Path $script:DataDir) {
            throw "folder still exists after deletion"
        }
        Write-Host "[ OK ] C:\ProgramData\Visage deleted."
    } catch {
        Write-Host "[FAILED] Could not delete C:\ProgramData\Visage."
        Write-Host "       Reason: $($_.Exception.Message)"
        Write-Host "       Close any program using those files and re-run this uninstaller"
        Write-Host "       as Administrator. Last resort: delete the folder by hand."
        $failed = $true
    }
}

# setup.ps1 wrote HKLM\SOFTWARE\Visage (PythonPath). Remove it in ALL cases -
# even when C:\ProgramData\Visage is already gone (it used to live inside the
# branch above, so a leftover key survived exactly that scenario).
$regKey = "HKLM:\SOFTWARE\Visage"
if (Test-Path $regKey) {
    Remove-Item -Path $regKey -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $regKey) {
        Write-Host "[WARN] Could not remove HKLM\SOFTWARE\Visage - remove it by hand in regedit."
    } else {
        Write-Host "[ OK ] HKLM\SOFTWARE\Visage removed."
    }
} else {
    Write-Host "[ OK ] HKLM\SOFTWARE\Visage not present - nothing to remove."
}

# ------------------------------------------------- 4. optional Python packages

Write-Host ""
Write-Host "[4/4] Python packages (optional)"
Write-Host ("-" * 64)
$rm = "n"
if ($Force) {
    # Wizard uninstaller: keep the Python packages (shared with other software).
    Write-Host "[SKIP] Keeping the Python packages (uninstaller was run with -Force)."
} else {
$rm = Read-Host "Also remove the Python packages Visage installed? (y/N)"
if ($rm -match '^[Yy]') {
    if (-not (Test-Path $script:ReqFile)) {
        Write-Host "[SKIP] engine\requirements.txt not found - cannot tell which"
        Write-Host "       packages Visage installed, so nothing was removed."
    } else {
        $pyCmd = Find-Python
        if (-not $pyCmd) {
            Write-Host "[SKIP] Python not found on PATH - skipping package removal."
        } else {
            Write-Host "Running: pip uninstall -y -r engine\requirements.txt"
            $pyPre = @()
            if ($pyCmd.Count -gt 1) { $pyPre = $pyCmd[1..($pyCmd.Count - 1)] }
            & $pyCmd[0] @pyPre @("-m", "pip", "uninstall", "-y", "-r", $script:ReqFile)
            $code = $LASTEXITCODE
            if ($code -eq 0) {
                Write-Host "[ OK ] Visage's Python packages removed."
            } else {
                Write-Host "[FAILED] pip uninstall exited with code $code."
                Write-Host "       You can remove leftovers by hand with:"
                Write-Host "         python -m pip uninstall -y -r `"$($script:ReqFile)`""
                $failed = $true
            }
        }
    }
} else {
    Write-Host "[SKIP] Keeping the Python packages."
}
}  # end of: if ($Force) { skip } else { ask }

# ------------------------------------------------- done

Write-Host ""
Write-Host "================================================================"
if ($failed) {
    Write-Host "  Visage Uninstall finished WITH FAILURES - see above"
    Write-Host "================================================================"
    Write-Host ""
    Write-Host "What may be left behind:"
    Write-Host "  - the Visage sign-in tile (reboot, then re-run this uninstaller)"
    Write-Host "  - C:\ProgramData\Visage (delete by hand as Administrator)"
    Write-Host "  - Python packages (python -m pip uninstall -y -r engine\requirements.txt)"
    Write-Host ""
    exit 1
} else {
    Write-Host "  Visage Uninstall complete"
    Write-Host "================================================================"
    Write-Host ""
    Write-Host "Removed: sign-in tile registration, C:\ProgramData\Visage,"
    Write-Host "and (if you chose it) the Python packages."
    Write-Host "The Visage program folder itself was left in place - delete it"
    Write-Host "yourself if you no longer want it."
    Write-Host ""
    exit 0
}
