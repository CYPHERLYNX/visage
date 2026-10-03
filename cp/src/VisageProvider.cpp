// VisageProvider.cpp
#include "VisageProvider.h"
#include "VisageCredential.h"
#include "KerbPack.h"
#include "Common.h"

#include <ntsecapi.h>

extern long g_cRef;

namespace visage
{

namespace
{
// Field descriptors for the single Face Unlock tile. Labels are shown in
// the UI; the guidFieldType is unused (GUID_NULL, as in the V2 sample).
const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR s_rgFieldDescriptors[] =
{
    { SFI_TILEIMAGE,   CPFT_TILE_IMAGE,    L"Face image",  GUID_NULL },
    { SFI_LABEL,       CPFT_LARGE_TEXT,    L"Face Unlock", GUID_NULL },
    { SFI_SUBLABEL,    CPFT_SMALL_TEXT,    L"Status",      GUID_NULL },
    { SFI_SUBMIT_BUTTON, CPFT_SUBMIT_BUTTON, L"Sign in",   GUID_NULL },
};

// Looks up the Kerberos authentication package id, which GetSerialization
// must stamp on the KERB_INTERACTIVE_UNLOCK_LOGON blob.
HRESULT RetrieveKerberosAuthPackage(ULONG* pulAuthPackage)
{
    if (pulAuthPackage == nullptr)
    {
        return E_POINTER;
    }
    *pulAuthPackage = 0;

    HANDLE hLsa = nullptr;
    NTSTATUS nts = LsaConnectUntrusted(&hLsa);
    if (nts != STATUS_SUCCESS)
    {
        return HRESULT_FROM_NT(nts);
    }

    LSA_STRING name;
    name.Buffer = MICROSOFT_KERBEROS_NAME_A; // "Kerberos"
    name.Length = static_cast<USHORT>(strlen(name.Buffer));
    name.MaximumLength = static_cast<USHORT>(name.Length + 1);

    ULONG ulAuthPackage = 0;
    nts = LsaLookupAuthenticationPackage(hLsa, &name, &ulAuthPackage);
    LsaDeregisterLogonProcess(hLsa);
    if (nts != STATUS_SUCCESS)
    {
        return HRESULT_FROM_NT(nts);
    }
    *pulAuthPackage = ulAuthPackage;
    return S_OK;
}
} // namespace

CVisageProvider::CVisageProvider()
    : m_cRef(1)
    , m_cpus(CPUS_INVALID)
    , m_dwFlags(0)
    , m_ulAuthPackage(0)
    , m_usageSet(false)
    , m_haveLogonId(false)
    , m_pEvents(nullptr)
    , m_upAdviseContext(0)
{
    InterlockedIncrement(&g_cRef);
    m_logonId.LowPart = 0;
    m_logonId.HighPart = 0;
}

CVisageProvider::~CVisageProvider()
{
    if (m_pEvents != nullptr)
    {
        m_pEvents->Release();
        m_pEvents = nullptr;
    }
    InterlockedDecrement(&g_cRef);
}

STDMETHODIMP CVisageProvider::QueryInterface(REFIID riid, void** ppv)
{
    if (ppv == nullptr)
    {
        return E_POINTER;
    }
    *ppv = nullptr;
    if (riid == IID_IUnknown || riid == IID_ICredentialProvider)
    {
        *ppv = static_cast<ICredentialProvider*>(this);
    }
    else
    {
        return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) CVisageProvider::AddRef()
{
    return static_cast<ULONG>(InterlockedIncrement(&m_cRef));
}

STDMETHODIMP_(ULONG) CVisageProvider::Release()
{
    LONG c = InterlockedDecrement(&m_cRef);
    if (c == 0)
    {
        delete this;
    }
    return static_cast<ULONG>(c);
}

STDMETHODIMP CVisageProvider::SetUsageScenario(
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD dwFlags)
{
    // We exist only on the sign-in screen and the lock screen. Anywhere
    // else (CredUI prompts, password change, ...) we refuse to load, so we
    // can never interfere with those flows.
    if (cpus != CPUS_LOGON && cpus != CPUS_UNLOCK_WORKSTATION)
    {
        VISAGE_TRACE(L"SetUsageScenario: unsupported cpus=%d, not loading", cpus);
        return E_NOTIMPL;
    }

    HRESULT hr = RetrieveKerberosAuthPackage(&m_ulAuthPackage);
    if (FAILED(hr))
    {
        // A provider that cannot stamp its serialization is worse than no
        // provider: fail here so no tile is enumerated.
        VISAGE_TRACE(L"SetUsageScenario: auth package lookup failed (hr=0x%08x)", hr);
        m_usageSet = false;
        return hr;
    }

    m_cpus = cpus;
    m_dwFlags = dwFlags;
    m_usageSet = true;
    return S_OK;
}

STDMETHODIMP CVisageProvider::SetSerialization(
    const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs)
{
    // On CPUS_UNLOCK_WORKSTATION, LogonUI hands us the locked session's
    // serialization. We stash its LogonId so GetSerialization can build an
    // unlock blob LSA will accept. Anything malformed is ignored: the
    // credential then refuses to submit for unlock (fail-safe).
    m_haveLogonId = false;
    m_logonId.LowPart = 0;
    m_logonId.HighPart = 0;
    if (pcpcs != nullptr && m_cpus == CPUS_UNLOCK_WORKSTATION)
    {
        LUID luid{};
        if (TryGetStashedLogonId(pcpcs, &luid))
        {
            m_haveLogonId = true;
            m_logonId = luid;
        }
        else
        {
            VISAGE_TRACE(L"SetSerialization: no usable LogonId in incoming blob");
        }
    }
    return S_OK;
}

STDMETHODIMP CVisageProvider::Advise(ICredentialProviderEvents* pcpe,
                                   UINT_PTR upAdviseContext)
{
    if (pcpe == nullptr)
    {
        return E_POINTER;
    }
    if (m_pEvents != nullptr)
    {
        m_pEvents->Release();
    }
    m_pEvents = pcpe;
    m_pEvents->AddRef();
    m_upAdviseContext = upAdviseContext;
    return S_OK;
}

STDMETHODIMP CVisageProvider::UnAdvise()
{
    if (m_pEvents != nullptr)
    {
        m_pEvents->Release();
        m_pEvents = nullptr;
    }
    m_upAdviseContext = 0;
    return S_OK;
}

STDMETHODIMP CVisageProvider::GetFieldDescriptorCount(DWORD* pdwCount)
{
    if (pdwCount == nullptr)
    {
        return E_POINTER;
    }
    *pdwCount = SFI_NUM_FIELDS;
    return S_OK;
}

STDMETHODIMP CVisageProvider::GetFieldDescriptorAt(
    DWORD dwIndex,
    CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd)
{
    if (ppcpfd == nullptr)
    {
        return E_POINTER;
    }
    *ppcpfd = nullptr;
    if (dwIndex >= SFI_NUM_FIELDS)
    {
        return E_INVALIDARG;
    }

    auto* pcpfd = static_cast<CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR*>(
        CoTaskMemAlloc(sizeof(CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR)));
    if (pcpfd == nullptr)
    {
        return E_OUTOFMEMORY;
    }
    pcpfd->dwFieldID = s_rgFieldDescriptors[dwIndex].dwFieldID;
    pcpfd->cpft = s_rgFieldDescriptors[dwIndex].cpft;
    pcpfd->guidFieldType = s_rgFieldDescriptors[dwIndex].guidFieldType;
    HRESULT hr = DupString(s_rgFieldDescriptors[dwIndex].pszLabel,
                           &pcpfd->pszLabel);
    if (FAILED(hr))
    {
        CoTaskMemFree(pcpfd);
        return hr;
    }
    *ppcpfd = pcpfd; // LogonUI frees with CoTaskMemFree
    return S_OK;
}

STDMETHODIMP CVisageProvider::GetCredentialCount(DWORD* pdwCount,
                                               DWORD* pdwDefault,
                                               BOOL* pbAutoLogonWithDefault)
{
    if (pdwCount == nullptr || pdwDefault == nullptr ||
        pbAutoLogonWithDefault == nullptr)
    {
        return E_POINTER;
    }
    if (!m_usageSet)
    {
        return E_FAIL; // SetUsageScenario never succeeded: enumerate nothing
    }

    // Exactly one tile: ours. Every other provider enumerates its own tiles
    // independently; we cannot (and do not) affect them.
    *pdwCount = 1;
    *pdwDefault = 0;              // face tile is pre-selected (Glance-like)
    *pbAutoLogonWithDefault = FALSE; // ...but never auto-submits
    return S_OK;
}

STDMETHODIMP CVisageProvider::GetCredentialAt(
    DWORD dwIndex, ICredentialProviderCredential** ppcpc)
{
    if (ppcpc == nullptr)
    {
        return E_POINTER;
    }
    *ppcpc = nullptr;
    if (dwIndex != 0 || !m_usageSet)
    {
        return E_INVALIDARG;
    }

    auto* pCred = new (std::nothrow) CVisageCredential(
        m_cpus, m_ulAuthPackage, m_haveLogonId, m_logonId);
    if (pCred == nullptr)
    {
        return E_OUTOFMEMORY;
    }
    *ppcpc = pCred; // refcount starts at 1
    return S_OK;
}

} // namespace visage
