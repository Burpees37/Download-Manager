"""Opens the real window for a few seconds, clicks through it and fails on any
error inside it. Skipped where Tk or a display isn't available."""
import os

import pytest

tk = pytest.importorskip("tkinter")


def _can_open():
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


@pytest.mark.skipif(not _can_open(), reason="no display")
def test_window_runs(tmp_path, monkeypatch, server):
    from jmdm_app import gui, manager, schedule
    monkeypatch.setattr(manager, "app_dir", lambda: str(tmp_path))
    errors = []
    monkeypatch.setattr(tk.Tk, "report_callback_exception",
                        lambda self, *exc: errors.append(exc))
    real_mgr = manager.Manager.__init__

    def mgr_init(self, *a, **k):
        real_mgr(self, *a, **k)
        self.settings.update(download_dir=str(tmp_path / "dl"), clipboard_watch=False,
                             auto_update_ytdlp=False, check_app_updates=False,
                             listen_port=0, theme="light")
        q = schedule.new_queue("Tonight")
        q.update(start_enabled=True)
        self.settings["queues"].append(q)
    monkeypatch.setattr(manager.Manager, "__init__", mgr_init)
    monkeypatch.setattr(gui, "Manager", manager.Manager)
    from jmdm_app import gui_fx
    monkeypatch.setattr(gui_fx, "DURATION", 0.2)       # the light/dark animation
    real_loop = tk.Tk.mainloop
    seen = {}

    def loop(self, n=0):
        U = gui.UI
        m = U["mgr"]
        tonight = m.settings["queues"][1]["id"]
        a = m.add(server.url("/file/a.zip?size=300000"))
        seen["a_url"] = a.url
        m.add(server.url("/file/b.exe?size=300000"), queue=tonight)
        bad = m.add(server.url("/file/c.pdf?size=1000"), start=False)
        bad.status, bad.error = "Error", "[youtube] x: Sign in to confirm you're not a bot"

        def check_rows():
            seen["rows"] = len(U["list"].rows)
            U["list"].select(a.id)

        def views():
            for v in ("downloading", "queued", "completed", "failed", "cat:Programs",
                      "q:" + tonight, "all"):
                U["nav"].select(v)
                U["refresh_now"]()
            seen["failed_title"] = None
            U["list"].select(bad.id)
            seen["details"] = U["details"].info and U["details"].info.get("hint")

        def menus():
            from jmdm_app.gui_popups import Dropdown
            menu = U["menu"]
            U["list"].select(a.id)
            menu.tk_popup(self.winfo_rootx() + 300, self.winfo_rooty() + 200)
            assert menu.win is not None
            idx = [i for i, it in enumerate(menu.items) if it.get("label") == "Copy link"][0]
            menu._activate(idx)                                # runs it and closes
            seen["menu_closed"] = menu.win is None
            self.after(50, lambda: seen.setdefault("copied", self.clipboard_get()))
            U["more_menu"]()
            seen["more_open"] = True
            U["sort_menu"]()                                   # opening another closes it
            def walk(w):
                for ch in w.winfo_children():
                    yield ch
                    yield from walk(ch)
            dd = [w for w in walk(self) if isinstance(w, Dropdown) and not w.instate(["disabled"])]
            if dd:
                dd[0]._open()
                dd[0].menu.close_all()
                seen["dropdown"] = True

        def box():
            def press_enter():
                tops = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]
                tops[-1].event_generate("<Return>")
            self.after(300, press_enter)
            seen["box"] = U["messagebox"].askyesno("Test", "Press Enter?", yes="Sure")

        steps = [check_rows, views,
                 lambda: U["buttons"]["Scheduler"].command(),
                 lambda: U["add_dialog"]("https://drive.google.com/file/d/"
                                         "1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view"),
                 lambda: U["settings_dialog"](),
                 lambda: U["toggle_theme"](),                 # dark, with dialogs open
                 lambda: seen.setdefault("dark", U["theme"].dark),
                 lambda: U["toggle_theme"](),                 # back to light
                 lambda: U["search"].set("b.exe"),
                 lambda: seen.setdefault("search_rows", len(U["list"].rows)),
                 lambda: U["search"].set(""),
                 menus,
                 box,
                 lambda: m.events.put(("queue_done", tonight)),
                 lambda: [w.destroy() for w in self.winfo_children()
                          if isinstance(w, tk.Toplevel)],
                 lambda: self.geometry("900x560")]            # narrowest window
        for i, f in enumerate(steps):
            self.after(900 + 450 * i, f)
        self.after(900 + 450 * len(steps) + 600, lambda: (m.shutdown(), self.destroy()))
        real_loop(self, n)
    monkeypatch.setattr(tk.Tk, "mainloop", loop)
    gui.run_gui()
    assert not errors, errors
    assert seen["rows"] == 3 and seen["dark"] is True and seen["search_rows"] == 1
    assert "signed-in browser" in seen["details"]
    assert seen["menu_closed"] and seen["copied"] == seen["a_url"]
    assert seen.get("dropdown") and seen["box"] is True


@pytest.mark.skipif(not _can_open(), reason="no display")
def test_add_dialog(tmp_path, monkeypatch, server):
    """The Add download window: checks a pasted link in the background, remembers
    the folder per type, and its three answers do what they say."""
    from jmdm_app import gui, gui_add, manager, schedule
    monkeypatch.setattr(manager, "app_dir", lambda: str(tmp_path))
    errors = []
    monkeypatch.setattr(tk.Tk, "report_callback_exception",
                        lambda self, *exc: errors.append(exc))
    real_mgr = manager.Manager.__init__
    games, shows = str(tmp_path / "Games"), str(tmp_path / "Shows")

    def mgr_init(self, *a, **k):
        real_mgr(self, *a, **k)
        self.settings.update(download_dir=str(tmp_path / "dl"), clipboard_watch=False,
                             auto_update_ytdlp=False, check_app_updates=False,
                             listen_port=0, theme="light", last_dirs={"Videos": shows})
        self.settings["queues"].append(schedule.new_queue("Tonight"))
    monkeypatch.setattr(manager.Manager, "__init__", mgr_init)
    monkeypatch.setattr(gui, "Manager", manager.Manager)
    asked = []

    def fake_video(url, ref="", cookie="", ua="", video=False):
        asked.append(url)
        return {"kind": "video", "title": "Episode 2", "duration": 1420, "height": 1080,
                "count": 12}
    real_loop = tk.Tk.mainloop
    seen = {}

    def loop(self, n=0):
        U = gui.UI
        m = U["mgr"]
        tonight = m.settings["queues"][1]["id"]
        dl = {}

        def file_link():
            dl["d"] = U["add_dialog"](server.url("/file/game.rar?size=300000"))

        def file_checked():
            d = dl["d"]
            seen["file_known"] = (d.known.get("kind"), d.known.get("size"), d.name_v.get())
            seen["file_badge"] = d.header.state["badge"][0]
            d.dir_v.set(games)
            d.dir_touched = True
            d.submit(start=False)                                  # Add paused

        def again():
            dl["d"] = U["add_dialog"](server.url("/file/other.rar?size=1000"))
            seen["remembered_rar"] = dl["d"].dir_v.get()
            dl["d"].cancel()

        def video_link():
            gui_add.AddDialog.probe = staticmethod(fake_video)
            dl["v"] = U["add_dialog"]("https://www.youtube.com/watch?v=abc&list=PLx")
            seen["video_dir"] = dl["v"].dir_v.get()

        def video_checked():
            v = dl["v"]
            seen["video_title"] = v.header.state["title"]
            seen["pl_label"] = v.pl_chk.cget("text")
            v.q_v.set("720p")
            v.queue_menu()                                        # Add to queue ▾ opens
            v.submit(start=True, qid=tonight)

        def browser_job():
            job = {"size": 5_000_000}
            d = U["add_dialog"]("https://host.example/dl/7", "setup.exe", "https://host.example/",
                                "sid=1", "", False, from_browser=True, job=job)
            seen["job_not_probed"] = not d.checking and d.known.get("size") == 5_000_000
            seen["job_season"] = d.season_ok
            d.cancel()

        def done():
            ds = {os.path.basename(x.filename or x.url): x for x in m.downloads}
            seen["downloads"] = sorted((x.status, x.queue == tonight, x.folder)
                                       for x in m.downloads)
            seen["ds"] = ds

        steps = [file_link, None, None, file_checked, again, video_link, None, video_checked,
                 browser_job, done]
        for i, f in enumerate(steps):
            if f:
                self.after(900 + 500 * i, f)
        self.after(900 + 500 * len(steps) + 300, lambda: (m.shutdown(), self.destroy()))
        real_loop(self, n)
    monkeypatch.setattr(tk.Tk, "mainloop", loop)
    real_probe = gui_add.AddDialog.probe
    try:
        gui.run_gui()
    finally:
        gui_add.AddDialog.probe = real_probe
    assert not errors, errors
    assert seen["file_known"] == ("file", 300000, "game.rar")
    assert seen["file_badge"] == "Resumable"
    assert seen["remembered_rar"] == games                    # per type: archives
    assert seen["video_dir"] == shows                         # videos have their own
    assert seen["video_title"] == "Episode 2" and "12 videos" in seen["pl_label"]
    assert asked == ["https://www.youtube.com/watch?v=abc&list=PLx"]
    assert seen["job_not_probed"] and seen["job_season"] is False
    st = seen["downloads"]
    assert ("Paused", False, games) in st
    assert any(s == "Queued" and t and f == shows for s, t, f in st), st
