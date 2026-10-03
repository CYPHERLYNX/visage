// Guids.h
// Visage Credential Provider - COM identity.
//
// The CLSID below was generated fresh for this project (uuid4). It must stay
// stable across builds: Windows registers the provider under this CLSID and
// the installer/uninstaller scripts reference it.
//
// NOTE: this header must NOT include <initguid.h> (that would emit a
// definition in every TU that includes it -> LNK2005). Exactly one TU
// (Dll.cpp) includes <initguid.h> before this header; every other TU gets
// a plain declaration.
#pragma once

#include <guiddef.h>

// {794AAC04-6CB6-4C77-A828-27C20D0F820D}
DEFINE_GUID(CLSID_VisageProvider,
    0x794aac04, 0x6cb6, 0x4c77, 0xa8, 0x28, 0x27, 0xc2, 0x0d, 0x0f, 0x82, 0x0d);
