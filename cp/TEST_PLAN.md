TEST PLAN - Visage Credential Provider
=====================================
ASCII-only document.

GOLDEN RULES
------------
1. VM FIRST. Never register this DLL on a physical machine until the full
   matrix below passes on a VM.
2. SNAPSHOT before registering. Name it "pre-visage-cp".
3. SPARE ADMIN ACCOUNT. Create a second local administrator account with a
   KNOWN password before you begin, and verify you can sign into it.
   If the tile ever misbehaves, you sign in as the spare admin and run
   unregister.ps1.
4. The built-in password/PIN tiles are never hidden by this provider. If at
   any point they are missing or broken, STOP: revert to the snapshot and
   report it - that would be a ship-blocking bug.

RECOVERY (if anything goes wrong)
----------------------------------
In order of preference:

A. Normal sign-in (expected path - always works):
   Sign in with your password or PIN tile (untouched by Visage), open an
   elevated PowerShell in the folder with unregister.ps1, run it, lock
   (Win+L) and confirm the tile is gone.

B. Safe Mode:
   1. Hold Shift while clicking Restart -> Troubleshoot -> Advanced options
      -> Startup Settings -> Restart -> press 4 (Safe Mode).
   2. Sign in as the spare admin. (Third-party credential providers do not
      load in Safe Mode, so you get the plain password tile.)
   3. Elevated PowerShell: cd to the DLL folder, run
        regsvr32 /u VisageCP.dll
      then delete the provider key if it remains:
        Remove-Item -Recurse -Force `
          "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{794AAC04-6CB6-4C77-A828-27C20D0F820D}"
      and the COM key:
        Remove-Item -Recurse -Force "HKLM:\SOFTWARE\Classes\CLSID\{794AAC04-6CB6-4C77-A828-27C20D0F820D}"
   4. Reboot normally. The tile is gone; built-ins are untouched.

C. WinRE offline registry (if Safe Mode is unavailable):
   1. Boot to Windows Recovery (Shift+Restart -> Troubleshoot ->
      Advanced options -> Command Prompt).
   2. reg load HKLM\TempSoft C:\Windows\System32\config\SOFTWARE
   3. reg delete "HKLM\TempSoft\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{794AAC04-6CB6-4C77-A828-27C20D0F820D}" /f
   4. reg delete "HKLM\TempSoft\Classes\CLSID\{794AAC04-6CB6-4C77-A828-27C20D0F820D}" /f
   5. reg unload HKLM\TempSoft
   6. Reboot.

TEST SETUP
----------
On the VM (as admin):
1. Build Release|x64 (see BUILD.md), copy VisageCP.dll + register.ps1 +
   unregister.ps1 to C:\ProgramData\Visage\cp\.
2. Install pywin32 in a Python (for the fake engine): pip install pywin32.
3. Deploy the FAKE engine as a package (the provider launches
   "python -m engine.visage_engine" with C:\ProgramData\Visage as cwd):
     mkdir C:\ProgramData\Visage\engine
     copy <cp>\test\fake_engine.py C:\ProgramData\Visage\engine\visage_engine.py
     type nul > C:\ProgramData\Visage\engine\__init__.py
   Then lock it down exactly like the real installer does (SYSTEM +
   Administrators only, no inheritance) - the test must prove the ACL
   story too, not bypass it:
     icacls C:\ProgramData\Visage\engine /inheritance:r `
       /grant:r "SYSTEM:(OI)(CI)F" /grant:r "Administrators:(OI)(CI)F"
   And pin the interpreter the provider will use:
     New-Item -Path "HKLM:\SOFTWARE\Visage" -Force
     Set-ItemProperty -Path "HKLM:\SOFTWARE\Visage" -Name "PythonPath" `
       -Value "<full path to the test python.exe>"
4. Create a TEST vault (test password only, never a real one):
     cd <cp>\test
     .\make_test_vault.ps1 -Username "<VMUsername>" -Password "<VMPassword>"
   Use the VM account's real password here so the KERB submission can
   actually succeed - it is a throwaway VM.
5. Run .\register.ps1 elevated. Confirm both registry keys exist.
6. Sanity: from C:\ProgramData\Visage, run
     python -m engine.visage_engine --help
   (with the REAL engine this must exit 0; with the fake it may exit
   non-zero - what matters is that the provider's spawn command resolves
   the module at all. If even the import fails, fix the deploy, not the test.)

TEST MATRIX
-----------
Key: [S] sign-in screen (fresh boot / sign out), [L] lock screen (Win+L).

#  Tile presence & safety
T1  [S][L] "Face Unlock" tile appears next to password/PIN tiles. PASS if
    all built-in tiles are present, clickable, and sign in normally.
T2  [S][L] Deselect/reselect tiles repeatedly; password tile always works.
T3  Run unregister.ps1; [S][L] tile is gone; built-ins unaffected.

#  Success path (fake_engine.py success)
T4  [S] Select tile (auto-scans) -> status shows "Looking for your face..."
    -> "Hold still..." -> "Recognized. Press Enter to sign in." ->
    press Enter -> signed in as the vault user.
T5  [L] Same as T4 on the lock screen (unlock path with session LUID).

#  Challenge prompt
T6  fake_engine.py challenge -> tile shows the engine's prompt text
    ("Blink twice, then look up") verbatim, then success.

#  Failure paths (each: tile stays up, password/PIN tiles usable)
T7  no_face   -> "No face detected. Try again."; button "Try Again".
T8  spoof     -> "Couldn't confirm a live face. Try again."
T9  Repeat T7 three times -> third failure shows
    "Face unlock unavailable - use your password or PIN." and the submit
    button disappears. (3-attempt lockout.)
T10 After T9, select the password tile and back: attempts reset, tile
    offers "Start" again. (Documented behavior.)
T11 timeout   -> engine never replies -> after ~30 s the tile shows
    "Face unlock unavailable - use your password or PIN." LogonUI never
    hangs or freezes during the wait.
T12 slow_cancel: start a scan, then select the password tile mid-scan ->
    scan cancels silently; no stuck "Scanning..." state; engine got
    {"cmd":"cancel"} (check fake engine output).
T13 While scanning, click the "Cancel" button -> scan stops, tile idle.

#  Engine missing
T14 Stop/remove the fake engine, delete the pipe, select the tile ->
    provider spawns nothing (no engine script installed) -> tile shows
    "Face unlock unavailable - use your password or PIN." within ~12 s.

#  Vault problems
T15 Rename vault.dat -> select tile -> scan does NOT start (fail fast);
    tile shows "Face unlock unavailable - use your password or PIN."
T16 Corrupt vault.dat (write random bytes) -> same as T15.
T17 Vault for a WRONG password, fake engine success -> Enter -> Windows
    rejects the logon -> tile shows "Your face matched, but the saved
    password didn't work. Use your password or PIN to sign in."

#  Robustness
T18 Malformed JSON flood: point the pipe at a sender emitting garbage
    lines -> after a few lines the tile shows "Face unlock unavailable -
    use your password or PIN." No crash, no hang.
T19 Rapid tile select/deselect x20 -> no crash, no leaked scans
    (watch for lingering python processes).
T20 Reboot with the provider registered -> tile present on [S]; no
    errors in the Application event log from LogonUI.

SIGN-OFF CRITERIA
------------------
- T1-T20 pass on a VM on both Windows 10 22H2 and Windows 11.
- Zero crashes/hangs of LogonUI across the matrix.
- unregister.ps1 leaves neither registry key behind.
- Recovery paths A-C each verified at least once on the VM.
