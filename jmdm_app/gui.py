"""The app window (Tkinter), in the Classic IDM layout: menu bar, big toolbar
with a speed graph, categories and queues on the left, the download list on
the right, the selected file's connections below it, and a status bar."""
import os
import re
import time
import queue
import threading
import urllib.parse
from .config import APP_NAME, BUILD, DEFAULT_CAPTURE_TYPES, MAX_CONNECTIONS, VERSION
from .util import clean_err, default_download_dir, find_ffmpeg, human_size, human_time, is_playlist_url, is_video_url, is_whole_playlist_url, sanitize
from .gates import looks_like_download
from .updates import _ytdlp_source, app_update_available, have_ytdlp, prepare_app_update, run_app_update, update_ytdlp, ytdlp_version
from .integration import register_browser_integration
from .browser import decision_expired, settle
from .manager import Manager
from .schedule import CATEGORY_NAMES, DAYS, MAIN, WHEN_DONE, describe, download_category, new_queue, parse_hhmm

ACCENT = "#1d5fbf"


def run_gui(initial_url=None):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from .gui_widgets import (CATEGORY_COLORS, FONT, TOOLBAR_BG, SpeedGraph, ToolButton,
                              badge_image, folder_image, ui_scale)

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
    root.geometry(f"{int(1180 * S)}x{int(700 * S)}")
    root.minsize(int(820 * S), int(480 * S))
    root.configure(bg="#ffffff")
    style = ttk.Style()
    for theme in ("vista", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break
    style.configure("Treeview", rowheight=int(24 * S), font=FONT)
    style.configure("Treeview.Heading", font=FONT)
    style.configure("Side.Treeview", rowheight=int(26 * S), background="#f8f9fb",
                    fieldbackground="#f8f9fb", borderwidth=0)
    style.map("Treeview", background=[("selected", "#cfe0f7")],
              foreground=[("selected", "#202124")])
    style.map("Side.Treeview", background=[("selected", "#dce8f8")],
              foreground=[("selected", "#202124")])
    style.configure("Status.TLabel", background="#f4f6f9", foreground="#5f6368", font=FONT)

    icon_size = max(14, int(16 * S))
    badges = {c: badge_image(root, *CATEGORY_COLORS[c], icon_size) for c in CATEGORY_COLORS}
    folders = {"cat": folder_image(root, "#fbd57a", "#b78a1a", icon_size),
               "state": folder_image(root, "#c9d3e0", "#6b7a8f", icon_size),
               "queue": folder_image(root, "#d9d2f5", "#5b3cc4", icon_size)}

    # ---- actions ---------------------------------------------------------
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

    def side_queue():
        """The queue picked in the sidebar, if any."""
        s = side.selection()
        return s[0][2:] if s and s[0].startswith("q:") and mgr.queue(s[0][2:]) else None

    # ---- menu bar --------------------------------------------------------
    menubar = tk.Menu(root)
    m_tasks = tk.Menu(menubar, tearoff=0)
    m_tasks.add_command(label="Add download…", accelerator="Ctrl+N",
                        command=lambda: add_dialog())
    m_tasks.add_command(label="Download video…", command=lambda: add_dialog(video=True))
    m_tasks.add_separator()
    m_tasks.add_command(label="Scheduler…", command=lambda: scheduler_dialog())
    m_tasks.add_separator()
    m_tasks.add_command(label="Exit", command=lambda: on_close())
    menubar.add_cascade(label="Tasks", menu=m_tasks)
    m_dl = tk.Menu(menubar, tearoff=0)
    m_dl.add_command(label="Resume", command=do_resume)
    m_dl.add_command(label="Stop", command=do_pause)
    m_dl.add_command(label="Resume all", command=do_resume_all)
    m_dl.add_command(label="Stop all", command=do_pause_all)
    m_dl.add_separator()
    m_dl.add_command(label="Redownload", command=do_redownload)
    m_dl.add_command(label="Delete…", accelerator="Del", command=do_delete)
    m_dl.add_command(label="Delete completed…", command=do_delete_completed)
    m_dl.add_separator()
    m_dl.add_command(label="Options…", command=lambda: settings_dialog())
    menubar.add_cascade(label="Downloads", menu=m_dl)
    m_q = tk.Menu(menubar, tearoff=0, postcommand=lambda: fill_queue_menu(m_q))
    menubar.add_cascade(label="Queues", menu=m_q)
    m_view = tk.Menu(menubar, tearoff=0)
    show_details = tk.BooleanVar(value=True)
    m_view.add_checkbutton(label="Connections panel", variable=show_details,
                           command=lambda: toggle_details())
    m_view.add_command(label="Open download folder", command=lambda: open_folder())
    menubar.add_cascade(label="View", menu=m_view)
    m_help = tk.Menu(menubar, tearoff=0)
    m_help.add_command(label="Check for updates", command=lambda: update_check(manual=True))
    m_help.add_command(label="About", command=lambda: messagebox.showinfo(
        "About", f"{APP_NAME} {VERSION}" + (f" (build {BUILD})" if BUILD else "")
        + f"\nyt-dlp {ytdlp_version() or '—'}"))
    menubar.add_cascade(label="Help", menu=m_help)
    root.config(menu=menubar)

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
        b = tb_queue
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    # ---- toolbar ---------------------------------------------------------
    bar = tk.Frame(root, bg=TOOLBAR_BG, padx=int(8 * S), pady=int(6 * S))
    bar.pack(fill="x")
    tk.Frame(root, height=1, bg="#dde1e7").pack(fill="x")
    buttons = [("Add URL", "add", "#e6effb", ACCENT, lambda: add_dialog()),
               ("Resume", "resume", "#e5f4ea", "#127a3a", do_resume),
               ("Stop", "stop", "#fdeceb", "#b3261e", do_pause),
               ("Stop all", "stop_all", "#fdeceb", "#b3261e", do_pause_all),
               ("Delete", "delete", "#f1f3f4", "#3c4043", do_delete),
               ("Video", "video", "#fbe9ec", "#a8172e", lambda: add_dialog(video=True)),
               ("Scheduler", "clock", "#fff3e0", "#a15c00", lambda: scheduler_dialog()),
               ("Start queue", "queue", "#ede8fb", "#5b3cc4", lambda: queue_button()),
               ("Options", "options", "#f1f3f4", "#3c4043", lambda: settings_dialog())]
    tb = {}
    for text, icon, tint, ink, cmd in buttons:
        tb[text] = ToolButton(bar, text, icon, tint, ink, cmd, S)
        tb[text].pack(side="left", padx=1)
    tb_queue = tb["Start queue"]
    graph = SpeedGraph(bar, ACCENT, S)
    graph.pack(side="right", padx=(int(10 * S), 0))
    sp = tk.Frame(bar, bg=TOOLBAR_BG)
    sp.pack(side="right")
    tk.Label(sp, text="Total speed", bg=TOOLBAR_BG, fg="#5f6368", font=FONT).pack(anchor="e")
    speed_var = tk.StringVar(value="0 B/s")
    tk.Label(sp, textvariable=speed_var, bg=TOOLBAR_BG, fg=ACCENT,
             font=("Segoe UI", 15, "bold")).pack(anchor="e")

    # ---- status bar (packed before the body so it stays at the bottom) ----
    status = tk.Frame(root, bg="#f4f6f9")
    status.pack(side="bottom", fill="x")
    tk.Frame(root, height=1, bg="#dde1e7").pack(side="bottom", fill="x")
    status_l = tk.StringVar()
    status_r = tk.StringVar()
    ttk.Label(status, textvariable=status_l, style="Status.TLabel",
              padding=(int(10 * S), int(3 * S))).pack(side="left")
    ttk.Label(status, textvariable=status_r, style="Status.TLabel",
              padding=(int(10 * S), int(3 * S))).pack(side="right")

    # ---- body: sidebar | list -------------------------------------------
    body = tk.PanedWindow(root, orient="horizontal", sashwidth=int(4 * S), bd=0,
                          bg="#dde1e7", sashrelief="flat")
    body.pack(fill="both", expand=True)

    left = tk.Frame(body, bg="#f8f9fb")
    side = ttk.Treeview(left, columns=("n",), show="tree", selectmode="browse",
                        style="Side.Treeview")
    side.column("#0", width=int(150 * S), stretch=True)
    side.column("n", width=int(56 * S), anchor="e", stretch=False)
    side.pack(fill="both", expand=True, padx=(int(4 * S), 0), pady=int(6 * S))
    side.tag_configure("head", foreground="#5f6368", font=("Segoe UI", 8, "bold"))
    side.insert("", "end", iid="cat:All", text=" All downloads", image=folders["cat"],
                open=True)
    for c in CATEGORY_NAMES:
        side.insert("cat:All", "end", iid=f"cat:{c}", text=f" {c}", image=folders["cat"])
    side.insert("", "end", iid="st:Unfinished", text=" Unfinished", image=folders["state"])
    side.insert("", "end", iid="st:Finished", text=" Finished", image=folders["state"])
    side.insert("", "end", iid="queues", text="QUEUES", tags=("head",), open=True)
    side.selection_set("cat:All")
    body.add(left, minsize=int(170 * S), width=int(230 * S))

    right = tk.Frame(body, bg="#ffffff")
    body.add(right, minsize=int(400 * S))
    cols = ("size", "status", "eta", "speed", "queue", "added")
    tree = ttk.Treeview(right, columns=cols, show="tree headings", selectmode="extended")
    tree.heading("#0", text="File name", anchor="w")
    tree.column("#0", width=int(300 * S), minwidth=int(160 * S), anchor="w", stretch=True)
    for c, title, w, anchor in (("size", "Size", 76, "e"), ("status", "Status", 150, "w"),
                                ("eta", "Time left", 72, "e"), ("speed", "Speed", 86, "e"),
                                ("queue", "Queue", 92, "w"), ("added", "Added", 108, "w")):
        tree.heading(c, text=title, anchor=anchor)
        tree.column(c, width=int(w * S), minwidth=int(40 * S), anchor=anchor,
                    stretch=(c == "status"))
    vs = ttk.Scrollbar(right, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vs.set)
    tree.tag_configure("odd", background="#fafbfc")
    tree.tag_configure("done", foreground="#1e6b38")
    tree.tag_configure("err", foreground="#b3261e")
    tree.tag_configure("wait", foreground="#5f6368")

    # details: the selected file's connections (IDM's segment map)
    det = tk.Frame(right, bg="#ffffff", padx=int(10 * S), pady=int(6 * S))
    tk.Frame(right, height=1, bg="#e5e8ed").pack(side="bottom", fill="x")
    det.pack(side="bottom", fill="x")
    info_var = tk.StringVar()
    tk.Label(det, textvariable=info_var, bg="#ffffff", fg="#3c4043", font=FONT,
             anchor="w", justify="left").pack(fill="x")
    seg_canvas = tk.Canvas(det, height=int(18 * S), highlightthickness=1,
                           highlightbackground="#c6ccd5", bg="#eef1f5")
    seg_canvas.pack(fill="x", pady=(int(4 * S), 0))
    tk.Frame(right, height=1, bg="#e5e8ed").pack(side="bottom", fill="x")
    vs.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    def toggle_details():
        if show_details.get():
            det.pack(side="bottom", fill="x", before=vs)
        else:
            det.pack_forget()

    # ---- context menu ----------------------------------------------------
    menu = tk.Menu(root, tearoff=0)
    move_menu = tk.Menu(menu, tearoff=0)

    def fill_move_menu():
        move_menu.delete(0, "end")
        for q in mgr.settings["queues"]:
            move_menu.add_command(label=q["name"], command=lambda i=q["id"]: [
                mgr.move_to_queue(d, i) for d in selected()])

    menu.add_command(label="Open", command=lambda: [open_file(d) for d in selected()[:1]])
    menu.add_command(label="Open folder", command=lambda: open_folder())
    menu.add_separator()
    menu.add_command(label="Resume", command=do_resume)
    menu.add_command(label="Stop", command=do_pause)
    menu.add_command(label="Redownload", command=do_redownload)
    menu.add_command(label="Add mirror link…", command=lambda: add_mirror_dialog())
    menu.add_cascade(label="Move to queue", menu=move_menu)
    menu.add_separator()
    menu.add_command(label="Copy link", command=copy_url)
    menu.add_command(label="Delete", command=do_delete)

    def popup(ev):
        row = tree.identify_row(ev.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
        fill_move_menu()
        menu.tk_popup(ev.x_root, ev.y_root)

    tree.bind("<Button-3>", popup)
    tree.bind("<Double-1>", lambda e: [open_file(d) for d in selected()[:1]])
    tree.bind("<Delete>", lambda e: do_delete())
    root.bind("<Control-n>", lambda e: add_dialog())

    side_menu = tk.Menu(root, tearoff=0)

    def side_popup(ev):
        row = side.identify_row(ev.y)
        if not (row.startswith("q:") or row == "queues"):
            return
        if row.startswith("q:"):
            side.selection_set(row)
        side_menu.delete(0, "end")
        qid = row[2:] if row.startswith("q:") else None
        if qid and qid != MAIN:
            running = mgr.queue_running.get(qid)
            side_menu.add_command(label="Stop queue" if running else "Start queue",
                                  command=lambda: mgr.stop_queue(qid) if running
                                  else mgr.start_queue(qid))
        if qid:
            side_menu.add_command(label="Edit / schedule…",
                                  command=lambda: scheduler_dialog(select=qid))
        side_menu.add_command(label="New queue…", command=lambda: scheduler_dialog(new=True))
        side_menu.tk_popup(ev.x_root, ev.y_root)

    side.bind("<Button-3>", side_popup)
    side.bind("<Double-1>", lambda e: side_queue() and scheduler_dialog(select=side_queue()))

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
                  foreground="#777").grid(row=r, column=1, sticky="w")
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
                                "folder, e.g. \\Programs)", foreground="#777"
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

    def scheduler_dialog(new=False, select=None):
        """Queues and their schedules, like IDM's Scheduler window."""
        win = tk.Toplevel(root)
        win.title("Scheduler")
        win.transient(root)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        lst = tk.Listbox(frm, width=24, height=14, activestyle="none", exportselection=False,
                         font=FONT)
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
        ttk.Label(r1, text="(24-hour, e.g. 01:30)", foreground="#777").pack(side="left")
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
                  foreground="#777").pack(side="left")
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
        ttk.Label(run, textvariable=state_v, foreground="#555").pack(side="left", padx=6)
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
        win = tk.Toplevel(root)
        win.title("Queue finished")
        win.transient(root)
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
        tick()

    def settings_dialog():
        s = mgr.settings
        win = tk.Toplevel(root)
        win.title("Options")
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
        catf_v = tk.BooleanVar(value=s.get("category_folders", False))
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
        ttk.Checkbutton(frm, text="Sort new downloads into category folders "
                                  "(Downloads\\Videos, \\Programs, \\Compressed …)",
                        variable=catf_v).grid(row=n, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Offer to download links I copy (clipboard watch)",
                        variable=clip_v).grid(row=n + 1, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Ask before starting downloads sent from the browser",
                        variable=ask_v).grid(row=n + 2, column=0, columnspan=3, sticky="w")
        cap = ttk.LabelFrame(frm, text="Browser downloads (same rules as the extension popup)",
                             padding=8)
        cap.grid(row=n + 3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
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
        upf.grid(row=n + 4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
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
                  justify="left").grid(row=n + 5, column=0, columnspan=3, sticky="w",
                                       pady=(10, 0))

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
                         max(0, int(min_v.get())), " ".join(excl_v.get().split()))
                if rules != (s["browser_capture"], s["capture_types"], s["capture_min_kb"],
                             s["capture_exclude"]):
                    s.update(browser_capture=rules[0], capture_types=rules[1],
                             capture_min_kb=rules[2], capture_exclude=rules[3],
                             capture_updated=time.time())
            except Exception:
                messagebox.showerror("Options", "Please enter whole numbers.", parent=win)
                return
            mgr.apply_settings()
            win.destroy()

        b = ttk.Frame(frm)
        b.grid(row=n + 6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left")

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
    tree_select = [None]
    last_clip = [None]
    ticks = [0]

    def status_text(d):
        pct = f"{min(100.0, d.downloaded * 100 / d.size):.1f}%" \
            if d.size and d.size > 0 else ""
        if d.status == "Error":
            return "Error: " + d.error
        if d.status == "Processing":
            return "Merging / converting…"
        if d.status == "Downloading":
            if d.kind == "file" and d.size is not None and not d.resumable:
                return (pct + " · " if pct else "") + "1 connection only"
            return pct or (human_size(d.downloaded) if d.downloaded else "Starting…")
        if d.status in ("Paused", "Queued") and pct and d.downloaded:
            return f"{d.status} · {pct}"
        return d.status

    def added_text(t):
        lt, now = time.localtime(t), time.localtime()
        if lt[:3] == now[:3]:
            return "Today " + time.strftime("%I:%M %p", lt).lstrip("0")
        if time.time() - t < 2 * 86400 and lt.tm_yday == (now.tm_yday - 1) % 366:
            return "Yesterday"
        return time.strftime("%b %d", lt)

    def draw_segments(d):
        c = seg_canvas
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        if d is None:
            return
        if d.status == "Complete":
            c.create_rectangle(0, 0, w, h, fill="#3a9a5b", outline="")
            return
        if d.kind == "video" or not d.size or d.size <= 0:
            if d.size and d.size > 0:
                c.create_rectangle(0, 0, w * d.downloaded / d.size, h,
                                   fill=ACCENT, outline="")
            return
        for start, end, pos, owner in d.segments_snapshot():
            x0 = start / d.size * w
            x1 = pos / d.size * w
            if x1 > x0:
                c.create_rectangle(x0, 0, x1, h, fill=ACCENT, outline="")
            if owner and pos <= end:
                c.create_line(x1, 0, x1, h, fill="#ff9800", width=2)
            c.create_line(start / d.size * w, 0, start / d.size * w, 4, fill="#555")

    def matches(d, view):
        if view == "cat:All":
            return True
        if view.startswith("cat:"):
            return download_category(d) == view[4:]
        if view == "st:Unfinished":
            return d.status != "Complete"
        if view == "st:Finished":
            return d.status == "Complete"
        if view.startswith("q:"):
            return mgr.queue_of(d) == view[2:]
        return True

    def refresh_sidebar(items):
        counts = {}
        for d in items:
            for key in ("cat:All", "cat:" + download_category(d),
                        "st:Finished" if d.status == "Complete" else "st:Unfinished",
                        "q:" + mgr.queue_of(d)):
                counts[key] = counts.get(key, 0) + 1
        for iid in ["cat:All"] + [f"cat:{c}" for c in CATEGORY_NAMES] + \
                   ["st:Unfinished", "st:Finished"]:
            side.set(iid, "n", counts.get(iid, 0) or "")
        have = set(side.get_children("queues"))
        want = []
        for q in mgr.settings["queues"]:
            iid = "q:" + q["id"]
            want.append(iid)
            running = mgr.queue_running.get(q["id"])
            label = f" {q['name']}"
            extra = "" if q["id"] == MAIN else (" ● running" if running else
                                                 f" · {q['start_time']}" if q["start_enabled"]
                                                 else "")
            if iid in have:
                side.item(iid, text=label + extra)
            else:
                side.insert("queues", "end", iid=iid, text=label + extra,
                            image=folders["queue"])
            side.set(iid, "n", counts.get(iid, 0) or "")
        for iid in have - set(want):
            if iid in side.selection():
                side.selection_set("cat:All")
            side.delete(iid)
        for i, iid in enumerate(want):
            side.move(iid, "queues", i)

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
        # finished queues
        while True:
            try:
                kind, qid = mgr.events.get_nowait()
            except queue.Empty:
                break
            if kind == "queue_done":
                when_done_dialog(qid)

        items = list(mgr.downloads)
        refresh_sidebar(items)
        view = (side.selection() or ("cat:All",))[0]
        existing = set(tree.get_children())
        current = []
        total_speed = 0.0
        active = waiting = 0
        qname = {q["id"]: q["name"] for q in mgr.settings["queues"]}
        for d in items:
            total_speed += d.speed
            if d.status == "Downloading":
                active += 1
            elif d.status == "Queued":
                waiting += 1
            if not matches(d, view):
                continue
            current.append(d.id)
            name = d.filename or d.url
            if d.mirrors:
                name = f"{name}   (+{len(d.mirrors)} mirror{'s' * (len(d.mirrors) > 1)})"
            running = d.status == "Downloading"
            vals = (human_size(d.size) if d.size and d.size > 0 else "",
                    status_text(d),
                    human_time(d.eta()) if running else "",
                    (human_size(d.speed) + "/s") if running else "",
                    qname.get(mgr.queue_of(d), ""),
                    added_text(d.added))
            tag = "done" if d.status == "Complete" else "err" if d.status == "Error" else \
                  "wait" if d.status in ("Queued", "Paused") else ""
            tags = tuple(t for t in (tag,) if t)
            img = badges[download_category(d)]
            if d.id in existing:
                tree.item(d.id, text=" " + name, values=vals, tags=tags, image=img)
            else:
                tree.insert("", "end", iid=d.id, text=" " + name, values=vals, tags=tags,
                            image=img)
        for iid in existing - set(current):
            tree.delete(iid)
        for i, iid in enumerate(current):                      # keep list order + stripes
            if tree.index(iid) != i:
                tree.move(iid, "", i)
            t = [x for x in tree.item(iid, "tags") if x != "odd"]
            if i % 2:
                t.append("odd")
            tree.item(iid, tags=tuple(t))
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
            info_var.set("Select a download to see its connections. "
                         "Blue = downloaded, orange = a connection working.")
        draw_segments(d)
        speed_var.set(f"{human_size(total_speed)}/s")
        ticks[0] += 1
        if ticks[0] % 2 == 0:
            graph.push(total_speed)
        lim = mgr.settings.get("speed_limit_kb", 0)
        status_l.set((mgr.notice + "   •   " if mgr.notice else "")
                     + f"{len(items)} downloads · {active} active · {waiting} queued"
                     + (f"   •   limit {lim} KB/s" if lim else ""))
        status_r.set(("Browser: connected" if listening else "Browser: port busy")
                     + f"   •   Saving to {mgr.settings['download_dir']}")
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

    side.bind("<<TreeviewSelect>>", lambda e: (side.selection() == ("queues",)
                                               and side.selection_set("cat:All")))
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(800, after_update_note)
    root.after(8000, daily_check)
    root.after(1000, poll_updates)
    root.after(200, refresh)
    root.after(1500, check_clipboard)
    if initial_url:
        root.after(400, lambda: add_dialog(initial_url))
    root.mainloop()
