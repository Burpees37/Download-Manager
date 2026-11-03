"""Updates: yt-dlp (quietly, daily) and the app itself (asks first)."""
import os
import sys
import re
import json
import shutil
import threading
import urllib.request
import urllib.parse
import urllib.error
from .config import APP_NAME, BUILD, VERSION
from .util import app_dir
from .net import _ssl_ctx

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
