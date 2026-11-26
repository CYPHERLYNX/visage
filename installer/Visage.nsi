; Visage.nsi — professional wizard installer for Visage (face unlock for Windows).
;
; Builds Visage-Setup.exe: a real software installer with UAC elevation,
; license page, progress, and an Add/Remove Programs entry.
;
; Build (Linux):  makensis installer/Visage.nsi
; Output:         installer/Visage-Setup-v0.1.exe
;
; What it does on the user's PC:
;   1. Requests Administrator (UAC) — required for the sign-in tile.
;   2. Copies the Visage tree to $PROGRAMFILES64\Visage.
;   3. Runs installer\setup.ps1, which does the 9 install steps
;      (Python check, folders, pip packages, face models, config,
;      builds + registers the tile, self-test, Start menu shortcut).
;   4. Registers an uninstaller (Add/Remove Programs + Uninstall.exe),
;      which runs installer\uninstall.ps1 -Force and removes the folder.

!include "MUI2.nsh"
!include "LogicLib.nsh"

; ---------------------------------------------------------------- settings

Name "Visage"
OutFile "Visage-Setup-v0.1.exe"

; 64-bit only (the credential provider DLL is x64).
InstallDir "$PROGRAMFILES64\Visage"
RequestExecutionLevel admin

; Version info shown in Explorer properties.
VIProductVersion "0.1.0.0"
VIAddVersionKey "ProductName" "Visage"
VIAddVersionKey "FileDescription" "Visage Setup - face unlock for Windows"
VIAddVersionKey "CompanyName" "X0D4N"
VIAddVersionKey "FileVersion" "0.1.0"
VIAddVersionKey "LegalCopyright" "(c) 2026 CYPHERLYNX"

; ---------------------------------------------------------------- UI pages

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to Visage Setup"
!define MUI_WELCOMEPAGE_TEXT "Visage brings face unlock to your Windows PC.$\r$\n$\r$\nThis wizard will install Visage, download the face models, and register the Visage sign-in tile. It needs Administrator rights and an internet connection.$\r$\n$\r$\nClick Next to continue."
!define MUI_FINISHPAGE_TITLE "Visage is installed"
!define MUI_FINISHPAGE_TEXT "Setup finished. Open Visage Manager from the Start menu to enroll your face, then press Win+L to try face unlock.$\r$\n$\r$\nIf a step reported PENDING or FAILED, re-run this installer after fixing what it named — it picks up where it left off."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------- install

Section "Visage" SEC_MAIN
  SectionIn RO

  SetOutPath "$INSTDIR"

  ; Copy the tree (skip dev/build artifacts and the models — setup.ps1
  ; downloads + hash-verifies those at install time).
  File /r /x ".git" /x "__pycache__" /x "*.pyc" /x "*.pyo" /x ".pytest_cache" \
       /x "models" /x "*.zip" /x "Visage-Setup-*.exe" "..\*.*"

  ; Run the PowerShell installer, which performs the 9 install steps and
  ; prints human-readable progress. Its window stays visible so the user
  ; sees what happened; its exit code tells us the outcome.
  DetailPrint "Running Visage setup (this takes several minutes)..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\installer\setup.ps1"' $0

  ${If} $0 == 0
    DetailPrint "Visage setup completed successfully."
  ${ElseIf} $0 == 2
    DetailPrint "Visage setup finished with PENDING steps — see its window for what to do next."
  ${Else}
    DetailPrint "Visage setup reported a problem (exit code $0) — see its window."
  ${EndIf}

  ; Uninstaller + Add/Remove Programs entry.
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "DisplayName" "Visage"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "DisplayIcon" "$INSTDIR\assets\icon.ico"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "DisplayVersion" "0.1.0"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "Publisher" "X0D4N"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage" \
    "NoRepair" 1
SectionEnd

; ---------------------------------------------------------------- uninstall

Section "Uninstall"
  ; Run the PowerShell uninstaller first (unregisters the tile, stops
  ; processes, removes data). -Force skips its interactive confirmations
  ; because the wizard already asked.
  DetailPrint "Running Visage uninstall..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\installer\uninstall.ps1" -Force' $0

  ; Remove what the installer put down.
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR"

  ; Remove the Start menu shortcut (uninstall.ps1 also tries; belt and braces).
  Delete "$SMPROGRAMS\Visage Manager.lnk"

  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Visage"
SectionEnd
