"""yt-dlp self-update and the app's self-update, against a fake release."""
import io
import json
import os
import sys
import zipfile

import pytest

from jmdm_app import updates


def fake_ytdlp(version):
    buf = io.BytesIO()
    buf.write(b"#!/usr/bin/env python3\n")                  # real zipapps start like this
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("yt_dlp/__init__.py", "from . import version\n")
        z.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
    return buf.getvalue()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "app_dir", lambda: str(tmp_path))
    saved = dict(sys.modules)
    path = list(sys.path)
    updates._forget_ytdlp()
    yield tmp_path
    updates._forget_ytdlp()
    sys.modules.update({k: v for k, v in saved.items() if k.startswith("yt_dlp")})
    sys.path[:] = path


def test_ytdlp_updates_and_loads(isolated, server, monkeypatch):
    server.httpd.blobs["y"] = fake_ytdlp("2099.01.02")
    monkeypatch.setattr(updates, "latest_ytdlp_version", lambda: "2099.01.02")
    monkeypatch.setattr(updates, "YTDLP_FILE", server.url("/blob?b=y"))
    assert updates.update_ytdlp() == "2099.01.02"
    assert updates.ytdlp_version() == "2099.01.02"
    assert updates._ytdlp_source[0] == "updated"
    assert updates.update_ytdlp() == ""                         # already current


def test_broken_update_is_set_aside(isolated):
    z = updates.ytdlp_zip()
    os.makedirs(os.path.dirname(z))
    with open(z, "wb") as fh:
        fh.write(b"not a zip")
    try:
        updates.load_ytdlp()
    except ImportError:
        pass                                                 # no bundled copy in tests
    assert os.path.exists(z + ".bad") and not os.path.exists(z)


def test_wrong_download_is_rejected(isolated, server, monkeypatch):
    server.httpd.blobs["old"] = fake_ytdlp("2001.01.01")
    monkeypatch.setattr(updates, "latest_ytdlp_version", lambda: "2099.01.02")
    monkeypatch.setattr(updates, "YTDLP_FILE", server.url("/blob?b=old"))
    with pytest.raises(ValueError):
        updates.update_ytdlp()
    assert not os.path.exists(updates.ytdlp_zip())


def test_app_update(isolated, server, monkeypatch):
    app = isolated
    os.makedirs(app / "extension")
    (app / "extension" / "manifest.json").write_text(json.dumps({"version": "1.0.0"}))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("JM Download Manager.exe", "jmdm-host.exe", "README.md"):
            z.writestr(n, "x")
        z.writestr("extension/manifest.json", json.dumps({"version": "1.1.0"}))
    server.httpd.blobs["zip"] = buf.getvalue()
    server.httpd.blobs["info"] = json.dumps({"version": "9.0.0", "build": 50}).encode()
    monkeypatch.setattr(updates, "APP_UPDATE_INFO", server.url("/blob?b=info"))
    monkeypatch.setattr(updates, "APP_UPDATE_ZIP", server.url("/blob?b=zip"))

    assert updates.app_update_available() is None              # not the GitHub .exe
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.os, "name", "nt")
    monkeypatch.setattr(updates, "BUILD", 49)
    assert updates.app_update_available() == {"version": "9.0.0", "build": 50}
    monkeypatch.setattr(updates, "BUILD", 50)
    assert updates.app_update_available() is None
    monkeypatch.setattr(updates.os, "name", "posix")

    bat, ext_changed = updates.prepare_app_update()
    script = open(bat).read()
    assert ext_changed
    assert f"set PID={os.getpid()}" in script
    assert 'find /i ".exe"' in script                        # exact-process wait
    assert script.count("copy /y") == 3 and "xcopy" in script
    assert "jmdm_data" not in script                         # never touches your list
