"""The app window (Tkinter), modern Classic layout (mockup D): a command bar
with the total speed, the sidebar (library, categories, queues), the download
list with a progress bar in every row, the selected download's details card
and a status bar. Light and dark, following Windows unless chosen in Options."""
import os
import re
import time
import queue
import threading
import urllib.parse
from .config import APP_NAME, BUILD, DEFAULT_CAPTURE_TYPES, MAX_CONNECTIONS, VERSION
from .util import clean_err, default_download_dir, error_hint, find_ffmpeg, human_size, human_time, is_playlist_url, is_video_url, is_whole_playlist_url, sanitize, short_error
from .gates import looks_like_download
from .updates import _ytdlp_source, app_update_available, have_ytdlp, prepare_app_update, run_app_update, update_ytdlp, ytdlp_version
from .integration import register_browser_integration
from .browser import decision_expired, settle
from .manager import Manager
from . import gdrive
from .schedule import CATEGORY_NAMES, DAYS, MAIN, WHEN_DONE, describe, download_category, new_queue, parse_hhmm

# Filled by run_gui: the main pieces, for the window test.
UI = {}

LIBRARY = [("all", "All downloads", "grid"), ("downloading", "Downloading", "down"),
           ("queued", "Queued & paused", "clock"), ("completed", "Completed", "check"),
           ("failed", "Failed", "warn")]
SORTS = [("newest", "Newest first"), ("oldest", "Oldest first"), ("name", "Name"),
         ("size", "Size"), ("status", "Status")]
SITE_NAMES = {"youtube.com": "YouTube", "youtu.be": "YouTube", "facebook.com": "Facebook",
              "fb.watch": "Facebook", "tiktok.com": "TikTok", "instagram.com": "Instagram",
              "x.com": "X", "twitter.com": "X", "vimeo.com": "Vimeo",
              "drive.google.com": "Google Drive", "drive.usercontent.google.com": "Google Drive",
              "docs.google.com": "Google Docs"}


def site_name(url):
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    for k, v in SITE_NAMES.items():
        if host == k or host.endswith("." + k):
            return v
    return host[4:] if host.startswith("www.") else host


def run_gui(initial_url=None):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from .gui_theme import Theme, ui_scale
    from .gui_widgets import (CATEGORY_ICON, CommandButton, DetailsCard, DownloadList,
                              IconButton, NavList, SpeedChip, StatusBar)

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
    S = ui_scale(root)
    root.geometry(f"{int(1240 * S)}x{int(760 * S)}")
    root.minsize(int(900 * S), int(560 * S))
    theme = Theme(root, mgr.settings.get("theme", "system"), S)
    pad = int(16 * S)

    # ---- actions ---------------------------------------------------------
    def selected():
        ids = set(lst.selection)
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

    def do_delete_completed():
        done = [d for d in mgr.downloads if d.status == "Complete"]
        if done and messagebox.askyesno(
                "Delete completed", f"Remove {len(done)} finished download(s) from the "
                                    "list? The files stay on disk."):
            for d in done:
                threading.Thread(target=mgr.remove, args=(d, False), daemon=True).start()

    def do_redownload():
        for d in selected():
            threading.Thread(target=mgr.redownload, args=(d,), daemon=True).start()

    def open_file(d):
        if d.status == "Complete" and d.path and os.path.exists(d.path):
            if os.name == "nt":
                os.startfile(d.path)

    def open_selected():
        for d in selected()[:1]:
            open_file(d)

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

    def copy_error():
        sel = [d for d in selected() if d.status == "Error"]
        if sel:
            root.clipboard_clear()
            root.clipboard_append(sel[0].error)
            last_clip[0] = sel[0].error
            mgr.notice = "Error message copied"

    def copy_url():
        sel = selected()
        if sel:
            root.clipboard_clear()
            root.clipboard_append(sel[0].url)
            last_clip[0] = sel[0].url

    def side_queue():
        """The queue picked in the sidebar, if any."""
        s = nav.selected
        return s[2:] if s.startswith("q:") and mgr.queue(s[2:]) else None

    def new_dialog():
        """A dialog window, kept hidden until show_dialog() has placed it."""
        win = tk.Toplevel(root)
        win.withdraw()
        win.transient(root)
        return win

    def show_dialog(win):
        """Center a dialog over the app window (on the same monitor), then show it."""
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        rw, rh = root.winfo_width(), root.winfo_height()
        x = rx + max(int(20 * S), (rw - w) // 2)
        y = ry + max(int(20 * S), (rh - h) // 3)
        if root.state() == "withdrawn" or rw <= 1:            # app window not shown
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 3
        win.geometry(f"+{x}+{y}")
        theme.window_chrome(win)
        win.deiconify()
        win.lift()
        win.focus_force()

    def fill_queue_menu(m):
        m.delete(0, "end")
        for q in mgr.settings["queues"]:
            if q["id"] == MAIN:
                continue
            running = mgr.queue_running.get(q["id"])
            m.add_command(label=f"{'Stop' if running else 'Start'} “{q['name']}”",
                          command=lambda i=q["id"], r=running:
                          mgr.stop_queue(i) if r else mgr.start_queue(i))
        if len(mgr.settings["queues"]) > 1:
            m.add_separator()
        m.add_command(label="New queue…", command=lambda: scheduler_dialog(new=True))
        m.add_command(label="Scheduler…", command=lambda: scheduler_dialog())

    def popup_under(widget, m):
        m.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height() + 2)

    def queue_button():
        qid = side_queue()
        if qid and qid != MAIN:
            q = mgr.queue(qid)
            if mgr.queue_running.get(qid):
                mgr.stop_queue(qid)
                mgr.notice = f"Stopped “{q['name']}”"
            else:
                mgr.start_queue(qid)
                mgr.notice = f"Started “{q['name']}”"
            return
        m = tk.Menu(root, tearoff=0)
        fill_queue_menu(m)
        popup_under(btn["Start queue"], m)

    def more_menu():
        m = tk.Menu(root, tearoff=0)
        m.add_command(label="Options…", command=lambda: settings_dialog())
        m.add_command(label="Scheduler…", command=lambda: scheduler_dialog())
        m.add_separator()
        m.add_command(label="Resume all", command=do_resume_all)
        m.add_command(label="Delete completed…", command=do_delete_completed)
        m.add_command(label="Open download folder", command=lambda: open_folder(None))
        m.add_separator()
        m.add_command(label="Check for updates", command=lambda: update_check(manual=True))
        m.add_command(label="About", command=lambda: messagebox.showinfo(
            "About", f"{APP_NAME} {VERSION}" + (f" (build {BUILD})" if BUILD else "")
            + f"\nyt-dlp {ytdlp_version() or '—'}"))
        m.add_separator()
        m.add_command(label="Exit", command=lambda: on_close())
        popup_under(btn["More"], m)

    def toggle_theme():
        mode = "light" if theme.dark else "dark"
        mgr.settings["theme"] = mode
        mgr.mark_dirty()
        theme.set_mode(mode)
        btn["Theme"].icon = "sun" if theme.dark else "moon"
        btn["Theme"].draw()

    # ---- command bar -----------------------------------------------------
    bar = tk.Frame(root, bd=0)
    theme.paint(bar, bg="bg")
    bar.pack(fill="x", padx=(pad, pad), pady=(int(10 * S), int(8 * S)))
    btn = {}
    left_buttons = [
        ("Add URL", "add", lambda: add_dialog(), "text", True, "Add a download (Ctrl+N)"),
        ("Resume", "resume", do_resume, "ok", False, "Resume the selected downloads"),
        ("Stop", "stop", do_pause, "bad", False, "Pause the selected downloads"),
        ("Stop all", "stop_all", do_pause_all, "bad", False, "Pause everything"),
        ("Delete", "delete", do_delete, "text", False, "Remove the selected downloads (Del)"),
        None,
        ("Video", "video", lambda: add_dialog(video=True), "text", False,
         "Download a video or playlist"),
        ("Scheduler", "clock", lambda: scheduler_dialog(), "text", False,
         "Queues and their schedules"),
        ("Start queue", "queue", lambda: queue_button(), "text", False,
         "Start or stop a queue"),
    ]
    seps = []
    for spec in left_buttons:
        if spec is None:
            sep = tk.Frame(bar, width=1, height=int(22 * S))
            theme.paint(sep, bg="border")
            sep.pack(side="left", padx=int(6 * S))
            seps.append(sep)
            continue
        text, icon, cmd_, ink, primary, tip = spec
        b = CommandButton(bar, theme, text, icon, cmd_, ink=ink, primary=primary, tip=tip)
        b.pack(side="left", padx=(0, int(8 * S)) if primary else 0)
        btn[text] = b
    BAR_KEYS = [s[0] for s in left_buttons if s]
    btn["More"] = IconButton(bar, theme, "options", more_menu, "More: options, updates, exit")
    btn["More"].pack(side="right")
    btn["Theme"] = IconButton(bar, theme, "sun" if theme.dark else "moon", toggle_theme,
                              "Switch light / dark")
    btn["Theme"].pack(side="right", padx=(int(4 * S), 0))
    chip = SpeedChip(bar, theme)
    chip.pack(side="right", padx=(0, int(4 * S)))

    def fit_bar(_e=None):
        """Narrow window: the command buttons lose their labels."""
        cmd = [btn[k] for k in BAR_KEYS]
        need = sum(b.natural_width(False) for b in cmd) + int(8 * S) + len(seps) * int(13 * S) \
            + chip.winfo_reqwidth() + 2 * int(40 * S) + int(12 * S)
        compact = bar.winfo_width() < need
        for b in cmd:
            b.set_compact(compact)

    bar.bind("<Configure>", fit_bar)

    # ---- status bar (packed before the body so it stays at the bottom) ----
    status = StatusBar(root, theme)
    status.pack(side="bottom", fill="x")

    # ---- body: sidebar | list + details ----------------------------------
    body = tk.Frame(root, bd=0)
    theme.paint(body, bg="bg")
    body.pack(fill="both", expand=True, padx=(int(10 * S), pad), pady=(0, int(10 * S)))
    def on_nav(_iid):
        lst.top, lst.selection = 0, []
        refresh_now()

    nav = NavList(body, theme, on_select=on_nav, on_menu=lambda iid, e: nav_menu(iid, e))
    nav.configure(width=int(232 * S))
    nav.pack(side="left", fill="y", padx=(0, int(12 * S)))
    main = tk.Frame(body, bd=0)
    theme.paint(main, bg="bg")
    main.pack(side="left", fill="both", expand=True)
    details = DetailsCard(main, theme, {
        "folder": lambda: open_folder(), "pause": do_pause, "resume": do_resume,
        "open": open_selected, "copy_error": copy_error, "redownload": do_redownload,
        "copy_link": copy_url})
    details.pack(side="bottom", fill="x", pady=(int(12 * S), 0))
    card = tk.Frame(main, bd=0, highlightthickness=1)
    theme.paint(card, bg="surface", highlightbackground="border", highlightcolor="border")
    card.pack(fill="both", expand=True)

    header = tk.Frame(card, bd=0)
    theme.paint(header, bg="surface")
    header.pack(fill="x", padx=int(18 * S), pady=(int(12 * S), int(10 * S)))
    title_var, summary_var = tk.StringVar(value="All downloads"), tk.StringVar()
    title_lbl = theme.paint(tk.Label(header, textvariable=title_var, font=theme.f["title"], bd=0),
                            bg="surface", fg="text")
    title_lbl.pack(side="left")
    summary_lbl = theme.paint(tk.Label(header, textvariable=summary_var, font=theme.f["small"],
                                       bd=0), bg="surface", fg="muted")
    summary_lbl.pack(side="left", padx=(int(12 * S), 0), pady=(int(4 * S), 0))

    def fit_header(_e=None):
        """Narrow window: the "10 files · 2 active" summary steps aside for the search box."""
        need = title_lbl.winfo_reqwidth() + summary_lbl.winfo_reqwidth() + int(12 * S) \
            + search.winfo_reqwidth() + int(8 * S) + btn["Sort"].winfo_reqwidth() + int(16 * S)
        shown = summary_lbl.winfo_manager() == "pack"
        if header.winfo_width() < need and shown:
            summary_lbl.pack_forget()
        elif header.winfo_width() >= need and not shown:
            summary_lbl.pack(side="left", padx=(int(12 * S), 0), pady=(int(4 * S), 0),
                             after=title_lbl)

    header.bind("<Configure>", fit_header)

    def sort_menu():
        m = tk.Menu(root, tearoff=0)
        cur = tk.StringVar(value=mgr.settings.get("sort", "newest"))
        for key, label in SORTS:
            m.add_radiobutton(label=label, variable=cur, value=key,
                              command=lambda k=key: set_sort(k))
        popup_under(btn["Sort"], m)

    def set_sort(key):
        mgr.settings["sort"] = key
        mgr.mark_dirty()
        btn["Sort"].text = dict(SORTS)[key]
        btn["Sort"].draw()
        refresh_now()

    btn["Sort"] = CommandButton(header, theme, dict(SORTS).get(mgr.settings.get("sort"), "Newest first"),
                                "chevron", sort_menu, tip="Sort the list", trailing=True)
    btn["Sort"].pack(side="right")
    search_v = tk.StringVar()
    PLACEHOLDER = "Search, or paste a link and press Enter"
    search = ttk.Entry(header, textvariable=search_v, width=34)
    search.pack(side="right", padx=(int(14 * S), int(8 * S)))
    hint_on = [False]

    def search_hint(show):
        if show and not search_v.get():
            hint_on[0] = True
            search.configure(foreground=theme.t["muted"])
            search_v.set(PLACEHOLDER)
        elif not show and hint_on[0]:
            hint_on[0] = False
            search.configure(foreground=theme.t["text"])
            search_v.set("")

    def query():
        v = search_v.get()
        return "" if hint_on[0] and v == PLACEHOLDER else v.strip()

    def on_search_change(*_a):
        if hint_on[0] and search_v.get() != PLACEHOLDER:       # real text arrived
            hint_on[0] = False
            search.configure(foreground=theme.t["text"])
        root.after_idle(refresh_now)

    def search_enter(_e=None):
        q = query()
        if re.match(r"^https?://\S+$", q):
            search_v.set("")
            add_dialog(q)
        else:
            refresh_now()

    search.bind("<FocusIn>", lambda e: search_hint(False))
    search.bind("<FocusOut>", lambda e: search_hint(True))
    search.bind("<Return>", search_enter)
    search.bind("<Escape>", lambda e: (search_v.set(""), lst.c.focus_set()))
    search_v.trace_add("write", on_search_change)
    theme.on_change(lambda: search_hint(True) if hint_on[0] or not search_v.get() else None)
    root.after(50, lambda: search_hint(True))

    # ---- context menus ---------------------------------------------------
    menu = tk.Menu(root, tearoff=0)
    move_menu = tk.Menu(menu, tearoff=0)

    def fill_move_menu():
        move_menu.delete(0, "end")
        for q in mgr.settings["queues"]:
            move_menu.add_command(label=q["name"], command=lambda i=q["id"]: [
                mgr.move_to_queue(d, i) for d in selected()])

    menu.add_command(label="Open", command=open_selected)
    menu.add_command(label="Open folder", command=lambda: open_folder())
    menu.add_separator()
    menu.add_command(label="Resume", command=do_resume)
    menu.add_command(label="Stop", command=do_pause)
    menu.add_command(label="Redownload", command=do_redownload)
    menu.add_command(label="Add mirror link…", command=lambda: add_mirror_dialog())
    menu.add_cascade(label="Move to queue", menu=move_menu)
    menu.add_separator()
    menu.add_command(label="Copy link", command=copy_url)
    menu.add_command(label="Copy error message", command=copy_error)
    menu.add_command(label="Delete", command=do_delete)

    def popup(ev):
        fill_move_menu()
        has_err = any(d.status == "Error" for d in selected())
        menu.entryconfigure("Copy error message", state="normal" if has_err else "disabled")
        menu.tk_popup(ev.x_root, ev.y_root)

    side_menu = tk.Menu(root, tearoff=0)

    def nav_menu(iid, ev):
        if not iid.startswith("q:"):
            return
        qid = iid[2:]
        side_menu.delete(0, "end")
        if qid != MAIN:
            running = mgr.queue_running.get(qid)
            side_menu.add_command(label="Stop queue" if running else "Start queue",
                                  command=lambda: mgr.stop_queue(qid) if running
                                  else mgr.start_queue(qid))
        side_menu.add_command(label="Edit / schedule…",
                              command=lambda: scheduler_dialog(select=qid))
        side_menu.add_command(label="New queue…", command=lambda: scheduler_dialog(new=True))
        side_menu.tk_popup(ev.x_root, ev.y_root)

    lst = DownloadList(card, theme, on_select=lambda: refresh_details(),
                       on_open=open_selected, on_menu=popup)
    lst.pack(fill="both", expand=True)
    lst.c.bind("<Delete>", lambda e: do_delete())
    nav.bind("<Double-Button-1>", lambda e: side_queue() and scheduler_dialog(select=side_queue()))
    root.bind("<Control-n>", lambda e: add_dialog())
    root.bind("<Control-f>", lambda e: (search.focus_set(), "break"))

    # ---- dialogs ---------------------------------------------------------
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
        win = new_dialog()
        win.title("Add download")
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
        default_dir = mgr.settings["download_dir"]
        dir_v = tk.StringVar(value=default_dir)
        conn_v = tk.IntVar(value=mgr.settings["connections"])
        ref_v = tk.StringVar(value=referer)
        cookie_v = tk.StringVar(value=cookie)
        vid_v = tk.BooleanVar(value=is_video_url(url) if video is None else video)
        q_v = tk.StringVar(value=mgr.settings["video_quality"])
        start_v = tk.BooleanVar(value=True)
        pl_v = tk.BooleanVar(value=is_whole_playlist_url(url))
        season_v = tk.BooleanVar(value=season)
        items_v = tk.StringVar(value="")
        qnames = [q["name"] for q in mgr.settings["queues"]]
        qids = [q["id"] for q in mgr.settings["queues"]]
        pick = side_queue() or MAIN
        queue_v = tk.StringVar(value=qnames[qids.index(pick)])

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
                  style="Hint.TLabel").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(frm, text="Folder:").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=dir_v).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(frm, text="Browse…",
                   command=lambda: dir_v.set(filedialog.askdirectory(
                       initialdir=dir_v.get(), parent=win) or dir_v.get())
                   ).grid(row=r, column=2, padx=(6, 0))
        r += 1
        if mgr.settings.get("category_folders"):
            ttk.Label(frm, text="(the default folder means: sorted into its category "
                                "folder, e.g. \\Programs)", style="Hint.TLabel"
                      ).grid(row=r, column=1, sticky="w")
            r += 1
        crow = ttk.Frame(frm)
        crow.grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(crow, text="Connections:").pack(side="left")
        ttk.Spinbox(crow, from_=1, to=MAX_CONNECTIONS, textvariable=conn_v,
                    width=6).pack(side="left", padx=(8, 18))
        ttk.Label(crow, text="Queue:").pack(side="left")
        ttk.Combobox(crow, textvariable=queue_v, values=qnames, state="readonly",
                     width=18).pack(side="left", padx=(8, 0))
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
                  style="Hint.TLabel").pack(side="left")
        r += 1
        season_chk = ttk.Checkbutton(
            frm, variable=season_v,
            text="Whole season — follow the numbered episode links on this page "
                 "(uses “Only items” too)")
        season_chk.grid(row=r, column=0, columnspan=3, sticky="w", pady=3)
        r += 1
        ttk.Label(frm, text="Mirrors:").grid(row=r, column=0, sticky="nw", pady=3)
        mir_txt = tk.Text(frm, height=3, width=60, wrap="none", font=("Consolas", 9))
        theme.style_text_widget(mir_txt)
        mir_txt.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        ttk.Label(frm, text="Optional: other links to the SAME file on other hosts, one per "
                            "line. Each host's speed adds up.",
                  style="Hint.TLabel").grid(row=r, column=1, columnspan=2, sticky="w")
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
        start_chk = ttk.Checkbutton(frm, text="Start now", variable=start_v)
        start_chk.grid(row=r, column=0, columnspan=2, sticky="w")
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=1, columnspan=2, sticky="e", pady=(8, 0))

        def on_queue_change(*_):
            main = qids[qnames.index(queue_v.get())] == MAIN
            start_chk.configure(text="Start now" if main else
                                "Add to the queue (starts when the queue runs)")

        queue_v.trace_add("write", on_queue_change)
        on_queue_change()

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
            qid = qids[qnames.index(queue_v.get())]
            folder = dir_v.get().strip()
            if folder == default_dir and mgr.settings.get("category_folders"):
                folder = None                          # let the category decide
            start = start_v.get()
            if season_v.get() and not is_video_url(u):
                mgr.add_series(u, folder, conns, q_v.get(),
                               ref_v.get().strip(), cookie_v.get().strip(), user_agent,
                               items=items_v.get(), start=start, queue=qid,
                               links=series_links)
                win.destroy()
                return
            if vid_v.get() and pl_v.get() and is_playlist_url(u):
                mgr.add_playlist(u, folder, conns, q_v.get(),
                                 ref_v.get().strip(), cookie_v.get().strip(), user_agent,
                                 items=items_v.get(), start=start, queue=qid)
                win.destroy()
                return
            mirrors = [x.strip() for x in mir_txt.get("1.0", "end").splitlines()
                       if re.match(r"^https?://", x.strip())]
            d = mgr.add(u, folder, name_v.get().strip(), conns,
                        ref_v.get().strip(), cookie_v.get().strip(), user_agent,
                        video=vid_v.get(), quality=q_v.get(), start=start,
                        mirrors=mirrors, queue=qid)
            win.destroy()
            pending_select[0] = d.id

        def on_url_change(*_):
            u = url_v.get().strip()
            if gdrive.looks_like_file_link(u):
                conn_v.set(min(conn_v.get() or 8, gdrive.MAX_CONNECTIONS))
            page_like = bool(re.match(r"^https?://", u)) and not is_video_url(u) \
                and not gdrive.looks_like_file_link(u) and not re.search(r"\.(zip|rar|7z|exe|msi|iso|apk|mp4|mkv|webm|m3u8|mpd|mp3|pdf)"
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
        if video:
            vid_v.set(True)
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
                      style="Hint.TLabel").grid(row=r + 1, column=0, columnspan=3, sticky="w")
            win.after(1000, watch_expiry)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.bind("<Return>", lambda e: ok())
        win.bind("<Escape>", lambda e: cancel())
        show_dialog(win)
        e_url.focus_set()
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))

    def scheduler_dialog(new=False, select=None):
        """Queues and their schedules, like IDM's Scheduler window."""
        win = new_dialog()
        win.title("Scheduler")
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        lst = tk.Listbox(frm, width=24, height=14, activestyle="none", exportselection=False,
                         font=theme.f["body"], bd=0, highlightthickness=1)
        theme.style_text_widget(lst)
        lst.grid(row=0, column=0, sticky="ns", rowspan=2)
        lb = ttk.Frame(frm)
        lb.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        form = ttk.Frame(frm, padding=(14, 0, 0, 0))
        form.grid(row=0, column=1, sticky="nsew")
        form.columnconfigure(1, weight=1)
        name_v = tk.StringVar()
        max_v = tk.IntVar(value=3)
        st_on = tk.BooleanVar()
        st_t = tk.StringVar()
        sp_on = tk.BooleanVar()
        sp_t = tk.StringVar()
        done_v = tk.StringVar()
        day_v = [tk.BooleanVar() for _ in DAYS]
        state_v = tk.StringVar()
        cur = {"id": None}

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="w", pady=3)
        name_e = ttk.Entry(form, textvariable=name_v, width=28)
        name_e.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(form, text="Downloads at the same time:").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(form, from_=1, to=10, textvariable=max_v, width=5
                    ).grid(row=1, column=1, sticky="w", pady=3)
        sch = ttk.LabelFrame(form, text="Schedule", padding=10)
        sch.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        r1 = ttk.Frame(sch)
        r1.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(r1, text="Start at", variable=st_on).pack(side="left")
        ttk.Entry(r1, textvariable=st_t, width=7).pack(side="left", padx=6)
        ttk.Label(r1, text="(24-hour, e.g. 01:30)", style="Hint.TLabel").pack(side="left")
        dr = ttk.Frame(sch)
        dr.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(dr, text="On:").pack(side="left", padx=(22, 4))
        for i, dname in enumerate(DAYS):
            ttk.Checkbutton(dr, text=dname, variable=day_v[i]).pack(side="left")
        qd = ttk.Frame(sch)
        qd.grid(row=2, column=0, sticky="w", pady=(4, 0))
        for label, days in (("Every day", range(7)), ("Weekdays", range(5)),
                            ("Weekends", (5, 6))):
            ttk.Button(qd, text=label, width=10, command=lambda ds=tuple(days): [
                v.set(i in ds) for i, v in enumerate(day_v)]).pack(side="left", padx=(0, 4))
        r2 = ttk.Frame(sch)
        r2.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(r2, text="Stop at", variable=sp_on).pack(side="left")
        ttk.Entry(r2, textvariable=sp_t, width=7).pack(side="left", padx=6)
        ttk.Label(r2, text="unfinished files wait for the next start",
                  style="Hint.TLabel").pack(side="left")
        r3 = ttk.Frame(sch)
        r3.grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Label(r3, text="When the queue is done:").pack(side="left")
        ttk.Combobox(r3, textvariable=done_v, values=list(WHEN_DONE.values()),
                     state="readonly", width=26).pack(side="left", padx=6)
        run = ttk.Frame(form)
        run.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        del_b = ttk.Button(lb, text="Delete")
        start_b = ttk.Button(run, text="Start now")
        start_b.pack(side="left")
        stop_b = ttk.Button(run, text="Stop now")
        stop_b.pack(side="left", padx=6)
        ttk.Label(run, textvariable=state_v, style="Hint.TLabel").pack(side="left", padx=6)
        sch_widgets = [w for w in sch.winfo_children()]

        def set_state(widget, enabled):
            try:
                widget.state(["!disabled"] if enabled else ["disabled"])
            except (tk.TclError, AttributeError):
                pass
            for w in widget.winfo_children():
                set_state(w, enabled)

        def fill_list():
            lst.delete(0, "end")
            for q in mgr.settings["queues"]:
                lst.insert("end", ("  " if q["id"] == MAIN else "  ") + q["name"])

        def load(qid):
            q = mgr.queue(qid)
            cur["id"] = qid
            name_v.set(q["name"])
            max_v.set(mgr.queue_limit(qid))
            st_on.set(q["start_enabled"])
            st_t.set(q["start_time"])
            sp_on.set(q["stop_enabled"])
            sp_t.set(q["stop_time"])
            done_v.set(WHEN_DONE[q["when_done"]])
            for i, v in enumerate(day_v):
                v.set(i in q["days"])
            main = qid == MAIN
            name_e.state(["disabled"] if main else ["!disabled"])
            for w in sch_widgets + [start_b, stop_b, del_b]:
                set_state(w, not main)
            show_state()

        def show_state():
            qid = cur["id"]
            if qid == MAIN:
                state_v.set("The main queue always runs.")
                return
            n = sum(1 for d in mgr.downloads if mgr.queue_of(d) == qid
                    and d.status != "Complete")
            state_v.set(("Running" if mgr.queue_running.get(qid) else "Stopped")
                        + f" · {describe(mgr.queue(qid))} · {n} file(s) to download")

        def save_current():
            qid = cur["id"]
            if not qid or not mgr.queue(qid):
                return True
            q = mgr.queue(qid)
            for label, v in (("Start", st_t), ("Stop", sp_t)):
                if parse_hhmm(v.get()) is None:
                    messagebox.showerror("Scheduler", f"{label} time: use hh:mm, e.g. 01:30",
                                         parent=win)
                    return False
            try:
                limit = max(1, min(10, int(max_v.get())))
            except (tk.TclError, ValueError):
                limit = q["max_active"]
            if qid == MAIN:
                mgr.settings["max_active"] = limit
            name = name_v.get().strip() or q["name"]
            h1, m1 = parse_hhmm(st_t.get())
            h2, m2 = parse_hhmm(sp_t.get())
            q.update(name=name, max_active=limit,
                     start_enabled=st_on.get(), start_time=f"{h1:02d}:{m1:02d}",
                     stop_enabled=sp_on.get(), stop_time=f"{h2:02d}:{m2:02d}",
                     days=[i for i, v in enumerate(day_v) if v.get()],
                     when_done=next(k for k, v in WHEN_DONE.items() if v == done_v.get()))
            mgr.mark_dirty()
            i = [x["id"] for x in mgr.settings["queues"]].index(qid)
            if lst.get(i).strip() != name:              # renamed: update the list row
                keep = lst.curselection()
                lst.delete(i)
                lst.insert(i, "  " + name)
                for j in keep:
                    lst.selection_set(j)
            return True

        def on_pick(_=None):
            sel = lst.curselection()
            if not sel:
                return
            qid = mgr.settings["queues"][sel[0]]["id"]
            if qid == cur["id"]:
                return
            if not save_current():
                i = [x["id"] for x in mgr.settings["queues"]].index(cur["id"])
                lst.selection_clear(0, "end")
                lst.selection_set(i)
                return
            load(qid)

        def add_queue():
            if not save_current():
                return
            n = len(mgr.settings["queues"])
            q = new_queue("Tonight" if n == 1 else f"Queue {n}", 2)
            q.update(start_enabled=n == 1)
            mgr.settings["queues"].append(q)
            mgr.mark_dirty()
            fill_list()
            lst.selection_clear(0, "end")
            lst.selection_set("end")
            load(q["id"])
            name_e.focus_set()
            name_e.select_range(0, "end")

        def delete_queue():
            qid = cur["id"]
            if qid == MAIN:
                return
            q = mgr.queue(qid)
            if not messagebox.askyesno("Delete queue", f"Delete “{q['name']}”? Its files "
                                       "move to the main queue.", parent=win):
                return
            mgr.delete_queue(qid)
            cur["id"] = None
            fill_list()
            lst.selection_set(0)
            load(MAIN)

        def start_now():
            if save_current():
                mgr.start_queue(cur["id"])
                win.after(200, show_state)

        def stop_now():
            mgr.stop_queue(cur["id"])
            win.after(200, show_state)

        def close():
            if save_current():
                win.destroy()

        start_b.configure(command=start_now)
        stop_b.configure(command=stop_now)
        ttk.Button(lb, text="New queue", command=add_queue).pack(side="left")
        del_b.configure(command=delete_queue)
        del_b.pack(side="left", padx=6)
        bb = ttk.Frame(frm)
        bb.grid(row=2, column=1, sticky="e", pady=(6, 0))
        ttk.Button(bb, text="Close", command=close).pack(side="right")
        lst.bind("<<ListboxSelect>>", on_pick)
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        fill_list()
        ids = [q["id"] for q in mgr.settings["queues"]]
        first = select if select in ids else MAIN
        lst.selection_set(ids.index(first))
        load(first)
        show_dialog(win)
        if new:
            add_queue()

    def when_done_dialog(qid):
        """Countdown before closing the app, sleeping or shutting down."""
        q = mgr.queue(qid)
        if not q:
            return
        action = q["when_done"]
        if action == "none":
            mgr.notice = f"“{q['name']}” is done"
            return
        win = new_dialog()
        win.title("Queue finished")
        win.attributes("-topmost", True)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        what = {"exit": "Closing the app", "sleep": "Putting the computer to sleep",
                "shutdown": "Shutting down the computer"}[action]
        msg = tk.StringVar()
        ttk.Label(frm, text=f"“{q['name']}” has finished downloading.",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frm, textvariable=msg).pack(anchor="w", pady=(6, 12))
        left_s = [60]
        cancelled = [False]

        def do_it():
            if cancelled[0]:
                return
            cancelled[0] = True
            win.destroy()
            if action == "exit":
                on_close(force=True)
                return
            import subprocess
            mgr.save()
            if os.name == "nt":
                if action == "sleep":
                    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
                else:
                    subprocess.Popen(["shutdown", "/s", "/t", "0"])

        def tick():
            if cancelled[0]:
                return
            msg.set(f"{what} in {left_s[0]} seconds…")
            if left_s[0] <= 0:
                do_it()
                return
            left_s[0] -= 1
            win.after(1000, tick)

        def cancel():
            cancelled[0] = True
            win.destroy()
            mgr.notice = f"Cancelled: {what.lower()}"

        b = ttk.Frame(frm)
        b.pack(anchor="e")
        ttk.Button(b, text="Do it now", command=do_it).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=cancel).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", cancel)
        show_dialog(win)
        tick()

    def settings_dialog():
        s = mgr.settings
        win = new_dialog()
        win.title("Options")
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        frm = ttk.Frame(nb, padding=14)                 # General
        cap = ttk.Frame(nb, padding=14)                 # Browser
        upf = ttk.Frame(nb, padding=14)                 # Updates
        nb.add(frm, text="General")
        nb.add(cap, text="Browser")
        nb.add(upf, text="Updates")
        frm.columnconfigure(1, weight=1)
        dir_v = tk.StringVar(value=s["download_dir"])
        conn_v = tk.IntVar(value=s["connections"])
        act_v = tk.IntVar(value=s["max_active"])
        lim_v = tk.IntVar(value=s["speed_limit_kb"])
        clip_v = tk.BooleanVar(value=s["clipboard_watch"])
        ytu_v = tk.BooleanVar(value=s["auto_update_ytdlp"])
        appu_v = tk.BooleanVar(value=s["check_app_updates"])
        ask_v = tk.BooleanVar(value=s["ask_before_start"])
        catf_v = tk.BooleanVar(value=s.get("category_folders", False))
        q_v = tk.StringVar(value=s["video_quality"])
        THEMES = {"system": "Same as Windows", "light": "Light", "dark": "Dark"}
        theme_v = tk.StringVar(value=THEMES.get(s.get("theme", "system"), "Same as Windows"))
        cap_v = tk.BooleanVar(value=s["browser_capture"])
        types_v = tk.StringVar(value=s["capture_types"])
        min_v = tk.IntVar(value=s["capture_min_kb"])
        excl_v = tk.StringVar(value=s["capture_exclude"])
        always_v = tk.StringVar(value=s.get("capture_always", ""))
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
            ("Appearance:", ttk.Combobox(frm, textvariable=theme_v, state="readonly",
                                         values=list(THEMES.values()), width=16)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3)
            widget.grid(row=i, column=1, sticky="w" if i else "ew", pady=3)
        ttk.Button(frm, text="Browse…",
                   command=lambda: dir_v.set(filedialog.askdirectory(
                       initialdir=dir_v.get(), parent=win) or dir_v.get())
                   ).grid(row=0, column=2, padx=(6, 0))
        n = len(rows)
        ttk.Checkbutton(frm, text="Sort new downloads into category folders "
                                  "(Downloads\\Videos, \\Programs, \\Compressed …)",
                        variable=catf_v).grid(row=n, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Offer to download links I copy (clipboard watch)",
                        variable=clip_v).grid(row=n + 1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Ask before starting downloads sent from the browser",
                        variable=ask_v).grid(row=n + 2, column=0, columnspan=3, sticky="w")
        cap.columnconfigure(1, weight=1)
        ttk.Checkbutton(cap, text="Catch downloads started in the browser",
                        variable=cap_v).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(cap, text="File types:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(cap, textvariable=types_v, width=60).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(cap, text="Default", command=lambda: types_v.set(DEFAULT_CAPTURE_TYPES)
                   ).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(cap, text="(blank = every file type)", style="Hint.TLabel"
                  ).grid(row=2, column=1, sticky="w")
        ttk.Label(cap, text="Smaller than (KB):").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(cap, from_=0, to=10000000, increment=100, textvariable=min_v,
                    width=10).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(cap, text="Every type from:").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(cap, textvariable=always_v).grid(row=4, column=1, columnspan=2, sticky="ew",
                                                   pady=3)
        ttk.Label(cap, text="Never catch on:").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(cap, textvariable=excl_v).grid(row=5, column=1, columnspan=2, sticky="ew",
                                                 pady=3)
        ttk.Label(cap, text="Sites separated by spaces. “Every type from” starts with Google "
                            "Drive and Docs.\nHold Alt while clicking a link to skip the app once. "
                            "These are the same rules as the extension popup.",
                  style="Hint.TLabel", justify="left").grid(row=6, column=0, columnspan=3,
                                                            sticky="w", pady=(6, 0))
        upf.columnconfigure(0, weight=1)
        ttk.Checkbutton(upf, text="Keep yt-dlp (YouTube, Facebook…) up to date by itself",
                        variable=ytu_v).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(upf, text="Tell me when a new version of the app is out",
                        variable=appu_v).grid(row=1, column=0, sticky="w")
        ttk.Button(upf, text="Check now", command=lambda: update_check(manual=True)
                   ).grid(row=0, column=1, rowspan=2, padx=(6, 0))
        build = f"build {BUILD}" if BUILD else "built by hand — app updates come from git"
        ttk.Label(upf, text=f"App {VERSION} ({build}) · yt-dlp {ytdlp_version() or '—'} "
                            f"({_ytdlp_source[0]})", style="Hint.TLabel"
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ff = find_ffmpeg()
        notes = [f"Browser extension: {'listening on port ' + str(s['listen_port']) if listening else 'port busy — is another copy running?'}",
                 f"Browser can start the app: {'yes — ' if integration['ok'] else 'no — '}{integration['msg']}",
                 f"yt-dlp (video sites): {'installed' if have_ytdlp() else 'NOT installed'}",
                 f"ffmpeg (HD video + audio merge): {ff or 'not found — put ffmpeg.exe next to the app'}"]
        ttk.Label(upf, text="\n".join(notes), style="Hint.TLabel",
                  justify="left").grid(row=3, column=0, columnspan=2, sticky="w",
                                       pady=(14, 0))

        def save():
            try:
                s.update(download_dir=dir_v.get().strip() or default_download_dir(),
                         connections=max(1, min(MAX_CONNECTIONS, int(conn_v.get()))),
                         max_active=max(1, int(act_v.get())),
                         speed_limit_kb=max(0, int(lim_v.get())),
                         clipboard_watch=clip_v.get(),
                         ask_before_start=ask_v.get(),
                         category_folders=catf_v.get(),
                         auto_update_ytdlp=ytu_v.get(),
                         check_app_updates=appu_v.get(),
                         video_quality=q_v.get())
                rules = (cap_v.get(), " ".join(types_v.get().split()),
                         max(0, int(min_v.get())), " ".join(excl_v.get().split()),
                         " ".join(always_v.get().split()))
                if rules != (s["browser_capture"], s["capture_types"], s["capture_min_kb"],
                             s["capture_exclude"], s.get("capture_always", "")):
                    s.update(browser_capture=rules[0], capture_types=rules[1],
                             capture_min_kb=rules[2], capture_exclude=rules[3],
                             capture_always=rules[4], capture_updated=time.time())
            except Exception:
                messagebox.showerror("Options", "Please enter whole numbers.", parent=win)
                return
            mode = next(k for k, v in THEMES.items() if v == theme_v.get())
            if mode != s.get("theme", "system"):
                s["theme"] = mode
                theme.set_mode(mode)
                btn["Theme"].icon = "sun" if theme.dark else "moon"
                btn["Theme"].draw()
            mgr.apply_settings()
            win.destroy()

        b = ttk.Frame(outer)
        b.pack(anchor="e", pady=(12, 0))
        ttk.Button(b, text="Save", command=save, style="Accent.TButton"
                   if theme.sv else "TButton").pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left")
        show_dialog(win)

    # ---- updates ---------------------------------------------------------
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

    # ---- periodic refresh ------------------------------------------------
    pending_select = [None]
    last_clip = [None]

    def handle_incoming():
        """Downloads sent by the browser extension."""
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
                pending_select[0] = same.id
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
        while True:                                    # finished queues
            try:
                kind, qid = mgr.events.get_nowait()
            except queue.Empty:
                break
            if kind == "queue_done":
                when_done_dialog(qid)

    def matches(d, view):
        st = d.status
        if view == "all":
            return True
        if view == "downloading":
            return st in ("Downloading", "Processing")
        if view == "queued":
            return st in ("Queued", "Paused")
        if view == "completed":
            return st == "Complete"
        if view == "failed":
            return st == "Error"
        if view.startswith("cat:"):
            return download_category(d) == view[4:]
        if view.startswith("q:"):
            return mgr.queue_of(d) == view[2:]
        return True

    def view_title(view):
        if view.startswith("cat:"):
            return view[4:]
        if view.startswith("q:"):
            q = mgr.queue(view[2:])
            return q["name"] if q else "Queue"
        return dict((k, v) for k, v, _ in LIBRARY).get(view, "All downloads")

    def nav_items(items):
        t = theme.t
        counts = {}
        for d in items:
            for key in ("all", "cat:" + download_category(d), "q:" + mgr.queue_of(d)):
                counts[key] = counts.get(key, 0) + 1
            for key, *_ in LIBRARY[1:]:
                if matches(d, key):
                    counts[key] = counts.get(key, 0) + 1
        soft = {"all": (t["accent_soft"], t["accent"]), "downloading": (t["accent_soft"], t["accent"]),
                "queued": (t["track"], t["muted"]), "completed": (t["track"], t["ok"]),
                "failed": (t["track"], t["bad"])}
        out = [{"head": True, "label": "Library"}]
        for key, label, icon in LIBRARY:
            tint, ink = soft[key]
            out.append({"id": key, "label": label, "icon": icon, "tint": tint, "ink": ink,
                        "count": counts.get(key, ""), "alert": key == "failed" and counts.get(key)})
        out.append({"head": True, "label": "Categories"})
        for c in CATEGORY_NAMES:
            tint, ink = theme.tile(c)
            out.append({"id": "cat:" + c, "label": c, "icon": CATEGORY_ICON[c], "tint": tint,
                        "ink": ink, "count": counts.get("cat:" + c, "")})
        out.append({"head": True, "label": "Queues"})
        for q in mgr.settings["queues"]:
            main_q = q["id"] == MAIN
            running = mgr.queue_running.get(q["id"])
            if main_q:
                sub = f"{mgr.queue_limit(MAIN)} at a time"
            elif running:
                sub = "● running"
            elif q["start_enabled"]:
                sub = f"{q['start_time']}" + (f" · then {WHEN_DONE[q['when_done']].lower()}"
                                              if q["when_done"] != "none" else "")
            else:
                sub = "starts when you press Start"
            out.append({"id": "q:" + q["id"], "label": q["name"], "sub": sub,
                        "sub_ink": t["ok"] if running else None,
                        "icon": "queue" if main_q else "clock",
                        "tint": t["track"] if main_q else t["queue_tint"],
                        "ink": t["text"] if main_q else t["queue_ink"],
                        "count": counts.get("q:" + q["id"], "")})
        return out

    def ext_label(d):
        name = d.filename or os.path.basename(d.path or "") or ""
        m = re.search(r"\.([A-Za-z0-9]{1,5})$", name)
        if m:
            return m.group(1).upper()
        return "VID" if d.kind == "video" else "FILE"

    def row_for(d, qobj):
        st = d.status
        running = st == "Downloading"
        pct = (d.downloaded / d.size) if d.size and d.size > 0 else None
        if st == "Complete":
            pct = None
        qid = mgr.queue_of(d)
        row = {"id": d.id, "ext": ext_label(d), "tile": theme.tile(download_category(d)),
               "name": d.filename or d.url, "queue": qobj["name"] if qobj else "",
               "queue_alt": qid != MAIN, "size": human_size(d.size) if d.size and d.size > 0 else "",
               "speed": f"{human_size(d.speed)}/s" if running else "",
               "eta": human_time(d.eta()) if running else ""}
        host = site_name(d.url)
        if st == "Error":
            row.update(sub=short_error(d.error), sub_ink="bad", pct=None,
                       state="Failed", state_icon="warn", state_ink="bad")
            return row
        if st == "Complete":
            row.update(sub=f"Saved to {d.folder}", pct=None, state="Completed",
                       state_icon="check", state_ink="ok")
            return row
        if running and d.kind == "video":
            sub = f"{host} · {getattr(d, 'quality', 'best')} quality"
        elif running:
            sub = f"{host} · {d.active_conns} connection{'s' * (d.active_conns != 1)}"
            if d.mirrors:
                sub += f" · +{len(d.mirrors)} mirror{'s' * (len(d.mirrors) > 1)}"
            if d.size is not None and not d.resumable:
                sub += " · server allows 1 connection"
        elif st == "Processing":
            sub = "Merging video and sound…"
        elif st == "Paused":
            sub = f"Paused · {human_size(d.downloaded)} of {human_size(d.size)}" \
                if d.size and d.size > 0 else "Paused"
        elif qid != MAIN and not mgr.queue_running.get(qid):
            when = f" ({qobj['start_time']})" if qobj and qobj["start_enabled"] else ""
            sub = f"Waits for {qobj['name'] if qobj else 'its queue'}{when}"
        else:
            sub = f"{host} · waiting for a free slot"
        row["sub"] = sub
        if st == "Processing":
            row.update(pct=None, state="Merging…", state_icon="hourglass", state_ink="accent")
        elif pct is not None and (running or (st == "Paused" and d.downloaded)):
            row.update(pct=pct, pct_text=f"{pct * 100:.0f}%",
                       bar="accent" if running else "paused")
        elif running:
            row.update(pct=None, state=human_size(d.downloaded) if d.downloaded else "Starting…",
                       state_icon="down", state_ink="accent")
        elif st == "Paused":
            row.update(pct=None, state="Paused", state_icon="pause", state_ink="muted")
        else:
            row.update(pct=None, state="Queued", state_icon="clock", state_ink="muted")
        return row

    def details_for(sel):
        if not sel:
            return None
        if len(sel) > 1:
            total = sum(d.size for d in sel if d.size and d.size > 0)
            done = sum(d.downloaded for d in sel)
            return {"title": f"{len(sel)} downloads selected", "chips": [],
                    "buttons": [("Stop", "pause", False), ("Resume", "resume", True)],
                    "segments": [(min(1.0, done / total) if total else 0.0, False)],
                    "stats": [("Selected", str(len(sel))), ("Total size", human_size(total)),
                              ("Downloaded", human_size(done)),
                              ("Speed", f"{human_size(sum(d.speed for d in sel))}/s")]}
        d = sel[0]
        name = d.filename or d.url
        st = d.status
        hosts = 1 + len(d.mirrors)
        host_text = site_name(d.url) + (f" + {len(d.mirrors)} mirror{'s' * (hosts > 2)}"
                                        if d.mirrors else "")
        if st == "Error":
            return {"title": name, "chips": [("Failed", "bad")], "error": d.error,
                    "hint": error_hint(d.error),
                    "buttons": [("Copy error", "copy_error", False),
                                ("Redownload", "redownload", True)]}
        if st == "Complete":
            return {"title": name, "chips": [("Completed", "ok")],
                    "buttons": [("Open folder", "folder", False), ("Open", "open", True)],
                    "segments": [(1.0, False)], "seg_color": "ok",
                    "stats": [("Size", human_size(d.size)), ("From", host_text),
                              ("Added", added_text(d.added)), ("Saved to", d.folder)]}
        chips = []
        if d.kind == "video":
            chips.append(("Video", "accent"))
        elif st == "Downloading":
            chips.append((f"{d.active_conns} connection{'s' * (d.active_conns != 1)}", "accent"))
        if d.mirrors:
            chips.append((f"{len(d.mirrors)} mirror{'s' * (len(d.mirrors) > 1)}", "accent"))
        if d.note:
            chips.append((d.note, "muted"))
        if d.kind == "file" and d.size and d.size > 0 and d.segments_snapshot():
            segs = []
            for start, end, pos, owner in d.segments_snapshot()[:48]:
                span = max(1, end - start + 1)
                segs.append((max(0.0, min(1.0, (pos - start) / span)), bool(owner)))
        else:
            frac = d.downloaded / d.size if d.size and d.size > 0 else 0.0
            segs = [(frac, st == "Downloading")]
        running = st == "Downloading"
        buttons = [("Open folder", "folder", False),
                   ("Pause", "pause", True) if running or st == "Queued"
                   else ("Resume", "resume", True)]
        size = human_size(d.size) if d.size and d.size > 0 else "?"
        return {"title": name, "chips": chips, "buttons": buttons, "segments": segs,
                "stats": [("Downloaded", f"{human_size(d.downloaded)} of {size}"),
                          ("Speed", f"{human_size(d.speed)}/s" if running else "—"),
                          ("Time left", human_time(d.eta()) if running and d.eta() else "—"),
                          ("Hosts", host_text), ("Saving to", d.folder)]}

    def added_text(t):
        lt, now = time.localtime(t), time.localtime()
        if lt[:3] == now[:3]:
            return "Today " + time.strftime("%I:%M %p", lt).lstrip("0")
        if time.time() - t < 2 * 86400 and lt.tm_yday == (now.tm_yday - 1) % 366:
            return "Yesterday"
        return time.strftime("%b %d", lt)

    def sort_rows(items):
        key = mgr.settings.get("sort", "newest")
        order = {"Downloading": 0, "Processing": 1, "Queued": 2, "Paused": 3, "Error": 4,
                 "Complete": 5}
        if key == "oldest":
            return sorted(items, key=lambda d: d.added)
        if key == "name":
            return sorted(items, key=lambda d: (d.filename or d.url).lower())
        if key == "size":
            return sorted(items, key=lambda d: -(d.size or 0))
        if key == "status":
            return sorted(items, key=lambda d: (order.get(d.status, 9), -d.added))
        return sorted(items, key=lambda d: -d.added)

    EMPTY = {"all": ("No downloads yet", "Click Add URL, copy a link, or download something "
                                         "in your browser."),
             "downloading": ("Nothing is downloading", "Downloads show here while they run."),
             "queued": ("Nothing is waiting", "Queued and paused downloads show here."),
             "completed": ("Nothing finished yet", "Completed downloads show here."),
             "failed": ("Nothing failed", "Good news.")}

    def refresh_details():
        details.show(details_for(selected()))

    def refresh_now():
        items = list(mgr.downloads)
        view = nav.selected
        if view.startswith("q:") and not mgr.queue(view[2:]):
            view = "all"
            nav.select(view)
        nav.set_items(nav_items(items))
        q = query().lower()
        shown = [d for d in items if matches(d, view)
                 and (not q or q in (d.filename or "").lower() or q in d.url.lower())]
        qobj = {x["id"]: x for x in mgr.settings["queues"]}
        rows = [row_for(d, qobj.get(mgr.queue_of(d))) for d in sort_rows(shown)]
        empty = ("No matches", f"Nothing here matches “{q}”.") if q else \
            EMPTY.get(view, ("Nothing here yet", ""))
        lst.set_rows(rows, empty)
        if pending_select[0] and pending_select[0] in lst.order:
            lst.select(pending_select[0])
            pending_select[0] = None
        title_var.set(view_title(view))
        act = sum(d.status == "Downloading" for d in shown)
        wait = sum(d.status == "Queued" for d in shown)
        summary_var.set(f"{len(shown)} file{'s' * (len(shown) != 1)}"
                        + (f" · {act} active" if act else "") + (f" · {wait} queued" if wait else ""))
        refresh_details()
        total_speed = sum(d.speed for d in items)
        chip.set(f"{human_size(total_speed)}/s", total_speed)
        seen = time.time() - getattr(mgr, "last_browser_ping", 0) < 600
        left = ["Browser connected" if seen and listening else
                "Browser extension ready" if listening else
                "Browser can't connect: port busy (is another copy running?)"]
        if mgr.notice:
            left.append(mgr.notice)
        lim = mgr.settings.get("speed_limit_kb", 0)
        if lim:
            left.append(f"Limit {lim} KB/s")
        right = []
        nxt = [x for x in mgr.settings["queues"] if x["id"] != MAIN and x["start_enabled"]
               and not mgr.queue_running.get(x["id"])]
        if nxt:
            right.append(f"{nxt[0]['name']} starts at {nxt[0]['start_time']}")
        right.append(f"Saving to {mgr.settings['download_dir']}")
        status.set(left, right, ok=listening)

    def refresh():
        try:
            handle_incoming()
            refresh_now()
        finally:
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

    def on_close(force=False):
        busy = any(d.status == "Downloading" for d in mgr.downloads)
        if busy and not force and not messagebox.askyesno(
                "Quit", "Downloads are running. They'll be paused and can be "
                        "resumed next time. Quit?"):
            return
        mgr.shutdown()
        root.destroy()

    UI.clear()
    UI.update(root=root, mgr=mgr, theme=theme, buttons=btn, nav=nav, list=lst,
              details=details, status=status, add_dialog=add_dialog,
              scheduler_dialog=scheduler_dialog, settings_dialog=settings_dialog,
              toggle_theme=toggle_theme, refresh_now=refresh_now, search=search_v)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(800, after_update_note)
    root.after(8000, daily_check)
    root.after(1000, poll_updates)
    root.after(100, refresh)
    root.after(1500, check_clipboard)
    if initial_url:
        root.after(400, lambda: add_dialog(initial_url))
    root.mainloop()
