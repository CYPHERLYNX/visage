// Vault.h
// Reads C:\ProgramData\Mien\vault.dat and decrypts it with DPAPI
// (LocalMachine scope), yielding the stored Windows credentials.
//
// Vault file format (written by the Mien installer / enrollment app):
//   DPAPI(CRYPTPROTECT_LOCAL_MACHINE)-encrypted UTF-8 JSON:
//   {"version":1,"username":"...","domain":"...","password":"..."}
//   - "domain" may be "" for a local account or a UPN-style username.
//   - "username" may be "DOMAIN\\user", a UPN, or a plain local name.
//
// Security notes:
//   - LocalMachine scope means any local process CAN unprotect the blob;
//     confidentiality therefore rests on the file ACL (SYSTEM +
//     Administrators only), which the installer must set. This mirrors how
//     Glance keeps its stored password behind a platform key store.
//   - The plaintext is returned in zeroing buffers and must be cleared by
//     the caller immediately after the KERB serialization is built.
#pragma once

#include <windows.h>
#include "Common.h"

namespace mien
{

struct VaultCreds
{
    SecureWBuffer username; // as stored; may contain DOMAIN\user or UPN
    SecureWBuffer domain;   // may be empty
    SecureWBuffer password; // secret: zeroed on destruction
};

// Reads, decrypts and parses the vault. Returns S_OK on success.
// Failure HRESULTs: file missing/unreadable, DPAPI failure, bad JSON,
// missing fields. *out is cleared on failure.
HRESULT ReadVaultCreds(VaultCreds& out);

inline const wchar_t* VaultPath()
{
    return L"C:\\ProgramData\\Mien\\vault.dat";
}

} // namespace mien
