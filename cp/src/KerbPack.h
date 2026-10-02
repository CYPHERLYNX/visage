// KerbPack.h
// Builds the KERB_INTERACTIVE_UNLOCK_LOGON serialization handed to LSA via
// CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION.
//
// We build the packed buffer ourselves (one CoTaskMemAlloc, struct followed
// by the string data, UNICODE_STRINGs pointing inside) instead of using
// KerbInteractiveUnlockLogonInit: this keeps allocation/free ownership
// unambiguous (LogonUI frees rgbSerialization with CoTaskMemFree) and lets
// us zero the password deterministically right after packing.
#pragma once

#include <windows.h>
#include <ntsecapi.h>
#include <credentialprovider.h>
#include "Common.h"

namespace mien
{

// Splits a stored username into domain + user for the KERB logon.
//   "DOMAIN\user" -> domain="DOMAIN", user="user" (explicit domain wins)
//   otherwise     -> domain=fallbackDomain (may be empty), user=whole string
// Returns false when the resulting username is empty.
bool SplitUserName(const wchar_t* stored, const wchar_t* fallbackDomain,
                   std::wstring& domain, std::wstring& user);

// Builds a self-contained KERB_INTERACTIVE_UNLOCK_LOGON buffer.
//   - CPUS_LOGON:            MessageType=KerbInteractiveLogon, LogonId=0.
//   - CPUS_UNLOCK_WORKSTATION: MessageType=KerbUnlockLogon, LogonId=*logonId
//     (logonId must be non-null; it comes from SetSerialization).
// The password is copied into the buffer and then zeroed in place before
// this function returns. *outBuf is CoTaskMemAlloc'd; LogonUI takes
// ownership and frees it with CoTaskMemFree.
HRESULT BuildKerbUnlockSerialization(const wchar_t* domain,
                                     const wchar_t* username,
                                     SecureWBuffer& password,
                                     CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
                                     const LUID* logonId,
                                     BYTE** outBuf, DWORD* outCb);

// Reads the LogonId LUID out of a serialization previously supplied to
// SetSerialization (packed KERB_INTERACTIVE_UNLOCK_LOGON). The struct layout
// is identical in packed and native form; only the string Buffer pointers
// move. Returns false when the blob is absent or too small to be valid.
bool TryGetStashedLogonId(
    const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* pcpcs, LUID* out);

} // namespace mien
