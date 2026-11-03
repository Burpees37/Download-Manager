"""Shared test fixtures: a local web server that behaves like the hosts the
app meets in real life (range support or not, slow or stalling connections,
redirects, gate pages), and a Manager that never touches your real settings."""
import hashlib
import http.server
import os
import sys
import threading
import time
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jmdm_app import manager  # noqa: E402


def content(size, seed="file"):
    """Deterministic bytes, so a download can be checked byte-for-byte."""
    out = bytearray()
    block = 0
    while len(out) < size:
        out += hashlib.sha256(f"{seed}:{block}".encode()).digest() * 64
        block += 1
    return bytes(out[:size])


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- helpers ---------------------------------------------------------
    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _file(self, q):
        srv = self.server
        size = int(q.get("size", ["1048576"])[0])
        seed = q.get("seed", ["file"])[0]
        ranges = q.get("ranges", ["1"])[0] == "1"
        cap = int(q.get("cap", ["0"])[0])            # bytes/s per connection
        stall = q.get("stall", ["0"])[0] == "1"     # the first connection hangs
        need_cookie = q.get("cookie", [""])[0]
        name = q.get("name", [""])[0]
        if need_cookie and need_cookie not in (self.headers.get("Cookie") or ""):
            return self._send(403, b"no cookie")
        data = content(size, seed)
        start, end = 0, size - 1
        code = 200
        rng = self.headers.get("Range")
        if ranges and rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a)
            end = int(b) if b else size - 1
            end = min(end, size - 1)
            code = 206
        headers = {"Content-Type": "application/octet-stream"}
        if ranges:
            headers["Accept-Ranges"] = "bytes"
        if code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if name:
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        with srv.lock:
            srv.hits += 1
            first = srv.hits == 1
            srv.ranges_seen.append(rng)
        body = data[start:end + 1]
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "HEAD":
            return
        if stall and first and code == 206 and start > 0:
            time.sleep(30)                          # a dead connection
            return
        step = 16384
        for i in range(0, len(body), step):
            try:
                self.wfile.write(body[i:i + step])
            except (BrokenPipeError, ConnectionResetError):
                return
            if cap:
                time.sleep(step / cap)

    # ---- routes ----------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path.startswith("/file"):
            return self._file(q)
        if u.path == "/redirect":                   # 302 that hands out a session cookie
            target = q["to"][0]
            return self._send(302, b"", {"Location": target,
                                         "Set-Cookie": "session=abc123; Path=/"})
        if u.path == "/meta":
            html = (f'<html><head><meta http-equiv="refresh" content="0;url={q["to"][0]}">'
                    "</head><body>Wait…</body></html>").encode()
            return self._send(200, html, {"Content-Type": "text/html"})
        if u.path == "/js":
            html = (f'<html><body><script>window.location.href = "{q["to"][0]}";'
                    "</script></body></html>").encode()
            return self._send(200, html, {"Content-Type": "text/html"})
        if u.path == "/page":
            return self._send(200, self.server.pages.get(q["p"][0], b"").encode(),
                              {"Content-Type": "text/html"})
        if u.path == "/blob":
            body = self.server.blobs.get(q["b"][0], b"")
            return self._send(200, body, {"Content-Type": "application/octet-stream"})
        self._send(404, b"not found")


class TestServer:
    def __init__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.lock = threading.Lock()
        self.httpd.hits = 0
        self.httpd.ranges_seen = []
        self.httpd.pages = {}
        self.httpd.blobs = {}
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        return self.base + path

    @property
    def hits(self):
        return self.httpd.hits

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = TestServer()
    yield s
    s.close()


@pytest.fixture
def server2():
    s = TestServer()
    yield s
    s.close()


@pytest.fixture
def mgr(tmp_path):
    m = manager.Manager(data_dir=str(tmp_path / "data"), persist=False)
    m.settings["download_dir"] = str(tmp_path / "dl")
    yield m
    m.shutdown()


def wait_for(d, statuses=("Complete", "Error"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if d.status in statuses:
            return d.status
        time.sleep(0.1)
    raise AssertionError(f"timed out; status={d.status} error={d.error}")
