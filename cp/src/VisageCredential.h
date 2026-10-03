// VisageCredential.h
// CVisageCredential: the single "Face Unlock" tile.
//
// Implements ICredentialProviderCredential following the SampleV2CredentialProvider
// pattern. The tile never blocks the UI thread: selecting the tile starts a
// worker thread that talks JSONL to the Visage engine over \\.\pipe\VisageAuth,
// and progress is marshaled back to the STA thread through a hidden
// message-only window (WM_VISAGE_SCAN).
//
// State machine (mirrors Glance's lock-screen flow):
//   Idle -> Scanning -> FaceFound -> Challenge -> Success -> (Enter) -> submitted
//      \-> Failed (retry, max 3 attempts) -> LockedOut ("use your password")
//   EngineError/VaultError are terminal for the attempt and always point the
//   user back at their password tile. The built-in providers are never
//   touched: this credential only fills in its own tile's fields.
#pragma once

#include <windows.h>
#include <credentialprovider.h>
#include "Common.h"
#include "PipeClient.h"
#include "Vault.h"

namespace visage
{

// Field indices for this credential's tile.
enum VISAGE_FIELD_ID
{
    SFI_TILEIMAGE = 0,
    SFI_LABEL = 1,
    SFI_SUBLABEL = 2,
    SFI_SUBMIT_BUTTON = 3,
    SFI_NUM_FIELDS = 4,
};

// Tile states (UI only; the engine owns the CV verdict).
enum class ScanState
{
    Idle,        // nothing running
    Scanning,    // engine searching / verifying
    FaceFound,   // face located, liveness/challenge in progress
    Challenge,   // showing the engine's challenge prompt
    Success,     // engine said success; waiting for Enter/submit
    Failed,      // attempt failed, retries remain
    LockedOut,   // 3 attempts used: "use your password instead"
    EngineError, // pipe/engine/vault unavailable: "use your password"
    PasswordRejected, // face OK, Windows rejected the saved password
};

class CVisageCredential : public ICredentialProviderCredential
{
public:
    CVisageCredential(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
                    DWORD ulAuthPackage,
                    bool haveLogonId, LUID logonId);
    ~CVisageCredential();

    // IUnknown
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;

    // ICredentialProviderCredential
    STDMETHODIMP Advise(ICredentialProviderCredentialEvents* pcpce) override;
    STDMETHODIMP UnAdvise() override;
    STDMETHODIMP SetSelected(BOOL* pbAutoLogon) override;
    STDMETHODIMP SetDeselected() override;
    STDMETHODIMP GetFieldState(DWORD dwFieldID,
                              CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
                              CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis) override;
    STDMETHODIMP GetStringValue(DWORD dwFieldID, PWSTR* ppwsz) override;
    STDMETHODIMP GetBitmapValue(DWORD dwFieldID, HBITMAP* phbmp) override;
    STDMETHODIMP GetCheckboxValue(DWORD dwFieldID, BOOL* pbChecked,
                                  PWSTR* ppwszLabel) override;
    STDMETHODIMP GetSubmitButtonValue(DWORD dwFieldID,
                                      DWORD* pdwAdjacentTo) override;
    STDMETHODIMP GetComboBoxValueCount(DWORD dwFieldID, DWORD* pcItems,
                                        DWORD* pdwSelectedItem) override;
    STDMETHODIMP GetComboBoxValueAt(DWORD dwFieldID, DWORD dwItem,
                                    PWSTR* ppwszItem) override;
    STDMETHODIMP SetStringValue(HWND hwndParent, DWORD dwFieldID,
                                PCWSTR pwz) override;
    STDMETHODIMP SetCheckboxValue(HWND hwndParent, DWORD dwFieldID,
                                   BOOL bChecked, PCWSTR pwzLabel) override;
    STDMETHODIMP SetComboBoxSelectedValue(HWND hwndParent, DWORD dwFieldID,
                                           DWORD dwSelectedItem) override;
    STDMETHODIMP CommandLinkClicked(HWND hwndParent, DWORD dwFieldID) override;
    STDMETHODIMP GetSerialization(
        CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
        CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs,
        PWSTR* ppwszOptionalStatusText,
        CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon) override;
    STDMETHODIMP ReportResult(NTSTATUS ntsStatus, NTSTATUS ntsSubstatus,
                              PWSTR* ppwszOptionalStatusText,
                              CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon) override;

    // Called on the STA thread by the hidden window proc.
    void RefreshTile();                // STA thread: push fields to LogonUI

private:
    // Non-copyable
    CVisageCredential(const CVisageCredential&) = delete;
    CVisageCredential& operator=(const CVisageCredential&) = delete;

    static DWORD WINAPI ScanThreadProc(LPVOID pv);
    HRESULT RunScan();                 // worker thread body
    void FailAttempt(const wchar_t* attemptText); // worker thread
    void StartScan();                  // STA thread: launch worker if idle
    void RequestStop(bool wait);       // STA thread: cancel worker
    void PostUpdate(ScanState state, const wchar_t* text); // worker -> STA
    HRESULT EnsureMessageWindow();     // STA thread
    void DestroyMessageWindow();       // STA thread

    static LRESULT CALLBACK MessageWndProc(HWND hwnd, UINT msg,
                                           WPARAM wp, LPARAM lp);

    LONG    m_cRef;
    CRITICAL_SECTION m_cs;             // guards state below

    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    DWORD   m_ulAuthPackage;
    bool    m_haveLogonId;
    LUID    m_logonId;

    ICredentialProviderCredentialEvents* m_pEvents; // STA thread only

    HWND    m_hwndMessage;             // hidden window, STA thread only
    HANDLE  m_hThread;                 // scan worker (null when idle)
    HANDLE  m_hCancelEvent;            // manual-reset; set to stop worker

    ScanState m_state;                 // guarded by m_cs
    std::wstring m_statusText;         // guarded by m_cs
    LONG    m_attempts;                // failed attempts, guarded by m_cs
    bool    m_authSucceeded;           // guarded by m_cs
    bool    m_engineAvailable;         // guarded by m_cs

    HBITMAP m_hTileBitmap;             // cached tile image (may be null)
};

} // namespace visage
