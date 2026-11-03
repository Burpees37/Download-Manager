"""The download engine: one file in parts over many connections and mirrors."""
import os
import re
import time
import uuid
import threading
import urllib.request
import urllib.parse
import urllib.error
from .config import CHUNK, MAX_CONNECTIONS, MAX_TOTAL_CONNECTIONS, MIN_SPLIT, STALL_SECONDS, UA
from .util import clean_err, human_size, sanitize, unique_path
from .net import Link, _UrllibResp
from .gates import _script_name, resolve_link

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
        self.queue = "main"         # which queue it waits in (see schedule.py)
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
                "gate": self.gate, "post": self.post, "queue": self.queue}

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
        obj.queue = d.get("queue", "main")
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
