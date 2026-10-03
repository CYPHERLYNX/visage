// VisageCredential.cpp
#include "VisageCredential.h"
#include "JsonMini.h"
#include "KerbPack.h"
#include "Guids.h"

#include <ntstatus.h>
#include <map>
#include <string>

#include "resource.h"

// Provided by Dll.cpp
extern long g_cRef;
extern HINSTANCE g_hInst;

namespace visage
{

namespace
{
constexpr UINT WM_VISAGE_SCAN = WM_APP + 0x4D49;
constexpr LONG kMaxAttempts = 3;
constexpr DWORD kEngineTimeoutMs = 15000;  // matches pipe protocol timeout_ms
constexpr DWORD kOverallScanMs = 30000;    // hard cap per attempt
constexpr DWORD kStopWaitMs = 5000;
constexpr int kMaxMalformedLines = 5; // then the engine is misbehaving: give up

const wchar_t* kIdleText =
    L"Look at the camera to sign in.";
// The single fallback for every terminal problem (engine down, pipe broken,
// corrupt vault, too many tries): calm, plain, and it always names the way
// out. Mandated copy: "Face unlock unavailable - use your password or PIN"
// (\u2014 renders the em dash on the tile; docs quote it with a hyphen).
const wchar_t* kUnavailableText =
    L"Face unlock unavailable \u2014 use your password or PIN.";

const wchar_t* ButtonLabelFor(ScanState s)
{
    switch (s)
    {
    case ScanState::Scanning:
    case ScanState::FaceFound:
    case ScanState::Challenge: return L"Cancel";
    case ScanState::Success:   return L"Sign In";
    case ScanState::Failed:    return L"Try Again";
    case ScanState::Idle:      return L"Start";
    default:                   return L"Start"; // hidden when terminal
    }
}

// The submit button is hidden entirely once the tile is terminal: there is
// nothing left to submit, and the copy already points at password/PIN.
bool SubmitVisible(ScanState s)
{
    return s != ScanState::LockedOut && s != ScanState::EngineError &&
           s != ScanState::PasswordRejected;
}

bool IsTerminal(ScanState s)
{
    return s == ScanState::LockedOut || s == ScanState::EngineError;
}

bool IsScanning(ScanState s)
{
    return s == ScanState::Scanning || s == ScanState::FaceFound ||
           s == ScanState::Challenge;
}
} // namespace

CVisageCredential::CVisageCredential(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
                                 DWORD ulAuthPackage,
                                 bool haveLogonId, LUID logonId)
    : m_cRef(1)
    , m_cpus(cpus)
    , m_ulAuthPackage(ulAuthPackage)
    , m_haveLogonId(haveLogonId)
    , m_pEvents(nullptr)
    , m_hwndMessage(nullptr)
    , m_hThread(nullptr)
    , m_hCancelEvent(nullptr)
    , m_state(ScanState::Idle)
    , m_attempts(0)
    , m_authSucceeded(false)
    , m_engineAvailable(true)
    , m_hTileBitmap(nullptr)
{
    InterlockedIncrement(&g_cRef);
    InitializeCriticalSection(&m_cs);
    m_statusText = kIdleText;
    m_hCancelEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (haveLogonId)
    {
        m_logonId = logonId;
    }
    else
    {
        m_logonId.LowPart = 0;
        m_logonId.HighPart = 0;
    }
}

CVisageCredential::~CVisageCredential()
{
    // The worker AddRefs for its lifetime, so reaching the destructor means
    // no worker is running. Anything left is defensive cleanup.
    if (m_hThread != nullptr)
    {
        CloseHandle(m_hThread);
        m_hThread = nullptr;
    }
    if (m_hwndMessage != nullptr)
    {
        // Best effort: normally destroyed on the STA thread in UnAdvise.
        // DestroyWindow from any other thread fails gracefully; leave the
        // handle so the STA thread can still destroy it afterwards.
        if (DestroyWindow(m_hwndMessage))
        {
            m_hwndMessage = nullptr;
        }
    }
    if (m_hCancelEvent != nullptr)
    {
        CloseHandle(m_hCancelEvent);
        m_hCancelEvent = nullptr;
    }
    if (m_pEvents != nullptr)
    {
        m_pEvents->Release();
        m_pEvents = nullptr;
    }
    if (m_hTileBitmap != nullptr)
    {
        DeleteObject(m_hTileBitmap);
        m_hTileBitmap = nullptr;
    }
    DeleteCriticalSection(&m_cs);
    InterlockedDecrement(&g_cRef);
}

// ---------- IUnknown ----------

STDMETHODIMP CVisageCredential::QueryInterface(REFIID riid, void** ppv)
{
    if (ppv == nullptr)
    {
        return E_POINTER;
    }
    *ppv = nullptr;
    if (riid == IID_IUnknown || riid == IID_ICredentialProviderCredential)
    {
        *ppv = static_cast<ICredentialProviderCredential*>(this);
    }
    else
    {
        return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) CVisageCredential::AddRef()
{
    return static_cast<ULONG>(InterlockedIncrement(&m_cRef));
}

STDMETHODIMP_(ULONG) CVisageCredential::Release()
{
    LONG c = InterlockedDecrement(&m_cRef);
    if (c == 0)
    {
        delete this;
    }
    return static_cast<ULONG>(c);
}

// ---------- message window (STA thread) ----------

LRESULT CALLBACK CVisageCredential::MessageWndProc(HWND hwnd, UINT msg,
                                                 WPARAM wp, LPARAM lp)
{
    if (msg == WM_VISAGE_SCAN)
    {
        auto* self = reinterpret_cast<CVisageCredential*>(
            GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (self != nullptr)
        {
            // wp == 0: refresh the tile from the credential's current state.
            self->RefreshTile();
        }
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

HRESULT CVisageCredential::EnsureMessageWindow()
{
    if (m_hwndMessage != nullptr)
    {
        return S_OK;
    }
    static const wchar_t* kClass = L"VisageCPMsgWindow";
    WNDCLASSEXW wc{};
    if (!GetClassInfoExW(g_hInst, kClass, &wc))
    {
        wc.cbSize = sizeof(wc);
        wc.lpfnWndProc = MessageWndProc;
        wc.hInstance = g_hInst;
        wc.lpszClassName = kClass;
        if (!RegisterClassExW(&wc))
        {
            return HRESULT_FROM_WIN32(GetLastError());
        }
    }
    m_hwndMessage = CreateWindowExW(0, kClass, L"", 0, 0, 0, 0, 0,
                                    HWND_MESSAGE, nullptr, g_hInst, nullptr);
    if (m_hwndMessage == nullptr)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    SetWindowLongPtrW(m_hwndMessage, GWLP_USERDATA,
                      reinterpret_cast<LONG_PTR>(this));
    return S_OK;
}

void CVisageCredential::DestroyMessageWindow()
{
    if (m_hwndMessage != nullptr)
    {
        DestroyWindow(m_hwndMessage);
        m_hwndMessage = nullptr;
    }
}

// ---------- Advise / UnAdvise ----------

STDMETHODIMP CVisageCredential::Advise(
    ICredentialProviderCredentialEvents* pcpce)
{
    if (pcpce == nullptr)
    {
        return E_POINTER;
    }
    if (m_pEvents != nullptr)
    {
        m_pEvents->Release();
    }
    m_pEvents = pcpce;
    m_pEvents->AddRef();
    // A missing message window only degrades live tile updates; the tile
    // still works (scan results surface on the next user action).
    HRESULT hr = EnsureMessageWindow();
    if (FAILED(hr))
    {
        VISAGE_TRACE(L"Advise: no message window (hr=0x%08x)", hr);
    }
    return S_OK;
}

STDMETHODIMP CVisageCredential::UnAdvise()
{
    RequestStop(true);
    DestroyMessageWindow();
    if (m_pEvents != nullptr)
    {
        m_pEvents->Release();
        m_pEvents = nullptr;
    }
    return S_OK;
}

// ---------- selection ----------

STDMETHODIMP CVisageCredential::SetSelected(BOOL* pbAutoLogon)
{
    if (pbAutoLogon == nullptr)
    {
        return E_POINTER;
    }
    // We never auto-logon: the face scan must run (and succeed) first.
    *pbAutoLogon = FALSE;

    EnterCriticalSection(&m_cs);
    bool succeeded = m_authSucceeded;
    ScanState state = m_state;
    LeaveCriticalSection(&m_cs);

    if (succeeded || IsScanning(state) || IsTerminal(state))
    {
        return S_OK;
    }
    StartScan();
    return S_OK;
}

STDMETHODIMP CVisageCredential::SetDeselected()
{
    RequestStop(true);
    // A deselect starts a fresh session: attempts reset, tile goes idle.
    // (The engine still enforces its own anti-spoofing per attempt.)
    EnterCriticalSection(&m_cs);
    m_attempts = 0;
    m_authSucceeded = false;
    m_engineAvailable = true;
    m_state = ScanState::Idle;
    m_statusText = kIdleText;
    LeaveCriticalSection(&m_cs);
    RefreshTile();
    return S_OK;
}

// ---------- worker thread ----------

void CVisageCredential::PostUpdate(ScanState state, const wchar_t* text)
{
    // Always apply the state under the lock so the tile's field getters see
    // it even if the message window is gone; the window message then only
    // triggers the LogonUI push (RefreshTile) on the STA thread.
    HWND hwnd = nullptr;
    EnterCriticalSection(&m_cs);
    m_state = state;
    if (text != nullptr)
    {
        m_statusText = text;
    }
    hwnd = m_hwndMessage;
    LeaveCriticalSection(&m_cs);

    if (hwnd != nullptr)
    {
        // wp == 0 means "refresh from current member state".
        PostMessageW(hwnd, WM_VISAGE_SCAN, 0, 0);
    }
}

void CVisageCredential::StartScan()
{
    EnterCriticalSection(&m_cs);
    if (m_hThread != nullptr)
    {
        LeaveCriticalSection(&m_cs);
        return; // already running
    }
    if (m_hCancelEvent != nullptr)
    {
        ResetEvent(m_hCancelEvent);
    }
    m_authSucceeded = false;
    m_state = ScanState::Scanning;
    m_statusText = L"Looking for your face\u2026";
    LeaveCriticalSection(&m_cs);

    // AddRef BEFORE CreateThread: the worker Releases at the end, and it
    // must never be the one to drop the count to zero while we still use
    // `this` below.
    AddRef();
    HANDLE hThread = CreateThread(nullptr, 0, ScanThreadProc, this, 0, nullptr);
    EnterCriticalSection(&m_cs);
    if (hThread != nullptr)
    {
        m_hThread = hThread;
    }
    else
    {
        m_state = ScanState::EngineError;
        m_statusText = kUnavailableText;
    }
    LeaveCriticalSection(&m_cs);
    if (hThread == nullptr)
    {
        Release();
    }
    RefreshTile();
}

void CVisageCredential::RequestStop(bool wait)
{
    HANDLE hThread = nullptr;
    EnterCriticalSection(&m_cs);
    hThread = m_hThread;
    LeaveCriticalSection(&m_cs);

    if (hThread == nullptr)
    {
        return;
    }
    if (m_hCancelEvent != nullptr)
    {
        SetEvent(m_hCancelEvent);
    }
    if (wait)
    {
        // Bounded: the worker polls the cancel event inside every pipe
        // read, so this returns in milliseconds in practice. We never hang
        // LogonUI's STA thread indefinitely.
        WaitForSingleObject(hThread, kStopWaitMs);
    }
    EnterCriticalSection(&m_cs);
    if (m_hThread == hThread)
    {
        CloseHandle(m_hThread);
        m_hThread = nullptr;
    }
    LeaveCriticalSection(&m_cs);
}

DWORD WINAPI CVisageCredential::ScanThreadProc(LPVOID pv)
{
    auto* self = static_cast<CVisageCredential*>(pv);
    try
    {
        self->RunScan();
    }
    catch (...)
    {
        // An uncaught exception in this worker would otherwise invoke
        // std::terminate and take down LogonUI with it. Degrade to the
        // unavailable tile instead: the user can still sign in another way.
        // (PostUpdate itself only throws on allocation failure; in that
        // pathological case the tile simply keeps its last state until the
        // user deselects it, which resets to idle.)
        self->PostUpdate(ScanState::EngineError, kUnavailableText);
    }
    // NOTE: no m_hThread manipulation here; the STA thread owns the handle.
    self->Release();
    return 0;
}

// ---------- scan worker body ----------

// Records one failed attempt. attemptText is the engine's human-readable
// message (or our own timeout/pipe message). After kMaxAttempts the tile
// locks out and points the user at their password.
void CVisageCredential::FailAttempt(const wchar_t* attemptText)
{
    LONG attempts = 0;
    EnterCriticalSection(&m_cs);
    if (m_attempts < 1000) // sanity cap; overflow must never reset the lockout
    {
        ++m_attempts;
    }
    attempts = m_attempts;
    LeaveCriticalSection(&m_cs);

    if (attempts >= kMaxAttempts)
    {
        PostUpdate(ScanState::LockedOut, kUnavailableText);
    }
    else
    {
        // Calm, plain, no jargon: the engine's message plus a quiet retry
        // invitation. No attempt counts on the tile.
        wchar_t buf[256];
        const wchar_t* msg = (attemptText != nullptr && attemptText[0] != 0)
            ? attemptText : L"Face not recognized.";
        StringCchPrintfW(buf, ARRAYSIZE(buf), L"%s Try again.", msg);
        PostUpdate(ScanState::Failed, buf);
    }
}

// Maps the engine's machine-readable failure reason to calm tile copy.
// Unknown reasons are never echoed: the whitelist is the copy.
const wchar_t* ReasonToCopy(const std::wstring& reason)
{
    if (reason == L"no_match")  return L"Face not recognized.";
    if (reason == L"spoof")     return L"Couldn't confirm a live face.";
    if (reason == L"timeout")   return L"That took too long.";
    if (reason == L"no_face")   return L"No face detected.";
    if (reason == L"no_camera") return L"Couldn't access the camera.";
    return L"Something went wrong.";
}

HRESULT CVisageCredential::RunScan()
{
    PostUpdate(ScanState::Scanning, L"Looking for your face\u2026");

    // Fail fast, Glance-style ("no password is stored yet"): if there is no
    // readable vault there is nothing a successful scan could submit, so do
    // not run the camera at all. The probe plaintext is zeroed on scope exit.
    {
        VaultCreds probe;
        if (FAILED(ReadVaultCreds(probe)))
        {
            VISAGE_TRACE(L"RunScan: no readable vault, not scanning");
            PostUpdate(ScanState::EngineError, kUnavailableText);
            return E_FAIL;
        }
    }

    PipeClient pipe;
    HRESULT hr = pipe.Connect(12000, m_hCancelEvent);
    if (FAILED(hr))
    {
        VISAGE_TRACE(L"RunScan: engine connect failed (hr=0x%08x)", hr);
        EnterCriticalSection(&m_cs);
        m_engineAvailable = false;
        LeaveCriticalSection(&m_cs);
        PostUpdate(ScanState::EngineError, kUnavailableText);
        return hr;
    }

    char authCmd[128];
    StringCchPrintfA(authCmd, ARRAYSIZE(authCmd),
                     "{\"cmd\":\"authenticate\",\"timeout_ms\":%u}",
                     kEngineTimeoutMs);
    hr = pipe.SendLine(authCmd);
    if (FAILED(hr))
    {
        VISAGE_TRACE(L"RunScan: send failed (hr=0x%08x)", hr);
        PostUpdate(ScanState::EngineError, kUnavailableText);
        return hr;
    }

    const ULONGLONG deadline = GetTickCount64() + kOverallScanMs;
    std::wstring line;
    std::map<std::wstring, std::wstring> obj;
    std::wstring status;
    int malformed = 0;

    for (;;)
    {
        ULONGLONG now = GetTickCount64();
        if (now >= deadline)
        {
            // The engine went quiet: that is an engine problem, not a failed
            // recognition. Do not burn an attempt; degrade gracefully.
            VISAGE_TRACE(L"RunScan: overall deadline hit");
            PostUpdate(ScanState::EngineError, kUnavailableText);
            return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        }
        DWORD remaining = static_cast<DWORD>(deadline - now);
        DWORD slice = (remaining > 2000) ? 2000 : remaining;

        hr = pipe.ReadLine(line, slice, m_hCancelEvent);
        if (hr == HRESULT_FROM_WIN32(ERROR_CANCELLED))
        {
            // User walked away (tile deselected / Cancel pressed). Silent.
            return S_FALSE;
        }
        if (FAILED(hr))
        {
            // Broken pipe mid-scan: engine problem, not a failed match.
            VISAGE_TRACE(L"RunScan: read failed (hr=0x%08x)", hr);
            PostUpdate(ScanState::EngineError, kUnavailableText);
            return hr;
        }
        if (line.empty())
        {
            continue; // blank line: keep waiting
        }
        bool wellFormed = JsonMini::ParseFlatObject(line, obj) &&
                          JsonMini::GetString(obj, L"status", status);
        if (!wellFormed)
        {
            // Strict parsing: garbage never crashes us, but a stream of it
            // means the engine is misbehaving -> unavailable, not a retry loop.
            if (++malformed > kMaxMalformedLines)
            {
                VISAGE_TRACE(L"RunScan: too many malformed lines");
                PostUpdate(ScanState::EngineError, kUnavailableText);
                return E_FAIL;
            }
            continue;
        }

        if (status == L"scanning")
        {
            PostUpdate(ScanState::Scanning, L"Looking for your face\u2026");
        }
        else if (status == L"face_found")
        {
            PostUpdate(ScanState::FaceFound, L"Hold still\u2026");
        }
        else if (status == L"challenge")
        {
            std::wstring prompt;
            if (!JsonMini::GetString(obj, L"prompt", prompt) || prompt.empty())
            {
                prompt = L"Follow the on-screen instruction.";
            }
            if (prompt.size() > 200)
            {
                prompt.resize(200);
            }
            PostUpdate(ScanState::Challenge, prompt.c_str());
        }
        else if (status == L"success")
        {
            EnterCriticalSection(&m_cs);
            m_authSucceeded = true;
            LeaveCriticalSection(&m_cs);
            PostUpdate(ScanState::Success,
                       L"Recognized. Press Enter to sign in.");
            return S_OK;
        }
        else if (status == L"failure")
        {
            // The human-readable message is shown verbatim (truncated); only
            // whitelisted reasons may be echoed when the message is absent.
            std::wstring message;
            if (!JsonMini::GetString(obj, L"message", message) || message.empty())
            {
                std::wstring reason;
                JsonMini::GetString(obj, L"reason", reason);
                message = ReasonToCopy(reason);
            }
            if (message.size() > 200)
            {
                message.resize(200);
            }
            FailAttempt(message.c_str());
            return S_OK;
        }
        else if (status == L"cancelled")
        {
            PostUpdate(ScanState::Idle, kIdleText);
            return S_FALSE;
        }
        // Unknown status: ignore and keep waiting for the deadline.
    }
}

// ---------- STA-side tile refresh ----------

void CVisageCredential::RefreshTile()
{
    if (m_pEvents == nullptr)
    {
        return;
    }
    std::wstring text;
    ScanState state;
    EnterCriticalSection(&m_cs);
    text = m_statusText;
    state = m_state;
    LeaveCriticalSection(&m_cs);

    // hwndParent: LogonUI tolerates NULL for field-value pushes; we have no
    // HWND from OnCreatingWindow on this path. (Verification item: confirm
    // on Windows that NULL is accepted here.)
    m_pEvents->SetFieldString(nullptr, SFI_SUBLABEL, text.c_str());
    m_pEvents->SetFieldString(nullptr, SFI_SUBMIT_BUTTON, ButtonLabelFor(state));
    m_pEvents->SetFieldState(nullptr, SFI_SUBMIT_BUTTON,
                             SubmitVisible(state)
                                 ? CPFS_DISPLAY_IN_SELECTED_TILE
                                 : CPFS_HIDDEN);

    // Every visible state keeps the button focused so Enter just works:
    // Start / Try Again / Cancel / Sign In. Hidden when terminal.
    CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE iState =
        SubmitVisible(state) ? CPFIS_FOCUSED : CPFIS_NONE;
    m_pEvents->SetFieldInteractiveState(nullptr, SFI_SUBMIT_BUTTON, iState);
}

// ---------- field getters ----------

STDMETHODIMP CVisageCredential::GetFieldState(
    DWORD dwFieldID,
    CREDENTIAL_PROVIDER_FIELD_STATE* pcpfs,
    CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* pcpfis)
{
    if (pcpfs == nullptr || pcpfis == nullptr)
    {
        return E_POINTER;
    }
    if (dwFieldID >= SFI_NUM_FIELDS)
    {
        return E_INVALIDARG;
    }

    *pcpfs = CPFS_HIDDEN;
    *pcpfis = CPFIS_NONE;

    EnterCriticalSection(&m_cs);
    ScanState state = m_state;
    LeaveCriticalSection(&m_cs);

    switch (dwFieldID)
    {
    case SFI_TILEIMAGE:
    case SFI_LABEL:
        *pcpfs = CPFS_DISPLAY_IN_BOTH;
        break;
    case SFI_SUBLABEL:
        // Visible only while the tile is selected; the deselected tile is
        // just the face image + "Face Unlock" label.
        *pcpfs = CPFS_DISPLAY_IN_SELECTED_TILE;
        break;
    case SFI_SUBMIT_BUTTON:
        // Visible while the tile is selected, except once terminal: then
        // there is nothing to submit and the copy points at password/PIN.
        *pcpfs = SubmitVisible(state) ? CPFS_DISPLAY_IN_SELECTED_TILE
                                      : CPFS_HIDDEN;
        break;
    default:
        return E_INVALIDARG;
    }

    if (dwFieldID == SFI_SUBMIT_BUTTON && SubmitVisible(state))
    {
        *pcpfis = CPFIS_FOCUSED; // Enter = Start / Try Again / Cancel / Sign In
    }
    return S_OK;
}

STDMETHODIMP CVisageCredential::GetStringValue(DWORD dwFieldID, PWSTR* ppwsz)
{
    if (ppwsz == nullptr)
    {
        return E_POINTER;
    }
    *ppwsz = nullptr;
    if (dwFieldID >= SFI_NUM_FIELDS)
    {
        return E_INVALIDARG;
    }

    switch (dwFieldID)
    {
    case SFI_LABEL:
        return DupString(L"Face Unlock", ppwsz);
    case SFI_SUBLABEL:
    {
        std::wstring text;
        EnterCriticalSection(&m_cs);
        text = m_statusText;
        LeaveCriticalSection(&m_cs);
        return DupString(text.c_str(), ppwsz);
    }
    case SFI_SUBMIT_BUTTON:
    {
        EnterCriticalSection(&m_cs);
        ScanState state = m_state;
        LeaveCriticalSection(&m_cs);
        return DupString(ButtonLabelFor(state), ppwsz);
    }
    default:
        return E_FAIL; // tile image has no string value
    }
}

STDMETHODIMP CVisageCredential::GetBitmapValue(DWORD dwFieldID, HBITMAP* phbmp)
{
    if (phbmp == nullptr)
    {
        return E_POINTER;
    }
    *phbmp = nullptr;
    if (dwFieldID != SFI_TILEIMAGE)
    {
        return E_INVALIDARG;
    }
    if (m_hTileBitmap == nullptr)
    {
        // Best effort: a missing bitmap must never break the tile.
        m_hTileBitmap = static_cast<HBITMAP>(LoadImageW(
            g_hInst, MAKEINTRESOURCEW(IDB_TILEIMAGE), IMAGE_BITMAP,
            0, 0, LR_DEFAULTCOLOR));
        if (m_hTileBitmap == nullptr)
        {
            VISAGE_TRACE(L"GetBitmapValue: tile bitmap missing (%u)", GetLastError());
            return E_FAIL;
        }
    }
    *phbmp = m_hTileBitmap; // LogonUI copies; we keep ownership.
    return S_OK;
}

STDMETHODIMP CVisageCredential::GetCheckboxValue(DWORD, BOOL*, PWSTR*)
{
    return E_NOTIMPL; // no checkbox fields
}

STDMETHODIMP CVisageCredential::GetSubmitButtonValue(DWORD dwFieldID,
                                                   DWORD* pdwAdjacentTo)
{
    if (pdwAdjacentTo == nullptr)
    {
        return E_POINTER;
    }
    if (dwFieldID != SFI_SUBMIT_BUTTON)
    {
        return E_INVALIDARG;
    }
    *pdwAdjacentTo = SFI_SUBLABEL;
    return S_OK;
}

STDMETHODIMP CVisageCredential::GetComboBoxValueCount(DWORD, DWORD*, DWORD*)
{
    return E_NOTIMPL; // no combo boxes
}

STDMETHODIMP CVisageCredential::GetComboBoxValueAt(DWORD, DWORD, PWSTR*)
{
    return E_NOTIMPL; // no combo boxes
}

STDMETHODIMP CVisageCredential::SetStringValue(HWND, DWORD, PCWSTR)
{
    return E_NOTIMPL; // no editable fields
}

STDMETHODIMP CVisageCredential::SetCheckboxValue(HWND, DWORD, BOOL, PCWSTR)
{
    return E_NOTIMPL; // no checkbox fields
}

STDMETHODIMP CVisageCredential::SetComboBoxSelectedValue(HWND, DWORD, DWORD)
{
    return E_NOTIMPL; // no combo boxes
}

STDMETHODIMP CVisageCredential::CommandLinkClicked(HWND, DWORD)
{
    return E_NOTIMPL; // no command links
}

// ---------- submission ----------

STDMETHODIMP CVisageCredential::GetSerialization(
    CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* pcpgsr,
    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs,
    PWSTR* ppwszOptionalStatusText,
    CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon)
{
    if (pcpgsr == nullptr || pcpcs == nullptr)
    {
        return E_POINTER;
    }
    *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
    if (ppwszOptionalStatusText != nullptr)
    {
        *ppwszOptionalStatusText = nullptr;
    }
    if (pcpsiOptionalStatusIcon != nullptr)
    {
        *pcpsiOptionalStatusIcon = CPSI_NONE;
    }

    bool succeeded = false;
    ScanState state;
    LONG attempts = 0;
    EnterCriticalSection(&m_cs);
    succeeded = m_authSucceeded;
    state = m_state;
    attempts = m_attempts;
    LeaveCriticalSection(&m_cs);

    if (!succeeded)
    {
        // Submit pressed without a verified face.
        if (IsTerminal(state))
        {
            // Locked out / engine down: keep the tile up, point at password.
            if (ppwszOptionalStatusText != nullptr)
            {
                DupString(kUnavailableText, ppwszOptionalStatusText);
            }
            return S_OK;
        }
        if (IsScanning(state))
        {
            // The button reads "Cancel" while scanning: honor it. Stop the
            // worker (bounded wait; it polls the cancel event) and go idle.
            RequestStop(true);
            EnterCriticalSection(&m_cs);
            m_state = ScanState::Idle;
            m_statusText = kIdleText;
            LeaveCriticalSection(&m_cs);
            RefreshTile();
            return S_OK;
        }
        // Idle or failed-with-attempts-left: Enter/click means "try (again)".
        if (attempts < kMaxAttempts)
        {
            StartScan();
        }
        return S_OK;
    }

    // ---- Verified: build the KERB serialization from the vault ----
    VaultCreds creds;
    HRESULT hr = ReadVaultCreds(creds);
    if (FAILED(hr))
    {
        VISAGE_TRACE(L"GetSerialization: vault read failed (hr=0x%08x)", hr);
        PostUpdate(ScanState::EngineError, kUnavailableText);
        EnterCriticalSection(&m_cs);
        m_authSucceeded = false;
        LeaveCriticalSection(&m_cs);
        if (ppwszOptionalStatusText != nullptr)
        {
            DupString(kUnavailableText, ppwszOptionalStatusText);
        }
        return S_OK;
    }

    std::wstring domain, user;
    if (!SplitUserName(creds.username.c_str(), creds.domain.c_str(), domain, user))
    {
        if (ppwszOptionalStatusText != nullptr)
        {
            DupString(kUnavailableText, ppwszOptionalStatusText);
        }
        return S_OK;
    }

    if (m_cpus == CPUS_UNLOCK_WORKSTATION && !m_haveLogonId)
    {
        // Without the locked session's LUID we cannot build an unlock
        // serialization LSA will accept. Refuse instead of submitting
        // something that would fail (or worse, misbehave).
        if (ppwszOptionalStatusText != nullptr)
        {
            DupString(kUnavailableText, ppwszOptionalStatusText);
        }
        return S_OK;
    }

    BYTE* rgb = nullptr;
    DWORD cb = 0;
    hr = BuildKerbUnlockSerialization(domain.c_str(), user.c_str(),
                                      creds.password, m_cpus,
                                      m_haveLogonId ? &m_logonId : nullptr,
                                      &rgb, &cb);
    // creds.password is zeroed inside BuildKerbUnlockSerialization in all paths.
    if (FAILED(hr) || rgb == nullptr || cb == 0)
    {
        VISAGE_TRACE(L"GetSerialization: KERB build failed (hr=0x%08x)", hr);
        if (rgb != nullptr)
        {
            SecureZeroMemory(rgb, cb);
            CoTaskMemFree(rgb);
        }
        if (ppwszOptionalStatusText != nullptr)
        {
            DupString(L"Could not prepare sign-in. Use your password or PIN.",
                      ppwszOptionalStatusText);
        }
        return S_OK;
    }

    pcpcs->ulAuthenticationPackage = m_ulAuthPackage;
    pcpcs->clsidCredentialProvider = CLSID_VisageProvider;
    pcpcs->rgbSerialization = rgb;   // LogonUI takes ownership (CoTaskMemFree)
    pcpcs->cbSerialization = cb;
    *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;
    return S_OK;
}

STDMETHODIMP CVisageCredential::ReportResult(
    NTSTATUS ntsStatus, NTSTATUS ntsSubstatus,
    PWSTR* ppwszOptionalStatusText,
    CREDENTIAL_PROVIDER_STATUS_ICON* pcpsiOptionalStatusIcon)
{
    if (ppwszOptionalStatusText != nullptr)
    {
        *ppwszOptionalStatusText = nullptr;
    }
    if (pcpsiOptionalStatusIcon != nullptr)
    {
        *pcpsiOptionalStatusIcon = CPSI_NONE;
    }

    if (ntsStatus == STATUS_SUCCESS)
    {
        // One-shot: never let a stale "verified" flag submit twice.
        EnterCriticalSection(&m_cs);
        m_authSucceeded = false;
        LeaveCriticalSection(&m_cs);
        return S_OK;
    }

    // Windows rejected the KERB blob. The usual cause is a stale vault
    // (password changed after enrollment). Do NOT burn face attempts for
    // this; tell the user plainly and require a fresh scan afterwards.
    VISAGE_TRACE(L"ReportResult: logon rejected (0x%08x/0x%08x)",
               static_cast<unsigned>(ntsStatus),
               static_cast<unsigned>(ntsSubstatus));
    EnterCriticalSection(&m_cs);
    m_authSucceeded = false;
    LeaveCriticalSection(&m_cs);
    PostUpdate(ScanState::PasswordRejected,
               L"Your face matched, but the saved password didn't work. "
               L"Use your password or PIN to sign in.");
    if (ppwszOptionalStatusText != nullptr)
    {
        DupString(L"Your face matched, but the saved password didn't work. "
                  L"Use your password or PIN to sign in.",
                  ppwszOptionalStatusText);
        if (pcpsiOptionalStatusIcon != nullptr)
        {
            *pcpsiOptionalStatusIcon = CPSI_ERROR;
        }
    }
    return S_OK;
}

} // namespace visage
