// Common.h
// Shared helpers for the Mien credential provider.
//
// Logging policy: OutputDebugStringW only, and NEVER log secrets (no
// passwords, no vault bytes, no pipe payloads that could carry them).
// State transitions and HRESULTs are fine.
#pragma once

#include <windows.h>
#include <strsafe.h>
#include <vector>

namespace mien
{

// Debug-only trace. Compiles to nothing in Release so a shipping DLL can
// never leak anything through the debug channel.
#ifdef _DEBUG
inline void Trace(const wchar_t* fmt, ...)
{
    wchar_t buf[512];
    va_list ap;
    va_start(ap, fmt);
    StringCchVPrintfW(buf, ARRAYSIZE(buf), fmt, ap);
    va_end(ap);
    OutputDebugStringW(L"[MienCP] ");
    OutputDebugStringW(buf);
    OutputDebugStringW(L"\n");
}
#define MIEN_TRACE mien::Trace
#else
inline void Trace(const wchar_t*, ...) {}
#define MIEN_TRACE(...) ((void)0)
#endif

// RAII buffer that zeroes its contents on destruction. Use for anything
// that transiently holds secret material (passwords, DPAPI plaintext).
class SecureWBuffer
{
public:
    SecureWBuffer() = default;
    explicit SecureWBuffer(size_t chars) { resize(chars); }

    SecureWBuffer(const SecureWBuffer&) = delete;
    SecureWBuffer& operator=(const SecureWBuffer&) = delete;

    ~SecureWBuffer() { clear(); }

    void resize(size_t chars)
    {
        clear();
        if (chars > 0)
        {
            m_data.resize(chars + 1, 0); // +1 for the terminator
        }
    }

    void clear()
    {
        if (!m_data.empty())
        {
            SecureZeroMemory(m_data.data(), m_data.size() * sizeof(wchar_t));
            m_data.clear();
        }
    }

    wchar_t* data() { return m_data.empty() ? nullptr : m_data.data(); }
    const wchar_t* c_str() const { return m_data.empty() ? L"" : m_data.data(); }
    size_t length() const { return m_data.empty() ? 0 : (m_data.size() - 1); }
    bool empty() const { return m_data.empty(); }

    // Copies exactly `chars` characters from src (no reliance on a
    // terminator inside src); always NUL-terminates.
    HRESULT assign(const wchar_t* src, size_t chars)
    {
        if (chars > 0 && src == nullptr)
        {
            return E_POINTER;
        }
        resize(chars);
        if (chars > 0)
        {
            CopyMemory(m_data.data(), src, chars * sizeof(wchar_t));
            m_data[chars] = 0;
        }
        return S_OK;
    }

private:
    std::vector<wchar_t> m_data;
};

// Same idea for byte buffers (DPAPI blobs, file contents).
class SecureByteBuffer
{
public:
    SecureByteBuffer() = default;
    explicit SecureByteBuffer(size_t bytes) { resize(bytes); }

    SecureByteBuffer(const SecureByteBuffer&) = delete;
    SecureByteBuffer& operator=(const SecureByteBuffer&) = delete;

    ~SecureByteBuffer() { clear(); }

    void resize(size_t bytes)
    {
        clear();
        if (bytes > 0)
        {
            m_data.resize(bytes, 0);
        }
    }

    void clear()
    {
        if (!m_data.empty())
        {
            SecureZeroMemory(m_data.data(), m_data.size());
            m_data.clear();
        }
    }

    BYTE* data() { return m_data.empty() ? nullptr : m_data.data(); }
    const BYTE* data() const { return m_data.empty() ? nullptr : m_data.data(); }
    size_t size() const { return m_data.size(); }
    bool empty() const { return m_data.empty(); }

private:
    std::vector<BYTE> m_data;
};

// Duplicate a string with CoTaskMemAlloc (caller frees with CoTaskMemFree).
// Returns S_OK with *out set, or an HRESULT failure with *out = nullptr.
inline HRESULT DupString(const wchar_t* src, wchar_t** out)
{
    if (out == nullptr)
    {
        return E_POINTER;
    }
    *out = nullptr;
    if (src == nullptr)
    {
        return E_POINTER;
    }
    size_t cch = wcslen(src) + 1;
    wchar_t* dst = static_cast<wchar_t*>(CoTaskMemAlloc(cch * sizeof(wchar_t)));
    if (dst == nullptr)
    {
        return E_OUTOFMEMORY;
    }
    HRESULT hr = StringCchCopyW(dst, cch, src);
    if (FAILED(hr))
    {
        CoTaskMemFree(dst);
        return hr;
    }
    *out = dst;
    return S_OK;
}

} // namespace mien
