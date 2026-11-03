"""Gate links (gate.php, go.php …): following them to the real file."""
import re
import urllib.request
import urllib.parse
import urllib.error
import html.parser
import base64
from .config import FILE_EXTS, UA
from .util import is_video_url
from .net import parse_probe
from .series import scan_page

GATE_PARAMS = {"url", "u", "link", "l", "go", "goto", "target", "dest", "destination",
               "to", "out", "redirect", "redir", "r", "file", "f", "dl", "download",
               "q", "data", "s", "site", "href", "path", "src"}
_GATE_NAME = re.compile(r"/(?:gate|go|goto|out|dl|down|download|get|getfile|file|files|"
                        r"redirect|redir|link|links|safe|safelink|r|away|jump|click|"
                        r"track|external|leave|forward|fwd|ref|visit|exit)\.php$", re.I)
_META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*?content\s*=\s*["']\s*\d*\s*[;,]?\s*"""
    r"""url\s*=\s*['"]?([^"'>\s]+)"""
    r"""|<meta[^>]+content\s*=\s*["']\s*\d*\s*[;,]?\s*url\s*=\s*['"]?([^"'>\s]+)['"]?[^>]*"""
    r"""http-equiv\s*=\s*["']?refresh""", re.I)
_JS_REDIRECT = re.compile(
    r"""(?:(?:window|document|top|self|parent)\.)?location(?:\.href)?\s*=\s*(["'`])(.+?)\1"""
    r"""|location\.(?:replace|assign)\(\s*(["'`])(.+?)\3\s*\)"""
    r"""|window\.open\(\s*(["'`])(https?://.+?)\5""", re.I)
_CAPTCHA = re.compile(r"g-recaptcha|h-captcha|hcaptcha\.com|cf-turnstile|"
                      r"challenges\.cloudflare\.com|name=[\"']?captcha", re.I)
_FILE_IN_TEXT = re.compile(r"""https?://[^\s"'<>\\]+?\.(?:%s)(?:\?[^\s"'<>\\]*)?(?=["'\s<\\]|$)"""
                           % "|".join(sorted(FILE_EXTS, key=len, reverse=True)), re.I)


def _decode_target(value):
    """A link hidden in a query value: plain/URL-encoded, base64 (also
    URL-safe or reversed) or hex. Returns the link or None."""
    v = urllib.parse.unquote(value or "").strip()
    if re.match(r"^https?://\S+$", v, re.I):
        return v
    if v.startswith("//") and "." in v[2:]:
        return "https:" + v
    s = re.sub(r"\s", "", v)
    if len(s) >= 12 and re.fullmatch(r"[A-Za-z0-9+/_=-]+", s):
        for cand in (s, s[::-1]):
            c = cand.rstrip("=").replace("-", "+").replace("_", "/")
            try:
                t = base64.b64decode(c + "=" * (-len(c) % 4)).decode("utf-8").strip()
            except Exception:
                continue
            if re.match(r"^https?://\S+$", t, re.I):
                return t
            if t != v and re.match(r"^(?:https?%3A|aHR0c)", t, re.I):
                return _decode_target(t)              # encoded twice
    if len(s) >= 20 and len(s) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", s):
        try:
            t = bytes.fromhex(s).decode("utf-8").strip()
            if re.match(r"^https?://\S+$", t, re.I):
                return t
        except Exception:
            pass
    return None


def gate_target(url):
    """The real link carried inside a gate link (?url=…, base64 …), or None."""
    best = None
    for part in urllib.parse.urlsplit(url).query.split("&"):
        k, _, v = part.partition("=")
        t = _decode_target(v)
        if t and t != url:
            if k.lower() in GATE_PARAMS:
                return t
            best = best or t
    return best


def _script_name(name):
    """gate.php, download.aspx … : the name of the script, not of the file."""
    return bool(re.fullmatch(r"(?i)[\w .()-]+\.(?:php\d?|aspx?|jsp|cgi|pl)", name or ""))


def is_gate_url(url):
    """gate.php / go.php / out.php style links, or any .php link that carries
    another link inside it."""
    p = urllib.parse.urlsplit(url)
    if _GATE_NAME.search(p.path):
        return True
    return p.path.lower().endswith(".php") and gate_target(url) is not None


class _Jar:
    """Minimal cookie jar: gates often set a session cookie on the redirect
    that the file server then checks."""

    def __init__(self, host, cookie):
        self.c = {}
        for part in (cookie or "").split(";"):
            n, _, v = part.strip().partition("=")
            if n:
                self.c.setdefault((host or "").lower(), {})[n] = v

    def absorb(self, host, headers):
        try:
            items = headers.get_all("Set-Cookie") or []
        except Exception:
            items = []
        for sc in items:
            first, *attrs = sc.split(";")
            n, _, v = first.partition("=")
            n = n.strip()
            if not n:
                continue
            dom, gone = (host or "").lower(), False
            for at in attrs:
                k, _, val = at.strip().partition("=")
                k = k.lower()
                if k == "domain" and val.strip():
                    dom = val.strip().lstrip(".").lower()
                elif k == "max-age" and re.fullmatch(r"-?\d+", val.strip()) \
                        and int(val.strip()) <= 0:
                    gone = True
            jar = self.c.setdefault(dom, {})
            if gone:
                jar.pop(n, None)
            else:
                jar[n] = v.strip()

    def header(self, host):
        host, out = (host or "").lower(), {}
        for dom, kv in self.c.items():
            if host == dom or host.endswith("." + dom):
                out.update(kv)
        return "; ".join(f"{k}={v}" for k, v in out.items())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None                                  # we follow them ourselves


_GATE_OPENER = None


def _gate_opener():
    global _GATE_OPENER
    if _GATE_OPENER is None:
        _GATE_OPENER = urllib.request.build_opener(_NoRedirect)
    return _GATE_OPENER


def _next_hop(text, page_url, came_from_url):
    """Where a gate page sends you. Returns (url, method, body) or None.
    Order: the page's own redirect, then the link packed into the gate URL,
    then the page's Download link/button (same site first, so an ad's
    "Download" button elsewhere on the page loses)."""
    def ok(u):
        u = urllib.parse.urljoin(page_url, html.unescape(u.strip()).replace("\\/", "/"))
        u = u.split("#")[0]
        if u.startswith(("http://", "https://")) and u != page_url:
            return u
        return None

    m = _META_REFRESH.search(text)
    if m and ok(m.group(1) or m.group(2)):
        return ok(m.group(1) or m.group(2)), "GET", None
    for m in _JS_REDIRECT.finditer(text):
        target = m.group(2) or m.group(4) or m.group(6) or ""
        if "+" in target and re.search(r"['\"`]\s*\+", m.group(0)):
            continue                                  # built from variables
        if ok(target):
            return ok(target), "GET", None

    packed = gate_target(page_url)
    if packed and packed != page_url and packed != came_from_url:
        return packed, "GET", None

    p = scan_page(text)
    here = urllib.parse.urlsplit(page_url).hostname
    links = []
    for href, label, attrs in p.links:
        u = ok(href) if not href.lower().startswith(("javascript:", "mailto:", "#")) else None
        if u:
            links.append((u, re.sub(r"\s+", " ", label).strip(), attrs))
    links.sort(key=lambda x: urllib.parse.urlsplit(x[0]).hostname != here)
    # 1) a link straight to a file (.zip, .exe, .mp4 …)
    for u, label, attrs in links:
        ext = urllib.parse.urlsplit(u).path.rsplit(".", 1)[-1].lower()
        if ext in FILE_EXTS or "download" in attrs:
            return u, "GET", None
    # 2) the page's Download button
    for u, label, attrs in links:
        hint = " ".join((label, attrs.get("id", ""), attrs.get("class", ""),
                         attrs.get("title", ""))).lower()
        if re.search(r"\bdownload|\bdl\b|get (?:the )?file|direct link|click here to (?:download|continue)", hint) \
                and u != came_from_url:
            return u, "GET", None
    # 3) a download form with only hidden fields (a plain button, no captcha)
    for f in p.forms:
        hint = (f["text"] + " " + " ".join(f["attrs"].values())).lower()
        if f["visible"] == 0 and re.search(r"download|\bdl\b|\bget\b|continue|proceed", hint):
            action = ok(f["action"]) if f["action"] else page_url
            if not action:
                continue
            data = urllib.parse.urlencode(f["fields"]).encode()
            if f["method"].lower() == "post":
                return action, "POST", data
            sep = "&" if "?" in action else "?"
            return action + (sep + data.decode() if data else ""), "GET", None
    # 4) a file link written into the page's scripts
    m = _FILE_IN_TEXT.search(text)
    if m and ok(m.group(0)):
        return ok(m.group(0)), "GET", None
    return None


def resolve_link(url, referer="", cookie="", user_agent="", max_hops=10,
                 post=None, keep_response=False):
    """Follow a link until it reaches the actual file. Handles plain HTTP
    redirects (keeping cookies set along the way, which gates often need),
    meta-refresh and JavaScript redirects, the real link packed into a
    gate.php?url=… link (plain, base64 or hex), and gate pages with one
    Download link or button. Returns
    {"url", "referer", "cookie", "info", "via_page"}, where info is
    parse_probe() of the file response. Timers and captchas are not bypassed:
    those pages raise an error asking you to use the browser.
    `post` = form data (urlencoded text) the browser sent when you clicked a
    site's Download button; the gate is then asked the same way. If the file
    comes straight back in answer to that form, keep_response=True returns
    the open response as "response" (it can't be asked for twice)."""
    start, start_referer = url, referer
    jar = _Jar(urllib.parse.urlsplit(url).hostname, cookie)
    method, body = ("POST", post.encode()) if post else ("GET", None)
    seen, hops, via_page, came_from = set(), 0, bool(post), ""
    tried_referer = bool(referer)
    while True:
        key = (method, url, body)
        if key in seen or hops > max_hops:
            raise IOError("The link keeps redirecting without reaching a file. Open it "
                          "in your browser and click Download — the extension will "
                          "catch the real file.")
        seen.add(key)
        host = urllib.parse.urlsplit(url).hostname
        h = {"User-Agent": user_agent or UA, "Accept": "*/*",
             "Accept-Encoding": "identity"}
        if body is None:
            h["Range"] = "bytes=0-"
        else:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        if referer:
            h["Referer"] = referer
        c = jar.header(host)
        if c:
            h["Cookie"] = c
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            resp = _gate_opener().open(req, timeout=20)
        except urllib.error.HTTPError as e:
            jar.absorb(host, e.headers)
            loc = e.headers.get("Location") if e.headers else None
            if e.code in (301, 302, 303, 307, 308) and loc:
                e.close()
                if e.code in (301, 302, 303):
                    method, body = "GET", None
                url = urllib.parse.urljoin(url, loc.strip())
                hops += 1
                continue
            if e.code in (403, 404) and not tried_referer:
                # many gate.php scripts answer 404/403 unless you come from their site
                tried_referer = True
                p0 = urllib.parse.urlsplit(url)
                referer = f"{p0.scheme}://{p0.netloc}/"
                seen.discard(key)
                e.close()
                continue
            if e.code in (403, 429, 503) and hops and _CAPTCHA.search(
                    e.read(65536).decode("utf-8", "replace")):
                raise IOError("This site wants a captcha / browser check first. Open the "
                              "link in your browser, finish it and click Download — the "
                              "extension will catch the real file.")
            raise
        jar.absorb(host, resp.headers)
        info = parse_probe(resp)
        cd = (resp.headers.get("Content-Disposition") or "").lower()
        if not info["html"] or "attachment" in cd:
            info["html"] = False
            if body is not None and keep_response:
                info["resumable"] = False            # a form answer can't be split
                return {"url": url, "referer": referer, "cookie": jar.header(host),
                        "info": info, "via_page": True, "response": resp,
                        "post": body.decode()}
            resp.close()
            if body is not None:
                raise IOError("This site only sends the file in answer to a form, which "
                              "can't be split into parts. Open the link in your browser "
                              "and click Download — the extension will catch it.")
            final_cookie = jar.header(host)
            return {"url": url, "referer": referer, "cookie": final_cookie,
                    "info": info,
                    # True when the plain link alone won't work again: it needed a
                    # gate page, or cookies the gate handed out on the way
                    "via_page": via_page or referer != start_referer
                                or (url != start and final_cookie != (cookie or ""))}
        raw = resp.read(2 * 1024 * 1024)
        resp.close()
        text = raw.decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        nxt = _next_hop(text, url, came_from)
        if nxt is None:
            if _CAPTCHA.search(text):
                raise IOError("This page asks for a captcha first. Open the link in your "
                              "browser, solve it and click Download — the extension will "
                              "catch the real file.")
            raise IOError("This link opens a web page, not a file. Open it in your "
                          "browser and click Download there — the extension will "
                          "catch the real file.")
        came_from, referer = url, url              # browsers send the page as Referer
        url, method, body = nxt
        via_page = True
        hops += 1

def looks_like_download(text):
    if not re.match(r"^https?://\S+$", text or ""):
        return False
    if is_video_url(text) and ("watch" in text or "youtu.be" in text
                               or "/videos/" in text or "/reel" in text):
        return True
    if is_gate_url(text):
        return True
    path = urllib.parse.urlparse(text).path.lower()
    return path.rsplit(".", 1)[-1] in FILE_EXTS if "." in path else False
