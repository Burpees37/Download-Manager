"""The app window (Tkinter)."""
import os
import re
import time
import queue
import threading
import urllib.request
import urllib.parse
import urllib.error
from .config import APP_NAME, BUILD, DEFAULT_CAPTURE_TYPES, MAX_CONNECTIONS, VERSION
from .util import clean_err, default_download_dir, find_ffmpeg, human_size, human_time, is_playlist_url, is_video_url, is_whole_playlist_url, sanitize
from .gates import looks_like_download
from .updates import _ytdlp_source, app_update_available, have_ytdlp, prepare_app_update, run_app_update, update_ytdlp, ytdlp_version
from .integration import register_browser_integration
from .browser import decision_expired, settle
from .manager import Manager

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
