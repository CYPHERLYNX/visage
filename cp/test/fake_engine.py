#!/usr/bin/env python3
"""fake_engine.py - scripted stand-in for mien_engine.py.

Implements the Mien named-pipe JSONL protocol so the credential provider can
be tested without the real computer-vision engine.

Usage (Windows):
    python fake_engine.py <scenario>

Scenarios:
    success     scanning -> face_found -> challenge -> success
    no_face     scanning -> failure(no_face)
    spoof       scanning -> face_found -> failure(spoof)
    timeout     accepts authenticate, then never replies (client must time out)
    slow_cancel scanning, then waits for {"cmd":"cancel"} -> cancelled
    challenge   scanning -> challenge (custom prompt) -> success

The pipe name is \\\\.\\pipe\\MienAuth. One client at a time is enough for
credential-provider testing.
"""
import json
import sys
import time

PIPE_NAME = r"\\.\pipe\MienAuth"


def send(f, obj):
    line = json.dumps(obj)
    f.write((line + "\n").encode("utf-8"))
    f.flush()


def scenario_lines(name):
    if name == "success":
        return [
            (0.5, {"status": "scanning"}),
            (0.5, {"status": "face_found"}),
            (0.8, {"status": "challenge", "prompt": "Turn your head slowly to the left"}),
            (1.2, {"status": "success"}),
        ]
    if name == "no_face":
        return [
            (0.5, {"status": "scanning"}),
            (1.5, {"status": "failure", "reason": "no_face",
                   "message": "No face detected. Make sure you are in front of the camera."}),
        ]
    if name == "spoof":
        return [
            (0.5, {"status": "scanning"}),
            (0.5, {"status": "face_found"}),
            (1.0, {"status": "failure", "reason": "spoof",
                   "message": "Possible spoof detected. Try again with a live face."}),
        ]
    if name == "challenge":
        return [
            (0.4, {"status": "scanning"}),
            (0.6, {"status": "challenge", "prompt": "Blink twice, then look up"}),
            (1.5, {"status": "success"}),
        ]
    if name == "timeout":
        return []  # never replies
    if name == "slow_cancel":
        return [(0.5, {"status": "scanning"})]  # then waits for cancel
    print("unknown scenario: %s" % name, file=sys.stderr)
    sys.exit(2)


def main():
    if sys.platform != "win32":
        print("This harness only runs on Windows (named pipes).", file=sys.stderr)
        sys.exit(1)
    import win32file  # noqa: F401  (pywin32, used implicitly below if present)
    scenario = sys.argv[1] if len(sys.argv) > 1 else "success"
    steps = scenario_lines(scenario)

    # Minimal pipe server via the built-in win32 API through ctypes-free
    # approach: use pywin32 if available, else raw CreateNamedPipe via ctypes.
    try:
        import win32pipe
        import win32api

        use_pywin32 = True
    except ImportError:
        use_pywin32 = False

    print("fake_engine: scenario=%s, pipe=%s" % (scenario, PIPE_NAME))
    if use_pywin32:
        pipe = win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, None)
        print("waiting for client ...")
        win32pipe.ConnectNamedPipe(pipe, None)
        print("client connected")
        buf = b""
        # read the authenticate line
        while not buf.endswith(b"\n"):
            err, chunk = win32file.ReadFile(pipe, 4096)
            if not chunk:
                break
            buf += chunk
        print("got: %s" % buf.decode("utf-8", "replace").strip())

        cancelled = False
        for delay, msg in steps:
            # poll for a cancel line during the delay
            deadline = time.time() + delay
            while time.time() < deadline:
                time.sleep(0.05)
            send_line = json.dumps(msg) + "\n"
            win32file.WriteFile(pipe, send_line.encode("utf-8"))
            print("sent: %s" % send_line.strip())

        if scenario in ("slow_cancel", "timeout"):
            # wait until the client sends cancel or disconnects
            try:
                while True:
                    err, chunk = win32file.ReadFile(pipe, 4096)
                    if not chunk:
                        break
                    for raw in chunk.split(b"\n"):
                        if not raw.strip():
                            continue
                        try:
                            obj = json.loads(raw.decode("utf-8"))
                        except ValueError:
                            continue
                        print("got: %s" % obj)
                        if obj.get("cmd") == "cancel":
                            cancelled = True
                            back = json.dumps({"status": "cancelled"}) + "\n"
                            win32file.WriteFile(pipe, back.encode("utf-8"))
                            print("sent: %s" % back.strip())
                            raise StopIteration
            except StopIteration:
                pass
            print("cancelled=%s" % cancelled)

        win32file.CloseHandle(pipe)
    else:
        print("pywin32 not installed; install it (pip install pywin32) to run this harness.",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
