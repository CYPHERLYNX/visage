// PipeClient.h
// JSONL client for the Visage engine named pipe (\\.\pipe\VisageAuth).
//
// Responsibilities:
//   - Connect to the pipe, spawning the engine first if it is not running.
//   - Send one JSON line, read reply lines, each with a deadline.
//   - Never throws; every failure is reported via HRESULT / bool.
//
// Threading: a PipeClient lives on the scan worker thread only.
#pragma once

#include <windows.h>
#include <string>

namespace visage
{

class PipeClient
{
public:
    PipeClient();
    ~PipeClient();

    PipeClient(const PipeClient&) = delete;
    PipeClient& operator=(const PipeClient&) = delete;

    // Connects to \\.\pipe\VisageAuth. If the pipe does not exist, tries to
    // spawn the engine and waits for the pipe to appear. connectTimeoutMs
    // bounds the whole operation. cancelEvent (may be null) is polled while
    // waiting so a blocked connect can be aborted promptly.
    HRESULT Connect(DWORD connectTimeoutMs, HANDLE cancelEvent = nullptr);

    // Best-effort "cancel" line; used when the user walks away mid-scan.
    void SendCancel();

    // Sends a single JSON line (without trailing newline; one is appended).
    HRESULT SendLine(const std::string& jsonUtf8);

    // Reads one LF-terminated line (CR stripped), decoded as UTF-8.
    // Returns S_OK on a line, HRESULT_FROM_WIN32(ERROR_TIMEOUT) on timeout,
    // or a failure HRESULT. *cancelEvent is polled so a blocked read can be
    // aborted promptly from another thread.
    HRESULT ReadLine(std::wstring& outLineUtf16, DWORD timeoutMs,
                     HANDLE cancelEvent);

    bool IsConnected() const { return m_pipe != INVALID_HANDLE_VALUE; }
    void Close();

    // Pipe / install locations. No user-profile paths anywhere.
    static const wchar_t* PipeName()  { return L"\\\\.\\pipe\\VisageAuth"; }
    // The engine is deployed as a PACKAGE at EngineDir()\engine\ (with
    // __init__.py). It MUST be launched as "python -m engine.visage_engine"
    // with EngineDir() as the working directory: direct-script execution
    // would die on the package's relative imports ("from . import ...").
    // (The flat fake_engine.py used in VM tests also works under -m.)
    static const wchar_t* EngineDir()   { return L"C:\\ProgramData\\Visage"; }
    static const wchar_t* EngineModule(){ return L"engine.visage_engine"; }
    // Existence probe: the package entry module.
    static const wchar_t* EngineMain()
    { return L"C:\\ProgramData\\Visage\\engine\\visage_engine.py"; }
    static const wchar_t* RegKey()    { return L"SOFTWARE\\Visage"; }
    static const wchar_t* RegPython() { return L"PythonPath"; }

private:
    HRESULT TryConnectOnce();
    HRESULT SpawnEngine();
    HRESULT ResolvePythonExe(std::wstring& out);

    // M3 (audit): a spawned engine that never brings its pipe up must not
    // linger as an orphan python.exe. We keep the child handle until the
    // pipe connects; on success the engine becomes persistent by design
    // (subsequent scans reuse it) and we just release the handle, on
    // failure we terminate the child.
    void KillSpawnedChild();
    void ReleaseChildHandle();

    HANDLE m_pipe;
    HANDLE m_hChild; // process handle of an engine WE spawned, else null
};

} // namespace visage
