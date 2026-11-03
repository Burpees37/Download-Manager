"""Small helpers: sizes, times, file names, link kinds."""
import os
import sys
import re
import shutil
import urllib.request
import urllib.parse
import urllib.error
from .config import VIDEO_SITES

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # running from source: the folder with jmdm.py, one level above this package
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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



def find_ffmpeg():
    local = os.path.join(app_dir(), "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.path.isfile(local):
        return local
    return shutil.which("ffmpeg")
