#!/usr/bin/env python3
"""
JM Download Manager — an IDM-style download accelerator.

How it beats per-connection speed caps:
  * Each file is split into parts that download in parallel over separate
    HTTP connections (HTTP Range requests).
  * Dynamic segmentation: when a connection finishes its part, it takes over
    half of the biggest part that is still downloading, so every connection
    stays busy until the very end (this is IDM's main trick).
  * Pause / resume, even after closing the app or rebooting.

Also:
  * Video sites (YouTube, Facebook, TikTok, ...) through yt-dlp, with
    parallel fragment downloads.
  * A local listener on 127.0.0.1 that the browser extension sends
    downloads to (with your cookies and referrer, so links from file hosts work).
  * Download queue, global speed limiter, clipboard watcher.

Run:   python jmdm.py                -> GUI
       python jmdm.py <url>          -> GUI with the Add dialog filled in
       python jmdm.py --cli <url> [-o folder] [-n connections] [--limit KB/s]
"""
import os
import sys
import re
import json
import time
import uuid
import queue
import shutil
import threading
import urllib.request
import urllib.parse
import urllib.error
import http.server
import http.client

APP_NAME = "JM Download Manager"
VERSION = "1.3.0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CHUNK = 64 * 1024
MIN_SPLIT = 256 * 1024          # never split a part smaller than 2x this
MAX_CONNECTIONS = 32            # per host
MAX_TOTAL_CONNECTIONS = 96      # across all mirrors of one file
STALL_SECONDS = 5               # no data this long = reconnect
SOCKET_TIMEOUT = 30
LISTEN_PORT = 8721
NATIVE_HOST = "com.jmbuilds.jmdm"
CHROME_EXTENSION_ID = "lmaohoejkhjffbfcngbcjhidlcefefdi"     # fixed by the "key" in extension/manifest.json
FIREFOX_EXTENSION_ID = "jmdm@jmbuilds"

VIDEO_SITES = ("youtube.com", "youtu.be", "facebook.com", "fb.watch", "fb.com",
               "tiktok.com", "instagram.com", "twitter.com", "x.com",
               "vimeo.com", "dailymotion.com", "twitch.tv", "reddit.com",
               "bilibili.com", "soundcloud.com")

FILE_EXTS = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "exe", "msi", "iso",
             "img", "bin", "apk", "xapk", "dmg", "deb", "rpm", "jar", "mp4",
             "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "mp3", "m4a",
             "flac", "wav", "aac", "ogg", "pdf", "epub", "docx", "xlsx", "pptx",
             "psd", "torrent", "001", "002", "part1"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_download_dir():
    if os.name == "nt" and os.path.isdir("D:\\"):
        return "D:\\Downloads"
    return os.path.join(os.path.expanduser("~"), "Downloads")


def human_size(n):
    if n is None or n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0


def human_time(sec):
    if sec is None or sec <= 0 or sec == float("inf"):
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return name[:200] or "download"


def filename_from_headers(headers, url):
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I)
    if m:
        return sanitize(urllib.parse.unquote(m.group(1).strip('"')))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or \
        re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return sanitize(m.group(1).strip())
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
    return sanitize(name) if name else "download"


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    i = 1
    while os.path.exists(path) or os.path.exists(path + ".part"):
        path = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return path


def is_video_url(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in VIDEO_SITES)


def is_playlist_url(url):
    """True for links that point at a list of videos (YouTube playlist/channel)."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if not (host.endswith("youtube.com") or host == "youtu.be"):
        return False
    q = urllib.parse.parse_qs(p.query)
    return ("list" in q or p.path.startswith("/playlist")
            or bool(re.match(r"^/(@[^/]+|c/[^/]+|channel/[^/]+)(/(videos|shorts|streams))?/?$",
                             p.path)))


def is_whole_playlist_url(url):
    """A playlist page itself (not a single video that happens to sit in a list)."""
    p = urllib.parse.urlparse(url)
    return is_playlist_url(url) and "v=" not in p.query and not p.path.startswith("/watch")


def playlist_url(url):
    """Link to hand yt-dlp for a list. A watch?v=..&list=.. link is kept as is,
    because YouTube Mixes (list=RD...) only work in that form."""
    return url


def clean_err(e):
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "").strip()
    m = re.match(r"<urlopen error (?:\[Errno -?\d+\] )?(.*)>$", msg)
    return m.group(1) if m else msg


def looks_like_download(text):
    if not re.match(r"^https?://\S+$", text or ""):
        return False
    if is_video_url(text) and ("watch" in text or "youtu.be" in text
                               or "/videos/" in text or "/reel" in text):
        return True
    path = urllib.parse.urlparse(text).path.lower()
    return path.rsplit(".", 1)[-1] in FILE_EXTS if "." in path else False


def find_ffmpeg():
    local = os.path.join(app_dir(), "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.path.isfile(local):
        return local
    return shutil.which("ffmpeg")


def have_ytdlp():
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def register_browser_integration():
    """Register the native messaging helper with Chrome, Edge, Brave and
    Firefox (current user only, no admin needed) so the extension can start
    the app. Safe to run on every launch; it also fixes paths if the app moved."""
    if os.name != "nt":
        return False, "Only needed on Windows"
    import winreg
    data = os.path.join(app_dir(), "jmdm_data")
    os.makedirs(data, exist_ok=True)
    if getattr(sys, "frozen", False):
        host = os.path.join(app_dir(), "jmdm-host.exe")
        if not os.path.isfile(host):
            return False, "jmdm-host.exe is missing next to the app"
    else:
        host = os.path.join(data, "jmdm-host.bat")
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            py = py[:-11] + "python.exe"
        with open(host, "w", encoding="utf-8") as fh:
            fh.write(f'@echo off\n"{py}" "{os.path.join(app_dir(), "native_host.py")}" %*\n')
    base = {"name": NATIVE_HOST, "description": "Starts JM Download Manager",
            "path": host, "type": "stdio"}
    chrome_json = os.path.join(data, "native_host_chrome.json")
    firefox_json = os.path.join(data, "native_host_firefox.json")
    with open(chrome_json, "w", encoding="utf-8") as fh:
        json.dump(dict(base, allowed_origins=[f"chrome-extension://{CHROME_EXTENSION_ID}/"]),
                  fh, indent=2)
    with open(firefox_json, "w", encoding="utf-8") as fh:
        json.dump(dict(base, allowed_extensions=[FIREFOX_EXTENSION_ID]), fh, indent=2)
    keys = [(r"Software\Google\Chrome\NativeMessagingHosts", chrome_json),
            (r"Software\Microsoft\Edge\NativeMessagingHosts", chrome_json),
            (r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts", chrome_json),
            (r"Software\Mozilla\NativeMessagingHosts", firefox_json)]
    done = 0
    for key, path in keys:
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + "\\" + NATIVE_HOST) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, path)
            done += 1
        except OSError:
            pass
    return done > 0, f"Registered with {done} browser profile location(s)"


class RateLimiter:
    """Global token bucket shared by every connection. rate = bytes/sec, 0 = off."""

    def __init__(self):
        self.rate = 0
        self._lock = threading.Lock()
        self._allow = 0.0
        self._last = time.monotonic()

    def consume(self, n, stop_event=None):
        while self.rate > 0:
            with self._lock:
                now = time.monotonic()
                self._allow = min(float(self.rate),
                                  self._allow + (now - self._last) * self.rate)
                self._last = now
                if self._allow > 0:
                    self._allow -= n
                    return
                wait = -self._allow / self.rate + 0.01
            if stop_event is not None and stop_event.wait(min(wait, 0.5)):
                return
            if stop_event is None:
                time.sleep(min(wait, 0.5))


# --------------------------------------------------------------------------
# segmented HTTP download
# --------------------------------------------------------------------------
class Segment:
    __slots__ = ("start", "end", "pos", "owner", "rate", "last_data", "started",
                 "kick", "kicked_at", "link")

    def __init__(self, start, end, pos=None):
        self.start = start
        self.end = end              # inclusive; may shrink when another connection steals half
        self.pos = start if pos is None else pos
        self.owner = False
        self.rate = 0.0             # this part's current speed (bytes/s)
        self.last_data = self.started = time.monotonic()
        self.kick = False           # watchdog asks the connection to reconnect
        self.kicked_at = 0.0
        self.link = None            # the connection currently filling this part

    def remaining(self):
        return max(0, self.end - self.pos + 1)


class Download:
    """One file, downloaded in parts over many connections — optionally from
    several mirrors (different hosts serving the same file) at the same time.
    Each host caps you separately, so every extra mirror adds its full speed."""
    kind = "file"

    def __init__(self, manager, url, folder, filename="", connections=8,
                 referer="", cookie="", user_agent="", id=None, mirrors=None):
        self.m = manager
        self.id = id or uuid.uuid4().hex[:12]
        self.url = url
        self.folder = folder
        self.filename = filename
        self.path = ""
        self.size = None            # None = not probed yet, -1 = unknown
        self.resumable = False
        self.connections = max(1, min(MAX_CONNECTIONS, int(connections)))
        self.referer = referer
        self.cookie = cookie
        self.user_agent = user_agent
        # extra hosts for the same file: [{"url","referer","cookie","user_agent"}]
        self.mirrors = [dict(m) for m in (mirrors or [])]
        self.status = "Queued"
        self.error = ""
        self.segments = []
        self.downloaded = 0
        self.added = time.time()
        self.speed = 0.0
        self.active_conns = 0
        self.hosts_active = 0
        self.note = ""              # e.g. "1 mirror skipped: different file size"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._threads = []
        self._accepting = False
        self._sources = []
        self._f = None
        self._last_error = ""
        self._sample = (time.monotonic(), 0)
        self.conn_opened = 0        # stats: new TCP/TLS connections
        self.conn_reused = 0        # stats: parts that reused an open connection
        self.splits = 0             # stats: dynamic splits
        self.kicks = 0              # stats: slow/stalled connections replaced

    # ---- persistence -----------------------------------------------------
    def to_dict(self):
        with self._lock:
            segs = [[s.start, s.end, s.pos] for s in self.segments]
        return {"kind": self.kind, "id": self.id, "url": self.url,
                "folder": self.folder, "filename": self.filename,
                "path": self.path, "size": self.size,
                "resumable": self.resumable, "connections": self.connections,
                "referer": self.referer, "cookie": self.cookie,
                "user_agent": self.user_agent, "mirrors": self.mirrors,
                "status": self.status, "error": self.error, "segments": segs,
                "downloaded": self.downloaded, "added": self.added}

    @classmethod
    def from_dict(cls, manager, d):
        obj = cls(manager, d["url"], d["folder"], d.get("filename", ""),
                  d.get("connections", 8), d.get("referer", ""),
                  d.get("cookie", ""), d.get("user_agent", ""), id=d["id"],
                  mirrors=d.get("mirrors", []))
        obj.path = d.get("path", "")
        obj.size = d.get("size")
        obj.resumable = d.get("resumable", False)
        obj.status = d.get("status", "Paused")
        if obj.status in ("Downloading", "Processing"):
            obj.status = "Paused"
        obj.error = d.get("error", "")
        obj.segments = [Segment(a, b, p) for a, b, p in d.get("segments", [])]
        obj.downloaded = d.get("downloaded", 0)
        obj.added = d.get("added", time.time())
        return obj

    # ---- control ---------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.status = "Downloading"
        self.error = ""
        self._last_error = ""
        self._stop.clear()
        self._sample = (time.monotonic(), self.downloaded)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        if self.status == "Downloading":
            self._stop.set()
        elif self.status == "Queued":
            self.status = "Paused"

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def update_speed(self):
        now = time.monotonic()
        t0, b0 = self._sample
        dt = now - t0
        if self.status != "Downloading":
            self.speed = 0.0
            self._sample = (now, self.downloaded)
            return
        if dt < 0.3:
            return
        inst = max(0.0, (self.downloaded - b0) / dt)
        self.speed = inst if self.speed == 0 else self.speed * 0.6 + inst * 0.4
        self._sample = (now, self.downloaded)

    def eta(self):
        if self.speed > 0 and self.size and self.size > 0:
            return (self.size - self.downloaded) / self.speed
        return None

    def segments_snapshot(self):
        with self._lock:
            return [(s.start, s.end, s.pos, s.owner) for s in self.segments]

    def add_mirror(self, url, referer="", cookie="", user_agent=""):
        """Add another host for this file. Works while it is downloading too."""
        m = {"url": url, "referer": referer, "cookie": cookie, "user_agent": user_agent}
        if url == self.url or any(x["url"] == url for x in self.mirrors):
            return False
        self.mirrors.append(m)
        if self.status == "Downloading" and self.resumable:
            threading.Thread(target=self._attach_source, args=(m,), daemon=True).start()
        return True

    # ---- network ---------------------------------------------------------
    def _main_source(self):
        return {"url": self.url, "referer": self.referer, "cookie": self.cookie,
                "user_agent": self.user_agent}

    def _headers(self, src):
        h = {"User-Agent": src.get("user_agent") or UA, "Accept": "*/*",
             "Accept-Encoding": "identity", "Connection": "keep-alive"}
        if src.get("referer"):
            h["Referer"] = src["referer"]
        if src.get("cookie"):
            h["Cookie"] = src["cookie"]
        return h

    def _open(self, src, start=None, end=None, timeout=30):
        req = urllib.request.Request(src["url"], headers=self._headers(src))
        if start is not None:
            req.add_header("Range", f"bytes={start}-" + ("" if end is None else str(end)))
        return urllib.request.urlopen(req, timeout=timeout)

    def _probe(self):
        resp = self._open(self._main_source(), 0, None)
        try:
            info = parse_probe(resp)
            if info["html"] and not self.filename:
                raise IOError("This link opens a web page, not a file. Open it in your "
                              "browser and click Download there — the extension will "
                              "catch the real file.")
            self.size = info["size"]
            self.resumable = info["resumable"]
            if not self.path:
                name = sanitize(self.filename) if self.filename else info["filename"]
                os.makedirs(self.folder, exist_ok=True)
                self.path = unique_path(self.folder, name)
                self.filename = os.path.basename(self.path)
            self.segments = []
        finally:
            resp.close()

    def _check_mirror(self, m):
        """A mirror is only used if it serves exactly the same size with ranges."""
        resp = self._open(m, 0, None, timeout=20)
        try:
            info = parse_probe(resp)
        finally:
            resp.close()
        if not info["resumable"]:
            raise IOError("host doesn't allow split downloads")
        if info["size"] != self.size:
            raise IOError(f"different file size ({human_size(info['size'])})")
        return True

    def _attach_source(self, m):
        try:
            self._check_mirror(m)
        except Exception as e:
            m["error"] = clean_err(e)
            self.note = f"Mirror skipped: {m['error']}"
            return
        m.pop("error", None)
        src = dict(m, bad=False, active=0)
        with self._lock:
            if not self._accepting:
                return
            self._sources.append(src)
            room = MAX_TOTAL_CONNECTIONS - len([t for t in self._threads if t.is_alive()])
        for _ in range(max(0, min(self.connections, room))):
            if not self._spawn(src):
                break
            time.sleep(0.05)

    def _spawn(self, src):
        t = threading.Thread(target=self._worker, args=(src,), daemon=True)
        with self._lock:
            if not self._accepting:
                return False
            self._threads.append(t)
        t.start()
        return True

    def _init_segments(self):
        if self.resumable:
            n = max(1, min(self.connections * (1 + len(self.mirrors)),
                           self.size // MIN_SPLIT or 1))
            part = self.size // n
            self.segments = [Segment(i * part,
                                     (i + 1) * part - 1 if i < n - 1 else self.size - 1)
                             for i in range(n)]
        else:
            end = self.size - 1 if self.size and self.size > 0 else 2 ** 62
            self.segments = [Segment(0, end)]
        self.downloaded = 0

    def _run(self):
        try:
            os.makedirs(self.folder, exist_ok=True)
            if self.size is None or not self.path:
                self._probe()
            part = self.path + ".part"
            if not os.path.exists(part) or not self.resumable or not self.segments:
                self._init_segments()
                with open(part, "wb") as fh:
                    if self.resumable:
                        fh.truncate(self.size)      # pre-allocate
            self._f = open(part, "r+b")
            self._threads = []
            self._sources = [dict(self._main_source(), bad=False, active=0)]
            self.note = ""
            with self._lock:
                self._accepting = True
            first = self.connections if self.resumable else 1
            for _ in range(first):
                self._spawn(self._sources[0])
                time.sleep(0.05)                    # stagger connection opening
            if self.resumable:
                for m in self.mirrors:
                    threading.Thread(target=self._attach_source, args=(m,),
                                     daemon=True).start()
            # wait for every connection, including ones added by mirrors later
            while True:
                with self._lock:
                    alive = [t for t in self._threads if t.is_alive()]
                    if not alive:
                        self._accepting = False
                        break
                alive[0].join(0.5)
                self._watchdog()
            self._f.flush()
            self._f.close()
            self._f = None
            if self._stop.is_set():
                self.status = "Paused"
                return
            remaining = sum(s.remaining() for s in self.segments)
            if remaining == 0:
                if not self.size or self.size < 0:
                    self.size = self.downloaded
                os.replace(part, self.path)
                self.segments = []
                self.status = "Complete"
            else:
                self.status = "Error"
                self.error = self._last_error or "Download stopped unexpectedly"
        except urllib.error.HTTPError as e:
            self.status, self.error = "Error", f"HTTP {e.code} {e.reason}"
        except Exception as e:
            self.status, self.error = "Error", clean_err(e)
        finally:
            with self._lock:
                self._accepting = False
            if self._f:
                try:
                    self._f.close()
                except Exception:
                    pass
                self._f = None
            self.active_conns = 0
            self.hosts_active = 0
            self.speed = 0.0
            self.m.mark_dirty()

    def _take_segment(self):
        with self._lock:
            for s in self.segments:
                if not s.owner and s.remaining() > 0:
                    s.owner = True
                    s.rate, s.last_data, s.kicked_at = 0.0, time.monotonic(), s.kicked_at
                    return s
            if not self.resumable:
                return None
            # Dynamic segmentation driven by network conditions: help the part
            # that will take LONGEST to finish (remaining bytes / its own speed),
            # and split it so both halves should finish at the same moment.
            active = [s for s in self.segments if s.owner and s.remaining() >= 2 * MIN_SPLIT]
            if not active:
                return None
            now = time.monotonic()

            def eff_rate(s):
                if now - s.last_data > 2.0 and now - s.started > 2.0:
                    return 0.0                      # silent for 2 s: treat as stuck
                return s.rate

            rates = [eff_rate(s) for s in active if eff_rate(s) > 0]
            avg = sum(rates) / len(rates) if rates else 0.0

            def time_left(s):
                r = eff_rate(s)
                if r == 0 and now - s.started < 2.0:
                    r = avg                         # just started, no measurement yet
                return s.remaining() / r if r > 0 else float("inf")

            best = max(active, key=lambda s: (time_left(s), s.remaining()))
            if time_left(best) < 1.0:
                return None                         # nearly done; a new connection won't help
            r_owner = eff_rate(best) if eff_rate(best) > 0 else (0.0 if time_left(best) == float("inf") else avg)
            r_new = avg or r_owner
            keep = int(best.remaining() * r_owner / (r_owner + r_new)) if r_owner + r_new > 0 else 0
            keep = max(MIN_SPLIT, min(best.remaining() - MIN_SPLIT, keep))
            mid = best.pos + keep
            new = Segment(mid, best.end)
            new.owner = True
            new.last_data = time.monotonic()
            best.end = mid - 1
            self.segments.append(new)
            self.splits += 1
            return new

    def _watchdog(self):
        """Called every ~0.5 s while downloading: kick connections that stalled
        or run far slower than the others, so they reconnect (often to a
        faster server) instead of dragging the whole download out."""
        now = time.monotonic()
        with self._lock:
            active = [s for s in self.segments if s.owner and s.remaining() > 0]
            rates = sorted(s.rate for s in active if s.rate > 0)
            median = rates[len(rates) // 2] if rates else 0.0
            for s in active:
                if now - s.kicked_at < 10:
                    continue
                stalled = now - s.last_data > STALL_SECONDS
                slow = (median > 0 and len(rates) >= 3 and 0 < s.rate < median / 5
                        and s.remaining() > 4 * MIN_SPLIT and now - s.started > 5)
                if stalled or slow:
                    s.kick = True
                    s.kicked_at = now
                    self.kicks += 1
                    if stalled and s.link is not None:
                        s.link.abort()             # unblock a read that is stuck waiting

    def _other_source(self, src):
        with self._lock:
            good = [s for s in self._sources if not s["bad"] and s is not src]
        return min(good, key=lambda s: s["active"]) if good else None

    def _worker(self, src):
        fails = 0
        link = Link(self)                          # this connection's reusable socket
        try:
            while not self._stop.is_set():
                seg = self._take_segment()
                if seg is None:
                    return
                try:
                    self._fetch(seg, src, link)
                    fails = 0
                except Exception as e:
                    link.close()
                    fails += 1
                    self._last_error = (f"HTTP {e.code} {e.reason}"
                                        if isinstance(e, urllib.error.HTTPError)
                                        else clean_err(e))
                    if fails >= 4:
                        other = self._other_source(src)
                        if other is not None:       # this host keeps failing; move on
                            src["bad"] = True
                            src, fails = other, 0
                            continue
                    if fails >= 8:
                        return
                    self._stop.wait(min(1.5 * fails, 10))
                finally:
                    with self._lock:
                        seg.owner = False
                        seg.kick = False
        finally:
            link.close()

    def _fetch(self, seg, src, link):
        f = self._f
        if self.resumable:
            with self._lock:
                start, end = seg.pos, seg.end
            if start > end:
                return
            resp = link.get(src, start, end)
            if resp.status != 206:
                link.close()
                raise IOError("Server stopped accepting multi-part requests "
                              "(the link may have expired)")
            cr = resp.getheader("Content-Range") or ""
            m = re.match(r"bytes\s+(\d+)-", cr)
            if m and int(m.group(1)) != start:
                link.close()
                raise IOError("Server returned the wrong part of the file")
        else:
            with self._lock:
                self.downloaded -= seg.pos
                seg.pos = 0
                f.seek(0)
                f.truncate()
            resp = link.get(src)
        with self._lock:
            self.active_conns += 1
            src["active"] += 1
            self.hosts_active = sum(1 for s in self._sources if s["active"] > 0)
            seg.started = seg.last_data = time.monotonic()
            seg.rate = 0.0
            seg.link = link
        ended_by_server = False
        complete_read = False
        win_t, win_b = time.monotonic(), 0
        try:
            while not self._stop.is_set():
                if seg.kick:
                    break                            # watchdog: reconnect this part
                data = resp.read(CHUNK)
                if not data:
                    ended_by_server = True
                    complete_read = True
                    break
                now = time.monotonic()
                with self._lock:
                    if self.resumable:
                        allowed = seg.end - seg.pos + 1
                        if allowed <= 0:
                            break                    # someone took the rest of this part
                        if len(data) > allowed:
                            data = data[:allowed]
                    f.seek(seg.pos)
                    f.write(data)
                    seg.pos += len(data)
                    self.downloaded += len(data)
                    seg.last_data = now
                    win_b += len(data)
                    if now - win_t >= 0.5:
                        inst = win_b / (now - win_t)
                        seg.rate = inst if seg.rate == 0 else seg.rate * 0.5 + inst * 0.5
                        win_t, win_b = now, 0
                self.m.limiter.consume(len(data), self._stop)
        finally:
            with self._lock:
                seg.link = None
                self.active_conns -= 1
                src["active"] -= 1
                self.hosts_active = sum(1 for s in self._sources if s["active"] > 0)
            if complete_read and not resp.will_close:
                link.release()                       # keep the socket for the next part
            else:
                try:
                    resp.close()
                except Exception:
                    pass
                link.close()                         # unread data left: can't reuse it
        if ended_by_server:
            with self._lock:
                if self.resumable:
                    if seg.remaining() > 0:
                        raise IOError("Connection closed early")
                else:
                    if self.size and self.size > 0 and seg.pos < self.size:
                        raise IOError("Connection closed early")
                    seg.end = seg.pos - 1


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
        if _uses_proxy(src["url"]):
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


def _ssl_ctx():
    global _SSL
    if _SSL is None:
        import ssl
        _SSL = ssl.create_default_context()
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


# --------------------------------------------------------------------------
# video sites through yt-dlp
# --------------------------------------------------------------------------
class _Abort(Exception):
    pass


class VideoDownload(Download):
    kind = "video"

    def __init__(self, *a, quality="best", name_prefix="", **kw):
        super().__init__(*a, **kw)
        self.quality = quality
        self.name_prefix = name_prefix      # "007 - " for playlist items
        # a name chosen by the user, or the page title for streams caught on
        # sites yt-dlp doesn't know (otherwise they'd be called "index.mp4")
        self.custom_name = os.path.splitext(sanitize(self.filename))[0] if self.filename else ""

    def to_dict(self):
        d = super().to_dict()
        d["quality"] = self.quality
        d["name_prefix"] = self.name_prefix
        d["custom_name"] = self.custom_name
        return d

    @classmethod
    def from_dict(cls, manager, d):
        obj = super().from_dict(manager, d)
        obj.quality = d.get("quality", "best")
        obj.name_prefix = d.get("name_prefix", "")
        obj.custom_name = d.get("custom_name", "")
        return obj

    def add_mirror(self, *a, **kw):
        return False

    def eta(self):
        return getattr(self, "_eta", None)

    def _run(self):
        try:
            import yt_dlp
        except Exception:
            self.status = "Error"
            self.error = "yt-dlp is not installed (pip install yt-dlp)"
            self.m.mark_dirty()
            return
        ffmpeg = find_ffmpeg()
        q = self.quality
        height = {"best": 1080, "1080p": 1080, "720p": 720, "480p": 480}.get(q)
        if q == "audio":
            fmt = "ba[ext=m4a]/ba/b"
        elif ffmpeg:
            # Prefer H.264 video + AAC (m4a) audio: plays in every Windows player.
            # YouTube's top picks are VP9/AV1 + Opus, and Opus inside .mp4 is the
            # "audio not supported" error in Movies & TV / Windows Media Player.
            # "max" takes the highest resolution (4K/8K are VP9/AV1 only) but
            # still uses AAC audio.
            h = f"[height<={height}]" if height else ""
            if q == "max":
                fmt = "bv*+ba[ext=m4a]/bv*+ba/b"
            else:
                fmt = (f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/bv*{h}+ba[ext=m4a]/"
                       f"bv*{h}+ba/b{h}/b")
        else:
            # without ffmpeg we can only take files that already have sound
            fmt = (f"b[height<={height}][ext=mp4]/b[height<={height}]/b" if height
                   else "b[ext=mp4]/b")
        headers = {"User-Agent": self.user_agent or UA}
        if self.referer:
            headers["Referer"] = self.referer
        if self.cookie:
            headers["Cookie"] = self.cookie

        def hook(d):
            if self._stop.is_set():
                raise _Abort()
            st = d.get("status")
            if st == "downloading":
                self.status = "Downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                part_done = d.get("downloaded_bytes") or 0
                fname = d.get("filename") or ""
                if fname:
                    self.filename = os.path.basename(fname)
                # several streams (video + audio) are downloaded one after the other
                key = fname
                self._parts[key] = (part_done, total or 0)
                self.downloaded = sum(p[0] for p in self._parts.values())
                tot = sum(p[1] for p in self._parts.values())
                self.size = tot if tot else -1
                self._eta = d.get("eta")
                self.active_conns = self.connections
            elif st == "finished":
                self.status = "Processing"
                self.active_conns = 0

        def pp_hook(d):
            if d.get("status") == "finished":
                fp = d.get("info_dict", {}).get("filepath")
                if fp:
                    self.path = fp
                    self.filename = os.path.basename(fp)

        self._parts = {}
        self._eta = None
        opts = {
            "outtmpl": os.path.join(self.folder, self.name_prefix.replace("%", "%%")
                                   + (self.custom_name.replace("%", "%%") if self.custom_name
                                      else "%(title).150B [%(id)s]") + ".%(ext)s"),
            "format": fmt,
            "concurrent_fragment_downloads": self.connections,
            "http_chunk_size": 10 * 1024 * 1024,
            "continuedl": True,
            "noplaylist": True,
            "retries": 10,
            "fragment_retries": 10,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "windowsfilenames": True,
            "http_headers": headers,
            "progress_hooks": [hook],
            "postprocessor_hooks": [pp_hook],
        }
        if self.m.limiter.rate > 0:
            opts["ratelimit"] = self.m.limiter.rate
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
            if q != "audio":
                opts["merge_output_format"] = "mp4"
        try:
            os.makedirs(self.folder, exist_ok=True)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                if info and not self.path:
                    self.path = ydl.prepare_filename(info)
                    self.filename = os.path.basename(self.path)
            if ffmpeg and self.path and os.path.isfile(self.path):
                self._make_playable(ffmpeg)
            if self.size is None or self.size < 0:
                self.size = self.downloaded
            self.downloaded = self.size
            self.status = "Complete"
        except _Abort:
            self.status = "Paused"
        except Exception as e:
            if self._stop.is_set():
                self.status = "Paused"
            else:
                msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
                self.status, self.error = "Error", msg
        finally:
            self.speed = 0.0
            self.active_conns = 0
            self.m.mark_dirty()

    def update_speed(self):
        super().update_speed()

    def _make_playable(self, ffmpeg):
        """Safety net: if the saved .mp4 still has Opus/Vorbis audio (some sites
        offer nothing else), convert just the audio to AAC so Windows players
        can play it. The video is copied untouched, so this takes seconds."""
        import subprocess
        if not self.path.lower().endswith((".mp4", ".m4v", ".mov")):
            return
        flags = 0x08000000 if os.name == "nt" else 0      # no console window
        try:
            probe = subprocess.run([ffmpeg, "-hide_banner", "-i", self.path],
                                   capture_output=True, text=True, errors="replace",
                                   creationflags=flags, timeout=60)
            codecs = re.findall(r"Audio: (\w+)", probe.stderr)
            if not codecs or not any(c in ("opus", "vorbis") for c in codecs):
                return
            self.status = "Processing"
            tmp = self.path + ".aac.mp4"
            r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                "-i", self.path, "-map", "0", "-c", "copy",
                                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                                tmp], capture_output=True, creationflags=flags, timeout=3600)
            if r.returncode == 0 and os.path.getsize(tmp) > 0:
                os.replace(tmp, self.path)
            elif os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# --------------------------------------------------------------------------
# manager: queue, persistence, browser listener
# --------------------------------------------------------------------------
class Manager:
    def __init__(self, data_dir=None, persist=True):
        self.persist = persist
        self.data_dir = data_dir or os.path.join(app_dir(), "jmdm_data")
        self.settings = {
            "download_dir": default_download_dir(),
            "connections": 16,
            "max_active": 3,
            "speed_limit_kb": 0,
            "clipboard_watch": True,
            "ask_before_start": True,
            "listen_port": LISTEN_PORT,
            "video_quality": "best",
        }
        self.downloads = []
        self.limiter = RateLimiter()
        self.incoming = queue.Queue()      # requests from the browser extension
        self.notice = ""
        self._dirty = False
        self._lock = threading.RLock()
        self._server = None
        if persist:
            self.load()
        self.apply_settings()
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- persistence -----------------------------------------------------
    def _file(self, name):
        return os.path.join(self.data_dir, name)

    def load(self):
        try:
            with open(self._file("settings.json"), encoding="utf-8") as fh:
                self.settings.update(json.load(fh))
        except Exception:
            pass
        try:
            with open(self._file("downloads.json"), encoding="utf-8") as fh:
                for d in json.load(fh):
                    cls = VideoDownload if d.get("kind") == "video" else Download
                    self.downloads.append(cls.from_dict(self, d))
        except Exception:
            pass

    def save(self):
        if not self.persist:
            return
        os.makedirs(self.data_dir, exist_ok=True)
        with self._lock:
            data = [d.to_dict() for d in self.downloads]
        for name, obj in (("downloads.json", data), ("settings.json", self.settings)):
            tmp = self._file(name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, indent=1)
            os.replace(tmp, self._file(name))
        self._dirty = False

    def mark_dirty(self):
        self._dirty = True

    def apply_settings(self):
        self.limiter.rate = int(self.settings.get("speed_limit_kb", 0)) * 1024
        self.mark_dirty()

    # ---- downloads -------------------------------------------------------
    def add(self, url, folder=None, filename="", connections=None, referer="",
            cookie="", user_agent="", video=None, quality=None, start=True,
            mirrors=None, name_prefix=""):
        url = url.strip()
        folder = folder or self.settings["download_dir"]
        connections = connections or self.settings["connections"]
        if video is None:
            video = is_video_url(url)
        if video:
            d = VideoDownload(self, url, folder, filename, connections, referer,
                              cookie, user_agent,
                              quality=quality or self.settings["video_quality"],
                              name_prefix=name_prefix)
        else:
            mlist = []
            for m in mirrors or []:
                m = {"url": m} if isinstance(m, str) else dict(m)
                if m.get("url") and m["url"] != url:
                    m.setdefault("referer", "")
                    m.setdefault("cookie", "")
                    m.setdefault("user_agent", user_agent)
                    mlist.append(m)
            d = Download(self, url, folder, filename, connections, referer,
                         cookie, user_agent, mirrors=mlist)
        d.status = "Queued" if start else "Paused"
        with self._lock:
            self.downloads.append(d)
        self.mark_dirty()
        return d

    def add_playlist(self, url, folder=None, connections=None, quality=None,
                     referer="", cookie="", user_agent="", items="", start=True,
                     done=None):
        """Expand a YouTube playlist/channel into one queued video per item.
        Runs in the background; `done(count, title, error)` is called at the end."""
        folder = folder or self.settings["download_dir"]
        self.notice = "Reading playlist…"

        def work():
            count, title, err = 0, "", ""
            try:
                import yt_dlp
                headers = {"User-Agent": user_agent or UA}
                if cookie:
                    headers["Cookie"] = cookie
                opts = {"extract_flat": "in_playlist", "quiet": True,
                        "no_warnings": True, "noplaylist": False,
                        "http_headers": headers}
                if items.strip():
                    opts["playlist_items"] = items.strip()
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(playlist_url(url), download=False)
                except Exception as ex:
                    info, list_err = None, clean_err(ex)
                else:
                    list_err = ""
                entries = [e for e in ((info or {}).get("entries") or []) if e]
                if not entries:
                    # Not really a list (or the list is private/unavailable):
                    # just download the video the link points at.
                    if is_whole_playlist_url(url):
                        raise IOError(list_err or "This playlist is empty or private")
                    self.add(url, folder, "", connections, referer, cookie,
                             user_agent, video=True, quality=quality, start=start)
                    self.notice = "Not a playlist — downloading the single video"
                    if done:
                        done(1, "", "")
                    return
                title = sanitize(info.get("title") or "Playlist")
                width = max(2, len(str(len(entries))))
                sub = os.path.join(folder, title)
                for i, e in enumerate(entries, 1):
                    u = e.get("url") or e.get("webpage_url") or ""
                    if not u.startswith("http") and e.get("id"):
                        u = "https://www.youtube.com/watch?v=" + e["id"]
                    if not u.startswith("http"):
                        continue
                    idx = e.get("playlist_index") or i
                    self.add(u, sub, e.get("title") or "", connections, referer,
                             cookie, user_agent, video=True, quality=quality,
                             start=start, name_prefix=f"{idx:0{width}d} - ")
                    count += 1
                self.notice = f"Added {count} videos from “{title}”"
            except Exception as ex:
                err = clean_err(ex)
                self.notice = "Playlist error: " + err
            if done:
                done(count, title, err)

        threading.Thread(target=work, daemon=True).start()

    def find_same_file(self, size, exclude_url=""):
        """An unfinished download with exactly this size (likely the same file
        from another host) — used to offer it as a mirror."""
        if not size or size <= 0:
            return None
        for d in self.downloads:
            if d.kind == "file" and d.size == size and d.resumable \
                    and d.status != "Complete" and d.url != exclude_url:
                return d
        return None

    def add_mirror(self, d, url, referer="", cookie="", user_agent=""):
        ok = d.add_mirror(url, referer, cookie, user_agent)
        self.mark_dirty()
        return ok

    def resume(self, d):
        if d.status in ("Paused", "Error"):
            d.status = "Queued"
            self.mark_dirty()

    def pause(self, d):
        d.pause()
        self.mark_dirty()

    def redownload(self, d):
        d.pause()
        d.join(5)
        for p in (d.path + ".part",) if d.path else ():
            try:
                os.remove(p)
            except OSError:
                pass
        d.segments, d.downloaded, d.size = [], 0, None
        if d.kind == "file":
            d.path = ""
        d.status = "Queued"
        self.mark_dirty()

    def remove(self, d, delete_file=False):
        d.pause()
        d.join(5)
        with self._lock:
            if d in self.downloads:
                self.downloads.remove(d)
        if d.path:
            targets = [d.path + ".part"]
            if delete_file:
                targets.append(d.path)
            for p in targets:
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.mark_dirty()

    def _loop(self):
        last_save = time.monotonic()
        while self._running:
            with self._lock:
                items = list(self.downloads)
            active = 0
            for d in items:
                d.update_speed()
                if d.status in ("Downloading", "Processing"):
                    active += 1
            for d in items:
                if active >= int(self.settings.get("max_active", 3)):
                    break
                if d.status == "Queued" and not d.is_running():
                    d.start()
                    active += 1
            now = time.monotonic()
            if (self._dirty or active) and now - last_save > 2:
                try:
                    self.save()
                except Exception:
                    pass
                last_save = now
            time.sleep(0.5)

    def shutdown(self):
        for d in list(self.downloads):
            if d.status == "Downloading":
                d._stop.set()
        for d in list(self.downloads):
            d.join(3)
        self._running = False
        if self._server:
            self._server.shutdown()
        try:
            self.save()
        except Exception:
            pass

    # ---- browser extension listener -------------------------------------
    def start_listener(self):
        mgr = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _allowed(self):
                # Only our browser extension may talk to us. Web pages cannot
                # send the custom header without a CORS preflight, which we
                # never approve, so random websites can't queue downloads.
                origin = self.headers.get("Origin", "")
                if origin and not origin.startswith(("chrome-extension://",
                                                     "moz-extension://",
                                                     "extension://")):
                    return False
                return self.headers.get("X-JMDM") == "1"

            def _reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/ping" and self._allowed():
                    return self._reply(200, {"app": APP_NAME, "version": VERSION})
                self._reply(403, {"error": "forbidden"})

            def do_POST(self):
                if self.path != "/add" or not self._allowed():
                    return self._reply(403, {"error": "forbidden"})
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    req = json.loads(self.rfile.read(min(n, 1 << 20)) or b"{}")
                    url = str(req.get("url", ""))
                    if not re.match(r"^https?://", url):
                        raise ValueError("bad url")
                except Exception:
                    return self._reply(400, {"error": "bad request"})
                job = {
                    "url": url,
                    "filename": sanitize(req["filename"]) if req.get("filename") else "",
                    "referer": str(req.get("referer", ""))[:2000],
                    "cookie": str(req.get("cookie", ""))[:16000],
                    "user_agent": str(req.get("userAgent", ""))[:500],
                    "video": bool(req.get("video")) or is_video_url(url),
                    "mirror_of": None, "size": None,
                    "title": str(req.get("title", ""))[:150],
                }
                self._reply(200, {"ok": True})
                if not job["video"]:
                    # same size as a file already downloading -> offer it as a mirror
                    try:
                        info = probe_info(url, job["referer"], job["cookie"],
                                          job["user_agent"])
                        job["size"] = info["size"]
                        same = mgr.find_same_file(info["size"], url) \
                            if info["resumable"] else None
                        if same:
                            job["mirror_of"] = same.id
                    except Exception:
                        pass
                mgr.incoming.put(job)

        port = int(self.settings.get("listen_port", LISTEN_PORT))
        try:
            self._server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            return False
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return True


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
def run_gui(initial_url=None):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    mgr = Manager()
    listening = mgr.start_listener()
    integration = {"ok": False, "msg": "checking…"}

    def _register():
        try:
            ok, msg = register_browser_integration()
        except Exception as e:
            ok, msg = False, clean_err(e)
        integration.update(ok=ok, msg=msg)

    threading.Thread(target=_register, daemon=True).start()

    root = tk.Tk()
    root.title(f"{APP_NAME} {VERSION}")
    root.geometry("1060x620")
    root.minsize(760, 420)
    style = ttk.Style()
    for theme in ("vista", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break
    style.configure("Treeview", rowheight=26)
    style.configure("Tool.TButton", padding=(10, 6))

    # ---- toolbar ----
    bar = ttk.Frame(root, padding=(8, 8, 8, 4))
    bar.pack(fill="x")

    def selected():
        ids = tree.selection()
        return [d for d in mgr.downloads if d.id in ids]

    def do_resume():
        for d in selected():
            mgr.resume(d)

    def do_pause():
        for d in selected():
            mgr.pause(d)

    def do_resume_all():
        for d in mgr.downloads:
            mgr.resume(d)

    def do_pause_all():
        for d in mgr.downloads:
            mgr.pause(d)

    def do_delete():
        sel = selected()
        if not sel:
            return
        ans = messagebox.askyesnocancel(
            "Delete", f"Remove {len(sel)} item(s) from the list.\n\n"
                      "Also delete the downloaded file(s) from disk?")
        if ans is None:
            return
        for d in sel:
            threading.Thread(target=mgr.remove, args=(d, ans), daemon=True).start()

    def do_redownload():
        for d in selected():
            threading.Thread(target=mgr.redownload, args=(d,), daemon=True).start()

    def open_file(d):
        if d.status == "Complete" and d.path and os.path.exists(d.path):
            if os.name == "nt":
                os.startfile(d.path)

    def open_folder(d=None):
        d = d or (selected() or [None])[0]
        folder = d.folder if d else mgr.settings["download_dir"]
        os.makedirs(folder, exist_ok=True)
        if os.name == "nt":
            import subprocess
            if d and d.path and os.path.exists(d.path):
                subprocess.Popen(["explorer", "/select,", d.path])
            else:
                os.startfile(folder)

    def copy_url():
        sel = selected()
        if sel:
            root.clipboard_clear()
            root.clipboard_append(sel[0].url)
            last_clip[0] = sel[0].url

    for text, cmd in (("➕  Add URL", lambda: add_dialog()),
                      ("▶  Resume", do_resume), ("⏸  Pause", do_pause),
                      ("⏵⏵  Resume all", do_resume_all), ("⏸⏸  Pause all", do_pause_all),
                      ("🗑  Delete", do_delete), ("📂  Open folder", lambda: open_folder()),
                      ("⚙  Settings", lambda: settings_dialog())):
        ttk.Button(bar, text=text, command=cmd, style="Tool.TButton").pack(side="left", padx=2)

    # ---- list ----
    mid = ttk.Frame(root, padding=(8, 0))
    mid.pack(fill="both", expand=True)
    cols = ("name", "size", "progress", "speed", "eta", "conns", "status")
    tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
    for c, title, w, anchor in (("name", "File name", 320, "w"),
                                ("size", "Size", 90, "e"),
                                ("progress", "Progress", 170, "w"),
                                ("speed", "Speed", 100, "e"),
                                ("eta", "Time left", 80, "e"),
                                ("conns", "Conn.", 55, "center"),
                                ("status", "Status", 200, "w")):
        tree.heading(c, text=title)
        tree.column(c, width=w, anchor=anchor, stretch=(c in ("name", "status")))
    vs = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vs.set)
    tree.pack(side="left", fill="both", expand=True)
    vs.pack(side="right", fill="y")
    tree.tag_configure("done", foreground="#1a7f37")
    tree.tag_configure("err", foreground="#c62828")

    # ---- details: segment map like IDM ----
    det = ttk.LabelFrame(root, text="Connections", padding=8)
    det.pack(fill="x", padx=8, pady=6)
    info_var = tk.StringVar(value="Select a download to see its parts.")
    ttk.Label(det, textvariable=info_var).pack(anchor="w")
    seg_canvas = tk.Canvas(det, height=24, highlightthickness=1,
                           highlightbackground="#bbb", bg="#eeeeee")
    seg_canvas.pack(fill="x", pady=(6, 0))

    status_var = tk.StringVar()
    ttk.Label(root, textvariable=status_var, anchor="w",
              padding=(10, 2, 10, 6)).pack(fill="x")

    # ---- context menu ----
    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="Open", command=lambda: [open_file(d) for d in selected()[:1]])
    menu.add_command(label="Open folder", command=lambda: open_folder())
    menu.add_separator()
    menu.add_command(label="Resume", command=do_resume)
    menu.add_command(label="Pause", command=do_pause)
    menu.add_command(label="Redownload", command=do_redownload)
    menu.add_command(label="Add mirror link…", command=lambda: add_mirror_dialog())
    menu.add_separator()
    menu.add_command(label="Copy link", command=copy_url)
    menu.add_command(label="Delete", command=do_delete)

    def popup(ev):
        row = tree.identify_row(ev.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
        menu.tk_popup(ev.x_root, ev.y_root)

    tree.bind("<Button-3>", popup)
    tree.bind("<Double-1>", lambda e: [open_file(d) for d in selected()[:1]])
    tree.bind("<Delete>", lambda e: do_delete())

    # ---- dialogs ----
    def add_mirror_dialog():
        sel = [d for d in selected() if d.kind == "file"]
        if not sel:
            messagebox.showinfo("Add mirror", "Select a file download first.")
            return
        d = sel[0]
        try:
            clip = root.clipboard_get().strip()
        except Exception:
            clip = ""
        from tkinter import simpledialog
        u = simpledialog.askstring(
            "Add mirror", f"Another link to the same file:\n{d.filename}\n\n"
                          "Tip: for file hosts, just click their Download button in the "
                          "browser — the app will offer it as a mirror automatically.",
            initialvalue=clip if re.match(r"^https?://", clip) else "", parent=root)
        if u and re.match(r"^https?://", u.strip()):
            last_clip[0] = u.strip()
            mgr.add_mirror(d, u.strip(), user_agent=d.user_agent)

    def add_dialog(url="", filename="", referer="", cookie="", user_agent="",
                   video=None, from_browser=False):
        win = tk.Toplevel(root)
        win.title("Add download")
        win.transient(root)
        win.resizable(True, False)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        if not url:
            try:
                clip = root.clipboard_get().strip()
                if re.match(r"^https?://\S+$", clip):
                    url = clip
            except Exception:
                pass
        url_v = tk.StringVar(value=url)
        name_v = tk.StringVar(value=filename)
        dir_v = tk.StringVar(value=mgr.settings["download_dir"])
        conn_v = tk.IntVar(value=mgr.settings["connections"])
        ref_v = tk.StringVar(value=referer)
        cookie_v = tk.StringVar(value=cookie)
        vid_v = tk.BooleanVar(value=is_video_url(url) if video is None else video)
        q_v = tk.StringVar(value=mgr.settings["video_quality"])
        start_v = tk.BooleanVar(value=True)
        pl_v = tk.BooleanVar(value=is_whole_playlist_url(url))
        items_v = tk.StringVar(value="")

        r = 0
        if from_browser:
            ttk.Label(frm, text="Download caught from your browser",
                      font=("Segoe UI", 10, "bold")).grid(row=r, column=0, columnspan=3,
                                                          sticky="w", pady=(0, 8))
            r += 1
        ttk.Label(frm, text="Link:").grid(row=r, column=0, sticky="w")
        e_url = ttk.Entry(frm, textvariable=url_v, width=70)
        e_url.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        ttk.Label(frm, text="Save as:").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=name_v).grid(row=r, column=1, columnspan=2,
                                                 sticky="ew", pady=3)
        r += 1
        ttk.Label(frm, text="(leave blank to use the server's file name)",
                  foreground="#777").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(frm, text="Folder:").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=dir_v).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(frm, text="Browse…",
                   command=lambda: dir_v.set(filedialog.askdirectory(
                       initialdir=dir_v.get(), parent=win) or dir_v.get())
                   ).grid(row=r, column=2, padx=(6, 0))
        r += 1
        ttk.Label(frm, text="Connections:").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(frm, from_=1, to=MAX_CONNECTIONS, textvariable=conn_v,
                    width=6).grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        vrow = ttk.Frame(frm)
        vrow.grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Checkbutton(vrow, text="Video site (YouTube, Facebook…)",
                        variable=vid_v).pack(side="left")
        ttk.Label(vrow, text="   Quality:").pack(side="left")
        ttk.Combobox(vrow, textvariable=q_v, state="readonly", width=8,
                     values=("best", "max", "1080p", "720p", "480p", "audio")).pack(side="left")
        r += 1
        prow = ttk.Frame(frm)
        prow.grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
        pl_chk = ttk.Checkbutton(prow, text="Whole playlist / channel", variable=pl_v)
        pl_chk.pack(side="left")
        ttk.Label(prow, text="   Only items:").pack(side="left")
        ttk.Entry(prow, textvariable=items_v, width=14).pack(side="left")
        ttk.Label(prow, text="  e.g. 1-20 or 3,5,9 (blank = all)",
                  foreground="#777").pack(side="left")
        r += 1
        ttk.Label(frm, text="Mirrors:").grid(row=r, column=0, sticky="nw", pady=3)
        mir_txt = tk.Text(frm, height=3, width=60, wrap="none", font=("Consolas", 9))
        mir_txt.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        ttk.Label(frm, text="Optional: other links to the SAME file on other hosts, one per "
                            "line. Each host's speed adds up.",
                  foreground="#777").grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        adv = ttk.LabelFrame(frm, text="Advanced (filled in automatically by the extension)",
                             padding=8)
        adv.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        adv.columnconfigure(1, weight=1)
        ttk.Label(adv, text="Referer:").grid(row=0, column=0, sticky="w")
        ttk.Entry(adv, textvariable=ref_v).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(adv, text="Cookies:").grid(row=1, column=0, sticky="w")
        ttk.Entry(adv, textvariable=cookie_v, show="•").grid(row=1, column=1,
                                                             sticky="ew", pady=2)
        r += 1
        ttk.Checkbutton(frm, text="Start now", variable=start_v).grid(row=r, column=0,
                                                                     columnspan=2, sticky="w")
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=1, columnspan=2, sticky="e", pady=(8, 0))

        def ok():
            u = url_v.get().strip()
            if not re.match(r"^https?://", u):
                messagebox.showerror("Add download", "Paste a link that starts with http(s)://",
                                     parent=win)
                return
            try:
                conns = int(conn_v.get())
            except Exception:
                conns = mgr.settings["connections"]
            last_clip[0] = u
            if vid_v.get() and pl_v.get() and is_playlist_url(u):
                mgr.add_playlist(u, dir_v.get().strip(), conns, q_v.get(),
                                 ref_v.get().strip(), cookie_v.get().strip(), user_agent,
                                 items=items_v.get(), start=start_v.get())
                win.destroy()
                return
            mirrors = [x.strip() for x in mir_txt.get("1.0", "end").splitlines()
                       if re.match(r"^https?://", x.strip())]
            d = mgr.add(u, dir_v.get().strip(), name_v.get().strip(), conns,
                        ref_v.get().strip(), cookie_v.get().strip(), user_agent,
                        video=vid_v.get(), quality=q_v.get(), start=start_v.get(),
                        mirrors=mirrors)
            win.destroy()
            tree_select[0] = d.id

        def on_url_change(*_):
            u = url_v.get().strip()
            vid_v.set(is_video_url(u) or (vid_v.get() and not re.match(r"^https?://", u)))
            if is_playlist_url(u):
                pl_chk.state(["!disabled"])
                if is_whole_playlist_url(u):
                    pl_v.set(True)
                    pl_chk.configure(text="Whole playlist / channel")
                else:
                    # a single video that sits inside a playlist: user chooses
                    pl_chk.configure(text="Whole playlist (this video is part of one)")
            else:
                pl_v.set(False)
                pl_chk.state(["disabled"])
                pl_chk.configure(text="Whole playlist / channel")

        url_v.trace_add("write", on_url_change)
        on_url_change()
        ttk.Button(btns, text="Download", command=ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left")
        win.bind("<Return>", lambda e: ok())
        win.bind("<Escape>", lambda e: win.destroy())
        e_url.focus_set()
        win.lift()
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))

    def settings_dialog():
        s = mgr.settings
        win = tk.Toplevel(root)
        win.title("Settings")
        win.transient(root)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        dir_v = tk.StringVar(value=s["download_dir"])
        conn_v = tk.IntVar(value=s["connections"])
        act_v = tk.IntVar(value=s["max_active"])
        lim_v = tk.IntVar(value=s["speed_limit_kb"])
        clip_v = tk.BooleanVar(value=s["clipboard_watch"])
        ask_v = tk.BooleanVar(value=s["ask_before_start"])
        q_v = tk.StringVar(value=s["video_quality"])
        rows = [
            ("Default folder:", ttk.Entry(frm, textvariable=dir_v, width=50)),
            ("Connections per file:", ttk.Spinbox(frm, from_=1, to=MAX_CONNECTIONS,
                                                  textvariable=conn_v, width=6)),
            ("Downloads at the same time:", ttk.Spinbox(frm, from_=1, to=10,
                                                        textvariable=act_v, width=6)),
            ("Speed limit (KB/s, 0 = none):", ttk.Spinbox(frm, from_=0, to=1000000,
                                                          increment=100,
                                                          textvariable=lim_v, width=10)),
            ("Default video quality:", ttk.Combobox(frm, textvariable=q_v, state="readonly",
                                                    values=("best", "max", "1080p", "720p",
                                                            "480p", "audio"), width=8)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3)
            widget.grid(row=i, column=1, sticky="w" if i else "ew", pady=3)
        ttk.Button(frm, text="Browse…",
                   command=lambda: dir_v.set(filedialog.askdirectory(
                       initialdir=dir_v.get(), parent=win) or dir_v.get())
                   ).grid(row=0, column=2, padx=(6, 0))
        n = len(rows)
        ttk.Checkbutton(frm, text="Offer to download links I copy (clipboard watch)",
                        variable=clip_v).grid(row=n, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Ask before starting downloads sent from the browser",
                        variable=ask_v).grid(row=n + 1, column=0, columnspan=3, sticky="w")
        ff = find_ffmpeg()
        notes = [f"Browser extension: {'listening on port ' + str(s['listen_port']) if listening else 'port busy — is another copy running?'}",
                 f"Browser can start the app: {'yes — ' if integration['ok'] else 'no — '}{integration['msg']}",
                 f"yt-dlp (video sites): {'installed' if have_ytdlp() else 'NOT installed'}",
                 f"ffmpeg (HD video + audio merge): {ff or 'not found — put ffmpeg.exe next to the app'}"]
        ttk.Label(frm, text="\n".join(notes), foreground="#555",
                  justify="left").grid(row=n + 2, column=0, columnspan=3, sticky="w",
                                       pady=(10, 0))

        def save():
            try:
                s.update(download_dir=dir_v.get().strip() or default_download_dir(),
                         connections=max(1, min(MAX_CONNECTIONS, int(conn_v.get()))),
                         max_active=max(1, int(act_v.get())),
                         speed_limit_kb=max(0, int(lim_v.get())),
                         clipboard_watch=clip_v.get(),
                         ask_before_start=ask_v.get(),
                         video_quality=q_v.get())
            except Exception:
                messagebox.showerror("Settings", "Please enter whole numbers.", parent=win)
                return
            mgr.apply_settings()
            win.destroy()

        b = ttk.Frame(frm)
        b.grid(row=n + 3, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left")

    # ---- periodic refresh ----
    tree_select = [None]
    last_clip = [None]

    def progress_text(d):
        if d.status == "Complete":
            return "██████████ 100%"
        if d.size and d.size > 0:
            p = min(1.0, d.downloaded / d.size)
            k = int(p * 10)
            return "█" * k + "░" * (10 - k) + f" {p * 100:.1f}%"
        return human_size(d.downloaded) if d.downloaded else ""

    def status_text(d):
        if d.status == "Error":
            return "Error: " + d.error
        if d.status == "Processing":
            return "Merging / converting…"
        if d.status == "Downloading" and d.kind == "file" and d.size is not None \
                and not d.resumable:
            return "Downloading (server allows 1 connection)"
        return d.status

    def draw_segments(d):
        c = seg_canvas
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        if d is None:
            return
        if d.status == "Complete":
            c.create_rectangle(0, 0, w, h, fill="#2e7d32", outline="")
            return
        if d.kind == "video" or not d.size or d.size <= 0:
            if d.size and d.size > 0:
                c.create_rectangle(0, 0, w * d.downloaded / d.size, h,
                                   fill="#2b7de9", outline="")
            return
        for start, end, pos, owner in d.segments_snapshot():
            x0 = start / d.size * w
            x1 = pos / d.size * w
            if x1 > x0:
                c.create_rectangle(x0, 0, x1, h, fill="#2b7de9", outline="")
            if owner and pos <= end:
                c.create_line(x1, 0, x1, h, fill="#ff9800", width=2)
            c.create_line(start / d.size * w, 0, start / d.size * w, 4, fill="#555")

    def refresh():
        # browser requests
        while True:
            try:
                req = mgr.incoming.get_nowait()
            except queue.Empty:
                break
            same = next((x for x in mgr.downloads if x.id == req.get("mirror_of")), None)
            if same is not None and messagebox.askyesno(
                    "Same file from another host",
                    f"This looks like the same file as\n\n  {same.filename}\n"
                    f"({human_size(same.size)})\n\nUse it as an extra mirror? Both hosts "
                    "will download it together, so their speeds add up.\n\n"
                    "Choose No to download it separately."):
                mgr.add_mirror(same, req["url"], req["referer"], req["cookie"],
                               req["user_agent"])
                tree_select[0] = same.id
                continue
            if req["video"] and not req["filename"] and req.get("title") \
                    and not is_video_url(req["url"]):
                req["filename"] = sanitize(req["title"])   # stream from a generic site
            if mgr.settings.get("ask_before_start", True):
                add_dialog(req["url"], req["filename"], req["referer"], req["cookie"],
                           req["user_agent"], req["video"], from_browser=True)
            elif req["video"] and is_whole_playlist_url(req["url"]):
                mgr.add_playlist(req["url"], referer=req["referer"], cookie=req["cookie"],
                                 user_agent=req["user_agent"])
            else:
                mgr.add(req["url"], filename=req["filename"], referer=req["referer"],
                        cookie=req["cookie"], user_agent=req["user_agent"],
                        video=req["video"])

        existing = set(tree.get_children())
        current = set()
        total_speed = 0.0
        active = 0
        for d in list(mgr.downloads):
            current.add(d.id)
            total_speed += d.speed
            if d.status == "Downloading":
                active += 1
            name = d.filename or d.url
            if d.kind == "video":
                name = "🎬 " + name
            elif d.mirrors:
                name = f"⧉{1 + len(d.mirrors)} " + name
            vals = (name, human_size(d.size) if d.size else "?",
                    progress_text(d),
                    (human_size(d.speed) + "/s") if d.status == "Downloading" else "",
                    human_time(d.eta()) if d.status == "Downloading" else "",
                    d.active_conns if d.status == "Downloading" else "",
                    status_text(d))
            tags = ("done",) if d.status == "Complete" else \
                   ("err",) if d.status == "Error" else ()
            if d.id in existing:
                tree.item(d.id, values=vals, tags=tags)
            else:
                tree.insert("", "end", iid=d.id, values=vals, tags=tags)
        for iid in existing - current:
            tree.delete(iid)
        if tree_select[0] and tree_select[0] in current:
            tree.selection_set(tree_select[0])
            tree.see(tree_select[0])
            tree_select[0] = None

        sel = selected()
        d = sel[0] if sel else None
        if d:
            parts = len(d.segments_snapshot())
            hosts = 1 + len(d.mirrors)
            info_var.set(f"{d.filename or d.url}   •   {human_size(d.downloaded)} of "
                         f"{human_size(d.size)}   •   {d.active_conns} active connection(s)"
                         + (f" on {d.hosts_active} host(s)" if hosts > 1 else "")
                         + (f", {parts} part(s)" if d.kind == "file" and parts else "")
                         + (f"   •   {hosts} host(s) total" if hosts > 1 else "")
                         + (f"   •   {d.note}" if d.note else ""))
        else:
            info_var.set("Select a download to see its parts. "
                         "Blue = downloaded, orange = connection working.")
        draw_segments(d)
        lim = mgr.settings.get("speed_limit_kb", 0)
        status_var.set((mgr.notice + "   •   " if mgr.notice else "")
                       + f"{active} downloading   •   total {human_size(total_speed)}/s"
                       + (f"   •   limit {lim} KB/s" if lim else "")
                       + ("   •   browser extension: ready" if listening
                          else "   •   browser extension: port busy"))
        root.after(500, refresh)

    def check_clipboard():
        if mgr.settings.get("clipboard_watch"):
            try:
                txt = root.clipboard_get().strip()
            except Exception:
                txt = ""
            if txt != last_clip[0]:
                first = last_clip[0] is None
                last_clip[0] = txt
                if not first and looks_like_download(txt) \
                        and not any(x.url == txt for x in mgr.downloads):
                    add_dialog(txt)
        root.after(1000, check_clipboard)

    def on_close():
        busy = any(d.status == "Downloading" for d in mgr.downloads)
        if busy and not messagebox.askyesno(
                "Quit", "Downloads are running. They'll be paused and can be "
                        "resumed next time. Quit?"):
            return
        mgr.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, refresh)
    root.after(1500, check_clipboard)
    if initial_url:
        root.after(400, lambda: add_dialog(initial_url))
    root.mainloop()


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def run_cli(argv):
    import argparse
    if sys.stdout is None and os.name == "nt":
        # the .exe is a windowed app; borrow the terminal it was started from
        try:
            import ctypes
            ctypes.windll.kernel32.AttachConsole(-1)
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
        except Exception:
            sys.stdout = sys.stderr = open(os.devnull, "w")
    ap = argparse.ArgumentParser(prog="jmdm --cli")
    ap.add_argument("url")
    ap.add_argument("-o", "--output", default=None, help="folder")
    ap.add_argument("-f", "--filename", default="")
    ap.add_argument("-n", "--connections", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="KB/s, 0 = no limit")
    ap.add_argument("--referer", default="")
    ap.add_argument("--cookie", default="")
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--quality", default="best")
    ap.add_argument("-m", "--mirror", action="append", default=[],
                    help="another link to the same file (repeatable)")
    ap.add_argument("--playlist", action="store_true", help="download the whole playlist")
    ap.add_argument("--items", default="", help="playlist items, e.g. 1-10")
    a = ap.parse_args(argv)
    mgr = Manager(persist=False)
    mgr.settings["speed_limit_kb"] = a.limit
    mgr.apply_settings()
    if a.playlist:
        done = threading.Event()
        mgr.add_playlist(a.url, a.output or os.getcwd(), a.connections, a.quality,
                         items=a.items, done=lambda *x: done.set())
        done.wait()
        print(mgr.notice)
        try:
            while any(x.status in ("Queued", "Downloading", "Processing")
                      for x in mgr.downloads):
                time.sleep(1)
                n_ok = sum(x.status == "Complete" for x in mgr.downloads)
                sys.stdout.write(f"\r{n_ok}/{len(mgr.downloads)} done   "
                                 f"{human_size(sum(x.speed for x in mgr.downloads))}/s   ")
                sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        print()
        for x in mgr.downloads:
            print(f"{x.status:<9} {x.filename}" + (f"  ({x.error})" if x.error else ""))
        mgr.shutdown()
        return 0
    d = mgr.add(a.url, a.output or os.getcwd(), a.filename, a.connections,
                a.referer, a.cookie, video=True if a.video else None, quality=a.quality,
                mirrors=a.mirror)
    t0 = time.time()
    try:
        while d.status in ("Queued", "Downloading", "Processing"):
            time.sleep(0.5)
            pct = (d.downloaded / d.size * 100) if d.size and d.size > 0 else 0
            sys.stdout.write(f"\r{d.status:<11} {pct:5.1f}%  {human_size(d.downloaded):>10}"
                             f"  {human_size(d.speed):>10}/s  conns={d.active_conns:<3}"
                             f" hosts={d.hosts_active}")
            sys.stdout.flush()
    except KeyboardInterrupt:
        d.pause()
        d.join(5)
    print(f"\n{d.status} in {time.time() - t0:.1f}s -> {d.path}"
          + (f"\n{d.error}" if d.error else ""))
    mgr.shutdown()
    return 0 if d.status == "Complete" else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        sys.exit(run_cli(sys.argv[2:]))
    run_gui(sys.argv[1] if len(sys.argv) > 1 else None)
