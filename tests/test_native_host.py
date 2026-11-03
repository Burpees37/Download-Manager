"""jmdm-host: the small helper the browser uses to start the app."""
import importlib.util
import io
import json
import os
import struct
import sys

from conftest import ROOT


def load():
    spec = importlib.util.spec_from_file_location("native_host", os.path.join(ROOT, "native_host.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_double_clicked_just_starts_the_app(monkeypatch):
    nh = load()
    started = []
    monkeypatch.setattr(nh, "launch", lambda: started.append(1) or True)
    monkeypatch.setattr(sys, "stdin", None)                 # windowless .exe, no browser
    monkeypatch.setattr(sys, "stdout", None)
    nh.main()                                               # must not crash
    assert started == [1]


def test_browser_message_round_trip(monkeypatch):
    nh = load()
    monkeypatch.setattr(nh, "launch", lambda: True)
    msg = json.dumps({"action": "launch"}).encode()
    fin = io.BytesIO(struct.pack("<I", len(msg)) + msg)
    fout = io.BytesIO()
    monkeypatch.setattr(nh, "_std_streams", lambda: (fin, fout))
    nh.main()
    out = fout.getvalue()
    (n,) = struct.unpack("<I", out[:4])
    assert json.loads(out[4:4 + n]) == {"ok": True}
