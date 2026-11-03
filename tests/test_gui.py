"""Opens the real window for a few seconds and fails on any error inside it.
Skipped where Tk or a display isn't available."""

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
    from jmdm_app import gui, gui_widgets, manager, schedule
    monkeypatch.setattr(manager, "app_dir", lambda: str(tmp_path))
    errors, buttons = [], {}
    monkeypatch.setattr(tk.Tk, "report_callback_exception",
                        lambda self, *exc: errors.append(exc))
    orig_btn = gui_widgets.ToolButton.__init__

    def btn(self, master, text, *a, **k):
        orig_btn(self, master, text, *a, **k)
        buttons[text] = self
    monkeypatch.setattr(gui_widgets.ToolButton, "__init__", btn)
    real_mgr = manager.Manager.__init__
    made = []

    def mgr_init(self, *a, **k):
        real_mgr(self, *a, **k)
        self.settings.update(download_dir=str(tmp_path / "dl"), clipboard_watch=False,
                             auto_update_ytdlp=False, check_app_updates=False,
                             listen_port=0)
        q = schedule.new_queue("Tonight")
        q.update(start_enabled=True)
        self.settings["queues"].append(q)
        made.append(self)
    monkeypatch.setattr(manager.Manager, "__init__", mgr_init)
    monkeypatch.setattr(gui, "Manager", manager.Manager)
    real_loop = tk.Tk.mainloop

    def loop(self, n=0):
        m = made[0]
        m.add(server.url("/file/a.zip?size=300000"))
        m.add(server.url("/file/b.exe?size=300000"), queue=m.settings["queues"][1]["id"])
        steps = [lambda: buttons["Scheduler"].command(),
                 lambda: buttons["Add URL"].command(),
                 lambda: buttons["Options"].command(),
                 lambda: m.events.put(("queue_done", m.settings["queues"][1]["id"])),
                 lambda: [w.destroy() for w in self.winfo_children()
                          if isinstance(w, tk.Toplevel)],
                 lambda: self.event_generate("<<Quit>>")]
        for i, f in enumerate(steps):
            self.after(800 + 600 * i, f)
        self.after(800 + 600 * len(steps), lambda: (m.shutdown(), self.destroy()))
        real_loop(self, n)
    monkeypatch.setattr(tk.Tk, "mainloop", loop)
    gui.run_gui()
    assert not errors, errors
