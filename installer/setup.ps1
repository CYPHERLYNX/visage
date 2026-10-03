#Requires -Version 5.1
<#
.SYNOPSIS
    Visage Setup - one-click installer for face unlock on Windows 10/11.

.DESCRIPTION
    Installs Visage (face unlock for Windows) with zero manual steps:
      1. Checks Windows 10/11 x64 and Python 3.10+.
      2. Creates C:\ProgramData\Visage (models\, identities\) locked to
         SYSTEM + Administrators.
      3. Installs the engine's Python dependencies.
      4. Downloads the YuNet + ArcFace ONNX models (hash-verified).
      5. Writes the default config.json.
      6. Registers the Visage Credential Provider sign-in tile.
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
$script:DataDir       = "C:\ProgramData\Visage"
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
    Write-Host "  Visage Setup - face unlock for Windows"
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
# progress. Returns the process exit code as a bare integer. Never throws.
#
# IMPORTANT: the child output is routed to the host (Write-Host), NOT to this
# function's output stream. Callers doing "$code = Invoke-Python ..." must
# receive ONLY the integer - if child stdout were emitted here, PowerShell
# would capture (output lines + exit code) as an array and "$code -ne 0"
# would misbehave (array -ne does filtering, not comparison).
function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $exe = $script:PythonCmd[0]
    $pre = @()
    if ($script:PythonCmd.Count -gt 1) { $pre = $script:PythonCmd[1..($script:PythonCmd.Count - 1)] }
    & $exe @pre @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    return $code
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

# Returns the concrete interpreter path via sys.executable, so the
# "py -3" launcher case pins the real python.exe instead of py.exe
# (storing py.exe alone would lose the "-3" selector and the provider
# would launch the wrong Python). Throws when the probe fails.
# Never returns an empty or non-existent path.
function Get-PythonExePath {
    $exe = $script:PythonCmd[0]
    $pre = @()
    if ($script:PythonCmd.Count -gt 1) { $pre = $script:PythonCmd[1..($script:PythonCmd.Count - 1)] }
    # 2>$null: stderr warnings must not pollute the captured stdout line.
    $out = & $exe @pre @("-c", "import sys; print(sys.executable)") 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "could not resolve the Python interpreter path (probe exit $LASTEXITCODE)"
    }
    $path = "$($out | Select-Object -First 1)".Trim()
    if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Python reported an unusable interpreter path: '$path'"
    }
    return $path
}

# ---------------------------------------------------------------- 0. admin

if (-not (Test-IsAdmin)) {
    Write-Host ""
    Write-Host "Visage Setup needs to run as Administrator."
    Write-Host "It registers a Windows sign-in component and writes protected"
    Write-Host "files under C:\ProgramData\Visage. No admin rights, no install."
    Write-Host ""
    Write-Host "Requesting elevation now - approve the UAC prompt to continue."
    $argList = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process powershell.exe -ArgumentList $argList -Verb RunAs
    exit 0
}

Write-Banner

# ------------------------------------------------- 1. OS and Python checks

Step-Header 1 8 "Checking Windows version and Python"

# --- Windows 10/11 x64 ---
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $build = [int]$os.BuildNumber
    $is64 = [Environment]::Is64BitOperatingSystem
    $caption = $os.Caption
} catch {
    Record-Result "Windows check" "FAILED" `
        "Could not read the Windows version. Visage needs Windows 10 or 11, 64-bit."
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
        "Found '$caption' (build $build). Visage needs Windows 10 or 11, 64-bit. Setup stopped."
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

Step-Header 2 8 "Creating C:\ProgramData\Visage (models, identities)"

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
        "C:\ProgramData\Visage\, models\, identities\ created; ACLs = SYSTEM + Administrators only."
} catch {
    Record-Result "Data folders" "FAILED" `
        "Could not create or lock down C:\ProgramData\Visage. Try running setup again as Administrator."
    Write-Host ""
    Write-Host "Setup stopped. Nothing else was installed."
    exit 1
}

# ------------------------------------------------- 3. Python dependencies

Step-Header 3 8 "Installing Python dependencies (engine\requirements.txt)"

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

Step-Header 4 8 "Downloading face models (YuNet + ArcFace ONNX)"

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
        Write-Host "     C:\ProgramData\Visage\models and re-run setup.ps1 for clean copies."
        Write-Host ""
        Write-Host "Setup stopped."
        exit 1
    }
}

# ------------------------------------------------- 5. engine package deploy

Step-Header 5 8 "Deploying the engine (runs as SYSTEM)"

$script:EngineTargetDir = Join-Path $script:DataDir "engine"
# Runtime modules only: the SYSTEM-spawned pipe server and its imports.
# (enroll.py / fetch_models.py / selftest.py are setup- and test-time tools
# and stay out of the privileged directory.)
$engineModules = @("__init__.py", "visage_engine.py", "pipe_protocol.py",
                   "vault.py", "identities.py", "liveness.py", "recognition.py")
try {
    New-Item -ItemType Directory -Path $script:EngineTargetDir -Force -ErrorAction Stop | Out-Null
    foreach ($mod in $engineModules) {
        $src = Join-Path $script:EngineDir $mod
        if (-not (Test-Path $src)) { throw "engine\$mod is missing from the project checkout" }
        Copy-Item $src (Join-Path $script:EngineTargetDir $mod) -Force -ErrorAction Stop
    }

    # D1 (security): this code runs as SYSTEM on the secure desktop. The
    # directory must be admin-writable-only, or any local user could plant
    # code that runs as SYSTEM.
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid  = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $eacl = New-Object System.Security.AccessControl.DirectorySecurity
    $eacl.SetAccessRuleProtection($true, $false)
    $eacl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $systemSid, "FullControl", "ContainerInherit, ObjectInherit", "None", "Allow")))
    $eacl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $adminSid, "FullControl", "ContainerInherit, ObjectInherit", "None", "Allow")))
    Set-Acl -Path $script:EngineTargetDir -AclObject $eacl -ErrorAction Stop

    # Deterministic interpreter for the credential provider: it reads
    # HKLM\SOFTWARE\Visage\PythonPath first, before any PATH probing.
    # Resolved via sys.executable so a "py -3" discovery pins the concrete
    # python.exe, not the py.exe launcher (which would lose the -3).
    $pyExePath = Get-PythonExePath
    New-Item -Path "HKLM:\SOFTWARE\Visage" -Force -ErrorAction Stop | Out-Null
    Set-ItemProperty -Path "HKLM:\SOFTWARE\Visage" -Name "PythonPath" `
        -Value $pyExePath -ErrorAction Stop

    # Smoke test: "--help" imports the package (catches broken relative
    # imports, the exact bug class that once shipped here) without starting
    # the pipe server. Must exit 0. Runs from C:\ProgramData\Visage so that
    # "python -m engine.visage_engine" resolves exactly like the provider's
    # own spawn command.
    Push-Location $script:DataDir
    try {
        $code = Invoke-Python @("-m", "engine.visage_engine", "--help")
    } finally {
        Pop-Location
    }
    if ($code -ne 0) {
        throw "deployed engine failed its --help smoke test (exit $code) - its imports are broken"
    }
    Record-Result "Engine deploy" "OK" `
        "7 runtime modules copied to C:\ProgramData\Visage\engine\ (SYSTEM + Administrators only); interpreter pinned in HKLM\SOFTWARE\Visage; --help smoke test passed."
} catch {
    Record-Result "Engine deploy" "FAILED" "Could not deploy the engine: $($_.Exception.Message)"
    Write-Host ""
    Write-Host "The sign-in tile spawns this engine - without it, face unlock cannot work."
    Write-Host "Fix what the error names, then re-run setup.ps1 (it picks up where it left off)."
    Write-Host ""
    Write-Host "Setup stopped."
    exit 1
}

# ------------------------------------------------- 6. default config.json

Step-Header 6 8 "Writing default config"

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
            "Wrote C:\ProgramData\Visage\config.json (match threshold 0.66, strictness heavy)."
    }
} catch {
    Record-Result "Default config" "FAILED" "Could not write config.json: $($_.Exception.Message)"
    Write-Host ""
    Write-Host "Setup stopped."
    exit 1
}

# ------------------------------------------------- 7. Credential Provider

Step-Header 7 8 "Building + registering the Visage sign-in tile"

$script:SlnPath = Join-Path $script:CpDir "VisageCP.sln"
$script:DllPath = Join-Path $script:CpDir "VisageCP.dll"

# Finds msbuild.exe via vswhere. Returns the full path, or $null.
function Find-MSBuild {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { return $null }
    $msb = & $vswhere -latest -requires Microsoft.Component.MSBuild `
        -find MSBuild\**\Bin\MSBuild.exe 2>$null | Select-Object -First 1
    if ($msb -and (Test-Path $msb)) { return $msb }
    return $null
}

# --- 6a. build the DLL (skipped when it is already there) ---
$cpBuilt = $false
if (Test-Path $script:DllPath) {
    Record-Result "Sign-in component build" "OK" "VisageCP.dll already present - skipped rebuild."
    $cpBuilt = $true
} elseif (-not (Test-Path $script:SlnPath)) {
    Record-Result "Sign-in component build" "PENDING" `
        "cp\VisageCP.sln not found - the Credential Provider source is not built yet."
} else {
    $msbuild = Find-MSBuild
    if (-not $msbuild) {
        Record-Result "Sign-in component build" "PENDING" `
            "Visual Studio (msbuild) was not found on this PC."
        Write-Host ""
        Write-Host "To build the sign-in component:"
        Write-Host "  1. Install Visual Studio 2022 (Community is free) with the"
        Write-Host "     'Desktop development with C++' workload, from"
        Write-Host "     https://visualstudio.microsoft.com/downloads/"
        Write-Host "  2. Re-run setup.ps1 - it will build and register automatically."
        Write-Host ""
    } else {
        Write-Host "Building VisageCP.sln (Release x64) with msbuild..."
        Write-Host ""
        & $msbuild $script:SlnPath /p:Configuration=Release /p:Platform=x64 `
            /v:minimal /nologo 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -eq 0) {
            $builtDll = Join-Path $script:CpDir "x64\Release\VisageCP.dll"
            if (Test-Path $builtDll) {
                Copy-Item $builtDll $script:DllPath -Force
                Record-Result "Sign-in component build" "OK" "VisageCP.dll built (Release x64)."
                $cpBuilt = $true
            } else {
                Record-Result "Sign-in component build" "FAILED" `
                    "msbuild reported success but x64\Release\VisageCP.dll was not produced."
            }
        } else {
            Record-Result "Sign-in component build" "FAILED" `
                "msbuild exited with code $LASTEXITCODE - see the build output above."
            Write-Host ""
            Write-Host "The C++ compiler reported errors (red lines above). Common causes:"
            Write-Host "  - An older Visual Studio without the v143 toolset."
            Write-Host "  - A Windows SDK older than 10.0."
            Write-Host "  Fix what the errors name, then re-run setup.ps1."
            Write-Host ""
            Write-Host "Setup stopped."
            exit 1
        }
    }
}

# --- 6b. register the tile ---
if (-not $cpBuilt) {
    Record-Result "Credential Provider" "PENDING" `
        "Skipped: the sign-in component is not built yet (see the build step above)."
} elseif (-not (Test-Path $script:RegisterCp)) {
    Record-Result "Credential Provider" "PENDING" `
        "cp\register.ps1 not found - cannot register the tile yet."
    Write-Host ""
    Write-Host "Re-run setup.ps1 once cp\register.ps1 is present - it picks up from here."
} else {
    Write-Host "Running: cp\register.ps1"
    Write-Host ""
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:RegisterCp
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Record-Result "Credential Provider" "OK" "Visage sign-in tile registered with Windows."
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

# ------------------------------------------------- 8. verify

Step-Header 8 8 "Verifying the install"

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
    Write-Host "  1. The Visage tile should appear on the sign-in screen."
    Write-Host "     Press Win+L to lock Windows and look for it."
    Write-Host "  2. If the tile is missing, reboot once - Windows loads"
    Write-Host "     credential providers at boot."
    Write-Host "  3. Enroll your face in Visage Manager BEFORE testing unlock"
    Write-Host "     (see the 3 steps below) - the tile does nothing until then."
    Write-Host "  4. If the camera light never comes on, close any other app"
    Write-Host "     using the camera (Zoom, Teams, browser tabs) and try again."
}

# ------------------------------------------------- summary

Write-Host ""
Write-Host "================================================================"
if ($script:HadFailure) {
    $exitCode = 1
    Write-Host "  Visage Setup finished WITH FAILURES"
} elseif ($script:HadPending) {
    $exitCode = 2
    Write-Host "  Visage Setup finished with PENDING steps"
} else {
    $exitCode = 0
    Write-Host "  Visage Setup complete"
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
Write-Host "  - Data folder:      C:\ProgramData\Visage\"
Write-Host "                      (models\, identities\, config.json - SYSTEM + Administrators only)"
Write-Host "  - Python packages:  from engine\requirements.txt"
Write-Host "  - Face models:      YuNet + ArcFace (ONNX) in C:\ProgramData\Visage\models\"
if (($script:Results | Where-Object { $_.Name -eq "Credential Provider" }).Status -eq "OK") {
    Write-Host "  - Sign-in tile:     Visage Credential Provider registered"
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
Write-Host "  1. Open the Visage Manager app (Start menu -> Visage Manager)."
Write-Host '  2. Click "Enroll face" and follow the on-screen guide.'
Write-Host "  3. Done. Press Win+L to lock Windows, then look at the camera."
Write-Host ""
Write-Host "Tip: re-running setup.ps1 at any time repairs the install."
Write-Host ""

exit $exitCode
