"""Whole seasons: finding numbered episode links and each episode's video."""
import re
import urllib.request
import urllib.parse
import urllib.error
import html.parser
from .config import UA
from .util import sanitize

class _PageScan(html.parser.HTMLParser):
    """Collects what a series or episode page contains, using only the
    standard library (no extra packages in the .exe)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []          # [href, text, attrs]
        self.media = []          # (url, where)
        self.iframes = []
        self.images = []         # (src, attrs)
        self.meta = {}
        self.forms = []          # {"action","method","attrs","fields":[(name,value)],"text"}
        self.title = ""
        self.h1 = ""
        self._a = None
        self._in = None
        self._form = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self._a = [a["href"], "", a]
            self.links.append(self._a)
        elif tag in ("video", "audio", "source"):
            for k in ("src", "data-src"):
                if a.get(k):
                    self.media.append((a[k], f"<{tag}>"))
        elif tag == "iframe" and (a.get("src") or a.get("data-src")):
            self.iframes.append(a.get("src") or a.get("data-src"))
        elif tag == "img" and (a.get("src") or a.get("data-src")):
            self.images.append((a.get("data-src") or a["src"], a))
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag in ("title", "h1"):
            self._in = tag
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get"),
                          "attrs": a, "fields": [], "text": "", "visible": 0}
            self.forms.append(self._form)
        elif tag in ("input", "button", "select", "textarea") and self._form is not None:
            typ = a.get("type", "submit" if tag == "button" else "text").lower()
            self._form["text"] += " " + " ".join(
                (a.get("name", ""), a.get("id", ""), a.get("class", ""), a.get("value", "")))
            if typ in ("text", "email", "password", "search", "number") or tag in ("select", "textarea"):
                self._form["visible"] += 1
            if a.get("name") and (typ not in ("submit", "button", "image", "reset",
                                               "checkbox", "radio") or "checked" in a
                                  or typ == "submit"):
                self._form["fields"].append((a["name"], a.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        if tag == "a":
            self._a = None
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._a is not None:
            self._a[1] += data
        if self._form is not None and len(self._form["text"]) < 2000:
            self._form["text"] += " " + data
        if self._in == "title":
            self.title += data
        elif self._in == "h1" and len(self.h1) < 200:
            self.h1 += data


_EP_TEXT = re.compile(r"^\s*(?:ep(?:isode)?\.?\s*|e|#)?\s*0*(\d{1,4})\s*$", re.I)
_EP_URL = re.compile(r"(?:[?&](?:ep|episode|e|eps|epi)=|(?:episode|ep)[-_/]?)0*(\d{1,4})(?:\D|$)",
                     re.I)
_STREAM_IN_TEXT = re.compile(r"""(?:https?:)?//[^\s"'<>\\]+?\.(?:m3u8|mpd|mp4|webm|mkv)"""
                             r"""(?:\?[^\s"'<>\\]*)?"""
                             r"""|(?<=["'])/[^\s"'<>\\]+?\.(?:m3u8|mpd|mp4|webm|mkv)"""
                             r"""(?:\?[^\s"'<>\\]*)?""", re.I)


def fetch_html(url, referer="", cookie="", user_agent="", timeout=30):
    h = {"User-Agent": user_agent or UA, "Accept": "text/html,*/*"}
    if referer:
        h["Referer"] = referer
    if cookie:
        h["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        raw = r.read(8 * 1024 * 1024)
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), r.geturl()


def scan_page(html_text):
    p = _PageScan()
    try:
        p.feed(html_text)
    except Exception:
        pass
    return p


def series_title(p):
    for t in (p.meta.get("og:title"), p.h1, p.title):
        t = re.sub(r"\s+", " ", (t or "")).strip()
        if t:
            # drop " - Watch online", " | MySite" style tails
            return sanitize(re.split(r"\s+[|–—-]\s+", t)[0]) or sanitize(t)
    return "Series"


def find_cover(p, base):
    if p.meta.get("og:image"):
        return urllib.parse.urljoin(base, p.meta["og:image"])
    for src, a in p.images:
        hint = " ".join((a.get("class", ""), a.get("id", ""), a.get("alt", ""), src)).lower()
        if any(w in hint for w in ("cover", "poster", "thumb", "banner")):
            return urllib.parse.urljoin(base, src)
    return None


def find_episodes(links, base):
    """Numbered episode links -> sorted [(number, url)].
    Matches link text like "1", "02", "Ep 3", "Episode 4", "E5", or URLs like
    watch.php?ep=6, /episode-7, ?episode=8 on the same site."""
    host = urllib.parse.urlsplit(base).hostname
    page = base.split("#")[0]
    groups = {}                        # URL pattern -> {number: url}
    hinted = set()                     # patterns whose links say "ep"/"episode"
    for href, text, attrs in links:
        url = urllib.parse.urljoin(base, href.strip()).split("#")[0]
        if not url.startswith(("http://", "https://")) or url == page:
            continue
        if urllib.parse.urlsplit(url).hostname != host:
            continue
        text = re.sub(r"\s+", " ", text or attrs.get("title", "")).strip()
        n = None
        m = _EP_TEXT.match(text)
        if m:
            n = int(m.group(1))
        else:
            m = _EP_URL.search(url)
            if m and (not text or re.search(r"\d|ep", text, re.I)):
                n = int(m.group(1))
        if n is None:
            continue
        pattern = re.sub(r"\d+", "#", url)
        groups.setdefault(pattern, {}).setdefault(n, url)
        if re.search(r"\bep|episode|^e\d", text, re.I) or _EP_URL.search(url):
            hinted.add(pattern)
    # Episode links share one URL pattern (watch.php?ep=1, ?ep=2 …). Pick the
    # most episode-like group, so stray numbered links such as a "2024" year
    # tag or comment pages "2", "3" never become episodes.
    good = [(p, g) for p, g in groups.items() if len(g) >= 3 or (p in hinted and len(g) >= 2)]
    if not good:
        return []
    pattern, best = max(good, key=lambda pg: (pg[0] in hinted, len(pg[1]), -min(pg[1])))
    return sorted(best.items())


def parse_items(spec, available):
    """'1-6,9' -> {1,2,3,4,5,6,9}; empty -> all."""
    if not spec.strip():
        return set(available)
    out = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        if a.strip().isdigit():
            out.update(range(int(a), int((b or a).strip() or a) + 1))
    return out


def find_episode_media(html_text, base, scan=None):
    """Best guess at the video on an episode page.
    Returns (url, kind) with kind "file", "stream" or "page" (yt-dlp)."""
    p = scan or scan_page(html_text)
    cands = [urllib.parse.urljoin(base, u) for u, _ in p.media]
    cands += [urllib.parse.urljoin(base, m.group(0)) for m in _STREAM_IN_TEXT.finditer(html_text)]
    for key in ("og:video", "og:video:url", "og:video:secure_url"):
        if p.meta.get(key):
            cands.append(urllib.parse.urljoin(base, p.meta[key]))
    cands = [c for c in cands if c.startswith(("http://", "https://"))]
    for c in cands:                                   # direct files first: fastest
        if re.search(r"\.(mp4|webm|mkv|m4v|mov)(\?|$)", c, re.I):
            return c, "file"
    for c in cands:
        if re.search(r"\.(m3u8|mpd)(\?|$)", c, re.I):
            return c, "stream"
    for f in p.iframes:                               # embedded player
        u = urllib.parse.urljoin(base, f)
        if u.startswith(("http://", "https://")):
            return u, "page"
    return None, None
