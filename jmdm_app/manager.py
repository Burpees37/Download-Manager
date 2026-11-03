"""The download list, queue, settings and saving."""
import os
import json
import time
import queue
import threading
import urllib.request
import urllib.parse
import urllib.error
from .config import CAPTURE_KEYS, DEFAULT_CAPTURE_TYPES, LISTEN_PORT, UA
from .util import app_dir, clean_err, default_download_dir, is_video_url, is_whole_playlist_url, playlist_url, sanitize, unique_path
from .gates import _script_name
from .updates import have_ytdlp, load_ytdlp
from .series import fetch_html, find_cover, find_episode_media, find_episodes, parse_items, scan_page, series_title
from .downloader import Download, RateLimiter
from .video import VideoDownload
from .browser import start_listener
from .schedule import MAIN, category_of, clean_queues, default_queues, schedule_events
import datetime

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
            "queues": default_queues(),
            "category_folders": False,     # save into Downloads\Videos, \Programs …
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
        self.settings["queues"] = clean_queues(self.settings.get("queues"))
        self.queue_running = {MAIN: True}      # other queues run when started
        self._queue_ran = set()                # queues that started something this run
        self.events = queue.Queue()            # ("queue_done", id) for the window
        self._sched_last = datetime.datetime.now()
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
            mirrors=None, name_prefix="", queue=MAIN):
        url = url.strip()
        if not folder and self.settings.get("category_folders"):
            v = is_video_url(url) if video is None else video
            folder = os.path.join(self.settings["download_dir"],
                                  category_of(filename, url, v))
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
        d.queue = queue if self.queue(queue) else MAIN
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
                     referer="", cookie="", user_agent="", items="", start=True, queue=MAIN,
                     done=None):
        """Expand a YouTube playlist/channel into one queued video per item.
        Runs in the background; `done(count, title, error)` is called at the end."""
        if not folder and self.settings.get("category_folders"):
            folder = os.path.join(self.settings["download_dir"], "Videos")
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
                             user_agent, video=True, quality=quality, start=start, queue=queue)
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
                             start=start, queue=queue, name_prefix=f"{idx:0{width}d} - ")
                    count += 1
                self.notice = f"Added {count} videos from “{title}”"
            except Exception as ex:
                err = clean_err(ex)
                self.notice = "Playlist error: " + err
            if done:
                done(count, title, err)

        threading.Thread(target=work, daemon=True).start()

    def add_series(self, url, folder=None, connections=None, quality=None,
                   referer="", cookie="", user_agent="", items="", start=True, queue=MAIN,
                   links=None, done=None):
        """Whole season from a series page: find the numbered episode links,
        open each episode, find its video and queue them all, in order, into
        one folder named after the series (with its cover).
        `links` = [[href, text], ...] already read from the rendered page by
        the browser extension (works when the list is built by JavaScript)."""
        if not folder and self.settings.get("category_folders"):
            folder = os.path.join(self.settings["download_dir"], "Videos")
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
                                 user_agent, video=False, start=start, queue=queue)
                    else:
                        if not have_ytdlp():
                            skipped.append(n)
                            continue
                        self.add(media, sub, name, connections, ep_url, cookie, user_agent,
                                 video=True, quality=quality, start=start, queue=queue)
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

    # ---- queues -----------------------------------------------------------
    def queue(self, qid):
        return next((q for q in self.settings["queues"] if q["id"] == qid), None)

    def queue_of(self, d):
        qid = getattr(d, "queue", MAIN)
        return qid if self.queue(qid) else MAIN

    def queue_limit(self, qid):
        if qid == MAIN:
            return int(self.settings.get("max_active", 3))
        return int(self.queue(qid)["max_active"])

    def start_queue(self, qid):
        if not self.queue(qid):
            return
        for d in list(self.downloads):
            if self.queue_of(d) == qid and d.status == "Paused":
                d.status = "Queued"
        self.queue_running[qid] = True
        self.mark_dirty()

    def stop_queue(self, qid):
        """Stop a queue: running files go back to waiting in it."""
        if qid == MAIN:
            return
        self.queue_running[qid] = False
        for d in list(self.downloads):
            if self.queue_of(d) == qid and d.status == "Downloading":
                d._requeue = True
                d.pause()
        self.mark_dirty()

    def move_to_queue(self, d, qid):
        if self.queue(qid):
            d.queue = qid
            self.mark_dirty()

    def delete_queue(self, qid):
        if qid == MAIN:
            return
        for d in list(self.downloads):
            if getattr(d, "queue", MAIN) == qid:
                d.queue = MAIN
        self.settings["queues"] = [q for q in self.settings["queues"] if q["id"] != qid]
        self.queue_running.pop(qid, None)
        self.mark_dirty()

    def _tick_schedule(self, now=None):
        now = now or datetime.datetime.now()
        for qid, what in schedule_events(self.settings["queues"], self._sched_last, now):
            if what == "start":
                self.start_queue(qid)
                self.notice = f"Started “{self.queue(qid)['name']}” on schedule"
            else:
                self.stop_queue(qid)
                self.notice = f"Stopped “{self.queue(qid)['name']}” on schedule"
        self._sched_last = now

    def _step(self):
        """One round of the queue: start what may start, notice finished queues."""
        with self._lock:
            items = list(self.downloads)
        for d in items:
            d.update_speed()
            if d.status == "Paused" and getattr(d, "_requeue", False):
                d._requeue = False                    # stopped queue: wait in it again
                d.status = "Queued"
        busy = ("Downloading", "Processing")
        for q in self.settings["queues"]:
            qid = q["id"]
            if not self.queue_running.get(qid):
                continue
            mine = [d for d in items if self.queue_of(d) == qid]
            active = sum(d.status in busy for d in mine)
            for d in mine:
                if active >= self.queue_limit(qid):
                    break
                if d.status == "Queued" and not d.is_running():
                    d.start()
                    active += 1
                    self._queue_ran.add(qid)
            if qid != MAIN and not any(d.status in busy or d.status == "Queued"
                                       for d in mine):
                self.queue_running[qid] = False       # nothing left: the queue is done
                if qid in self._queue_ran:
                    self._queue_ran.discard(qid)
                    self.events.put(("queue_done", qid))
        return sum(d.status in busy for d in items)

    def _loop(self):
        last_save = time.monotonic()
        while self._running:
            try:
                self._tick_schedule()
            except Exception:
                pass
            active = self._step()
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
        return start_listener(self)
