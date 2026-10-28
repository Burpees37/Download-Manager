#!/usr/bin/env python3
"""
page_media_grabber.py — find the videos on a web page and download them, one
after another, into a folder.

What it looks for in the page's HTML:
  * <video src="...">  and  <source src="..."> inside <video>/<audio>
  * <a href="...">  pointing at media files (.mp4 .webm .mkv .mov .m4v .mp3 .m4a)
  * HLS playlists (.m3u8) and DASH manifests (.mpd) anywhere in the HTML,
    including inside <script> blocks
  * <meta property="og:video" content="...">

How it downloads:
  * plain files  -> streamed to disk, resumes a half-finished file
  * .m3u8 / .mpd -> handed to ffmpeg, which joins the stream into one .mp4
                    (falls back to a built-in downloader for simple HLS)
  * DRM-protected streams (Widevine/PlayReady/FairPlay) are skipped.

Limits: it reads the HTML the server sends. Pages that build their player
with JavaScript after loading usually have nothing to find. For those, use
the browser extension's "Download video" button instead.

Usage:
  python page_media_grabber.py https://myserver.local/show/
  python page_media_grabber.py URL --list                 # just show what it finds
  python page_media_grabber.py URL --pick 1,3 -o D:\\Videos\\Show
  python page_media_grabber.py --from-file pages.txt -o D:\\Videos\\Show --numbered
  python page_media_grabber.py URL --cookie "session=..." --referer https://site/

Needs:  pip install requests beautifulsoup4   (ffmpeg on PATH for streams)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlsplit, unquote
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
FILE_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".mp3", ".m4a", ".ogg", ".flac")
STREAM_RE = re.compile(r"""(?:https?:)?//[^\s"'<>\\]+?\.(?:m3u8|mpd)(?:\?[^\s"'<>\\]*)?"""
                       r"""|(?<=["'])/[^\s"'<>\\]+?\.(?:m3u8|mpd)(?:\?[^\s"'<>\\]*)?""",
                       re.I)
FILE_RE = re.compile(r"""(?:https?:)?//[^\s"'<>\\]+?\.(?:mp4|webm|m4v|mov)(?:\?[^\s"'<>\\]*)?""",
                     re.I)
DRM_HINTS = ("com.widevine", "com.microsoft.playready", "com.apple.streamingkeydelivery",
             "METHOD=SAMPLE-AES")


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
@dataclass
class MediaItem:
    url: str
    kind: str                 # "file", "hls" or "dash"
    found_in: str             # which tag/where it was found
    label: str = ""           # alt text / title near the video, if any
    page: str = ""            # page it came from (used as Referer)

    def default_name(self) -> str:
        path = unquote(urlsplit(self.url).path)
        base = os.path.splitext(os.path.basename(path))[0] or "video"
        if self.kind != "file" and base.lower() in ("index", "master", "playlist", "manifest"):
            base = self.label or os.path.basename(os.path.dirname(path)) or "video"
        return safe_name(self.label or base)


@dataclass
class Settings:
    out_dir: str = "."
    referer: str | None = None
    cookie: str | None = None
    user_agent: str = UA
    delay: float = 1.0                  # pause between downloads (be kind to servers)
    respect_robots: bool = True
    numbered: bool = False
    extra_headers: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return name[:150] or "video"


def make_session(s: Settings) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": s.user_agent, "Accept-Encoding": "identity"})
    if s.cookie:
        sess.headers["Cookie"] = s.cookie
    sess.headers.update(s.extra_headers)
    return sess


def allowed_by_robots(url: str, user_agent: str) -> bool:
    parts = urlsplit(url)
    rp = robotparser.RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True                     # no robots.txt reachable -> allowed
    return rp.can_fetch(user_agent, url)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --------------------------------------------------------------------------
# 1) fetch
# --------------------------------------------------------------------------
def fetch_page(url: str, sess: requests.Session, s: Settings) -> str:
    if s.respect_robots and not allowed_by_robots(url, s.user_agent):
        raise PermissionError(f"robots.txt asks tools not to fetch {url} "
                              "(use --ignore-robots for your own server)")
    headers = {"Referer": s.referer} if s.referer else {}
    r = sess.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


# --------------------------------------------------------------------------
# 2) extract
# --------------------------------------------------------------------------
def extract_media(html: str, page_url: str) -> list[MediaItem]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, MediaItem] = {}

    def add(raw: str | None, where: str, label: str = ""):
        if not raw or raw.startswith(("blob:", "data:", "javascript:")):
            return
        url = urljoin(page_url, raw.strip())
        if not url.startswith(("http://", "https://")):
            return
        low = urlsplit(url).path.lower()
        kind = "hls" if low.endswith(".m3u8") else "dash" if low.endswith(".mpd") else "file"
        key = url.split("#")[0]
        if key not in found:
            found[key] = MediaItem(url, kind, where, label.strip()[:120], page_url)

    for v in soup.find_all(["video", "audio"]):
        label = v.get("title") or v.get("aria-label") or ""
        add(v.get("src"), f"<{v.name} src>", label)
        add(v.get("data-src"), f"<{v.name} data-src>", label)
        for src in v.find_all("source"):
            add(src.get("src"), "<source src>", label or src.get("title", ""))
            add(src.get("data-src"), "<source data-src>", label)
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"^og:video")}):
        add(meta.get("content"), "<meta og:video>")
    for a in soup.find_all("a", href=True):
        path = urlsplit(urljoin(page_url, a["href"])).path.lower()
        if path.endswith(FILE_EXTS + (".m3u8", ".mpd")):
            add(a["href"], "<a href>", a.get_text(" ", strip=True))
    # stream / file links hidden in scripts or attributes
    for m in STREAM_RE.finditer(html):
        add(m.group(0), "in page text")
    for m in FILE_RE.finditer(html):
        add(m.group(0), "in page text")
    return list(found.values())


# --------------------------------------------------------------------------
# 3) download
# --------------------------------------------------------------------------
def download_file(item: MediaItem, dest: str, sess: requests.Session, s: Settings) -> str:
    """Stream a normal media file to disk, resuming a .part file if present."""
    headers = {"Referer": s.referer or item.page}
    ext = os.path.splitext(urlsplit(item.url).path)[1] or ".mp4"
    path = dest + ext
    part = path + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have:
        headers["Range"] = f"bytes={have}-"
    with sess.get(item.url, headers=headers, stream=True, timeout=30) as r:
        if r.status_code == 416:            # already complete
            os.replace(part, path)
            return path
        r.raise_for_status()
        if have and r.status_code != 206:   # server ignored the resume request
            have = 0
        total = int(r.headers.get("Content-Length", 0)) + have
        mode = "ab" if have else "wb"
        done, t0 = have, time.time()
        with open(part, mode) as fh:
            for chunk in r.iter_content(256 * 1024):
                fh.write(chunk)
                done += len(chunk)
                pct = f"{done / total * 100:5.1f}%" if total else ""
                speed = (done - have) / max(time.time() - t0, 0.01)
                print(f"\r    {pct} {human(done)}  {human(speed)}/s   ", end="", flush=True)
    print()
    os.replace(part, path)
    return path


def _ffmpeg() -> str | None:
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    for cand in (os.path.join(here, "ffmpeg.exe"), os.path.join(here, "..", "ffmpeg.exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffmpeg")


def _looks_drm(text: str) -> bool:
    return any(h.lower() in text.lower() for h in DRM_HINTS)


def download_stream(item: MediaItem, dest: str, sess: requests.Session, s: Settings) -> str:
    """HLS (.m3u8) or DASH (.mpd) -> one .mp4. Uses ffmpeg when available."""
    headers = {"Referer": s.referer or item.page}
    manifest = sess.get(item.url, headers=headers, timeout=30)
    manifest.raise_for_status()
    if _looks_drm(manifest.text):
        raise RuntimeError("stream is DRM-protected — skipped")
    path = dest + ".mp4"
    ff = _ffmpeg()
    if ff:
        hdr = "".join(f"{k}: {v}\r\n" for k, v in
                      {**headers, "User-Agent": s.user_agent,
                       **({"Cookie": s.cookie} if s.cookie else {})}.items())
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-stats", "-y",
               "-headers", hdr, "-i", item.url, "-map", "0:v?", "-map", "0:a?",
               "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", path]
        res = subprocess.run(cmd)
        if res.returncode == 0 and os.path.getsize(path) > 0:
            return path
        raise RuntimeError("ffmpeg could not save this stream")
    if item.kind != "hls":
        raise RuntimeError("DASH streams need ffmpeg (put ffmpeg.exe next to this script)")
    return _hls_builtin(item.url, manifest.text, path, sess, headers)


def _hls_builtin(url: str, text: str, path: str, sess, headers) -> str:
    """Minimal HLS downloader for plain (unencrypted) streams, no ffmpeg needed.
    Saves MPEG-TS data; most players open it even with the .mp4 name, but
    installing ffmpeg gives a proper .mp4."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):     # master: pick best quality
        best, best_bw = None, -1
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF"):
                bw = int((re.search(r"BANDWIDTH=(\d+)", l) or [0, 0])[1])
                nxt = next((x for x in lines[i + 1:] if not x.startswith("#")), None)
                if nxt and bw > best_bw:
                    best, best_bw = urljoin(url, nxt), bw
        url = best
        text = sess.get(url, headers=headers, timeout=30).text
        lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(l.startswith("#EXT-X-KEY") and "METHOD=NONE" not in l for l in lines):
        raise RuntimeError("encrypted stream — install ffmpeg to download it")
    segs = [urljoin(url, l) for l in lines if not l.startswith("#")]
    ts_path = os.path.splitext(path)[0] + ".ts"
    with open(ts_path, "wb") as out:
        for n, seg in enumerate(segs, 1):
            for attempt in range(4):
                try:
                    r = sess.get(seg, headers=headers, timeout=30)
                    r.raise_for_status()
                    out.write(r.content)
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(1 + attempt)
            print(f"\r    segment {n}/{len(segs)}   ", end="", flush=True)
    print()
    return ts_path


def download_item(item: MediaItem, name: str, sess: requests.Session, s: Settings) -> str:
    os.makedirs(s.out_dir, exist_ok=True)
    dest = os.path.join(s.out_dir, safe_name(name))
    if item.kind == "file":
        return download_file(item, dest, sess, s)
    return download_stream(item, dest, sess, s)


# --------------------------------------------------------------------------
# 4) pipeline
# --------------------------------------------------------------------------
def choose(items: list[MediaItem], pick: str | None) -> list[MediaItem]:
    """--pick "1,3-4" -> those items; default: streams first, else all files."""
    if pick:
        idx: set[int] = set()
        for part in pick.split(","):
            a, _, b = part.strip().partition("-")
            idx.update(range(int(a), int(b or a) + 1))
        return [it for i, it in enumerate(items, 1) if i in idx]
    return items


def run(pages: Iterable[str], s: Settings, pick: str | None = None,
        list_only: bool = False) -> int:
    sess = make_session(s)
    counter, failures = 0, 0
    seen: set[str] = set()                  # same video linked from several pages
    for page in pages:
        print(f"\n== {page}")
        low = urlsplit(page).path.lower()
        if low.endswith(FILE_EXTS + (".m3u8", ".mpd")):
            # a direct media link: nothing to scan, download it as is
            kind = "hls" if low.endswith(".m3u8") else "dash" if low.endswith(".mpd") else "file"
            items = [MediaItem(page, kind, "direct link", "", s.referer or page)]
        else:
            try:
                html = fetch_page(page, sess, s)
            except Exception as e:
                print(f"   could not load page: {e}")
                failures += 1
                continue
            items = extract_media(html, page)
        if not items:
            print("   no <video>, media links or .m3u8/.mpd found in the HTML "
                  "(the player may be built by JavaScript)")
            continue
        for i, it in enumerate(items, 1):
            print(f"   [{i}] {it.kind:4} {it.found_in:16} {it.url}")
        if list_only:
            continue
        for it in choose(items, pick):
            if it.url in seen:
                print(f"   already downloaded: {it.url}")
                continue
            seen.add(it.url)
            counter += 1
            name = it.default_name()
            if s.numbered:
                name = f"{counter:02d} - {name}"
            print(f"-> {name}")
            try:
                path = download_item(it, name, sess, s)
                print(f"   saved {path}")
            except Exception as e:
                print(f"   failed: {e}")
                failures += 1
            time.sleep(s.delay)
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Find videos on web pages and download them in order.")
    ap.add_argument("url", nargs="?", help="page to scan")
    ap.add_argument("--from-file", help="text file with one page URL per line (batch)")
    ap.add_argument("-o", "--out", default=".", help="folder to save into")
    ap.add_argument("--list", action="store_true", help="only list what was found")
    ap.add_argument("--pick", help='which items to download, e.g. "1,3-4" (default: all)')
    ap.add_argument("--numbered", action="store_true", help='prefix files "01 - ", "02 - " …')
    ap.add_argument("--referer")
    ap.add_argument("--cookie", help='e.g. "session=abc123" for pages behind your login')
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between downloads")
    ap.add_argument("--ignore-robots", action="store_true",
                    help="skip the robots.txt check (for your own server)")
    a = ap.parse_args(argv)
    pages: list[str] = []
    if a.url:
        pages.append(a.url)
    if a.from_file:
        with open(a.from_file, encoding="utf-8") as fh:
            pages += [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    if not pages:
        ap.error("give a URL or --from-file")
    s = Settings(out_dir=a.out, referer=a.referer, cookie=a.cookie, delay=a.delay,
                 respect_robots=not a.ignore_robots, numbered=a.numbered)
    return run(pages, s, a.pick, a.list)


if __name__ == "__main__":
    sys.exit(main())
