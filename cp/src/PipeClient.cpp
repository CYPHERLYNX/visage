// PipeClient.cpp
#include "PipeClient.h"
#include "Common.h"

#include <vector>

namespace visage
{

PipeClient::PipeClient()
    : m_pipe(INVALID_HANDLE_VALUE)
    , m_hChild(nullptr)
{
}

PipeClient::~PipeClient()
{
    Close();
}

void PipeClient::KillSpawnedChild()
{
    if (m_hChild != nullptr)
    {
        DWORD exitCode = 0;
        if (GetExitCodeProcess(m_hChild, &exitCode) &&
            exitCode == STILL_ACTIVE)
        {
            TerminateProcess(m_hChild, 1);
        }
        CloseHandle(m_hChild);
        m_hChild = nullptr;
    }
}

void PipeClient::ReleaseChildHandle()
{
    // The pipe is up: the engine is now persistent by design (it serves
    // future scans). We no longer need the process handle.
    if (m_hChild != nullptr)
    {
        CloseHandle(m_hChild);
        m_hChild = nullptr;
    }
}

void PipeClient::Close()
{
    if (m_pipe != INVALID_HANDLE_VALUE)
    {
        // Best effort: tell the engine to stand down before we go away.
        SendCancel();
        CloseHandle(m_pipe);
        m_pipe = INVALID_HANDLE_VALUE;
    }
    // Safety net: if we spawned an engine but never connected (Connect
    // failed on every path), do not leave the orphan behind.
    KillSpawnedChild();
}

HRESULT PipeClient::TryConnectOnce()
{
    HANDLE h = CreateFileW(PipeName(),
                           GENERIC_READ | GENERIC_WRITE,
                           0,              // no sharing
                           nullptr,
                           OPEN_EXISTING,
                           FILE_FLAG_OVERLAPPED,
                           nullptr);
    if (h == INVALID_HANDLE_VALUE)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    // We speak byte-stream JSONL; force byte mode in case the server left
    // the handle in message mode.
    DWORD mode = PIPE_READMODE_BYTE | PIPE_WAIT;
    if (!SetNamedPipeHandleState(h, &mode, nullptr, nullptr))
    {
        HRESULT hr = HRESULT_FROM_WIN32(GetLastError());
        CloseHandle(h);
        return hr;
    }
    m_pipe = h;
    return S_OK;
}

HRESULT PipeClient::Connect(DWORD connectTimeoutMs, HANDLE cancelEvent)
{
    if (IsConnected())
    {
        return S_OK;
    }

    auto IsCancelled = [&]() -> bool {
        return cancelEvent != nullptr &&
               WaitForSingleObject(cancelEvent, 0) == WAIT_OBJECT_0;
    };

    ULONGLONG deadline = GetTickCount64() + connectTimeoutMs;

    HRESULT hr = TryConnectOnce();
    if (SUCCEEDED(hr))
    {
        return S_OK;
    }

    DWORD err = HRESULT_CODE(hr);
    if (err == ERROR_PIPE_BUSY)
    {
        // Server exists but all instances are busy: wait in short slices so
        // the cancel event stays responsive.
        for (;;)
        {
            if (IsCancelled())
            {
                return HRESULT_FROM_WIN32(ERROR_CANCELLED);
            }
            ULONGLONG now = GetTickCount64();
            if (now >= deadline)
            {
                return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
            }
            DWORD slice = static_cast<DWORD>(deadline - now);
            if (slice > 250)
            {
                slice = 250;
            }
            if (WaitNamedPipeW(PipeName(), slice))
            {
                return TryConnectOnce();
            }
            DWORD wle = GetLastError();
            if (wle != ERROR_SEM_TIMEOUT && wle != ERROR_FILE_NOT_FOUND)
            {
                return HRESULT_FROM_WIN32(wle);
            }
            if (wle == ERROR_FILE_NOT_FOUND)
            {
                // Pipe vanished mid-wait: don't hot-spin.
                Sleep(100);
            }
        }
    }

    if (err != ERROR_FILE_NOT_FOUND)
    {
        VISAGE_TRACE(L"Connect: unexpected error %u", err);
        return hr;
    }

    // No engine running: spawn it, then wait for the pipe to appear.
    VISAGE_TRACE(L"Connect: engine not running, spawning");
    hr = SpawnEngine();
    if (FAILED(hr))
    {
        return hr;
    }

    for (;;)
    {
        if (IsCancelled())
        {
            KillSpawnedChild();
            return HRESULT_FROM_WIN32(ERROR_CANCELLED);
        }
        ULONGLONG now = GetTickCount64();
        if (now >= deadline)
        {
            KillSpawnedChild();
            return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        }
        DWORD remaining = static_cast<DWORD>(deadline - now);
        if (remaining > 250)
        {
            remaining = 250;
        }
        if (WaitNamedPipeW(PipeName(), remaining))
        {
            hr = TryConnectOnce();
            if (SUCCEEDED(hr))
            {
                ReleaseChildHandle(); // engine is up; it persists by design
                return S_OK;
            }
            if (HRESULT_CODE(hr) != ERROR_FILE_NOT_FOUND &&
                HRESULT_CODE(hr) != ERROR_PIPE_BUSY)
            {
                KillSpawnedChild();
                return hr;
            }
            // else: keep waiting until the deadline
        }
        else if (GetLastError() != ERROR_SEM_TIMEOUT)
        {
            KillSpawnedChild();
            return HRESULT_FROM_WIN32(GetLastError());
        }
    }
}

HRESULT PipeClient::ResolvePythonExe(std::wstring& out)
{
    out.clear();

    // 1. Visage's own configuration, written by the installer (admin-owned).
    {
        HKEY hKey = nullptr;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, RegKey(), 0, KEY_READ, &hKey) == ERROR_SUCCESS)
        {
            wchar_t buf[MAX_PATH];
            DWORD cb = sizeof(buf);
            DWORD type = 0;
            if (RegQueryValueExW(hKey, RegPython(), nullptr, &type,
                                 reinterpret_cast<LPBYTE>(buf), &cb) == ERROR_SUCCESS &&
                type == REG_SZ && buf[0] != 0)
            {
                buf[ARRAYSIZE(buf) - 1] = 0;
                DWORD attrs = GetFileAttributesW(buf);
                if (attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY))
                {
                    out.assign(buf);
                    RegCloseKey(hKey);
                    return S_OK;
                }
                VISAGE_TRACE(L"ResolvePythonExe: configured path missing: %s", buf);
            }
            RegCloseKey(hKey);
        }
    }

    // 2. The Python launcher lives in System32 (a system path, never a user
    //    path) and knows where every registered Python is.
    {
        wchar_t sysDir[MAX_PATH];
        if (GetSystemDirectoryW(sysDir, ARRAYSIZE(sysDir)) != 0)
        {
            std::wstring py = std::wstring(sysDir) + L"\\py.exe";
            DWORD attrs = GetFileAttributesW(py.c_str());
            if (attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY))
            {
                out.assign(py);
                return S_OK;
            }
        }
    }

    // 3. Last resort: whatever pythonw.exe PATH resolves to. (On the secure
    //    desktop PATH is minimal, so this rarely hits.)
    {
        wchar_t found[MAX_PATH];
        // SearchPathW with lpPath == nullptr searches the system search order.
        DWORD n = SearchPathW(nullptr, L"pythonw.exe", nullptr,
                              ARRAYSIZE(found), found, nullptr);
        if (n != 0 && n < ARRAYSIZE(found))
        {
            out.assign(found);
            return S_OK;
        }
    }

    return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
}

HRESULT PipeClient::SpawnEngine()
{
    // The engine is a package; probe the entry module. We never execute
    // anything else.
    DWORD attrs = GetFileAttributesW(EngineMain());
    if (attrs == INVALID_FILE_ATTRIBUTES || (attrs & FILE_ATTRIBUTE_DIRECTORY))
    {
        VISAGE_TRACE(L"SpawnEngine: engine package not found");
        return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
    }

    std::wstring pythonExe;
    HRESULT hr = ResolvePythonExe(pythonExe);
    if (FAILED(hr))
    {
        VISAGE_TRACE(L"SpawnEngine: no Python interpreter found (hr=0x%08x)", hr);
        return hr;
    }

    // Launch as "-m engine.visage_engine" (NOT as a script path): the engine
    // package uses relative imports, which only resolve under -m. The
    // working directory puts the package's parent on sys.path. The command
    // line is built, never interpreted by a shell.
    std::wstring cmdLine = L"\"" + pythonExe + L"\" -m " + EngineModule();

    // CreateProcessW may modify the buffer, so use a mutable copy.
    std::vector<wchar_t> cmdBuf(cmdLine.begin(), cmdLine.end());
    cmdBuf.push_back(0);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    // No window: the engine is headless; the tile is its UI.
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;

    PROCESS_INFORMATION pi{};
    // NOTE (security): this runs as SYSTEM on the secure desktop, so
    // C:\ProgramData\Visage\engine\ must be ACL'd admin-writable-only by the
    // installer. We execute exactly one fixed module.
    if (!CreateProcessW(nullptr, cmdBuf.data(),
                        nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                        nullptr, EngineDir(), &si, &pi))
    {
        HRESULT chr = HRESULT_FROM_WIN32(GetLastError());
        VISAGE_TRACE(L"SpawnEngine: CreateProcess failed (hr=0x%08x)", chr);
        return chr;
    }

    VISAGE_TRACE(L"SpawnEngine: started pid=%u", pi.dwProcessId);
    CloseHandle(pi.hThread);
    // M3 (audit): retain the process handle so a failed start can be
    // reaped; released once the pipe is up (engine then persists).
    KillSpawnedChild(); // defensive: never leak a previous child
    m_hChild = pi.hProcess;
    return S_OK;
}

HRESULT PipeClient::SendLine(const std::string& jsonUtf8)
{
    if (!IsConnected())
    {
        return E_HANDLE;
    }
    // The handle was opened with FILE_FLAG_OVERLAPPED, so even writes must
    // go through an OVERLAPPED (a NULL lpOverlapped would fail).
    std::string line = jsonUtf8 + "\n";

    OVERLAPPED ov{};
    ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (ov.hEvent == nullptr)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }

    DWORD written = 0;
    HRESULT hr = S_OK;
    BOOL ok = WriteFile(m_pipe, line.data(),
                        static_cast<DWORD>(line.size()), &written, &ov);
    if (!ok)
    {
        DWORD le = GetLastError();
        if (le != ERROR_IO_PENDING)
        {
            hr = HRESULT_FROM_WIN32(le);
        }
        else if (WaitForSingleObject(ov.hEvent, 5000) == WAIT_OBJECT_0)
        {
            if (!GetOverlappedResult(m_pipe, &ov, &written, FALSE) ||
                written != line.size())
            {
                hr = HRESULT_FROM_WIN32(GetLastError());
                if (SUCCEEDED(hr))
                {
                    hr = E_FAIL; // short write
                }
            }
        }
        else
        {
            CancelIoEx(m_pipe, &ov);
            GetOverlappedResult(m_pipe, &ov, &written, TRUE); // drain
            hr = HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        }
    }
    else if (written != line.size())
    {
        hr = E_FAIL; // synchronous short write
    }
    CloseHandle(ov.hEvent);
    if (FAILED(hr))
    {
        VISAGE_TRACE(L"SendLine: write failed (hr=0x%08x)", hr);
    }
    // NOTE: no FlushFileBuffers. On a byte-mode pipe it can block until the
    // server reads, which would hang the worker on a dead engine. WriteFile
    // delivery is sufficient for our small JSON lines.
    return hr;
}

void PipeClient::SendCancel()
{
    if (IsConnected())
    {
        SendLine("{\"cmd\":\"cancel\"}");
    }
}

HRESULT PipeClient::ReadLine(std::wstring& outLineUtf16, DWORD timeoutMs,
                             HANDLE cancelEvent)
{
    outLineUtf16.clear();
    if (!IsConnected())
    {
        return E_HANDLE;
    }

    std::string raw;
    raw.reserve(256);

    ULONGLONG deadline = GetTickCount64() + timeoutMs;

    for (;;)
    {
        ULONGLONG now = GetTickCount64();
        if (now >= deadline)
        {
            return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        }
        DWORD remaining = static_cast<DWORD>(deadline - now);

        // Overlapped single-byte read so we can wait with a timeout and
        // still honor the cancel event.
        OVERLAPPED ov{};
        ov.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (ov.hEvent == nullptr)
        {
            return HRESULT_FROM_WIN32(GetLastError());
        }

        char ch = 0;
        DWORD got = 0;
        BOOL ok = ReadFile(m_pipe, &ch, 1, &got, &ov);
        DWORD waitResult;
        if (!ok)
        {
            DWORD le = GetLastError();
            if (le != ERROR_IO_PENDING)
            {
                CloseHandle(ov.hEvent);
                if (le == ERROR_BROKEN_PIPE)
                {
                    return HRESULT_FROM_WIN32(ERROR_BROKEN_PIPE);
                }
                return HRESULT_FROM_WIN32(le);
            }
            HANDLE waits[2] = { ov.hEvent, cancelEvent };
            DWORD nWait = (cancelEvent != nullptr) ? 2 : 1;
            waitResult = WaitForMultipleObjects(nWait, waits, FALSE, remaining);
            if (waitResult == WAIT_OBJECT_0)
            {
                if (!GetOverlappedResult(m_pipe, &ov, &got, FALSE))
                {
                    DWORD gle = GetLastError();
                    CloseHandle(ov.hEvent);
                    if (gle == ERROR_BROKEN_PIPE || gle == ERROR_HANDLE_EOF)
                    {
                        return HRESULT_FROM_WIN32(ERROR_BROKEN_PIPE);
                    }
                    return HRESULT_FROM_WIN32(gle);
                }
            }
            else
            {
                // Timeout or cancel: pull the plug on the pending read.
                CancelIoEx(m_pipe, &ov);
                // Drain the completion so the OVERLAPPED isn't left dangling.
                GetOverlappedResult(m_pipe, &ov, &got, TRUE);
                CloseHandle(ov.hEvent);
                if (waitResult == WAIT_OBJECT_0 + 1)
                {
                    return HRESULT_FROM_WIN32(ERROR_CANCELLED);
                }
                return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
            }
        }
        else
        {
            waitResult = WAIT_OBJECT_0; // completed synchronously
        }
        CloseHandle(ov.hEvent);

        if (got == 0)
        {
            return HRESULT_FROM_WIN32(ERROR_BROKEN_PIPE);
        }
        if (ch == '\n')
        {
            break;
        }
        if (ch != '\r')
        {
            raw.push_back(ch);
            if (raw.size() > 8192)
            {
                // Absurd line length: the engine is misbehaving; bail.
                return E_FAIL;
            }
        }
    }

    if (raw.empty())
    {
        return S_OK; // blank line: not an error, caller keeps reading
    }

    int cch = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                  raw.data(), static_cast<int>(raw.size()),
                                  nullptr, 0);
    if (cch <= 0)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    std::wstring w(static_cast<size_t>(cch), 0);
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                            raw.data(), static_cast<int>(raw.size()),
                            &w[0], cch) == 0)
    {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    outLineUtf16 = std::move(w);
    return S_OK;
}

} // namespace visage
