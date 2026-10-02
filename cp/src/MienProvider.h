// MienProvider.h
// CMienProvider: ICredentialProvider implementation for Mien face unlock.
//
// INVARIANT (A1): this provider ADDS exactly one tile - its own "Face Unlock"
// tile. It never enumerates, filters, hides, disables, or otherwise alters
// the built-in password/PIN providers (or any other provider's tiles) in ANY
// code path. GetCredentialCount always returns exactly 1; SetUsageScenario
// refuses every scenario except CPUS_LOGON and CPUS_UNLOCK_WORKSTATION; no
// method here can reach another provider's state. A bug in this file can at
// worst break our own tile - the user can always still sign in another way.
//
// SAFETY CONTRACT (the whole point of this file):
//   - It enumerates exactly ONE credential: its own "Face Unlock" tile.
//   - It never touches, filters, or hides any other provider's tiles.
//   - It only activates for CPUS_LOGON and CPUS_UNLOCK_WORKSTATION. Every
//     other usage scenario (CredUI, change-password, ...) gets E_NOTIMPL,
//     so the provider is never loaded there.
//   - If the Kerberos auth-package lookup fails, SetUsageScenario fails and
//     no tile is enumerated at all: a half-initialized provider is worse
//     than no provider.
#pragma once

#include <windows.h>
#include <credentialprovider.h>

namespace mien
{

class CMienProvider : public ICredentialProvider
{
public:
    CMienProvider();
    ~CMienProvider();

    // IUnknown
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;

    // ICredentialProvider
    STDMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
                                  DWORD dwFlags) override;
    STDMETHODIMP SetSerialization(
        const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs) override;
    STDMETHODIMP Advise(ICredentialProviderEvents* pcpe,
                        UINT_PTR upAdviseContext) override;
    STDMETHODIMP UnAdvise() override;
    STDMETHODIMP GetFieldDescriptorCount(DWORD* pdwCount) override;
    STDMETHODIMP GetFieldDescriptorAt(
        DWORD dwIndex,
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** ppcpfd) override;
    STDMETHODIMP GetCredentialCount(DWORD* pdwCount, DWORD* pdwDefault,
                                    BOOL* pbAutoLogonWithDefault) override;
    STDMETHODIMP GetCredentialAt(DWORD dwIndex,
                                 ICredentialProviderCredential** ppcpc) override;

private:
    CMienProvider(const CMienProvider&) = delete;
    CMienProvider& operator=(const CMienProvider&) = delete;

    LONG    m_cRef;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO m_cpus;
    DWORD   m_dwFlags;
    DWORD   m_ulAuthPackage;   // Kerberos package id from LSA
    bool    m_usageSet;        // SetUsageScenario succeeded

    bool    m_haveLogonId;     // stashed from SetSerialization (unlock only)
    LUID    m_logonId;

    ICredentialProviderEvents* m_pEvents;
    UINT_PTR m_upAdviseContext;
};

} // namespace mien
