"""HTTP connections that stay open between parts, and reading a file's size and name."""
import re
import urllib.request
import urllib.parse
import urllib.error
import http.server
import http.client
from .config import SOCKET_TIMEOUT, UA
from .util import filename_from_headers

class Link:
    """One worker's keep-alive HTTP(S) connection.

    * The socket (and its TLS session) stays open between parts, so taking a
      new part costs one request instead of a new TCP + TLS handshake.
    * The final URL after redirects is remembered per source, so later parts
      go straight to the file server instead of replaying the redirect chain
      (that's IDM's "connection replay"). If that URL stops working, it falls
      back to the original link once.
    """

    def __init__(self, dl):
        self.dl = dl
        self.conn = None
        self.key = None

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn, self.key = None, None

    def release(self):
        pass                                        # socket stays open for reuse

    def abort(self):
        """Called by the watchdog from another thread to break a stuck read."""
        try:
            sock = self.conn.sock if self.conn is not None else None
            if sock is not None:
                import socket
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def _connect(self, key):
        scheme, host, port = key
        if self.conn is not None and self.key == key:
            self.dl.conn_reused += 1
            return self.conn
        self.close()
        cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        kw = {"timeout": SOCKET_TIMEOUT}
        if scheme == "https":
            kw["context"] = _ssl_ctx()
        self.conn = cls(host, port, **kw)
        self.key = key
        self.dl.conn_opened += 1
        return self.conn

    def get(self, src, start=None, end=None):
        if _uses_proxy(src["url"]) or src.get("post"):
            return _UrllibResp(self.dl._open(src, start, end, timeout=SOCKET_TIMEOUT))
        headers = self.dl._headers(src)
        if start is not None:
            headers["Range"] = f"bytes={start}-" + ("" if end is None else str(end))
        url = src.get("resolved") or src["url"]
        used_cache = "resolved" in src
        for _hop in range(8):
            p = urllib.parse.urlsplit(url)
            key = (p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80))
            path = (p.path or "/") + ("?" + p.query if p.query else "")
            hdrs = dict(headers, Host=p.netloc)
            for attempt in (0, 1):
                conn = self._connect(key)
                try:
                    conn.request("GET", path, headers=hdrs)
                    resp = conn.getresponse()
                    break
                except (http.client.RemoteDisconnected, ConnectionResetError,
                        BrokenPipeError, http.client.CannotSendRequest,
                        http.client.BadStatusLine):
                    self.close()                    # stale keep-alive socket; reconnect once
                    if attempt:
                        raise
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                resp.read()
                if resp.will_close:
                    self.close()
                if not loc:
                    raise IOError("Redirect without a target")
                url = urllib.parse.urljoin(url, loc)
                continue
            if resp.status >= 400:
                resp.read(65536)
                self.close()
                if used_cache:                       # remembered URL expired: start over
                    src.pop("resolved", None)
                    url, used_cache = src["url"], False
                    continue
                raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.msg, None)
            src["resolved"] = url
            return resp
        raise IOError("Too many redirects")


class _UrllibResp:
    """Gives a urllib response the same small interface as http.client's."""

    def __init__(self, r):
        self.r = r
        self.status = r.getcode()
        self.will_close = True

    def getheader(self, name, default=None):
        return self.r.headers.get(name, default)

    def read(self, n=-1):
        return self.r.read(n)

    def close(self):
        self.r.close()


_SSL = None


def make_ssl_context(*args, **kwargs):
    """Windows' certificates plus the up-to-date list shipped with the app
    (certifi). Windows only keeps the certificates it has needed before and
    fetches others on demand, which Python can't do — without the bundled
    list some sites (e.g. GitHub's download servers) fail with
    CERTIFICATE_VERIFY_FAILED."""
    import ssl
    ctx = ssl.create_default_context(*args, **kwargs)
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def use_bundled_certificates():
    """Make every HTTPS connection in the app (urllib, http.client, yt-dlp's
    fallbacks) use make_ssl_context. Called once at startup."""
    import ssl
    ssl._create_default_https_context = make_ssl_context


def _ssl_ctx():
    global _SSL
    if _SSL is None:
        _SSL = make_ssl_context()
    return _SSL


def _uses_proxy(url):
    """If Windows/env proxy settings apply, let urllib handle the request."""
    try:
        proxies = urllib.request.getproxies()
        host = urllib.parse.urlsplit(url).hostname or ""
        scheme = urllib.parse.urlsplit(url).scheme
        return scheme in proxies and not urllib.request.proxy_bypass(host)
    except Exception:
        return False


def parse_probe(resp):
    """Read size / range support / file name from a 'bytes=0-' response."""
    code = resp.getcode()
    hdr = resp.headers
    cr = hdr.get("Content-Range") or ""
    size, resumable = -1, False
    if code == 206 and cr:
        m = re.search(r"/(\d+)", cr)
        if m:
            size, resumable = int(m.group(1)), True
    else:
        cl = hdr.get("Content-Length")
        if cl and cl.isdigit():
            size = int(cl)
        if (hdr.get("Accept-Ranges") or "").lower() == "bytes" and size > 0:
            resumable = True
    return {"size": size, "resumable": resumable and size > 0,
            "filename": filename_from_headers(hdr, resp.geturl()),
            "html": "text/html" in (hdr.get("Content-Type") or "").lower()}


def probe_info(url, referer="", cookie="", user_agent=""):
    """Quick look at a link without downloading it. Returns parse_probe() dict."""
    h = {"User-Agent": user_agent or UA, "Accept-Encoding": "identity",
         "Range": "bytes=0-"}
    if referer:
        h["Referer"] = referer
    if cookie:
        h["Cookie"] = cookie
    resp = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15)
    try:
        return parse_probe(resp)
    finally:
        resp.close()
