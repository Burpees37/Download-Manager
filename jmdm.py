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
import html.parser
import base64

APP_NAME = "JM Download Manager"
VERSION = "1.7.0"
BUILD = 0          # set by the GitHub build (run number); 0 = built by hand
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CHUNK = 64 * 1024
MIN_SPLIT = 256 * 1024          # never split a part smaller than 2x this
MAX_CONNECTIONS = 32            # per host
MAX_TOTAL_CONNECTIONS = 96      # across all mirrors of one file
STALL_SECONDS = 5               # no data this long = reconnect
SOCKET_TIMEOUT = 30
LISTEN_PORT = 8721
# Which browser downloads the extension hands to the app (IDM-style rules).
# The extension keeps a copy; whichever side changed last wins.
DEFAULT_CAPTURE_TYPES = ("zip rar 7z tar gz bz2 xz zst iso img bin cue exe msi msix appx "
                         "apk xapk apks obb jar dmg deb rpm mp4 mkv avi mov wmv flv webm "
                         "m4v mpg mpeg 3gp mp3 m4a aac flac wav ogg opus wma")
CAPTURE_KEYS = ("browser_capture", "capture_types", "capture_min_kb", "capture_exclude",
                "capture_updated")
# How long a caught download waits for the Add dialog before the browser keeps it.
DECISION_WAIT = 240
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
    name = re.sub(r"\s*:\s+", " - ", name)            # "Show: Season 1" -> "Show - Season 1"
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
    if is_gate_url(text):
        return True
    path = urllib.parse.urlparse(text).path.lower()
    return path.rsplit(".", 1)[-1] in FILE_EXTS if "." in path else False


def find_ffmpeg():
    local = os.path.join(app_dir(), "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.path.isfile(local):
        return local
    return shutil.which("ffmpeg")


# --------------------------------------------------------------------------
# updates: yt-dlp (quietly, daily) and the app itself (asks first)
# --------------------------------------------------------------------------
REPO = "Burpees37/Download-Manager"
APP_UPDATE_INFO = f"https://github.com/{REPO}/releases/download/latest/version.json"
APP_UPDATE_ZIP = f"https://github.com/{REPO}/releases/download/latest/JM-Download-Manager-Windows.zip"
YTDLP_LATEST = "https://github.com/yt-dlp/yt-dlp/releases/latest"
YTDLP_FILE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
_ytdlp_lock = threading.Lock()
_ytdlp_source = ["bundled"]


def ytdlp_zip():
    """A newer yt-dlp downloaded by the app. Python imports it straight from
    the file, so the .exe doesn't need rebuilding when YouTube changes."""
    return os.path.join(app_dir(), "jmdm_data", "yt-dlp", "yt-dlp.zip")


def _forget_ytdlp():
    for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
        del sys.modules[k]


def load_ytdlp():
    """Import yt-dlp, preferring the updated copy; falls back to the one built
    into the app if the update can't load (then it is set aside)."""
    with _ytdlp_lock:
        if "yt_dlp" in sys.modules:
            return sys.modules["yt_dlp"]
        z = ytdlp_zip()
        if os.path.isfile(z):
            sys.path.insert(0, z)
            try:
                import yt_dlp
                _ytdlp_source[0] = "updated"
                return yt_dlp
            except Exception:
                sys.path.remove(z)
                _forget_ytdlp()
                try:
                    os.replace(z, z + ".bad")
                except OSError:
                    pass
        import yt_dlp
        _ytdlp_source[0] = "bundled"
        return yt_dlp


def have_ytdlp():
    try:
        load_ytdlp()
        return True
    except Exception:
        return False


def ytdlp_version():
    try:
        return load_ytdlp().version.__version__
    except Exception:
        return ""


def _ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx())


def latest_ytdlp_version():
    # The releases/latest page redirects to .../tag/<version>; no API limits.
    class Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    opener = urllib.request.build_opener(
        Stop, urllib.request.HTTPSHandler(context=_ssl_ctx()))
    try:
        r = opener.open(urllib.request.Request(YTDLP_LATEST, method="HEAD"), timeout=20)
        loc = r.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
    m = re.search(r"/tag/([\w.]+)", loc or "")
    if m:
        return m.group(1)
    with _get("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest") as r:
        return json.load(r)["tag_name"]


def _zip_ytdlp_version(path):
    import zipfile
    with zipfile.ZipFile(path) as zf:
        text = zf.read("yt_dlp/version.py").decode("utf-8", "replace")
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", text)
    return m.group(1) if m else ""


def update_ytdlp(busy=lambda: False):
    """Download a newer yt-dlp if there is one. Returns the new version, or
    "" when already current. busy(): True while a video download runs."""
    latest = latest_ytdlp_version()
    if _ver_tuple(latest) <= _ver_tuple(ytdlp_version()):
        return ""
    z = ytdlp_zip()
    os.makedirs(os.path.dirname(z), exist_ok=True)
    tmp = z + ".part"
    with _get(YTDLP_FILE, timeout=120) as r, open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh)
    got = _zip_ytdlp_version(tmp)                 # also proves it's a real zip
    if _ver_tuple(got) < _ver_tuple(latest):
        os.remove(tmp)
        raise ValueError("downloaded yt-dlp looks wrong")
    os.replace(tmp, z)
    with _ytdlp_lock:
        if not busy():
            _forget_ytdlp()                       # next video uses the new one
    return got


def app_update_available():
    """{"version", "build"} of a newer build on GitHub, or None. Only for the
    .exe made by the GitHub build (BUILD > 0); hand-built copies are skipped."""
    if not getattr(sys, "frozen", False) or os.name != "nt" or BUILD <= 0:
        return None
    with _get(APP_UPDATE_INFO) as r:
        info = json.load(r)
    if int(info.get("build", 0)) > BUILD:
        return {"version": str(info.get("version", "")), "build": int(info["build"])}
    return None


def _manifest_version(folder):
    try:
        with open(os.path.join(folder, "extension", "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version", "")
    except Exception:
        return ""


def prepare_app_update(progress=lambda done, total: None):
    """Download and unpack the new build, then write a script that swaps the
    files once this app has closed and starts it again. Returns
    (script path, extension_changed). jmdm_data (list, settings) is untouched."""
    import zipfile
    import tempfile
    here = app_dir()
    work = os.path.join(tempfile.gettempdir(), "jmdm-update")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    zpath = os.path.join(work, "update.zip")
    with _get(APP_UPDATE_ZIP, timeout=60) as r, open(zpath, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            progress(done, total)
    new = os.path.join(work, "new")
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(new)
    if not os.path.isfile(os.path.join(new, APP_NAME + ".exe")):
        raise ValueError("the update is missing the app")
    ext_changed = _manifest_version(new) != _manifest_version(here)
    exe = os.path.join(here, APP_NAME + ".exe")
    bat = os.path.join(work, "update.bat")
    lines = [
        "@echo off",
        f"set PID={os.getpid()}",
        "set W=0",
        ":wait",
        # tasklist names the .exe only while that exact process still runs
        'tasklist /FI "PID eq %PID%" /NH 2>nul | find /i ".exe" >nul || goto closed',
        "set /a W+=1",
        "if %W% lss 60 (timeout /t 1 /nobreak >nul & goto wait)",
        ":closed",
    ]
    files = [f for f in os.listdir(new) if os.path.isfile(os.path.join(new, f))]
    for i, f in enumerate(files):
        # retry: the browser helper or ffmpeg may still be closing
        lines += ["set N=0",
                  f":copy{i}",
                  f'copy /y "{os.path.join(new, f)}" "{here}" >nul && goto next{i}',
                  "set /a N+=1",
                  f"if %N% lss 30 (timeout /t 1 /nobreak >nul & goto copy{i})",
                  f":next{i}"]
    if os.path.isdir(os.path.join(new, "extension")):
        lines.append(f'xcopy /e /i /y /q "{os.path.join(new, "extension")}" '
                     f'"{os.path.join(here, "extension")}" >nul')
    # start the new app, then remove this folder (the script included)
    lines += [f'start "" "{exe}"', 'cd /d "%TEMP%"', f'(goto) 2>nul & rd /s /q "{work}"']
    with open(bat, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as fh:
        fh.write("\r\n".join(lines) + "\r\n")
    return bat, ext_changed


def run_app_update(bat):
    import subprocess
    flags = 0x08000000 | 0x00000008               # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True)


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
        # the gate.php-style link this file came from: {"url","referer","cookie","post"}
        self.gate = {}
        self.post = ""              # form data, when the file only comes back for a form
        self._pending = None        # that form's answer, already open
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
                "downloaded": self.downloaded, "added": self.added,
                "gate": self.gate, "post": self.post}

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
        obj.gate = d.get("gate") or {}
        obj.post = d.get("post", "")
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
                "user_agent": self.user_agent, "post": self.post}

    def _headers(self, src):
        h = {"User-Agent": src.get("user_agent") or UA, "Accept": "*/*",
             "Accept-Encoding": "identity", "Connection": "keep-alive"}
        if src.get("referer"):
            h["Referer"] = src["referer"]
        if src.get("cookie"):
            h["Cookie"] = src["cookie"]
        return h

    def _open(self, src, start=None, end=None, timeout=30):
        if src.get("post"):
            h = dict(self._headers(src), **{"Content-Type": "application/x-www-form-urlencoded"})
            req = urllib.request.Request(src["url"], data=src["post"].encode(), headers=h,
                                         method="POST")
            return urllib.request.urlopen(req, timeout=timeout)
        req = urllib.request.Request(src["url"], headers=self._headers(src))
        if start is not None:
            req.add_header("Range", f"bytes={start}-" + ("" if end is None else str(end)))
        return urllib.request.urlopen(req, timeout=timeout)

    def _probe(self):
        # A gate link (gate.php, go.php …) or redirect page is followed to the
        # real file; a plain file link is just one request, as before.
        g = self.gate or {"url": self.url, "referer": self.referer, "cookie": self.cookie,
                          "post": self.post}
        r = resolve_link(g["url"], g["referer"], g["cookie"], self.user_agent,
                         post=g.get("post") or None, keep_response=True)
        self.use_resolved(g, r)
        info = r["info"]
        self.size = info["size"]
        self.resumable = info["resumable"]
        if not self.path:
            name = sanitize(self.filename) if self.filename and \
                not _script_name(self.filename) else info["filename"]
            if _script_name(name):
                name = "download"                   # a script's name isn't the file's
            os.makedirs(self.folder, exist_ok=True)
            self.path = unique_path(self.folder, name)
            self.filename = os.path.basename(self.path)
        self.segments = []

    def use_resolved(self, g, r):
        """Take over what resolve_link() found for gate g."""
        if r["via_page"]:
            self.gate = dict(g)
            self.url, self.referer, self.cookie = r["url"], r["referer"], r["cookie"]
            self.post = r.get("post", "")
        if r.get("response") is not None:
            if self._pending is not None:
                self._pending.close()
            self._pending = _UrllibResp(r["response"])
        self.size = r["info"]["size"]
        self.resumable = r["info"]["resumable"]

    def _refresh_gate(self):
        """Resuming a file that came through a gate link: the file link it gave
        may have expired, so go through the gate again and use the new link if
        it leads to the same file."""
        g = self.gate
        try:
            r = resolve_link(g["url"], g.get("referer", ""), g.get("cookie", ""),
                             self.user_agent, post=g.get("post") or None,
                             keep_response=True)
        except Exception:
            return                                  # keep the old link and try it
        if r["info"]["size"] == self.size or r.get("response") is not None:
            self.use_resolved(g, r)

    def _check_mirror(self, m):
        """A mirror is only used if it serves exactly the same size with ranges."""
        g = m.get("gate") or m
        r = resolve_link(g["url"], g.get("referer", ""), g.get("cookie", ""),
                         m.get("user_agent", "") or self.user_agent)
        info = r["info"]
        if r["via_page"]:
            m["gate"] = {k: g.get(k, "") for k in ("url", "referer", "cookie")}
            m.update(url=r["url"], referer=r["referer"], cookie=r["cookie"])
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
            elif self.gate and self._pending is None:
                self._refresh_gate()
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
                resp, self._pending = self._pending, None
            if resp is None:
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
        name = sanitize(self.filename) if self.filename else ""
        if re.search(r"\.(mp4|mkv|webm|m4v|mov|m4a|mp3)$", name, re.I):
            name = name.rsplit(".", 1)[0]
        self.custom_name = name

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
            yt_dlp = load_ytdlp()
        except Exception:
            self.status = "Error"
            self.error = "yt-dlp is not installed (pip install yt-dlp)"
            self.m.mark_dirty()
            return
        ffmpeg = find_ffmpeg()
        q = self.quality
        height = {"best": 1080, "1080p": 1080, "720p": 720, "480p": 480,
                  "8K": 4320, "4K": 2160, "1440p": 1440}.get(q)
        if q == "audio":
            fmt = "ba[ext=m4a]/ba/b"
        elif ffmpeg:
            # Prefer H.264 video + AAC (m4a) audio: plays in every Windows player.
            # YouTube's top picks are VP9/AV1 + Opus, and Opus inside .mp4 is the
            # "audio not supported" error in Movies & TV / Windows Media Player.
            # "max" takes the highest resolution (4K/8K are VP9/AV1 only) but
            # still uses AAC audio.
            h = f"[height<={height}]" if height else ""
            if q in ("max", "8K", "4K", "1440p"):
                # Above 1080p YouTube only has VP9/AV1 video (8K is AV1).
                fmt = f"bv*{h}+ba[ext=m4a]/bv*{h}+ba/b{h}/b"
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
# series pages: "cover + episode 1 2 3 …" -> whole season
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# gate / redirect links (gate.php, go.php, out.php, download.php …)
# --------------------------------------------------------------------------
# Query keys that usually carry the real link inside a gate link.
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


# --------------------------------------------------------------------------
# manager: queue, persistence, browser listener
# --------------------------------------------------------------------------
def settle(job, accepted):
    """Record the answer for a browser download that is waiting on the Add
    dialog. Returns False when the wait already ran out and the browser kept
    the file, so the app must not download it as well."""
    dec = job.get("decision")
    if not dec:
        return True
    with dec["lock"]:
        if dec["expired"]:
            return False
        dec["accepted"] = accepted
        dec["event"].set()
        return True


def decision_expired(job):
    dec = job.get("decision")
    return bool(dec and dec["expired"])


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
            "browser_capture": True,
            "capture_types": DEFAULT_CAPTURE_TYPES,
            "capture_min_kb": 0,
            "capture_exclude": "",
            "capture_updated": 0,
            "auto_update_ytdlp": True,
            "check_app_updates": True,
            "last_update_check": 0,
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

    def capture_settings(self):
        return {k: self.settings[k] for k in CAPTURE_KEYS}

    def update_capture(self, new):
        """Rules sent by the extension; kept only if newer than ours."""
        try:
            stamp = float(new.get("capture_updated", 0))
        except (TypeError, ValueError):
            return False
        if stamp <= float(self.settings.get("capture_updated", 0)):
            return False
        try:
            self.settings.update(
                browser_capture=bool(new.get("browser_capture", True)),
                capture_types=str(new.get("capture_types", ""))[:2000],
                capture_min_kb=max(0, int(new.get("capture_min_kb", 0))),
                capture_exclude=str(new.get("capture_exclude", ""))[:4000],
                capture_updated=stamp)
        except (TypeError, ValueError):
            return False
        self.mark_dirty()
        return True

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

    def add_checked(self, job, r):
        """A browser download the app has already opened (see the listener's
        "verify"): the site sent the file straight back for a form, so the open
        answer must be used now — it starts at once, without the Add dialog."""
        d = Download(self, job["url"], self.settings["download_dir"], job["filename"],
                     1, job["referer"], job["cookie"], job["user_agent"])
        g = {"url": job["url"], "referer": job["referer"], "cookie": job["cookie"],
             "post": job.get("post", "")}
        d.use_resolved(g, r)
        name = sanitize(d.filename) if d.filename and \
            not _script_name(d.filename) else r["info"]["filename"]
        if _script_name(name):
            name = "download"
        os.makedirs(d.folder, exist_ok=True)
        d.path = unique_path(d.folder, name)
        d.filename = os.path.basename(d.path)
        d.note = "Sent by a site's form — downloads on one connection"
        with self._lock:
            self.downloads.append(d)
        d.start()
        self.notice = f"Downloading “{d.filename}”"
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
                yt_dlp = load_ytdlp()
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

    def add_series(self, url, folder=None, connections=None, quality=None,
                   referer="", cookie="", user_agent="", items="", start=True,
                   links=None, done=None):
        """Whole season from a series page: find the numbered episode links,
        open each episode, find its video and queue them all, in order, into
        one folder named after the series (with its cover).
        `links` = [[href, text], ...] already read from the rendered page by
        the browser extension (works when the list is built by JavaScript)."""
        folder = folder or self.settings["download_dir"]
        self.notice = "Reading episode list…"

        def work():
            count, title, err = 0, "", ""
            try:
                page_html, base = fetch_html(url, referer, cookie, user_agent)
                p = scan_page(page_html)
                title = series_title(p)
                all_links = list(p.links)
                for item in links or []:
                    if isinstance(item, (list, tuple)) and item:
                        all_links.append([str(item[0]), str(item[1]) if len(item) > 1 else "", {}])
                eps = find_episodes(all_links, base)
                if not eps:
                    raise IOError("No numbered episode links found on this page. If the "
                                  "episode list only appears in the browser, right-click "
                                  "the page → “Download all episodes on this page”")
                wanted = parse_items(items, [n for n, _ in eps])
                eps = [(n, u) for n, u in eps if n in wanted]
                width = max(2, len(str(eps[-1][0]))) if eps else 2
                sub = os.path.join(folder, title)
                os.makedirs(sub, exist_ok=True)
                cover = find_cover(p, base)
                if cover:
                    try:
                        req = urllib.request.Request(cover, headers={
                            "User-Agent": user_agent or UA, "Referer": base})
                        with urllib.request.urlopen(req, timeout=20) as r:
                            data = r.read(10 * 1024 * 1024)
                        ext = os.path.splitext(urllib.parse.urlsplit(cover).path)[1].lower()
                        ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"
                        with open(os.path.join(sub, "cover" + ext), "wb") as fh:
                            fh.write(data)
                    except Exception:
                        pass
                skipped = []
                for i, (n, ep_url) in enumerate(eps, 1):
                    self.notice = f"Reading episode {n} ({i}/{len(eps)}) of “{title}”…"
                    name = f"{title} - E{n:0{width}d}"
                    try:
                        ep_html, ep_base = fetch_html(ep_url, base, cookie, user_agent)
                        media, kind = find_episode_media(ep_html, ep_base)
                    except Exception:
                        media, kind = None, None
                    if media is None:
                        # nothing in the HTML: let yt-dlp try the episode page itself
                        media, kind = ep_url, "page"
                    if kind == "file":
                        ext = os.path.splitext(urllib.parse.urlsplit(media).path)[1] or ".mp4"
                        self.add(media, sub, name + ext, connections, ep_url, cookie,
                                 user_agent, video=False, start=start)
                    else:
                        if not have_ytdlp():
                            skipped.append(n)
                            continue
                        self.add(media, sub, name, connections, ep_url, cookie, user_agent,
                                 video=True, quality=quality, start=start)
                    count += 1
                    time.sleep(0.2)                  # be gentle with the server
                self.notice = (f"Added {count} episodes of “{title}”"
                               + (f" (skipped {len(skipped)}: video tool missing)"
                                  if skipped else ""))
            except Exception as ex:
                err = clean_err(ex)
                self.notice = "Season error: " + err
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
            if d.gate:
                d.url, d.referer, d.cookie = (d.gate["url"], d.gate.get("referer", ""),
                                              d.gate.get("cookie", ""))
                d.post = d.gate.get("post", "")
                d.gate = {}
            d._pending = None
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
                if self.path == "/settings" and self._allowed():
                    return self._reply(200, mgr.capture_settings())
                self._reply(403, {"error": "forbidden"})

            def _hand_over(self, job, wait):
                """Queue a caught download. With "Ask before starting" on, the
                browser holds its copy until the Add dialog is answered:
                Download -> ok; Cancel or no answer -> the browser keeps it."""
                if not (wait and mgr.settings.get("ask_before_start", True)):
                    self._reply(200, {"ok": True})
                    mgr.incoming.put(job)
                    return
                dec = {"event": threading.Event(), "lock": threading.Lock(),
                       "accepted": False, "expired": False}
                job["decision"] = dec
                mgr.incoming.put(job)
                dec["event"].wait(DECISION_WAIT)
                with dec["lock"]:
                    if not dec["event"].is_set():
                        dec["expired"] = True
                ok = dec["accepted"] and not dec["expired"]
                self._reply(200, {"ok": ok} if ok else
                            {"ok": False, "error": "cancelled in the app"})

            def do_POST(self):
                if self.path == "/settings" and self._allowed():
                    try:
                        n = int(self.headers.get("Content-Length", "0"))
                        mgr.update_capture(json.loads(self.rfile.read(min(n, 1 << 16)) or b"{}"))
                    except Exception:
                        return self._reply(400, {"error": "bad request"})
                    return self._reply(200, mgr.capture_settings())
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
                    "filename": sanitize(req["filename"]) if req.get("filename")
                    and not _script_name(str(req["filename"])) else "",
                    "referer": str(req.get("referer", ""))[:2000],
                    "cookie": str(req.get("cookie", ""))[:16000],
                    "user_agent": str(req.get("userAgent", ""))[:500],
                    "video": bool(req.get("video")) or is_video_url(url),
                    "mirror_of": None, "size": None,
                    "title": str(req.get("title", ""))[:150],
                    "series": bool(req.get("series")),
                    "links": [[str(x[0])[:2000], str(x[1])[:100]]
                              for x in (req.get("links") or [])[:3000]
                              if isinstance(x, (list, tuple)) and x],
                }
                post = req.get("post")
                job["post"] = post if isinstance(post, str) else ""
                if req.get("verify") and not job["video"] and not job["series"]:
                    # The browser has paused its own download and waits for this
                    # answer: ok -> it cancels its copy; not ok -> it carries on, so
                    # nothing is lost when a link (e.g. gate.php) can't be re-opened.
                    try:
                        r = resolve_link(url, job["referer"], job["cookie"],
                                         job["user_agent"], post=job["post"] or None,
                                         keep_response=True)
                    except Exception as ex:
                        return self._reply(200, {"ok": False, "error": clean_err(ex)})
                    if r.get("response") is not None:
                        try:
                            mgr.add_checked(job, r)
                        except Exception as ex:
                            r["response"].close()
                            return self._reply(200, {"ok": False, "error": clean_err(ex)})
                        return self._reply(200, {"ok": True})
                    job.update(url=r["url"], referer=r["referer"], cookie=r["cookie"],
                               size=r["info"]["size"])
                    if r["info"]["resumable"]:
                        same = mgr.find_same_file(r["info"]["size"], r["url"])
                        if same:
                            job["mirror_of"] = same.id
                    return self._hand_over(job, bool(req.get("wait")))
                self._reply(200, {"ok": True})
                if not job["video"] and not job["series"] and not is_gate_url(url):
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
                   video=None, from_browser=False, season=False, series_links=None,
                   job=None):
        # job: a browser download held until this dialog is answered
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
        season_v = tk.BooleanVar(value=season)
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
                     values=("best", "max", "8K", "4K", "1440p", "1080p", "720p",
                             "480p", "audio")).pack(side="left")
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
        season_chk = ttk.Checkbutton(
            frm, variable=season_v,
            text="Whole season — follow the numbered episode links on this page "
                 "(uses “Only items” too)")
        season_chk.grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
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
            if job is not None and not settle(job, True):
                messagebox.showinfo("Add download", "The browser already downloaded this "
                                    "file because nobody answered in time.", parent=win)
                win.destroy()
                return
            last_clip[0] = u
            if season_v.get() and not is_video_url(u):
                mgr.add_series(u, dir_v.get().strip(), conns, q_v.get(),
                               ref_v.get().strip(), cookie_v.get().strip(), user_agent,
                               items=items_v.get(), start=start_v.get(),
                               links=series_links)
                win.destroy()
                return
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
            page_like = bool(re.match(r"^https?://", u)) and not is_video_url(u) \
                and not re.search(r"\.(zip|rar|7z|exe|msi|iso|apk|mp4|mkv|webm|m3u8|mpd|mp3|pdf)"
                                  r"(\?|$)", urllib.parse.urlsplit(u).path + "?", re.I)
            season_chk.state(["!disabled"] if page_like else ["disabled"])
            if not page_like:
                season_v.set(False)
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
        def cancel():
            if job is not None:
                settle(job, False)                     # the browser downloads it instead
            win.destroy()

        def watch_expiry():
            try:
                if not win.winfo_exists():
                    return
            except tk.TclError:
                return                                 # dialog already closed
            if decision_expired(job):
                win.destroy()                          # timed out: the browser kept it
                mgr.notice = "No answer in time — the browser downloaded it instead"
                return
            win.after(1000, watch_expiry)

        if job is not None:
            ttk.Label(frm, text="Cancel lets the browser download it as usual.",
                      foreground="#777").grid(row=r + 1, column=0, columnspan=3, sticky="w")
            win.after(1000, watch_expiry)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.bind("<Return>", lambda e: ok())
        win.bind("<Escape>", lambda e: cancel())
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
        ytu_v = tk.BooleanVar(value=s["auto_update_ytdlp"])
        appu_v = tk.BooleanVar(value=s["check_app_updates"])
        ask_v = tk.BooleanVar(value=s["ask_before_start"])
        q_v = tk.StringVar(value=s["video_quality"])
        cap_v = tk.BooleanVar(value=s["browser_capture"])
        types_v = tk.StringVar(value=s["capture_types"])
        min_v = tk.IntVar(value=s["capture_min_kb"])
        excl_v = tk.StringVar(value=s["capture_exclude"])
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
                                                    values=("best", "max", "8K", "4K",
                                                            "1440p", "1080p", "720p",
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
        cap = ttk.LabelFrame(frm, text="Browser downloads (same rules as the extension popup)",
                             padding=8)
        cap.grid(row=n + 2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        cap.columnconfigure(1, weight=1)
        ttk.Checkbutton(cap, text="Catch downloads started in the browser",
                        variable=cap_v).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(cap, text="File types:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(cap, textvariable=types_v, width=60).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(cap, text="Default", command=lambda: types_v.set(DEFAULT_CAPTURE_TYPES)
                   ).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(cap, text="(blank = every file type)", foreground="#777"
                  ).grid(row=2, column=1, sticky="w")
        ttk.Label(cap, text="Smaller than (KB):").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(cap, from_=0, to=10000000, increment=100, textvariable=min_v,
                    width=10).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(cap, text="Never catch on:").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(cap, textvariable=excl_v).grid(row=4, column=1, columnspan=2, sticky="ew",
                                                 pady=3)
        ttk.Label(cap, text="Sites separated by spaces, e.g. drive.google.com mega.nz. "
                            "Hold Alt while clicking a link to skip the app once.",
                  foreground="#777").grid(row=5, column=0, columnspan=3, sticky="w")
        upf = ttk.LabelFrame(frm, text="Updates", padding=8)
        upf.grid(row=n + 3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        upf.columnconfigure(0, weight=1)
        ttk.Checkbutton(upf, text="Keep yt-dlp (YouTube, Facebook…) up to date by itself",
                        variable=ytu_v).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(upf, text="Tell me when a new version of the app is out",
                        variable=appu_v).grid(row=1, column=0, sticky="w")
        ttk.Button(upf, text="Check now", command=lambda: update_check(manual=True)
                   ).grid(row=0, column=1, rowspan=2, padx=(6, 0))
        build = f"build {BUILD}" if BUILD else "built by hand — app updates come from git"
        ttk.Label(upf, text=f"App {VERSION} ({build}) · yt-dlp {ytdlp_version() or '—'} "
                            f"({_ytdlp_source[0]})", foreground="#777"
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ff = find_ffmpeg()
        notes = [f"Browser extension: {'listening on port ' + str(s['listen_port']) if listening else 'port busy — is another copy running?'}",
                 f"Browser can start the app: {'yes — ' if integration['ok'] else 'no — '}{integration['msg']}",
                 f"yt-dlp (video sites): {'installed' if have_ytdlp() else 'NOT installed'}",
                 f"ffmpeg (HD video + audio merge): {ff or 'not found — put ffmpeg.exe next to the app'}"]
        ttk.Label(frm, text="\n".join(notes), foreground="#555",
                  justify="left").grid(row=n + 4, column=0, columnspan=3, sticky="w",
                                       pady=(10, 0))

        def save():
            try:
                s.update(download_dir=dir_v.get().strip() or default_download_dir(),
                         connections=max(1, min(MAX_CONNECTIONS, int(conn_v.get()))),
                         max_active=max(1, int(act_v.get())),
                         speed_limit_kb=max(0, int(lim_v.get())),
                         clipboard_watch=clip_v.get(),
                         ask_before_start=ask_v.get(),
                         auto_update_ytdlp=ytu_v.get(),
                         check_app_updates=appu_v.get(),
                         video_quality=q_v.get())
                rules = (cap_v.get(), " ".join(types_v.get().split()),
                         max(0, int(min_v.get())), " ".join(excl_v.get().split()))
                if rules != (s["browser_capture"], s["capture_types"], s["capture_min_kb"],
                             s["capture_exclude"]):
                    s.update(browser_capture=rules[0], capture_types=rules[1],
                             capture_min_kb=rules[2], capture_exclude=rules[3],
                             capture_updated=time.time())
            except Exception:
                messagebox.showerror("Settings", "Please enter whole numbers.", parent=win)
                return
            mgr.apply_settings()
            win.destroy()

        b = ttk.Frame(frm)
        b.grid(row=n + 5, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left")

    # ---- updates ----
    upd = {"busy": False, "offer": None, "report": None, "ready": None}

    def video_busy():
        return any(d.kind == "video" and d.status == "Downloading" for d in mgr.downloads)

    def update_check(manual=False):
        if upd["busy"]:
            return
        upd["busy"] = True

        def work():
            lines = []
            try:
                if manual or mgr.settings.get("auto_update_ytdlp", True):
                    try:
                        v = update_ytdlp(video_busy)
                        if v:
                            mgr.notice = f"yt-dlp updated to {v}"
                            lines.append(f"yt-dlp updated to {v}.")
                        else:
                            lines.append(f"yt-dlp is up to date ({ytdlp_version()}).")
                    except Exception as e:
                        lines.append(f"yt-dlp: couldn't check ({clean_err(e)}).")
                if manual or mgr.settings.get("check_app_updates", True):
                    try:
                        offer = app_update_available()
                        if offer:
                            upd["offer"] = offer
                        elif BUILD <= 0:
                            lines.append("This copy was built by hand, so app updates come "
                                         "from the GitHub repo (git pull, then build.bat).")
                        else:
                            lines.append(f"The app is up to date ({VERSION}).")
                    except Exception as e:
                        lines.append(f"App: couldn't check ({clean_err(e)}).")
                mgr.settings["last_update_check"] = time.time()
                mgr.mark_dirty()
            finally:
                upd["busy"] = False
                if manual and not upd["offer"]:
                    upd["report"] = lines

        threading.Thread(target=work, daemon=True).start()

    def daily_check():
        if time.time() - float(mgr.settings.get("last_update_check", 0)) > 86400:
            update_check()
        root.after(3600 * 1000, daily_check)

    def install_update(offer):
        if any(d.status == "Downloading" for d in mgr.downloads) and not messagebox.askyesno(
                "Update", "Downloads are running. They'll be paused and continue after the "
                          "update. Go ahead?"):
            return

        def progress(done, total):
            mgr.notice = (f"Downloading the update… {done * 100 // total}%" if total
                          else f"Downloading the update… {human_size(done)}")

        def work():
            try:
                bat, ext = prepare_app_update(progress)
                upd["ready"] = (offer, bat, ext)
            except Exception as e:
                mgr.notice = f"Update failed: {clean_err(e)}"

        mgr.notice = "Downloading the update…"
        threading.Thread(target=work, daemon=True).start()

    def finish_update(offer, bat, ext):
        mgr.settings["just_updated"] = {"version": offer["version"], "extension": ext}
        mgr.shutdown()
        try:
            run_app_update(bat)
        except Exception as e:
            mgr.settings.pop("just_updated", None)
            mgr.save()
            messagebox.showerror("Update", f"Couldn't start the update: {clean_err(e)}")
            return
        root.destroy()

    def poll_updates():
        if upd["offer"]:
            offer, upd["offer"] = upd["offer"], None
            if messagebox.askyesno(
                    "Update available",
                    f"JM Download Manager {offer['version']} (build {offer['build']}) is "
                    f"out. You have {VERSION} (build {BUILD}).\n\nUpdate now? Your download "
                    "list and settings are kept, and the app reopens by itself."):
                install_update(offer)
        if upd["report"]:
            lines, upd["report"] = upd["report"], None
            messagebox.showinfo("Updates", "\n".join(lines))
        if upd["ready"]:
            ready, upd["ready"] = upd["ready"], None
            finish_update(*ready)
            return
        root.after(1000, poll_updates)

    def after_update_note():
        info = mgr.settings.pop("just_updated", None)
        if not info:
            return
        mgr.mark_dirty()
        text = f"Updated to version {VERSION}."
        if info.get("extension"):
            text += ("\n\nThe browser extension changed too. Open brave://extensions "
                     "(or chrome://extensions) and click the reload arrow on "
                     "JM Download Manager.")
        messagebox.showinfo("Updated", text)

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
            if req.get("series"):
                if mgr.settings.get("ask_before_start", True):
                    add_dialog(req["url"], "", req["referer"], req["cookie"],
                               req["user_agent"], False, from_browser=True,
                               season=True, series_links=req.get("links"))
                else:
                    mgr.add_series(req["url"], referer=req["referer"], cookie=req["cookie"],
                                   user_agent=req["user_agent"], links=req.get("links"))
                continue
            if decision_expired(req):
                continue                               # the browser already kept it
            same = next((x for x in mgr.downloads if x.id == req.get("mirror_of")), None)
            if same is not None and messagebox.askyesno(
                    "Same file from another host",
                    f"This looks like the same file as\n\n  {same.filename}\n"
                    f"({human_size(same.size)})\n\nUse it as an extra mirror? Both hosts "
                    "will download it together, so their speeds add up.\n\n"
                    "Choose No to download it separately.") and settle(req, True):
                mgr.add_mirror(same, req["url"], req["referer"], req["cookie"],
                               req["user_agent"])
                tree_select[0] = same.id
                continue
            if req["video"] and not req["filename"] and req.get("title") \
                    and not is_video_url(req["url"]):
                req["filename"] = sanitize(req["title"])   # stream from a generic site
            if mgr.settings.get("ask_before_start", True):
                add_dialog(req["url"], req["filename"], req["referer"], req["cookie"],
                           req["user_agent"], req["video"], from_browser=True, job=req)
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
                        and not any(txt in (x.url, (x.gate or {}).get("url"))
                                    for x in mgr.downloads):
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
    root.after(800, after_update_note)
    root.after(8000, daily_check)
    root.after(1000, poll_updates)
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
    ap.add_argument("--items", default="", help="playlist/season items, e.g. 1-10")
    ap.add_argument("--season", action="store_true",
                    help="series page: download every numbered episode into one folder")
    a = ap.parse_args(argv)
    mgr = Manager(persist=False)
    mgr.settings["speed_limit_kb"] = a.limit
    mgr.apply_settings()
    if a.playlist or a.season:
        done = threading.Event()
        if a.season:
            mgr.add_series(a.url, a.output or os.getcwd(), a.connections, a.quality,
                           a.referer, a.cookie, items=a.items, done=lambda *x: done.set())
        else:
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
