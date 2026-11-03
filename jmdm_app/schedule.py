"""Categories (by file type), queues and the scheduler's clock — IDM-style.

A queue is a named group of downloads with its own "how many at once".
The main queue always runs. Other queues run when you start them, or when
their schedule says so, and can do something when they finish (close the
app, sleep, shut down). Everything here is plain data, so it is easy to test.
"""
import datetime
import os
import urllib.parse
import uuid

# Shown in this order in the sidebar. "Other" catches the rest.
CATEGORIES = [
    ("Compressed", "zip rar 7z tar gz tgz bz2 xz zst cab iso img bin cue dmg 001 002 r00"),
    ("Programs", "exe msi msix msixbundle appx appxbundle apk xapk apks obb jar deb rpm"),
    ("Videos", "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts m3u8 mpd"),
    ("Music", "mp3 m4a aac flac wav ogg opus wma"),
    ("Documents", "pdf epub mobi doc docx xls xlsx ppt pptx txt rtf odt csv psd"),
]
CATEGORY_NAMES = [c for c, _ in CATEGORIES] + ["Other"]
_EXT = {e: c for c, exts in CATEGORIES for e in exts.split()}

MAIN = "main"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WHEN_DONE = {"none": "Do nothing", "exit": "Close the app",
             "sleep": "Put the computer to sleep", "shutdown": "Shut down the computer"}


def _ext(name):
    base = os.path.basename(name or "").lower()
    if "." not in base:
        return ""
    ext = base.rsplit(".", 1)[1]
    if ext.startswith("part") and ext[4:].isdigit():          # x.part1 -> archive part
        return "rar"
    return ext


def category_of(filename="", url="", video=False):
    """Which sidebar category a download belongs in."""
    if video:
        return "Videos"
    for name in (filename, urllib.parse.urlsplit(url or "").path):
        ext = _ext(name)
        if ext:
            return _EXT.get(ext, "Other")
    return "Other"


def download_category(d):
    return category_of(d.filename or os.path.basename(d.path or ""), d.url,
                       getattr(d, "kind", "file") == "video")


# ---- queues ----------------------------------------------------------------
def new_queue(name, max_active=3):
    return {"id": uuid.uuid4().hex[:8], "name": name, "max_active": max_active,
            "start_enabled": False, "start_time": "01:00", "days": list(range(7)),
            "stop_enabled": False, "stop_time": "07:00", "when_done": "none"}


def default_queues():
    main = new_queue("Main queue")
    main["id"] = MAIN
    return [main]


def clean_queues(qs):
    """Settings from disk -> a valid list, the main queue always first."""
    out, seen = [], set()
    for q in qs if isinstance(qs, list) else []:
        if not isinstance(q, dict) or not q.get("id") or q["id"] in seen:
            continue
        base = new_queue(str(q.get("name") or "Queue"))
        base.update({k: q[k] for k in base if k in q})
        base["id"] = str(q["id"])
        try:
            base["max_active"] = max(1, min(10, int(base["max_active"])))
        except (TypeError, ValueError):
            base["max_active"] = 3
        base["days"] = sorted({int(x) for x in base.get("days") or [] if str(x).isdigit()
                               and 0 <= int(x) <= 6})
        if base["when_done"] not in WHEN_DONE:
            base["when_done"] = "none"
        for k in ("start_time", "stop_time"):
            if parse_hhmm(base[k]) is None:
                base[k] = "01:00" if k == "start_time" else "07:00"
        seen.add(base["id"])
        out.append(base)
    if MAIN not in seen:
        out = default_queues() + out
    else:
        out.sort(key=lambda q: q["id"] != MAIN)
    return out


def parse_hhmm(text):
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
    except ValueError:
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def _crossed(hhmm, days, last, now):
    """True if the clock passed hh:mm on one of `days` in (last, now]."""
    t = parse_hhmm(hhmm)
    if t is None or now <= last or now - last > datetime.timedelta(hours=6):
        return False                       # asleep or clock jumped: don't fire late
    day = last.date()
    while day <= now.date():
        at = datetime.datetime.combine(day, datetime.time(*t))
        if last < at <= now and at.weekday() in days:
            return True
        day += datetime.timedelta(days=1)
    return False


def schedule_events(queues, last, now):
    """[(queue_id, "start"|"stop")] the scheduler should act on between two ticks."""
    out = []
    for q in queues:
        if q["id"] == MAIN:
            continue
        if q.get("start_enabled") and _crossed(q["start_time"], q["days"], last, now):
            out.append((q["id"], "start"))
        if q.get("stop_enabled") and _crossed(q["stop_time"], q["days"], last, now):
            out.append((q["id"], "stop"))
    return out


def describe(q):
    """One line for the sidebar/scheduler, e.g. "Mon–Fri at 01:00"."""
    if q["id"] == MAIN:
        return f"{q['max_active']} at a time"
    if not q.get("start_enabled"):
        return "starts when you press Start"
    days = q.get("days") or []
    if len(days) == 7:
        when = "daily"
    elif days == [0, 1, 2, 3, 4]:
        when = "weekdays"
    elif days == [5, 6]:
        when = "weekends"
    else:
        when = ", ".join(DAYS[d] for d in days) or "never"
    return f"{when} at {q['start_time']}"
