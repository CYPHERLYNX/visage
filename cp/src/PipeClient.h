// PipeClient.h
// JSONL client for the Mien engine named pipe (\\.\pipe\MienAuth).
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

namespace mien
{

class PipeClient
{
public:
    PipeClient();
    ~PipeClient();

    PipeClient(const PipeClient&) = delete;
    PipeClient& operator=(const PipeClient&) = delete;

    // Connects to \\.\pipe\MienAuth. If the pipe does not exist, tries to
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
    static const wchar_t* PipeName()  { return L"\\\\.\\pipe\\MienAuth"; }
    static const wchar_t* EnginePath(){ return L"C:\\ProgramData\\Mien\\engine\\mien_engine.py"; }
    static const wchar_t* RegKey()    { return L"SOFTWARE\\Mien"; }
    static const wchar_t* RegPython() { return L"PythonPath"; }

private:
    HRESULT TryConnectOnce();
    HRESULT SpawnEngine();
    HRESULT ResolvePythonExe(std::wstring& out);

    HANDLE m_pipe;
};

} // namespace mien
