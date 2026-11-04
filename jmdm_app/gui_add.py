"""The Add download window (mockups H, I and J).

A header that says what the link is (type tile, name, size or length, host
and a badge), the link, video quality chips with the playlist switch, the
season switch for episode pages, where to save it (with the free space) and
the file name. "More options" opens the queue, connections, mirrors and the
login the browser sent. Footer: Download now, Add to queue ▾ and Cancel.

Links pasted or copied in are looked at in the background (size, name, can
it resume; video title, quality, playlist length) while the buttons stay
usable. Downloads caught from the browser are NOT asked again: some sites
hand out links that work only once, so the details the browser sent are used.

The save folder is remembered per type of file (videos, programs, archives …),
so a folder picked for episodes doesn't change where games go."""
import os
import queue as _queue
import re
import shutil
import threading
import urllib.parse
import tkinter as tk
from tkinter import filedialog, ttk

from .config import MAX_CONNECTIONS, UA
from .util import (clean_err, human_size, is_playlist_url, is_video_url,
                   is_whole_playlist_url, is_youtube_url, playlist_url)
from .schedule import MAIN, category_of
from .browser import decision_expired, settle
from . import gdrive
from .gui_widgets import draw_icon, fit, rounded_rect
from .gui_popups import Dropdown, PopupMenu

QUALITIES = [("best", "Best"), ("4K", "4K"), ("1440p", "1440p"), ("1080p", "1080p"),
             ("720p", "720p"), ("480p", "480p"), ("audio", "Audio only")]
_FILE_EXT = re.compile(r"\.(zip|rar|7z|exe|msi|iso|apk|mp4|mkv|webm|m3u8|mpd|mp3|pdf|"
                       r"\d{3})(\?|$)", re.I)
_EXT_LABEL = {"Compressed": "ZIP", "Programs": "EXE", "Videos": "VID", "Music": "MP3",
              "Documents": "DOC", "Other": "FILE"}


# ---- helpers (no window needed; tested directly) ---------------------------
def page_like(url):
    """A web page (not a file or a known video site): may be a season page."""
    if not re.match(r"^https?://", url or ""):
        return False
    if is_video_url(url) or gdrive.looks_like_file_link(url):
        return False
    return not _FILE_EXT.search(urllib.parse.urlsplit(url).path + "?")


def free_space(folder):
    """'412 GB free on D:' for the drive the folder is on ('' if unknown)."""
    p = os.path.abspath(folder or ".")
    while p and not os.path.isdir(p):
        up = os.path.dirname(p)
        if up == p:
            return ""
        p = up
    try:
        free = shutil.disk_usage(p).free
    except OSError:
        return ""
    drive = os.path.splitdrive(p)[0] or "this drive"
    return f"{human_size(free)} free on {drive}"


def height_label(h):
    if not h:
        return ""
    return {4320: "8K", 2160: "4K"}.get(h, "8K" if h > 4320 else "4K" if h > 2160 else f"{h}p")


def remembered_folder(settings, category):
    """The last folder used for this type of file, if it still makes sense."""
    d = (settings.get("last_dirs") or {}).get(category)
    return d if isinstance(d, str) and d.strip() else ""


def remember_folder(settings, category, folder):
    dirs = settings.setdefault("last_dirs", {})
    if folder and folder != settings["download_dir"]:
        dirs[category] = folder
    else:
        dirs.pop(category, None)                      # back to the default


def probe_link(url, referer="", cookie="", user_agent="", video=False):
    """What a link is, without downloading it. Returns a dict:
    kind: "file" | "page" | "video" | "error", plus whatever was found —
    size, resumable, filename (files); title, duration, height, count,
    list_title (videos); error."""
    try:
        if video or is_video_url(url):
            return _probe_video(url, referer, cookie, user_agent)
        from .gates import resolve_link
        r = resolve_link(url, referer, cookie, user_agent)
        info = r["info"]
        if info.get("html"):
            return {"kind": "page"}
        return {"kind": "file", "size": info.get("size") or 0,
                "resumable": bool(info.get("resumable")), "filename": info.get("filename") or ""}
    except Exception as e:
        return {"kind": "error", "error": clean_err(e)}


def _probe_video(url, referer, cookie, user_agent):
    from .updates import load_ytdlp
    yt = load_ytdlp()
    yt_site = is_youtube_url(url)
    headers = {"User-Agent": UA if yt_site else (user_agent or UA)}
    if not yt_site:                       # YouTube: signed out, like the download itself
        if cookie:
            headers["Cookie"] = cookie
        if referer:
            headers["Referer"] = referer
    base = {"quiet": True, "no_warnings": True, "skip_download": True,
            "http_headers": headers}
    out = {"kind": "video"}
    if is_playlist_url(url):
        try:
            with yt.YoutubeDL({**base, "extract_flat": "in_playlist", "noplaylist": False}) as y:
                info = y.extract_info(playlist_url(url), download=False) or {}
            out["count"] = len([e for e in info.get("entries") or [] if e])
            out["list_title"] = info.get("title") or ""
        except Exception as e:
            if is_whole_playlist_url(url):
                raise
            out["list_error"] = clean_err(e)
    if not is_whole_playlist_url(url):
        with yt.YoutubeDL({**base, "noplaylist": True}) as y:
            info = y.extract_info(url, download=False) or {}
        out["title"] = info.get("title") or ""
        out["duration"] = info.get("duration") or 0
        out["height"] = max([f.get("height") or 0 for f in info.get("formats") or []] + [
            info.get("height") or 0])
        out["site"] = info.get("extractor_key") or ""
    return out


def clock(sec):
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---- small drawn pieces ------------------------------------------------------
def frame_bg(widget):
    return ttk.Style(widget).lookup("TFrame", "background") or "#ffffff"


class Banner(tk.Canvas):
    """'Caught from your browser — …' strip."""

    def __init__(self, master, theme, text, width):
        super().__init__(master, highlightthickness=0, bd=0)
        self.th, self.text, self.w = theme, text, width
        theme.on_change(self.draw)
        self.draw()

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f["small"]
        h = int(32 * S)
        self.configure(width=self.w, height=h, bg=frame_bg(self))
        self.delete("all")
        rounded_rect(self, 0, 0, self.w - 1, h - 1, 8 * S, fill=t["accent_soft"])
        draw_icon(self, "info", 10 * S, (h - 15 * S) / 2, 15 * S, t["accent_ink"], t["accent_soft"])
        self.create_text(32 * S, h / 2, text=fit(f, self.text, self.w - 42 * S), anchor="w",
                         font=f, fill=t["accent_ink"])


class Header(tk.Canvas):
    """Type tile, name, 'size · host' and a badge."""

    def __init__(self, master, theme, width):
        super().__init__(master, highlightthickness=0, bd=0)
        self.th, self.w = theme, width
        self.state = {"category": "Other", "title": "", "parts": [], "badge": ("", "muted")}
        theme.on_change(self.draw)

    def show(self, **kw):
        self.state.update(kw)
        self.draw()

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        st = self.state
        h = int(52 * S)
        bg = frame_bg(self)
        self.configure(width=self.w, height=h, bg=bg)
        self.delete("all")
        tint, ink = self.th.tile(st["category"])
        rounded_rect(self, 0, 0, h - 1, h - 1, 12 * S, fill=tint)
        self.create_text(h / 2, h / 2, text=_EXT_LABEL.get(st["category"], "FILE") if
                         not st.get("ext") else st["ext"], font=f["small_bold"], fill=ink)
        x = h + 14 * S
        room = self.w - x
        self.create_text(x, 15 * S, text=fit(f["big"], st["title"] or "—", room), anchor="w",
                         font=f["big"], fill=t["text"])
        sub = "  ·  ".join(p for p in st["parts"] if p)
        y = 37 * S
        cx = x
        if sub:
            sub = fit(f["small"], sub, room * 0.62)
            self.create_text(cx, y, text=sub, anchor="w", font=f["small"], fill=t["muted"])
            cx += f["small"].measure(sub) + 10 * S
        label, kind = st["badge"]
        if label:
            col = {"ok": t["ok"], "warn": t["warn"], "bad": t["bad"], "accent": t["accent_ink"]
                   }.get(kind, t["muted"])
            soft = t["accent_soft"] if kind == "accent" else t["track"] if kind == "muted" \
                else t["sel"] if kind == "ok" else t["hover"]
            label = fit(f["small_bold"], label, max(40 * S, self.w - cx - 20 * S))
            bw = f["small_bold"].measure(label) + (30 if kind != "muted" else 16) * S
            rounded_rect(self, cx, y - 10 * S, cx + bw, y + 10 * S, 10 * S, fill=soft)
            tx = cx + 8 * S
            if kind != "muted":
                icon = {"ok": "check_plain", "warn": "warn", "bad": "error", "accent": "video"}[kind]
                draw_icon(self, icon, tx, y - 6.5 * S, 13 * S, col, soft)
                tx += 16 * S
            self.create_text(tx, y, text=label, anchor="w", font=f["small_bold"], fill=col)


class Chips(tk.Canvas):
    """Pick-one row of rounded chips (video quality)."""

    def __init__(self, master, theme, choices, variable):
        super().__init__(master, highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.th, self.choices, self.var = theme, list(choices), variable
        self.boxes = []
        self.hover = None
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Left>", lambda e: self._step(-1))
        self.bind("<Right>", lambda e: self._step(1))
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())
        variable.trace_add("write", lambda *_: self.draw())
        theme.on_change(self.draw)
        self.draw()

    def _hit(self, x, y):
        for i, (x0, x1) in enumerate(self.boxes):
            if x0 <= x <= x1:
                return i
        return None

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.draw()

    def _motion(self, e):
        self._set_hover(self._hit(e.x, e.y))

    def _click(self, e):
        i = self._hit(e.x, e.y)
        if i is not None:
            self.var.set(self.choices[i][0])
        self.focus_set()

    def _step(self, d):
        vals = [v for v, _ in self.choices]
        cur = vals.index(self.var.get()) if self.var.get() in vals else 0
        self.var.set(vals[max(0, min(len(vals) - 1, cur + d))])

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f["small_bold"]
        h = int(30 * S)
        gap, pad = 6 * S, 12 * S
        bg = frame_bg(self)
        self.delete("all")
        x, self.boxes = 1, []
        for i, (val, label) in enumerate(self.choices):
            w = f.measure(label) + 2 * pad
            on = self.var.get() == val
            fill = t["accent"] if on else (t["btn_hover"] if i == self.hover else "")
            rounded_rect(self, x, 1, x + w, h - 1, (h - 2) / 2, fill=fill,
                         outline="" if on else t["border"], width=1)
            self.create_text(x + w / 2, h / 2, text=label, font=f,
                             fill=t["on_accent"] if on else t["text"])
            self.boxes.append((x, x + w))
            x += w + gap
        if self.focus_get() is self and self.var.get() in [v for v, _ in self.choices]:
            i = [v for v, _ in self.choices].index(self.var.get())
            x0, x1 = self.boxes[i]
            rounded_rect(self, x0 - 1, 0, x1 + 1, h, h / 2, outline=t["accent_ink"], width=2)
        self.configure(width=int(x), height=h, bg=bg)


class Link(ttk.Label):
    """Clickable accent text ('More options ⌄', 'Edit')."""

    def __init__(self, master, theme, text, command):
        super().__init__(master, text=text, cursor="hand2", takefocus=1)
        self.th = theme
        self.bind("<ButtonRelease-1>", lambda e: command())
        self.bind("<KeyPress-Return>", lambda e: command())
        self.bind("<KeyPress-space>", lambda e: command())
        theme.on_change(self._paint)
        self._paint()

    def _paint(self):
        self.configure(foreground=self.th.t["accent"], font=self.th.f["body"])


# ---- the window ---------------------------------------------------------------
class AddDialog:
    """ctx: root, theme, mgr, S, new_dialog, show_dialog, messagebox, side_queue,
    last_clip, pending_select, site_name, scheduler_dialog.
    job: a browser download held until this window is answered."""
    probe = staticmethod(probe_link)          # the window test swaps this

    def __init__(self, ctx, url="", filename="", referer="", cookie="", user_agent="",
                 video=None, from_browser=False, season=False, series_links=None, job=None):
        self.c = ctx
        self.mgr, self.th, self.S = ctx["mgr"], ctx["theme"], ctx["S"]
        self.job, self.from_browser, self.series_links = job, from_browser, series_links
        self.user_agent = user_agent
        self.forced_video = video
        mgr = self.mgr
        if not url:
            try:
                clip = ctx["root"].clipboard_get().strip()
                if re.match(r"^https?://\S+$", clip):
                    url = clip
            except Exception:
                pass
        self.initial_url = url
        self.known = {}                          # what we know about the link
        if job is not None and job.get("size"):
            self.known = {"kind": "file", "size": job["size"], "resumable": None}
        self.url_v = tk.StringVar(value=url)
        self.name_v = tk.StringVar(value=filename)
        self.name_touched = bool(filename)
        self.default_dir = mgr.settings["download_dir"]
        self.dir_v = tk.StringVar(value=self.default_dir)
        self.dir_touched = False
        self.conn_v = tk.IntVar(value=mgr.settings["connections"])
        self.ref_v = tk.StringVar(value=referer)
        self.cookie_v = tk.StringVar(value=cookie)
        self.vid_v = tk.BooleanVar(value=is_video_url(url) if video is None else bool(video))
        q = mgr.settings.get("video_quality", "best")
        self.quals = list(QUALITIES)
        if q not in [v for v, _ in self.quals]:
            self.quals.insert(1, (q, {"max": "Max"}.get(q, q)))
        self.q_v = tk.StringVar(value=q)
        self.pl_v = tk.BooleanVar(value=is_whole_playlist_url(url))
        self.season_v = tk.BooleanVar(value=season)
        self.items_v = tk.StringVar(value="")
        self.qnames = [x["name"] for x in mgr.settings["queues"]]
        self.qids = [x["id"] for x in mgr.settings["queues"]]
        pick = ctx["side_queue"]() or MAIN
        self.queue_v = tk.StringVar(value=self.qnames[self.qids.index(pick)])
        self.results = _queue.Queue()
        self.token = 0
        self.probe_job = None
        self.checking = False
        self.more_open = False
        self.login_open = False
        self._build()

    # ---- layout -------------------------------------------------------------
    def _build(self):
        c, S, th = self.c, self.S, self.th
        win = self.win = c["new_dialog"]()
        win.title("Add download")
        win.resizable(False, False)
        W = self.W = int(540 * S)
        frm = self.frm = ttk.Frame(win, padding=(int(22 * S), int(18 * S), int(22 * S), int(18 * S)))
        frm.pack(fill="both", expand=True)
        gap = int(12 * S)
        if self.from_browser:
            text = ("Caught from your browser — Cancel lets the browser download it as usual."
                    if self.job is not None else "Sent from your browser.")
            Banner(frm, th, text, W).pack(fill="x", pady=(0, gap))
        self.header = Header(frm, th, W)
        self.header.pack(fill="x", pady=(0, gap))
        self.e_url = ttk.Entry(frm, textvariable=self.url_v)
        self.e_url.pack(fill="x", pady=(0, gap))

        # video: quality chips + playlist switch
        self.video_box = ttk.Frame(frm)
        ttk.Label(self.video_box, text="Quality", style="Hint.TLabel",
                  font=th.f["small_bold"]).pack(anchor="w")
        Chips(self.video_box, th, self.quals, self.q_v).pack(anchor="w", pady=(int(6 * S), 0))
        self.q_hint = ttk.Label(self.video_box, style="Hint.TLabel")
        self.q_hint.pack(anchor="w", pady=(int(5 * S), 0))
        self.q_v.trace_add("write", lambda *_: self._quality_hint())
        self._quality_hint()
        self.pl_box, self.pl_chk, self.pl_sub = self._switch_box(
            self.video_box, self.pl_v, "Whole playlist",
            "Saved in a folder named after the playlist, numbered in order")
        self.pl_box.pack(fill="x", pady=(int(8 * S), 0))

        # episode page: whole season
        self.season_box, self.season_chk, _ = self._switch_box(
            frm, self.season_v, "Whole season",
            "Follows the numbered episode links on this page, into one folder")

        # where to save
        self.grid = g = ttk.Frame(frm)
        g.columnconfigure(1, weight=1)
        lw = int(78 * S)
        g.columnconfigure(0, minsize=lw)
        ttk.Label(g, text="Save to", style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        row = ttk.Frame(g)
        row.grid(row=0, column=1, sticky="ew")
        self.e_dir = ttk.Entry(row, textvariable=self.dir_v)
        self.e_dir.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._browse).pack(side="left", padx=(int(6 * S), 0))
        self.space = ttk.Label(g, style="Hint.TLabel", font=th.f["small"])
        self.space.grid(row=1, column=1, sticky="w", pady=(int(3 * S), int(8 * S)))
        ttk.Label(g, text="File name", style="Hint.TLabel").grid(row=2, column=0, sticky="w")
        self.e_name = ttk.Entry(g, textvariable=self.name_v)
        self.e_name.grid(row=2, column=1, sticky="ew")
        self.name_hint = ttk.Label(g, style="Hint.TLabel", font=th.f["small"])
        self.name_hint.grid(row=3, column=1, sticky="w", pady=(int(3 * S), 0))
        self.e_name.bind("<Key>", lambda e: setattr(self, "name_touched", True))
        self.e_dir.bind("<Key>", lambda e: setattr(self, "dir_touched", True))
        self.dir_v.trace_add("write", lambda *_: self._show_space())

        self.more = self._build_more(frm)

        # footer
        self.footer = foot = ttk.Frame(frm)
        self.more_link = Link(foot, th, "More options  ⌄", self.toggle_more)
        self.more_link.pack(side="left")
        primary = "Accent.TButton" if th.sv else "TButton"
        self.b_now = ttk.Button(foot, text="Download now", style=primary,
                                command=lambda: self.submit(start=True, force=True))
        self.b_now.pack(side="right")
        self.b_queue = ttk.Button(foot, text="Add to queue  ⌄", command=self.queue_menu)
        self.b_queue.pack(side="right", padx=(0, int(8 * S)))
        ttk.Button(foot, text="Cancel", command=self.cancel).pack(side="right",
                                                                  padx=(0, int(8 * S)))
        self.gap = gap
        self._relayout()

        win.protocol("WM_DELETE_WINDOW", self.cancel)
        win.bind("<Return>", lambda e: None if isinstance(e.widget, tk.Text)
                 else self.submit(start=True, force=True))
        win.bind("<Escape>", lambda e: self.cancel())
        self.url_v.trace_add("write", lambda *_: self.on_url_change())
        self.on_url_change(first=True)
        if self.job is not None:
            win.after(1000, self._watch_expiry)
        win.after(120, self._poll)
        c["show_dialog"](win)
        self.e_url.focus_set()
        self.e_url.icursor("end")
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))

    def _switch_box(self, parent, var, title, sub):
        S, th = self.S, self.th
        box = ttk.Frame(parent, padding=(int(10 * S), int(8 * S)))
        style = "Switch.TCheckbutton" if th.sv else "TCheckbutton"
        top = ttk.Frame(box)
        top.pack(fill="x")
        chk = ttk.Checkbutton(top, text=title, variable=var, style=style)
        chk.pack(side="left")
        only = ttk.Frame(top)
        only.pack(side="right")
        ttk.Label(only, text="Only", style="Hint.TLabel").pack(side="left", padx=(0, int(6 * S)))
        e = ttk.Entry(only, textvariable=self.items_v, width=8)
        e.pack(side="left")
        lab = ttk.Label(box, text=sub + "  ·  “Only”: e.g. 1-20 or 3,5,9", style="Hint.TLabel",
                        font=th.f["small"])
        lab.pack(anchor="w", pady=(int(3 * S), 0))
        var.trace_add("write", lambda *_: self._relayout())
        return box, chk, lab

    def _build_more(self, frm):
        S, th = self.S, self.th
        more = ttk.Frame(frm)
        ttk.Separator(more).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, int(12 * S)))
        more.columnconfigure(0, minsize=int(78 * S))
        more.columnconfigure(1, weight=1)
        r = 1
        ttk.Label(more, text="Queue", style="Hint.TLabel").grid(row=r, column=0, sticky="w")
        qrow = ttk.Frame(more)
        qrow.grid(row=r, column=1, sticky="w")
        Dropdown(qrow, th, textvariable=self.queue_v, values=self.qnames, width=16).pack(side="left")
        ttk.Label(qrow, text="Connections", style="Hint.TLabel").pack(side="left",
                                                                        padx=(int(16 * S), int(8 * S)))
        ttk.Spinbox(qrow, from_=1, to=MAX_CONNECTIONS, textvariable=self.conn_v,
                    width=4).pack(side="left")
        r += 1
        ttk.Label(more, text="Mirrors", style="Hint.TLabel").grid(row=r, column=0, sticky="nw",
                                                                   pady=(int(10 * S), 0))
        self.mir_txt = tk.Text(more, height=3, width=40, wrap="none", font=th.f["small"],
                               relief="flat", bd=0, highlightthickness=1, padx=6, pady=4)
        th.style_text_widget(self.mir_txt)
        self.mir_txt.grid(row=r, column=1, sticky="ew", pady=(int(10 * S), 0))
        r += 1
        ttk.Label(more, text="Other links to the same file, one per line — each host's speed "
                             "adds up.", style="Hint.TLabel", font=th.f["small"]
                  ).grid(row=r, column=1, sticky="w", pady=(int(3 * S), 0))
        r += 1
        ttk.Label(more, text="Login", style="Hint.TLabel").grid(row=r, column=0, sticky="w",
                                                                 pady=(int(10 * S), 0))
        lrow = ttk.Frame(more)
        lrow.grid(row=r, column=1, sticky="w", pady=(int(10 * S), 0))
        self.login_lab = ttk.Label(lrow, style="Hint.TLabel", font=th.f["small"])
        self.login_lab.pack(side="left")
        Link(lrow, th, "Edit", self.toggle_login).pack(side="left", padx=(int(6 * S), 0))
        r += 1
        self.login = lf = ttk.Frame(more)
        lf.columnconfigure(1, weight=1)
        ttk.Label(lf, text="Referer").grid(row=0, column=0, sticky="w", padx=(0, int(8 * S)))
        ttk.Entry(lf, textvariable=self.ref_v).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(lf, text="Cookies").grid(row=1, column=0, sticky="w", padx=(0, int(8 * S)))
        ttk.Entry(lf, textvariable=self.cookie_v, show="•").grid(row=1, column=1, sticky="ew",
                                                                 pady=2)
        self.login_row = r
        r += 1
        ttk.Checkbutton(more, text="Video page — let the video finder (yt-dlp) fetch it",
                        variable=self.vid_v, command=self._video_toggled
                        ).grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(int(10 * S), 0))
        self.ref_v.trace_add("write", lambda *_: self._login_text())
        self.cookie_v.trace_add("write", lambda *_: self._login_text())
        self._login_text()
        return more

    def _relayout(self):
        """Show the parts that apply to this link, in order."""
        gap = int(12 * self.S)
        for w in (self.video_box, self.season_box, self.grid, self.more, self.footer):
            w.pack_forget()
        video = self.vid_v.get()
        if video:
            self.video_box.pack(fill="x", pady=(0, gap))
            pl = is_playlist_url(self.url_v.get().strip())
            if pl:
                self.pl_box.pack(fill="x", pady=(int(8 * self.S), 0))
            else:
                self.pl_box.pack_forget()
        elif self.season_ok:
            self.season_box.pack(fill="x", pady=(0, gap))
        self.grid.pack(fill="x", pady=(0, gap))
        if self.more_open:
            self.more.pack(fill="x", pady=(0, gap))
        self.footer.pack(fill="x", pady=(int(4 * self.S), 0))
        many = (video and self.pl_v.get() and is_playlist_url(self.url_v.get().strip())) or \
            (not video and self.season_ok and self.season_v.get())
        self.e_name.state(["disabled"] if many else ["!disabled"])
        self.name_hint.configure(
            text="Each item keeps its own name" if many else
            ("Blank = the video's title" if video else "Blank = the name the site gives it"))

    season_ok = False

    def toggle_more(self):
        self.more_open = not self.more_open
        self.more_link.configure(text="Fewer options  ⌃" if self.more_open else "More options  ⌄")
        self._relayout()

    def toggle_login(self):
        self.login_open = not self.login_open
        if self.login_open:
            self.login.grid(row=self.login_row, column=0, columnspan=2, sticky="ew",
                            pady=(int(6 * self.S), 0))
        else:
            self.login.grid_forget()

    def _login_text(self):
        have = [n for n, v in (("referer", self.ref_v), ("cookies", self.cookie_v)) if v.get().strip()]
        if not have:
            t = "None — only needed for sites that want you signed in"
        elif self.from_browser:
            t = "Your browser's " + " and ".join(have) + " are attached"
        else:
            t = " and ".join(have).capitalize() + " set"
        self.login_lab.configure(text=t)

    def _quality_hint(self):
        q = self.q_v.get()
        self.q_hint.configure(
            text="Audio only — saved as an M4A/MP3 sound file." if q == "audio" else
            "Saved as MP4 — plays in every Windows player." if q in ("best", "1080p", "720p", "480p")
            else "Above 1080p the site only has VP9/AV1 video — saved as MP4 or MKV.")

    # ---- folder ----------------------------------------------------------------
    def category(self):
        u = self.url_v.get().strip()
        return category_of(self.name_v.get().strip() or self.known.get("filename", ""), u,
                           self.vid_v.get())

    def _apply_remembered(self):
        if self.dir_touched:
            return
        d = remembered_folder(self.mgr.settings, self.category())
        self.dir_v.set(d or self.default_dir)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.dir_v.get(), parent=self.win)
        if d:
            self.dir_v.set(os.path.normpath(d))
            self.dir_touched = True

    def _show_space(self):
        folder = self.dir_v.get().strip()
        txt = free_space(folder)
        if folder == self.default_dir and self.mgr.settings.get("category_folders"):
            txt = (txt + "  ·  " if txt else "") + f"sorted into \\{self.category()}"
        self.space.configure(text=txt)

    # ---- the link --------------------------------------------------------------
    def on_url_change(self, first=False):
        u = self.url_v.get().strip()
        ok = bool(re.match(r"^https?://", u))
        if gdrive.looks_like_file_link(u):
            try:
                self.conn_v.set(min(int(self.conn_v.get() or 8), gdrive.MAX_CONNECTIONS))
            except (tk.TclError, ValueError):
                self.conn_v.set(gdrive.MAX_CONNECTIONS)
        if not first:
            self.known = {}
            if self.forced_video is None or not ok:
                self.vid_v.set(is_video_url(u))
        # a name that says "file" (game.rar) or a known size: not an episode page
        named_file = category_of(self.name_v.get().strip()) != "Other" or \
            self.known.get("kind") == "file"
        self.season_ok = (page_like(u) and not named_file) or (self.season_v.get() and first)
        if not self.season_ok:
            self.season_v.set(False)
        if is_playlist_url(u):
            if is_whole_playlist_url(u):
                self.pl_v.set(True)
        else:
            self.pl_v.set(False)
        self._apply_remembered()
        self._relayout()
        self._show_space()
        self._show_header()
        # look the link up in the background (not one the browser handed over:
        # those can be single-use, and the browser already told us about it)
        if self.probe_job:
            self.win.after_cancel(self.probe_job)
            self.probe_job = None
        self.token += 1
        handed_over = self.job is not None and u == self.initial_url and not self.vid_v.get()
        series = self.series_links is not None and u == self.initial_url
        if ok and not handed_over and not series:
            self.checking = True
            self._show_header()
            self.probe_job = self.win.after(10 if first else 600, self._start_probe)
        else:
            self.checking = False
            self._show_header()

    def _video_toggled(self):
        self.forced_video = self.vid_v.get()
        self.on_url_change()

    def _start_probe(self):
        self.probe_job = None
        tok = self.token
        args = (self.url_v.get().strip(), self.ref_v.get().strip(), self.cookie_v.get().strip(),
                self.user_agent, self.vid_v.get())

        def work():
            try:
                r = AddDialog.probe(*args)
            except Exception as e:                     # never let the window hang on "Checking"
                r = {"kind": "error", "error": clean_err(e)}
            self.results.put((tok, r))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            while True:
                tok, r = self.results.get_nowait()
                if tok == self.token:
                    self.checking = False
                    self.known = r
                    self._got(r)
        except _queue.Empty:
            pass
        self.win.after(120, self._poll)

    def _got(self, r):
        if r.get("kind") == "file" and r.get("filename") and not self.name_touched:
            self.name_v.set(r["filename"])
        if r.get("kind") == "page" and not self.vid_v.get():
            self.season_ok = True
        if r.get("count") and is_playlist_url(self.url_v.get().strip()):
            self.pl_chk.configure(text=f"Whole playlist — {r['count']} videos")
        self._apply_remembered()
        self._relayout()
        self._show_space()
        self._show_header()

    def _show_header(self):
        u = self.url_v.get().strip()
        k = self.known
        video = self.vid_v.get()
        host = self.c["site_name"](u) if re.match(r"^https?://", u) else ""
        name = self.name_v.get().strip() or k.get("filename") or ""
        parts = []
        badge = ("", "muted")
        if video:
            title = k.get("title") or k.get("list_title") or name or \
                (host + " video" if host else "Paste a video link")
            if k.get("duration"):
                parts.append(clock(k["duration"]))
            if k.get("height"):
                parts.append("up to " + height_label(k["height"]))
            parts.append(host)
            if self.checking:
                badge = ("Checking link…", "muted")
            elif k.get("kind") == "video":
                badge = (f"Playlist · {k['count']} videos" if k.get("count") and self.pl_v.get()
                         else "Video found", "ok")
            elif k.get("kind") == "error":
                badge = ("Couldn't check — you can still try", "warn")
        else:
            if not name and u:
                name = urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(u).path)) or host
            title = name or "Paste a link"
            size = k.get("size")
            if size and size > 0:
                parts.append(human_size(size))
            parts.append(host)
            if self.checking:
                badge = ("Checking link…", "muted")
            elif k.get("kind") == "file":
                badge = ("Resumable", "ok") if k.get("resumable") else \
                    ("Can't pause and resume", "warn") if k.get("resumable") is False else ("", "muted")
            elif k.get("kind") == "page":
                badge = ("Web page — " + ("whole season?" if self.season_ok else "not a file"),
                         "accent" if self.season_ok else "warn")
            elif k.get("kind") == "error":
                badge = (fit(self.th.f["small_bold"], k.get("error", "Couldn't check"),
                             260 * self.S), "bad")
        if self.season_ok and self.season_v.get() and not video:
            title = "Whole season from " + (host or "this page")
        cat = category_of(name, u, video)
        self.header.show(category=cat, title=title, parts=parts, badge=badge,
                         ext=(os.path.splitext(name)[1][1:4].upper() if not video and
                              os.path.splitext(name)[1] else None))

    # ---- answers ----------------------------------------------------------------
    def queue_menu(self):
        m = PopupMenu(self.c["root"], self.th)
        others = [q for q in self.mgr.settings["queues"] if q["id"] != MAIN]
        for q in others:
            m.add_command(label=f"Add to “{q['name']}”", icon="queue",
                          command=lambda i=q["id"]: self.submit(start=True, qid=i))
        if others:
            m.add_separator()
        m.add_command(label="Add paused — start it yourself later", icon="pause_line",
                      command=lambda: self.submit(start=False))
        if not others:
            m.add_command(label="New queue…", icon="add",
                          command=lambda: self.c["scheduler_dialog"](new=True))
        b = self.b_queue
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2)

    def submit(self, start=True, qid=None, force=False):
        """Download now: start=True, force=True (starts even in a waiting queue).
        Add to a queue: start=True, qid=that queue. Add paused: start=False."""
        c, mgr = self.c, self.mgr
        u = self.url_v.get().strip()
        if not re.match(r"^https?://", u):
            c["messagebox"].showerror("Add download", "Paste a link that starts with http(s)://",
                                      parent=self.win)
            return
        try:
            conns = int(self.conn_v.get())
        except (tk.TclError, ValueError):
            conns = mgr.settings["connections"]
        if self.job is not None and not settle(self.job, True):
            c["messagebox"].showinfo("Add download", "The browser already downloaded this "
                                     "file because nobody answered in time.", parent=self.win)
            self.close()
            return
        c["last_clip"][0] = u
        if qid is None:
            qid = self.qids[self.qnames.index(self.queue_v.get())]
        folder = os.path.normpath(self.dir_v.get().strip()) if self.dir_v.get().strip() else ""
        cat = self.category()
        remember_folder(mgr.settings, cat, folder)
        mgr.mark_dirty()
        if folder == os.path.normpath(self.default_dir) and mgr.settings.get("category_folders"):
            folder = None                              # let the category decide
        folder = folder or None
        ref, cookie, ua, q = (self.ref_v.get().strip(), self.cookie_v.get().strip(),
                              self.user_agent, self.q_v.get())
        video = self.vid_v.get()
        if self.season_ok and self.season_v.get() and not video:
            mgr.add_series(u, folder, conns, q, ref, cookie, ua, items=self.items_v.get(),
                           start=start, queue=qid, links=self.series_links)
            self.close()
            return
        if video and self.pl_v.get() and is_playlist_url(u):
            mgr.add_playlist(u, folder, conns, q, ref, cookie, ua, items=self.items_v.get(),
                             start=start, queue=qid)
            self.close()
            return
        mirrors = [x.strip() for x in self.mir_txt.get("1.0", "end").splitlines()
                   if re.match(r"^https?://", x.strip())]
        d = mgr.add(u, folder, self.name_v.get().strip(), conns, ref, cookie, ua,
                    video=video, quality=q, start=start, mirrors=mirrors, queue=qid)
        if force and start and qid != MAIN and not d.is_running():
            d.start()                                  # "now" even in a waiting queue
        c["pending_select"][0] = d.id
        self.close()

    def cancel(self):
        if self.job is not None:
            settle(self.job, False)                    # the browser downloads it instead
        self.close()

    def close(self):
        self.token += 1                                # late probe answers are ignored
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _watch_expiry(self):
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        if decision_expired(self.job):
            self.close()                               # timed out: the browser kept it
            self.mgr.notice = "No answer in time — the browser downloaded it instead"
            return
        self.win.after(1000, self._watch_expiry)
