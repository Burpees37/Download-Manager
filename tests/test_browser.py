"""The browser extension's side door: /ping, /settings and /add with the
"held until the Add dialog is answered" hand-off."""
import json
import threading
import time
import urllib.request

import pytest

from jmdm_app import browser

H = {"X-JMDM": "1", "Content-Type": "application/json"}


@pytest.fixture
def app(mgr):
    mgr.settings["listen_port"] = 0
    assert mgr.start_listener()
    base = f"http://127.0.0.1:{mgr._server.server_port}"

    def call(path, obj=None, headers=H):
        req = urllib.request.Request(base + path, headers=headers,
                                     data=json.dumps(obj).encode() if obj is not None else None)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    return mgr, call


def answer_next(mgr, accepted, delay=0.2):
    def go():
        job = mgr.incoming.get(timeout=10)
        time.sleep(delay)
        browser.settle(job, accepted)
    threading.Thread(target=go, daemon=True).start()


def test_only_the_extension_may_talk(app):
    _, call = app
    assert call("/ping")["app"] == browser.APP_NAME
    with pytest.raises(urllib.error.HTTPError):
        call("/ping", headers={})                                   # no X-JMDM header
    with pytest.raises(urllib.error.HTTPError):
        call("/ping", headers={**H, "Origin": "https://evil.example"})


def test_rules_sync_newest_wins(app):
    mgr, call = app
    s = call("/settings")
    assert s["browser_capture"] is True and "zip" in s["capture_types"].split()
    call("/settings", {"capture_updated": 0, "browser_capture": False})      # older: ignored
    assert call("/settings")["browser_capture"] is True
    r = call("/settings", {"capture_updated": time.time(), "browser_capture": False,
                           "capture_types": "zip", "capture_min_kb": 5,
                           "capture_exclude": "mega.nz"})
    assert (r["browser_capture"], r["capture_min_kb"], r["capture_exclude"]) == \
        (False, 5, "mega.nz")


def test_add_dialog_decides(app, server):
    mgr, call = app
    mgr.settings["ask_before_start"] = True
    job = {"url": server.url("/file/game.zip?size=50000"), "filename": "game.zip",
           "verify": True, "wait": True}
    answer_next(mgr, True)
    assert call("/add", job)["ok"] is True
    answer_next(mgr, False)
    assert call("/add", job)["ok"] is False


def test_no_answer_gives_it_back(app, server, monkeypatch):
    mgr, call = app
    monkeypatch.setattr(browser, "DECISION_WAIT", 1)
    job = {"url": server.url("/file/game.zip?size=50000"), "verify": True, "wait": True}
    assert call("/add", job)["ok"] is False
    late = mgr.incoming.get_nowait()
    assert browser.decision_expired(late) and browser.settle(late, True) is False


def test_without_asking_it_is_taken_at_once(app, server):
    mgr, call = app
    mgr.settings["ask_before_start"] = False
    job = {"url": server.url("/file/game.zip?size=50000"), "verify": True, "wait": True}
    assert call("/add", job)["ok"] is True
    assert mgr.incoming.get_nowait()["url"] == job["url"]


def test_bad_requests(app):
    _, call = app
    with pytest.raises(urllib.error.HTTPError):
        call("/add", {"url": "file:///etc/passwd"})
