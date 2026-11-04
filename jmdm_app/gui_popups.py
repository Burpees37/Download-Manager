"""Pop-ups drawn in the app's own style, light and dark: menus (right-click,
⋯, sort, queues — with icons, separators, ticks, disabled items and
sub-menus) and message boxes / a text prompt. They replace Windows' own menus
and message boxes, which ignore the app's colours.

PopupMenu has the parts of tk.Menu the app uses (add_command, add_separator,
add_radiobutton, add_cascade, delete, entryconfigure, tk_popup), and Boxes has
the parts of tkinter.messagebox / simpledialog (askyesno, askyesnocancel,
showinfo, showerror, showwarning, askstring), so they drop in."""
import tkinter as tk
from tkinter import ttk

from .gui_widgets import draw_icon, fit, rounded_rect, wrap


class PopupMenu:
    def __init__(self, root, theme):
        self.root, self.th = root, theme
        self.items = []
        self.win = self.canvas = None
        self.parent = None              # the menu this one opened from
        self.child = None               # an open sub-menu
        self.hover = -1
        self._hits = []
        self._sub_job = None

    # ---- the tk.Menu parts the app uses ---------------------------------
    def add_command(self, label, command=None, state="normal", accelerator="", icon=None):
        self.items.append({"kind": "command", "label": label, "command": command,
                           "state": state, "accel": accelerator, "icon": icon})

    def add_separator(self):
        self.items.append({"kind": "sep"})

    def add_radiobutton(self, label, variable, value, command=None):
        self.items.append({"kind": "radio", "label": label, "var": variable, "value": value,
                           "command": command, "state": "normal"})

    def add_cascade(self, label, menu, icon=None):
        self.items.append({"kind": "cascade", "label": label, "menu": menu,
                           "state": "normal", "icon": icon})

    def delete(self, _first=0, _last=None):
        self.items = []

    def entryconfigure(self, label, **kw):
        for it in self.items:
            if it.get("label") == label:
                it.update(kw)

    def tk_popup(self, x, y):
        self.close_all()
        self._open(x, y)
        self.win.grab_set()
        self.canvas.focus_set()

    # ---- showing --------------------------------------------------------
    def _size(self):
        S, f = self.th.S, self.th.f
        w = 180 * S
        for it in self.items:
            if it["kind"] == "sep":
                continue
            w = max(w, f["body"].measure(it["label"]) + 76 * S
                    + (f["small"].measure(it.get("accel", "")) + 16 * S if it.get("accel") else 0))
        h = 12 * S + sum((9 if it["kind"] == "sep" else 32) * S for it in self.items)
        return int(min(w, 420 * S)), int(h)

    def _open(self, x, y, parent=None):
        self.parent = parent
        self.hover = -1
        w, h = self._size()
        master = parent.win if parent else self.root
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.win, width=w, height=h, highlightthickness=0, bd=0,
                                takefocus=1)
        self.canvas.pack()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        if x + w > sw:
            x = (parent.win.winfo_rootx() - w + 4) if parent else sw - w - 4
        if y + h > sh:
            y = max(0, sh - h - 4)
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self._draw()
        c = self.canvas
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", lambda e: self._set_hover(-1) if not self.child else None)
        c.bind("<ButtonRelease-1>", self._release)
        for key, fn in (("<Up>", lambda e: self._step(-1)), ("<Down>", lambda e: self._step(1)),
                        ("<Return>", lambda e: self._activate(self.hover)),
                        ("<space>", lambda e: self._activate(self.hover)),
                        ("<Right>", lambda e: self._open_sub(self.hover, keyboard=True)),
                        ("<Left>", lambda e: self._back()),
                        ("<Escape>", lambda e: self._back())):
            c.bind(key, fn)
        self.win.bind("<ButtonPress>", self._press_anywhere)
        self.win.deiconify()
        self.win.lift()

    def _draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        c = self.canvas
        w, h = int(c["width"]), int(c["height"])
        c.configure(bg=t["border"])
        c.delete("all")
        c.create_rectangle(1, 1, w - 2, h - 2, fill=t["surface"], outline="")
        self._hits = []
        y = 6 * S
        for i, it in enumerate(self.items):
            if it["kind"] == "sep":
                c.create_line(10 * S, y + 4.5 * S, w - 10 * S, y + 4.5 * S, fill=t["border"])
                y += 9 * S
                continue
            rh = 32 * S
            off = it.get("state") == "disabled"
            if i == self.hover and not off:
                rounded_rect(c, 5 * S, y + 1 * S, w - 5 * S, y + rh - 1 * S, 6 * S,
                             fill=t["btn_hover"], outline="")
            ink = t["muted"] if off else t["text"]
            mark = it.get("icon")
            if it["kind"] == "radio":
                mark = "check_plain" if it["var"].get() == it["value"] else None
            if mark:
                draw_icon(c, mark, 14 * S, y + (rh - 16 * S) / 2, 16 * S,
                          t["accent"] if it["kind"] == "radio" else (t["muted"] if off else
                                                                       t["text"]))
            right = w - 14 * S
            if it["kind"] == "cascade":
                draw_icon(c, "chevron_right", right - 12 * S, y + (rh - 12 * S) / 2, 12 * S,
                          t["muted"])
                right -= 18 * S
            if it.get("accel"):
                c.create_text(right, y + rh / 2, text=it["accel"], anchor="e", font=f["small"],
                              fill=t["muted"])
                right -= f["small"].measure(it["accel"]) + 12 * S
            c.create_text(42 * S, y + rh / 2, text=fit(f["body"], it["label"], right - 42 * S),
                          anchor="w", font=f["body"], fill=ink)
            self._hits.append((y, y + rh, i))
            y += rh

    # ---- interaction ----------------------------------------------------
    def _index_at(self, y):
        for y0, y1, i in self._hits:
            if y0 <= y < y1:
                return i
        return -1

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self._draw()

    def _motion(self, e):
        i = self._index_at(e.y)
        self._set_hover(i)
        if self._sub_job:
            self.canvas.after_cancel(self._sub_job)
            self._sub_job = None
        if i >= 0 and self.items[i]["kind"] == "cascade":
            self._sub_job = self.canvas.after(200, lambda: self._open_sub(i))
        elif self.child:
            self._sub_job = self.canvas.after(250, self._close_child)

    def _release(self, e):
        if 0 <= e.x < int(self.canvas["width"]) and 0 <= e.y < int(self.canvas["height"]):
            self._activate(self._index_at(e.y))

    def _step(self, d):
        idx = [i for _, _, i in self._hits if self.items[i].get("state") != "disabled"]
        if not idx:
            return "break"
        cur = idx.index(self.hover) if self.hover in idx else (-1 if d > 0 else 0)
        self._set_hover(idx[(cur + d) % len(idx)])
        return "break"

    def _activate(self, i):
        if i < 0:
            return
        it = self.items[i]
        if it.get("state") == "disabled":
            return
        if it["kind"] == "cascade":
            self._open_sub(i, keyboard=True)
            return
        if it["kind"] == "radio":
            it["var"].set(it["value"])
        fn = it.get("command")
        self.close_all()
        if fn:
            self.root.after_idle(fn)

    def _open_sub(self, i, keyboard=False):
        if i < 0 or self.items[i]["kind"] != "cascade":
            return
        sub = self.items[i]["menu"]
        if self.child is sub and sub.win:
            if keyboard:
                sub.canvas.focus_set()
                sub._step(1)
            return
        self._close_child()
        y0 = next(y for y, _, j in self._hits if j == i)
        x = self.win.winfo_rootx() + int(self.canvas["width"]) - 4
        y = self.win.winfo_rooty() + y0 - 6 * self.th.S
        sub._open(x, y, parent=self)
        self.child = sub
        if keyboard:
            sub.canvas.focus_set()
            sub._step(1)

    def _close_child(self):
        self._sub_job = None
        if self.child:
            self.child._close_own()
            self.child = None
            self.canvas.focus_set()

    def _close_own(self):
        if self.child:
            self.child._close_own()
            self.child = None
        if self.win:
            try:
                self.win.grab_release()
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = self.canvas = None

    def _top(self):
        m = self
        while m.parent:
            m = m.parent
        return m

    def close_all(self):
        self._top()._close_own()

    def _back(self):
        if self.parent:
            p = self.parent
            p._close_child()
        else:
            self.close_all()
        return "break"

    def _inside_any(self, x, y):
        m = self._top()
        while m and m.win:
            wx, wy = m.win.winfo_rootx(), m.win.winfo_rooty()
            if wx <= x < wx + m.win.winfo_width() and wy <= y < wy + m.win.winfo_height():
                return True
            m = m.child
        return False

    def _press_anywhere(self, e):
        """A click outside every open menu closes them (the menu holds the grab)."""
        if not self._inside_any(e.x_root, e.y_root):
            self.close_all()


class Boxes:
    """Message boxes and a text prompt in the app's style."""

    ICON = {"info": ("info", "accent"), "question": ("question", "accent"),
            "warning": ("warn", "warn"), "error": ("error", "bad")}

    def __init__(self, root, theme, place):
        self.root, self.th, self.place = root, theme, place      # place(win): center + show

    def _box(self, title, message, buttons, kind="info", parent=None, entry=None,
             cancel=None, detail=""):
        """buttons: [(label, value, primary)]. Returns the chosen value; Esc or
        closing the window gives `cancel`."""
        S, f, t = self.th.S, self.th.f, self.th.t
        win = tk.Toplevel(parent or self.root)
        win.withdraw()
        win.title(title)
        win.transient(parent or self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=(int(20 * S), int(18 * S)))
        frm.pack(fill="both", expand=True)
        result = {"value": cancel}
        bg = ttk.Style(win).lookup("TFrame", "background") or t["surface"]
        top = ttk.Frame(frm)
        top.pack(fill="x")
        icon, ink = self.ICON.get(kind, self.ICON["info"])
        ic = tk.Canvas(top, width=int(32 * S), height=int(32 * S), highlightthickness=0, bd=0,
                       bg=bg)
        draw_icon(ic, icon, 0, 0, 32 * S, t[ink], bg)
        ic.pack(side="left", anchor="n", padx=(0, int(14 * S)))
        body = ttk.Frame(top)
        body.pack(side="left", fill="both", expand=True)
        width = int(380 * S)
        ttk.Label(body, text=title, font=f["big"]).pack(anchor="w")
        lines = []
        for para in str(message).split("\n"):
            lines.extend(wrap(f["body"], para, width, 12) if para.strip() else [""])
        ttk.Label(body, text="\n".join(lines), font=f["body"], justify="left").pack(
            anchor="w", pady=(int(6 * S), 0))
        if detail:
            ttk.Label(body, text=detail, style="Hint.TLabel", justify="left",
                      wraplength=width).pack(anchor="w", pady=(int(8 * S), 0))
        var = None
        if entry is not None:
            var = tk.StringVar(value=entry)
            e = ttk.Entry(body, textvariable=var, width=52)
            e.pack(fill="x", pady=(int(10 * S), 0))
            e.select_range(0, "end")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(int(18 * S), 0))
        default = None

        def choose(v):
            result["value"] = (var.get() if var is not None and v is True else v)
            win.grab_release()
            win.destroy()

        for label, value, primary in reversed(buttons):
            b = ttk.Button(row, text=label, command=lambda v=value: choose(v),
                           style="Accent.TButton" if primary and self.th.sv else "TButton")
            b.pack(side="right", padx=(int(8 * S), 0))
            if primary:
                default = (b, value)
        win.bind("<Escape>", lambda e: choose(cancel))
        win.protocol("WM_DELETE_WINDOW", lambda: choose(cancel))
        if default:
            win.bind("<Return>", lambda e: choose(default[1]))
        self.place(win)
        (e if entry is not None else (default[0] if default else win)).focus_set()
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(win)
        return result["value"]

    # ---- tkinter.messagebox-style calls ---------------------------------
    def showinfo(self, title, message, parent=None, **kw):
        self._box(title, message, [("OK", True, True)], "info", parent, cancel=True)

    def showwarning(self, title, message, parent=None, **kw):
        self._box(title, message, [("OK", True, True)], "warning", parent, cancel=True)

    def showerror(self, title, message, parent=None, **kw):
        self._box(title, message, [("OK", True, True)], "error", parent, cancel=True)

    def askyesno(self, title, message, parent=None, yes="Yes", no="No", kind="question",
                 **kw):
        return bool(self._box(title, message, [(no, False, False), (yes, True, True)], kind,
                              parent, cancel=False))

    def askyesnocancel(self, title, message, parent=None, yes="Yes", no="No",
                       cancel="Cancel", kind="question", **kw):
        return self._box(title, message, [(cancel, None, False), (no, False, False),
                                          (yes, True, True)], kind, parent, cancel=None)

    def askstring(self, title, prompt, initialvalue="", parent=None, ok="OK", **kw):
        v = self._box(title, prompt, [("Cancel", None, False), (ok, True, True)], "question",
                      parent, entry=initialvalue or "", cancel=None)
        return v if isinstance(v, str) else None


class Dropdown(ttk.Menubutton):
    """A pick-one-from-a-list box whose list is the app's own menu (the plain
    Windows list is cramped and ignores dark mode). Takes the arguments the
    app gave ttk.Combobox(state="readonly")."""

    def __init__(self, master, theme, textvariable, values, width=12, state=None, **kw):
        super().__init__(master, textvariable=textvariable, width=width, direction="below")
        self.th, self.var, self.values = theme, textvariable, list(values)
        self.menu = None
        for ev in ("<ButtonPress-1>", "<KeyPress-space>", "<Return>", "<Down>"):
            self.bind(ev, self._open)

    def _open(self, _e=None):
        if self.instate(["disabled"]):
            return "break"
        self.focus_set()
        self.menu = PopupMenu(self.winfo_toplevel(), self.th)
        for v in self.values:
            self.menu.add_radiobutton(label=v, variable=self.var, value=v)
        self.menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2)
        cur = self.var.get()
        if cur in self.values:                       # start on the current choice
            self.menu._set_hover(self.values.index(cur))
        return "break"
