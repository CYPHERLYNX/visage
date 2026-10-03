// Dll.cpp
// DLL entry points, class factory, and self-registration for VisageCP.dll.
//
// Registration writes exactly two keys:
//   HKCR\CLSID\{794AAC04-6CB6-4C77-A828-27C20D0F820D}\InprocServer32
//       (default) = full path of this DLL, ThreadingModel = Apartment
//   HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{...}
//       (default) = "Visage Face Unlock"
// Unregistration removes both. Nothing else in the registry is touched,
// so unregistering can never disturb the built-in providers.
#include <windows.h>
#include <initguid.h> // before Guids.h: exactly one definition of CLSID_VisageProvider
#include "Guids.h"
#include "VisageProvider.h"
#include "Common.h"

long g_cRef = 0;          // outstanding objects + factory locks
HINSTANCE g_hInst = nullptr;

namespace visage
{

class CVisageProviderFactory : public IClassFactory
{
public:
    CVisageProviderFactory() : m_cRef(1)
    {
        InterlockedIncrement(&g_cRef);
    }

    // IUnknown
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override
    {
VISAGE_COM_BEGIN
        if (ppv == nullptr)
        {
            return E_POINTER;
        }
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_IClassFactory)
        {
            *ppv = static_cast<IClassFactory*>(this);
        }
        else
        {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
        VISAGE_COM_END
}
    STDMETHODIMP_(ULONG) AddRef() override
    {
        return static_cast<ULONG>(InterlockedIncrement(&m_cRef));
    }
    STDMETHODIMP_(ULONG) Release() override
    {
        LONG c = InterlockedDecrement(&m_cRef);
        if (c == 0)
        {
            delete this;
        }
        return static_cast<ULONG>(c);
    }

    // IClassFactory
    STDMETHODIMP CreateInstance(IUnknown* pUnkOuter, REFIID riid,
                                void** ppv) override
    {
VISAGE_COM_BEGIN
        if (ppv == nullptr)
        {
            return E_POINTER;
        }
        *ppv = nullptr;
        if (pUnkOuter != nullptr)
        {
            return CLASS_E_NOAGGREGATION; // we do not support aggregation
        }
        auto* pProvider = new (std::nothrow) CVisageProvider();
        if (pProvider == nullptr)
        {
            return E_OUTOFMEMORY;
        }
        HRESULT hr = pProvider->QueryInterface(riid, ppv);
        pProvider->Release(); // balance the constructor's ref
        return hr;
        VISAGE_COM_END
}
    STDMETHODIMP LockServer(BOOL fLock) override
    {
        if (fLock)
        {
            InterlockedIncrement(&g_cRef);
        }
        else
        {
            InterlockedDecrement(&g_cRef);
        }
        return S_OK;
    }

private:
    LONG m_cRef;
};

// ---------- registry helpers (namespace-scope const => internal linkage) ----------

const wchar_t* kClsidString =
    L"{794AAC04-6CB6-4C77-A828-27C20D0F820D}";
const wchar_t* kProviderName = L"Visage Face Unlock";
const wchar_t* kProvidersKey =
    L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\Credential Providers";

HRESULT SetRegString(HKEY hRoot, const wchar_t* subKey,
                     const wchar_t* valueName, const wchar_t* value)
{
    HKEY hKey = nullptr;
    LONG lr = RegCreateKeyExW(hRoot, subKey, 0, nullptr, 0,
                              KEY_WRITE, nullptr, &hKey, nullptr);
    if (lr != ERROR_SUCCESS)
    {
        return HRESULT_FROM_WIN32(lr);
    }
    lr = RegSetValueExW(hKey, valueName, 0, REG_SZ,
                        reinterpret_cast<const BYTE*>(value),
                        static_cast<DWORD>((wcslen(value) + 1) * sizeof(wchar_t)));
    RegCloseKey(hKey);
    return (lr == ERROR_SUCCESS) ? S_OK : HRESULT_FROM_WIN32(lr);
}

// M5 (audit): report deletion failures instead of swallowing them.
// A key that is already absent counts as success (goal state achieved).
HRESULT DeleteRegTree(HKEY hRoot, const wchar_t* subKey)
{
    // RegDeleteTreeW removes the key and everything under it.
    LONG lr = RegDeleteTreeW(hRoot, subKey);
    if (lr == ERROR_SUCCESS || lr == ERROR_FILE_NOT_FOUND ||
        lr == ERROR_PATH_NOT_FOUND)
    {
        return S_OK;
    }
    return HRESULT_FROM_WIN32(lr);
}

} // namespace visage

// ---------- standard DLL exports ----------

BOOL APIENTRY DllMain(HINSTANCE hInst, DWORD dwReason, LPVOID)
{
    switch (dwReason)
    {
    case DLL_PROCESS_ATTACH:
        g_hInst = hInst;
        DisableThreadLibraryCalls(hInst);
        break;
    case DLL_PROCESS_DETACH:
        break;
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, void** ppv)
{
VISAGE_COM_BEGIN
    if (ppv == nullptr)
    {
        return E_POINTER;
    }
    *ppv = nullptr;
    // IsEqualGUID (guiddef.h); cguid.h's IsEqualCLSID is the same function.
    if (!IsEqualGUID(rclsid, CLSID_VisageProvider))
    {
        return CLASS_E_CLASSNOTAVAILABLE;
    }
    auto* pFactory = new (std::nothrow) visage::CVisageProviderFactory();
    if (pFactory == nullptr)
    {
        return E_OUTOFMEMORY;
    }
    HRESULT hr = pFactory->QueryInterface(riid, ppv);
    pFactory->Release();
    return hr;
VISAGE_COM_END
}

STDAPI DllCanUnloadNow()
{
    return (g_cRef == 0) ? S_OK : S_FALSE;
}

STDAPI DllRegisterServer()
{
VISAGE_COM_BEGIN
    wchar_t dllPath[MAX_PATH];
    // L3 (audit): a return of exactly ARRAYSIZE means the path was
    // truncated to fit — treat it as failure, not success.
    DWORD cchPath = GetModuleFileNameW(g_hInst, dllPath, ARRAYSIZE(dllPath));
    if (cchPath == 0)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    if (cchPath == ARRAYSIZE(dllPath))
    {
        return HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER);
    }

    wchar_t clsidKey[128];
    HRESULT hr = StringCchPrintfW(clsidKey, ARRAYSIZE(clsidKey),
                                 L"CLSID\\%s", visage::kClsidString);
    if (FAILED(hr))
    {
        return hr;
    }

    // 1. COM server registration. M4 (audit): ANY failure after the first
    // write rolls the CLSID tree back, so we never leave a half-registration
    // behind (deleting a not-yet-created key is a harmless no-op).
    wchar_t inprocKey[160];
    hr = StringCchPrintfW(inprocKey, ARRAYSIZE(inprocKey),
                          L"%s\\InprocServer32", clsidKey);
    if (FAILED(hr))
    {
        return hr;
    }
    hr = visage::SetRegString(HKEY_CLASSES_ROOT, inprocKey, nullptr, dllPath);
    if (FAILED(hr))
    {
        visage::DeleteRegTree(HKEY_CLASSES_ROOT, clsidKey);
        return hr;
    }
    hr = visage::SetRegString(HKEY_CLASSES_ROOT, inprocKey,
                            L"ThreadingModel", L"Apartment");
    if (FAILED(hr))
    {
        visage::DeleteRegTree(HKEY_CLASSES_ROOT, clsidKey);
        return hr;
    }
    hr = visage::SetRegString(HKEY_CLASSES_ROOT, clsidKey, nullptr,
                            visage::kProviderName);
    if (FAILED(hr))
    {
        visage::DeleteRegTree(HKEY_CLASSES_ROOT, clsidKey);
        return hr;
    }

    // 2. Credential provider enrollment: THIS is what makes LogonUI load us.
    wchar_t providerKey[256];
    hr = StringCchPrintfW(providerKey, ARRAYSIZE(providerKey), L"%s\\%s",
                          visage::kProvidersKey, visage::kClsidString);
    if (FAILED(hr))
    {
        // Roll back the COM key so we never leave a half-registration.
        visage::DeleteRegTree(HKEY_CLASSES_ROOT, clsidKey);
        return hr;
    }
    hr = visage::SetRegString(HKEY_LOCAL_MACHINE, providerKey, nullptr,
                            visage::kProviderName);
    if (FAILED(hr))
    {
        visage::DeleteRegTree(HKEY_CLASSES_ROOT, clsidKey);
        return hr;
    }
    return S_OK;
VISAGE_COM_END
}

STDAPI DllUnregisterServer()
{
VISAGE_COM_BEGIN
    wchar_t clsidKey[128];
    wchar_t providerKey[256];
    HRESULT hr = StringCchPrintfW(clsidKey, ARRAYSIZE(clsidKey),
                                  L"CLSID\\%s", visage::kClsidString);
    if (SUCCEEDED(hr))
    {
        hr = StringCchPrintfW(providerKey, ARRAYSIZE(providerKey),
                              L"%s\\%s", visage::kProvidersKey,
                              visage::kClsidString);
    }
    if (FAILED(hr))
    {
        return hr;
    }
    // Only our own two keys. Built-in providers are untouched.
    // M5 (audit): a failed deletion is now reported, not swallowed.
    hr = visage::DeleteRegTree(HKEY_LOCAL_MACHINE, providerKey);
    if (SUCCEEDED(hr))
    {
        hr = visage::DeleteRegTree(HKEY_CLASSES_ROOT, clsidKey);
    }
    return hr;
VISAGE_COM_END
}
