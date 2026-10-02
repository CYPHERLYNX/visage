// KerbPack.cpp
#include "KerbPack.h"

#include <string>

// The V2 sample uses KerbUnlockLogon; some SDK vintages only name it
// KerbWorkstationUnlockLogon. Map defensively; if neither exists the build
// fails loudly instead of submitting a wrong MessageType.
#ifndef KerbUnlockLogon
#ifdef KerbWorkstationUnlockLogon
#define KerbUnlockLogon KerbWorkstationUnlockLogon
#else
#error "ntsecapi.h provides neither KerbUnlockLogon nor KerbWorkstationUnlockLogon"
#endif
#endif

namespace mien
{

bool SplitUserName(const wchar_t* stored, const wchar_t* fallbackDomain,
                   std::wstring& domain, std::wstring& user)
{
    domain.clear();
    user.clear();
    if (stored == nullptr || stored[0] == 0)
    {
        return false;
    }
    const wchar_t* bs = wcschr(stored, L'\\');
    if (bs != nullptr && bs != stored && bs[1] != 0)
    {
        // Explicit length form (not the iterator-pair overload): assigning
        // [stored, bs) is unambiguous to every reader and every compiler.
        domain.assign(stored, static_cast<size_t>(bs - stored));
        user.assign(bs + 1);
    }
    else
    {
        if (fallbackDomain != nullptr)
        {
            domain.assign(fallbackDomain);
        }
        user.assign(stored);
    }
    return !user.empty();
}

// Fills one UNICODE_STRING to point at `chars` characters inside the packed
// buffer, advancing *ppCursor past the copied data (including terminator).
// Empty strings become NULL/0/0.
static void PackUnicodeString(UNICODE_STRING& dst, const wchar_t* src,
                              size_t chars, BYTE** ppCursor)
{
    if (chars == 0 || src == nullptr)
    {
        dst.Length = 0;
        dst.MaximumLength = 0;
        dst.Buffer = nullptr;
        return;
    }
    dst.Length = static_cast<USHORT>(chars * sizeof(wchar_t));
    dst.MaximumLength = static_cast<USHORT>((chars + 1) * sizeof(wchar_t));
    dst.Buffer = reinterpret_cast<PWSTR>(*ppCursor);
    CopyMemory(dst.Buffer, src, chars * sizeof(wchar_t));
    dst.Buffer[chars] = 0;
    *ppCursor += (chars + 1) * sizeof(wchar_t);
}

HRESULT BuildKerbUnlockSerialization(const wchar_t* domain,
                                     const wchar_t* username,
                                     SecureWBuffer& password,
                                     CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
                                     const LUID* logonId,
                                     BYTE** outBuf, DWORD* outCb)
{
    if (outBuf == nullptr || outCb == nullptr)
    {
        return E_POINTER;
    }
    *outBuf = nullptr;
    *outCb = 0;

    if (username == nullptr || username[0] == 0)
    {
        return E_INVALIDARG;
    }
    if (cpus == CPUS_UNLOCK_WORKSTATION && logonId == nullptr)
    {
        // Unlocking without the locked session's LUID would hand LSA a
        // serialization it cannot match to a session. Refuse loudly.
        return E_INVALIDARG;
    }

    size_t cchDomain = (domain != nullptr) ? wcslen(domain) : 0;
    size_t cchUser = wcslen(username);
    size_t cchPass = password.length();

    // Guard against absurd sizes (and USHORT overflow in UNICODE_STRING:
    // MaximumLength is (chars+1)*sizeof(wchar_t), so chars must stay <=
    // (65535/2)-1 = 32766).
    const size_t kMaxChars = 32766;
    if (cchDomain > kMaxChars || cchUser > kMaxChars || cchPass > kMaxChars)
    {
        password.clear();
        return E_INVALIDARG;
    }

    size_t cbTotal = sizeof(KERB_INTERACTIVE_UNLOCK_LOGON) +
        (cchDomain + 1 + cchUser + 1 + cchPass + 1) * sizeof(wchar_t);

    BYTE* buf = static_cast<BYTE*>(CoTaskMemAlloc(cbTotal));
    if (buf == nullptr)
    {
        password.clear();
        return E_OUTOFMEMORY;
    }
    SecureZeroMemory(buf, cbTotal);

    auto* pkiul = reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(buf);
    pkiul->Logon.MessageType = (cpus == CPUS_UNLOCK_WORKSTATION)
        ? KerbUnlockLogon
        : KerbInteractiveLogon;
    if (logonId != nullptr)
    {
        pkiul->LogonId = *logonId;
    }

    BYTE* cursor = buf + sizeof(KERB_INTERACTIVE_UNLOCK_LOGON);
    PackUnicodeString(pkiul->Logon.LogonDomainName, domain, cchDomain, &cursor);
    PackUnicodeString(pkiul->Logon.UserName, username, cchUser, &cursor);
    PackUnicodeString(pkiul->Logon.Password, password.c_str(), cchPass, &cursor);

    // The password now exists only inside the packed buffer (owned by
    // LogonUI after GetSerialization returns). Scrub our copy now.
    password.clear();

    // Defensive: cursor must land exactly at the end of the allocation.
    if (cursor != buf + cbTotal)
    {
        MIEN_TRACE(L"BuildKerbUnlockSerialization: size mismatch");
        SecureZeroMemory(buf, cbTotal);
        CoTaskMemFree(buf);
        return E_FAIL;
    }

    *outBuf = buf;
    *outCb = static_cast<DWORD>(cbTotal);
    return S_OK;
}

bool TryGetStashedLogonId(
    const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs, LUID* out)
{
    if (pcpcs == nullptr || out == nullptr)
    {
        return false;
    }
    if (pcpcs->rgbSerialization == nullptr ||
        pcpcs->cbSerialization < sizeof(KERB_INTERACTIVE_UNLOCK_LOGON))
    {
        return false;
    }
    const auto* pkiul = reinterpret_cast<const KERB_INTERACTIVE_UNLOCK_LOGON*>(
        pcpcs->rgbSerialization);
    // Sanity: only accept blobs that actually look like Kerberos logons.
    if (pkiul->Logon.MessageType != KerbInteractiveLogon &&
        pkiul->Logon.MessageType != KerbUnlockLogon)
    {
        return false;
    }
    *out = pkiul->LogonId;
    return true;
}

} // namespace mien
