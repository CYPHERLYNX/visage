#Requires -Version 5.1
<#
.SYNOPSIS
    Mien Setup - one-click installer for face unlock on Windows 10/11.

.DESCRIPTION
    Installs Mien (face unlock for Windows) with zero manual steps:
      1. Checks Windows 10/11 x64 and Python 3.10+.
      2. Creates C:\ProgramData\Mien (models\, identities\) locked to
         SYSTEM + Administrators.
      3. Installs the engine's Python dependencies.
      4. Downloads the YuNet + ArcFace ONNX models (hash-verified).
      5. Writes the default config.json.
      6. Registers the Mien Credential Provider sign-in tile.
      7. Verifies the install (engine self-test, or a manual checklist).

    Idempotent: re-running setup.ps1 repairs a partial install instead of
    breaking a working one. It never overwrites an existing config.json.

    Must run as Administrator. If it is not, it asks to relaunch elevated.

    Exit codes: 0 = installed, 1 = failed (see messages), 2 = installed with
    pending steps (a component was not built yet - re-run setup later).
#>

# ---------------------------------------------------------------- helpers

$script:InstallerDir  = $PSScriptRoot
$script:ProjectRoot   = Split-Path -Parent $script:InstallerDir
$script:EngineDir     = Join-Path $script:ProjectRoot "engine"
$script:CpDir         = Join-Path $script:ProjectRoot "cp"
$script:ReqFile       = Join-Path $script:EngineDir "requirements.txt"
$script:FetchModels   = Join-Path $script:EngineDir "fetch_models.py"
$script:SelfTest      = Join-Path $script:EngineDir "selftest.py"
$script:RegisterCp    = Join-Path $script:CpDir "register.ps1"
$script:DataDir       = "C:\ProgramData\Mien"
$script:ModelsDir     = Join-Path $script:DataDir "models"
$script:IdentitiesDir = Join-Path $script:DataDir "identities"
$script:ConfigPath    = Join-Path $script:DataDir "config.json"

$script:Results    = @()   # @{ Name=; Status=; Detail= }  Status: OK, PENDING, FAILED
$script:HadFailure = $false
$script:HadPending = $false
$script:PythonCmd  = $null # e.g. @("python") or @("py", "-3")

function Write-Banner {
    Write-Host ""
    Write-Host "================================================================"
    Write-Host "  Mien Setup - face unlock for Windows"
    Write-Host "================================================================"
    Write-Host ""
}

function Step-Header {
    param([int]$Number, [int]$Total, [string]$Title)
    Write-Host ""
    Write-Host "[$Number/$Total] $Title"
    Write-Host ("-" * 64)
}

function Record-Result {
    param([string]$Name, [string]$Status, [string]$Detail)
    $script:Results += [pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail }
    if ($Status -eq "FAILED") { $script:HadFailure = $true }
    if ($Status -eq "PENDING") { $script:HadPending = $true }
    $tag = @{ OK = "[ OK ]"; PENDING = "[PENDING]"; FAILED = "[FAILED]" }[$Status]
    Write-Host "$tag $Name"
    if ($Detail) { Write-Host "       $Detail" }
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Runs python with the given arguments, streaming its output so the user sees
# progress. Returns the process exit code. Never throws.
function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $exe = $script:PythonCmd[0]
    $pre = @()
    if ($script:PythonCmd.Count -gt 1) { $pre = $script:PythonCmd[1..($script:PythonCmd.Count - 1)] }
    & $exe @pre @Arguments
    return $LASTEXITCODE
}

# Finds "python" or the "py" launcher on PATH with version >= 3.10.
# Returns @{ Cmd = @("python") or @("py","-3"); Version = "Python 3.12.1" },
# or $null when not found. Never throws.
function Find-Python {
    $candidates = @(
        @{ Exe = "python"; Pre = @() },
        @{ Exe = "py";     Pre = @("-3") }
    )
    foreach ($c in $candidates) {
        $probe = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $probe) { continue }
        $verOut = & $c.Exe @($c.Pre) @("--version") 2>&1
        $code = $LASTEXITCODE
        if ($code -ne 0) { continue }
        if (($verOut -join " ") -match 'Python\s+(\d+)\.(\d+)') {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
                return @{ Cmd = @($c.Exe) + $c.Pre; Version = ($verOut -join " ").Trim() }
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------- 0. admin

if (-not (Test-IsAdmin)) {
    Write-Host ""
    Write-Host "Mien Setup needs to run as Administrator."
    Write-Host "It registers a Windows sign-in component and writes protected"
    Write-Host "files under C:\ProgramData\Mien. No admin rights, no install."
    Write-Host ""
    Write-Host "Requesting elevation now - approve the UAC prompt to continue."
    $argList = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process powershell.exe -ArgumentList $argList -Verb RunAs
    exit 0
}

Write-Banner

# ------------------------------------------------- 1. OS and Python checks

Step-Header 1 7 "Checking Windows version and Python"

# --- Windows 10/11 x64 ---
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $build = [int]$os.BuildNumber
    $is64 = [Environment]::Is64BitOperatingSystem
    $caption = $os.Caption
} catch {
    Record-Result "Windows check" "FAILED" `
        "Could not read the Windows version. Mien needs Windows 10 or 11, 64-bit."
    Write-Host ""
    Write-Host "Setup cannot continue on this machine. Nothing was installed."
    exit 1
}

if ($build -ge 10240 -and $is64) {
    $winLabel = "Windows 11"
    if ($build -lt 22000) { $winLabel = "Windows 10" }
    Record-Result "Windows check" "OK" "$caption (build $build), 64-bit. Detected as $winLabel."
} elseif ($build -ne 0) {
    Record-Result "Windows check" "FAILED" `
        "Found '$caption' (build $build). Mien needs Windows 10 or 11, 64-bit. Setup stopped."
    Write-Host ""
    Write-Host "Setup cannot continue on this machine. Nothing was installed."
    exit 1
}

# --- Python 3.10+ ---
$pyInfo = Find-Python
if ($pyInfo) {
    $script:PythonCmd = $pyInfo.Cmd
    Record-Result "Python check" "OK" "$($pyInfo.Version) on PATH (via $($script:PythonCmd -join ' '))."
} else {
    Record-Result "Python check" "FAILED" `
        "Python 3.10 or newer was not found on PATH."
    Write-Host ""
    Write-Host "To fix this:"
    Write-Host "  1. Go to https://www.python.org/downloads/ and download Python 3.12"
    Write-Host "     (any 3.10+ works)."
    Write-Host "  2. During install, tick the box 'Add python.exe to PATH'."
    Write-Host "  3. Close this window, open a new PowerShell, and run setup.ps1 again."
    Write-Host ""
    Write-Host "Setup stopped. Nothing was installed."
    exit 1
}

# ------------------------------------------- 2. data dirs + locked-down ACLs

Step-Header 2 7 "Creating C:\ProgramData\Mien (models, identities)"

try {
    foreach ($d in @($script:DataDir, $script:ModelsDir, $script:IdentitiesDir)) {
        New-Item -ItemType Directory -Path $d -Force -ErrorAction Stop | Out-Null
    }

    # Lock down: SYSTEM + Administrators only, full control, no inheritance.
    # Uses well-known SIDs so this works on non-English Windows too.
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid  = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    foreach ($d in @($script:DataDir, $script:ModelsDir, $script:IdentitiesDir)) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $systemSid, "FullControl", "ContainerInherit, ObjectInherit", "None", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $adminSid, "FullControl", "ContainerInherit, ObjectInherit", "None", "Allow")))
        Set-Acl -Path $d -AclObject $acl -ErrorAction Stop
    }
    Record-Result "Data folders" "OK" `
        "C:\ProgramData\Mien\, models\, identities\ created; ACLs = SYSTEM + Administrators only."
} catch {
    Record-Result "Data folders" "FAILED" `
        "Could not create or lock down C:\ProgramData\Mien. Try running setup again as Administrator."
    Write-Host ""
    Write-Host "Setup stopped. Nothing else was installed."
    exit 1
}

# ------------------------------------------------- 3. Python dependencies

Step-Header 3 7 "Installing Python dependencies (engine\requirements.txt)"

if (-not (Test-Path $script:ReqFile)) {
    Record-Result "Python dependencies" "PENDING" `
        "engine\requirements.txt not found - the engine is not built yet. Re-run setup.ps1 after it lands."
} else {
    Write-Host "Running: pip install -r engine\requirements.txt"
    Write-Host "(this downloads packages from PyPI - it can take a few minutes)"
    Write-Host ""
    $code = Invoke-Python @("-m", "pip", "install", "--disable-pip-version-check", "-r", $script:ReqFile)
    if ($code -eq 0) {
        Record-Result "Python dependencies" "OK" "All packages from engine\requirements.txt installed."
    } else {
        Record-Result "Python dependencies" "FAILED" "pip exited with code $code."
        Write-Host ""
        Write-Host "To fix this:"
        Write-Host "  1. Make sure this PC is connected to the internet."
        Write-Host "  2. Upgrade pip, then re-run setup (it picks up where it left off):"
        Write-Host "       python -m pip install --upgrade pip"
        Write-Host "       .\setup.ps1"
        Write-Host "  3. If it still fails, the red lines above name the package that"
        Write-Host "     could not be installed - that is the one to investigate."
        Write-Host ""
        Write-Host "Setup stopped."
        exit 1
    }
}

# ------------------------------------------------- 4. face models (YuNet + ArcFace)

Step-Header 4 7 "Downloading face models (YuNet + ArcFace ONNX)"

if (-not (Test-Path $script:FetchModels)) {
    Record-Result "Face models" "PENDING" `
        "engine\fetch_models.py not found - the engine is not built yet. Re-run setup.ps1 after it lands."
} else {
    Write-Host "Running: python engine\fetch_models.py"
    Write-Host "(downloads YuNet + ArcFace ONNX models with hash verification)"
    Write-Host ""
    $code = Invoke-Python @($script:FetchModels)
    if ($code -eq 0) {
        Record-Result "Face models" "OK" "YuNet + ArcFace ONNX models downloaded and hash-verified."
    } else {
        Record-Result "Face models" "FAILED" "fetch_models.py exited with code $code."
        Write-Host ""
        Write-Host "To fix this:"
        Write-Host "  1. Check your internet connection - the models are a few hundred MB."
        Write-Host "  2. Make sure drive C: has at least 1 GB free."
        Write-Host "  3. Re-run setup.ps1 - files that already downloaded and verified"
        Write-Host "     are kept, so it resumes instead of starting over."
        Write-Host "  4. If the script reported a hash mismatch, delete"
        Write-Host "     C:\ProgramData\Mien\models and re-run setup.ps1 for clean copies."
        Write-Host ""
        Write-Host "Setup stopped."
        exit 1
    }
}

# ------------------------------------------------- 5. default config.json

Step-Header 5 7 "Writing default config"

try {
    $needsWrite = $true
    if (Test-Path $script:ConfigPath) {
        # Keep the user's existing config, but make sure it is still valid JSON.
        try {
            Get-Content $script:ConfigPath -Raw -ErrorAction Stop | ConvertFrom-Json | Out-Null
            Record-Result "Default config" "OK" "config.json already exists and is valid - kept as-is."
            $needsWrite = $false
        } catch {
            $bak = "$($script:ConfigPath).bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item $script:ConfigPath $bak -Force -ErrorAction Stop
            Write-Host "NOTE: existing config.json was corrupt - backed up to $bak."
            Write-Host "      Writing fresh defaults."
        }
    }

    if ($needsWrite) {
        # Machine-wide engine defaults, read by engine/load_machine_config().
        # Only these two keys are honored today; unknown keys are ignored.
        # match_threshold mirrors the engine default (0.66, Glance's shipped
        # value); strictness is light | heavy | off (lowercase).
        $config = [ordered]@{
            match_threshold = 0.66
            strictness      = "heavy"
        }
        $json = ($config | ConvertTo-Json -Depth 4)
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($script:ConfigPath, $json, $utf8NoBom)
        Record-Result "Default config" "OK" `
            "Wrote C:\ProgramData\Mien\config.json (match threshold 0.66, strictness heavy)."
    }
} catch {
    Record-Result "Default config" "FAILED" "Could not write config.json: $($_.Exception.Message)"
    Write-Host ""
    Write-Host "Setup stopped."
    exit 1
}

# ------------------------------------------------- 6. Credential Provider

Step-Header 6 7 "Registering the Mien sign-in tile (Credential Provider)"

if (-not (Test-Path $script:RegisterCp)) {
    Record-Result "Credential Provider" "PENDING" `
        "..\cp\register.ps1 not found - the Credential Provider is not built yet."
    Write-Host ""
    Write-Host "NOTE: the Mien sign-in tile cannot be registered until the CP worker"
    Write-Host "delivers cp\register.ps1. Everything else installed fine."
    Write-Host "Re-run setup.ps1 after the CP lands - it will pick up from here."
} else {
    Write-Host "Running: cp\register.ps1"
    Write-Host ""
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:RegisterCp
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Record-Result "Credential Provider" "OK" "Mien sign-in tile registered with Windows."
    } else {
        Record-Result "Credential Provider" "FAILED" "cp\register.ps1 exited with code $code."
        Write-Host ""
        Write-Host "To fix this:"
        Write-Host "  1. Setup must run as Administrator (it checks this at the start)."
        Write-Host "  2. Reboot once - Windows only loads credential providers at boot,"
        Write-Host "     so a fresh boot can resolve a half-registered state."
        Write-Host "  3. Re-run setup.ps1."
        Write-Host ""
        Write-Host "Setup stopped."
        exit 1
    }
}

# ------------------------------------------------- 7. verify

Step-Header 7 7 "Verifying the install"

if (Test-Path $script:SelfTest) {
    Write-Host "Running engine self-test (pipe protocol): python engine\selftest.py"
    Write-Host ""
    $code = Invoke-Python @($script:SelfTest)
    if ($code -eq 0) {
        Record-Result "Verification" "OK" "Engine pipe-protocol self-test passed."
    } else {
        Record-Result "Verification" "FAILED" "Engine self-test exited with code $code - see its output above."
        Write-Host ""
        Write-Host "The install is on disk but the self-test reported a problem."
        Write-Host "Read the self-test output above, fix what it names, then re-run setup.ps1."
        Write-Host ""
        Write-Host "Setup stopped."
        exit 1
    }
} else {
    Record-Result "Verification" "PENDING" `
        "engine\selftest.py not found - showing the manual checklist instead."
    Write-Host ""
    Write-Host "Manual verification checklist:"
    Write-Host "  1. The Mien tile should appear on the sign-in screen."
    Write-Host "     Press Win+L to lock Windows and look for it."
    Write-Host "  2. If the tile is missing, reboot once - Windows loads"
    Write-Host "     credential providers at boot."
    Write-Host "  3. Enroll your face in Mien Manager BEFORE testing unlock"
    Write-Host "     (see the 3 steps below) - the tile does nothing until then."
    Write-Host "  4. If the camera light never comes on, close any other app"
    Write-Host "     using the camera (Zoom, Teams, browser tabs) and try again."
}

# ------------------------------------------------- summary

Write-Host ""
Write-Host "================================================================"
if ($script:HadFailure) {
    $exitCode = 1
    Write-Host "  Mien Setup finished WITH FAILURES"
} elseif ($script:HadPending) {
    $exitCode = 2
    Write-Host "  Mien Setup finished with PENDING steps"
} else {
    $exitCode = 0
    Write-Host "  Mien Setup complete"
}
Write-Host "================================================================"
Write-Host ""
Write-Host "Step results:"
foreach ($r in $script:Results) {
    $tag = @{ OK = "[ OK ]"; PENDING = "[PENDING]"; FAILED = "[FAILED]" }[$r.Status]
    Write-Host "  $tag $($r.Name)"
}
Write-Host ""
Write-Host "What was installed:"
Write-Host "  - Data folder:      C:\ProgramData\Mien\"
Write-Host "                      (models\, identities\, config.json - SYSTEM + Administrators only)"
Write-Host "  - Python packages:  from engine\requirements.txt"
Write-Host "  - Face models:      YuNet + ArcFace (ONNX) in C:\ProgramData\Mien\models\"
if (($script:Results | Where-Object { $_.Name -eq "Credential Provider" }).Status -eq "OK") {
    Write-Host "  - Sign-in tile:     Mien Credential Provider registered"
} else {
    Write-Host "  - Sign-in tile:     NOT registered yet (see PENDING step above)"
}
Write-Host ""
if ($script:HadPending) {
    Write-Host "Pending steps: re-run setup.ps1 after the missing component lands -"
    Write-Host "it repairs and continues instead of starting over."
    Write-Host ""
}
Write-Host "To start using face unlock (3 steps):"
Write-Host "  1. Open the Mien Manager app (Start menu -> Mien Manager)."
Write-Host '  2. Click "Enroll face" and follow the on-screen guide.'
Write-Host "  3. Done. Press Win+L to lock Windows, then look at the camera."
Write-Host ""
Write-Host "Tip: re-running setup.ps1 at any time repairs the install."
Write-Host ""

exit $exitCode
