"""Command-line mode."""
import os
import sys
import time
import threading
from .util import human_size
from .manager import Manager

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
