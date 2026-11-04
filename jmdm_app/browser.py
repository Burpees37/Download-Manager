"""The browser extension's side door on 127.0.0.1, and the held hand-off."""
import re
import json
import threading
import time
import http.server
import http.client
from .config import APP_NAME, DECISION_WAIT, LISTEN_PORT, VERSION
from .util import clean_err, is_video_url, sanitize
from .net import probe_info
from . import gdrive
from .gates import _script_name, is_gate_url, resolve_link

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


def start_listener(mgr):
    """Listen on 127.0.0.1 for the browser extension: /ping, /settings, /add."""

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
                mgr.last_browser_ping = time.time()
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
            if not bool(req.get("video")) and not req.get("series"):
                url = gdrive.direct_link(url) or url      # Drive page -> the file itself
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

    port = int(mgr.settings.get("listen_port", LISTEN_PORT))
    try:
        mgr._server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return False
    threading.Thread(target=mgr._server.serve_forever, daemon=True).start()
    return True
