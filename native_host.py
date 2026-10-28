"""
JM Download Manager — native messaging host.

The browser extension can't start programs by itself. Browsers allow it to
talk to a small registered helper instead (this file, built as jmdm-host.exe).
When the extension asks, the helper starts the app and waits until it's ready.

Protocol (Chrome/Edge/Firefox native messaging): each message is a 4-byte
little-endian length followed by that many bytes of UTF-8 JSON.
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

APP_EXE = "JM Download Manager.exe"
DEFAULT_PORT = 8721


def _std_streams():
    """Raw stdin/stdout even when built as a windowless .exe (where Python
    may set sys.stdin/sys.stdout to None)."""
    if os.name == "nt":
        try:
            import ctypes
            import msvcrt
            k32 = ctypes.windll.kernel32
            fin = msvcrt.open_osfhandle(k32.GetStdHandle(-10), os.O_RDONLY | os.O_BINARY)
            fout = msvcrt.open_osfhandle(k32.GetStdHandle(-11), os.O_BINARY)
            return os.fdopen(fin, "rb", buffering=0), os.fdopen(fout, "wb", buffering=0)
        except Exception:
            pass
    return sys.stdin.buffer, sys.stdout.buffer


def here():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def port():
    try:
        with open(os.path.join(here(), "jmdm_data", "settings.json"), encoding="utf-8") as fh:
            return int(json.load(fh).get("listen_port", DEFAULT_PORT))
    except Exception:
        return DEFAULT_PORT


def app_running():
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port()}/ping",
                                     headers={"X-JMDM": "1"})
        return urllib.request.urlopen(req, timeout=1).status == 200
    except Exception:
        return False


def launch():
    if app_running():
        return True
    exe = os.path.join(here(), APP_EXE)
    if os.path.isfile(exe):
        cmd = [exe]
    else:                                   # running from source
        py = sys.executable
        if os.name == "nt" and py.lower().endswith("python.exe"):
            pyw = py[:-10] + "pythonw.exe"
            py = pyw if os.path.isfile(pyw) else py
        cmd = [py, os.path.join(here(), "jmdm.py")]
    flags = 0
    if os.name == "nt":
        # Browsers run the helper inside a job object; break away so the app
        # keeps running after the helper exits.
        flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED | NEW_GROUP | BREAKAWAY
    try:
        subprocess.Popen(cmd, cwd=here(), creationflags=flags, close_fds=True)
    except OSError:
        subprocess.Popen(cmd, cwd=here(), creationflags=flags & ~0x01000000, close_fds=True)
    for _ in range(40):
        if app_running():
            return True
        time.sleep(0.25)
    return False


def main():
    fin, fout = _std_streams()

    def reply(obj):
        data = json.dumps(obj).encode("utf-8")
        fout.write(struct.pack("<I", len(data)) + data)
        fout.flush()

    raw = fin.read(4)
    if len(raw) < 4:
        return
    (n,) = struct.unpack("<I", raw)
    try:
        msg = json.loads(fin.read(n) or b"{}")
    except Exception:
        return reply({"ok": False, "error": "bad message"})
    action = msg.get("action")
    if action == "launch":
        reply({"ok": launch()})
    elif action == "ping":
        reply({"ok": True, "running": app_running()})
    else:
        reply({"ok": False, "error": "unknown action"})


if __name__ == "__main__":
    main()
