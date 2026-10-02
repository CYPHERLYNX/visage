// Vault.cpp
#include "Vault.h"
#include "JsonMini.h"

#include <dpapi.h>
#include <vector>

namespace mien
{

HRESULT ReadVaultCreds(VaultCreds& out)
{
    out.username.clear();
    out.domain.clear();
    out.password.clear();

    // --- Read the file ---
    HANDLE hFile = CreateFileW(VaultPath(), GENERIC_READ, FILE_SHARE_READ,
                               nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                               nullptr);
    if (hFile == INVALID_HANDLE_VALUE)
    {
        DWORD le = GetLastError();
        MIEN_TRACE(L"ReadVaultCreds: cannot open vault (%u)", le);
        return HRESULT_FROM_WIN32(le);
    }

    LARGE_INTEGER fileSize{};
    HRESULT hr = S_OK;
    if (!GetFileSizeEx(hFile, &fileSize) ||
        fileSize.QuadPart <= 0 || fileSize.QuadPart > (16 * 1024 * 1024))
    {
        hr = E_FAIL; // empty or absurdly large: not our vault
        CloseHandle(hFile);
        return hr;
    }

    SecureByteBuffer cipher(static_cast<size_t>(fileSize.QuadPart));
    DWORD bytesRead = 0;
    if (!ReadFile(hFile, cipher.data(), static_cast<DWORD>(cipher.size()),
                  &bytesRead, nullptr) ||
        bytesRead != cipher.size())
    {
        hr = HRESULT_FROM_WIN32(GetLastError());
        CloseHandle(hFile);
        return hr;
    }
    CloseHandle(hFile);

    // --- DPAPI unprotect (LocalMachine scope; no extra entropy) ---
    DATA_BLOB inBlob{};
    inBlob.cbData = static_cast<DWORD>(cipher.size());
    inBlob.pbData = cipher.data();

    DATA_BLOB outBlob{};
    if (!CryptUnprotectData(&inBlob, nullptr, nullptr, nullptr, nullptr,
                            CRYPTPROTECT_LOCAL_MACHINE, &outBlob))
    {
        DWORD le = GetLastError();
        MIEN_TRACE(L"ReadVaultCreds: CryptUnprotectData failed (%u)", le);
        return HRESULT_FROM_WIN32(le);
    }

    // Copy into a zeroing buffer immediately; LocalFree the DPAPI blob.
    SecureByteBuffer plain(outBlob.cbData);
    if (outBlob.cbData > 0 && outBlob.pbData != nullptr)
    {
        CopyMemory(plain.data(), outBlob.pbData, outBlob.cbData);
    }
    SecureZeroMemory(outBlob.pbData, outBlob.cbData);
    LocalFree(outBlob.pbData);
    outBlob.pbData = nullptr;
    outBlob.cbData = 0;

    // --- Parse UTF-8 JSON ---
    int cch = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                  reinterpret_cast<const char*>(plain.data()),
                                  static_cast<int>(plain.size()),
                                  nullptr, 0);
    if (cch <= 0)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    std::wstring json(static_cast<size_t>(cch), 0);
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                            reinterpret_cast<const char*>(plain.data()),
                            static_cast<int>(plain.size()),
                            &json[0], cch) == 0)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    plain.clear(); // plaintext JSON no longer needed in this form

    std::map<std::wstring, std::wstring> obj;
    if (!JsonMini::ParseFlatObject(json, obj))
    {
        MIEN_TRACE(L"ReadVaultCreds: vault JSON malformed");
        return E_FAIL;
    }
    // Best effort: drop the wide JSON text now (it contains the password).
    SecureZeroMemory(&json[0], json.size() * sizeof(wchar_t));

    std::wstring version, username, domain, password;
    if (!JsonMini::GetString(obj, L"version", version) || version != L"1")
    {
        MIEN_TRACE(L"ReadVaultCreds: unsupported/missing vault version");
        return E_FAIL;
    }
    if (!JsonMini::GetString(obj, L"username", username) || username.empty())
    {
        MIEN_TRACE(L"ReadVaultCreds: vault missing username");
        return E_FAIL;
    }
    // domain may legitimately be empty (local account / UPN).
    JsonMini::GetString(obj, L"domain", domain);
    if (!JsonMini::GetString(obj, L"password", password) || password.empty())
    {
        MIEN_TRACE(L"ReadVaultCreds: vault missing password");
        return E_FAIL;
    }

    hr = out.username.assign(username.data(), username.size());
    if (SUCCEEDED(hr)) hr = out.domain.assign(domain.data(), domain.size());
    if (SUCCEEDED(hr)) hr = out.password.assign(password.data(), password.size());

    // Scrub the std::wstring copies (best effort; SSO may keep copies, but
    // the authoritative copies now live in the zeroing buffers).
    SecureZeroMemory(&username[0], username.size() * sizeof(wchar_t));
    SecureZeroMemory(&domain[0],   domain.size()   * sizeof(wchar_t));
    SecureZeroMemory(&password[0], password.size() * sizeof(wchar_t));

    return hr;
}

} // namespace mien
