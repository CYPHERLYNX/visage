BUILD.md - Building VisageCP.dll
================================

ASCII-only document.

1. PREREQUISITES
----------------
- Windows 10 version 2004+ or Windows 11, 64-bit.
- Visual Studio 2022 (17.x), any edition. Workload required:
    "Desktop development with C++"
  Make sure these components are checked in the VS Installer:
    - MSVC v143 build tools (x64/x86)
    - Windows 11 SDK (10.0.22621.0 or later). The project sets
      <WindowsTargetPlatformVersion>10.0</WindowsTargetPlatformVersion>,
      which means "use the newest SDK installed". If you only have an older
      Windows 10 SDK, retarget: right-click the project -> Retarget solution.
    - C++ ATL is NOT needed. MFC is NOT needed.
- No NuGet packages, no third-party libraries. The DLL links only to
  system libraries: secur32.lib, crypt32.lib, shlwapi.lib, ole32.lib.

2. BUILD (Visual Studio IDE)
---------------------------
1. Open VisageCP.sln.
2. Set the configuration to Release and the platform to x64.
   (There is deliberately no Win32/ARM32 configuration: on 64-bit Windows
   a credential provider DLL must match the OS bitness. For ARM64 Windows,
   add an ARM64 platform and rebuild; the code is platform-neutral C++.)
3. Build -> Build Solution.
4. Expected result: 0 errors. /W4 + SDLCheck are on; treat every warning
   as a bug and fix it before shipping.

3. BUILD (command line)
-----------------------
From a "Developer Command Prompt for VS 2022" (or any prompt after
running VsDevCmd.bat):

    msbuild VisageCP.sln /p:Configuration=Release /p:Platform=x64

For a Debug build (enables VISAGE_TRACE OutputDebugString logging):

    msbuild VisageCP.sln /p:Configuration=Debug /p:Platform=x64

4. EXPECTED OUTPUTS
-------------------
- x64\Release\VisageCP.dll   (ship this)
- x64\Release\VisageCP.pdb   (keep for debugging; do not ship)

Sanity checks on the built DLL:
- `dumpbin /exports x64\Release\VisageCP.dll` must list exactly:
    DllGetClassObject, DllCanUnloadNow, DllRegisterServer, DllUnregisterServer
- `dumpbin /headers` -> "machine (x64)".
- The tile bitmap is embedded as a resource (IDB_TILEIMAGE).

5. REGISTER
-----------
Copy these three files to a folder on the test machine (e.g.
C:\ProgramData\Visage\cp\): VisageCP.dll, register.ps1, unregister.ps1.
Then, from an ELEVATED PowerShell in that folder:

    .\register.ps1

(If script execution is blocked: Set-ExecutionPolicy -Scope Process Bypass)

The script calls regsvr32, which runs DllRegisterServer. It writes exactly
two keys:

    HKCR\CLSID\{794AAC04-6CB6-4C77-A828-27C20D0F820D}\InprocServer32
        (default)      = full path of VisageCP.dll
        ThreadingModel = Apartment
    HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\
        Credential Providers\{794AAC04-6CB6-4C77-A828-27C20D0F820D}
        (default)      = "Visage Face Unlock"

The second key is what makes LogonUI load the provider. No other registry
state is touched.

6. VERIFY
---------
- Lock the PC (Win+L). A "Face Unlock" tile appears next to the
  password/PIN tiles. The built-in tiles must still be present and usable.
- See TEST_PLAN.md for the full matrix.

7. UNREGISTER
-------------
From an elevated PowerShell in the same folder:

    .\unregister.ps1

This calls `regsvr32 /u` and then deletes the two keys above directly
(belt and suspenders). Lock the PC again to confirm the tile is gone.

8. TROUBLESHOOTING BUILD ISSUES
--------------------------------
- "Windows SDK not found": install the Windows 11 SDK via the VS
  Installer, or retarget the project to your installed SDK version.
- LNK2019 on LsaConnectUntrusted / CryptUnprotectData / SHStrDupW:
  the link line must include secur32.lib, crypt32.lib, shlwapi.lib
  (already set in the vcxproj; check you did not override
  AdditionalDependencies).
- C1083 on ntsecapi.h / credentialprovider.h: the Windows SDK include
  path is missing; reinstall the SDK component.
