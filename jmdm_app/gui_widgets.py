"""Drawn pieces of the main window (mockup D): command bar buttons, the speed
chip, the sidebar, the download list with progress bars in each row, the
details card and the status bar. Everything is drawn on Tk canvases with
vector icons, so there are no image files and it stays sharp at any Windows
display scaling. Colours come from gui_theme.Theme and follow light/dark."""
import tkinter as tk

# icon = drawing steps on a 24x24 grid: (kind, coords, filled)
ICONS = {
    "add": [("line", (12, 5, 12, 19), False), ("line", (5, 12, 19, 12), False)],
    "resume": [("poly", (8, 5, 19, 12, 8, 19), True)],
    "pause": [("rect", (7, 5, 10, 19), True), ("rect", (14, 5, 17, 19), True)],
    "stop": [("rect", (7, 7, 17, 17), True)],
    "stop_all": [("rect", (4, 7, 11, 17), True), ("rect", (13, 7, 20, 17), True)],
    "delete": [("line", (4, 7, 20, 7), False), ("line", (9, 7, 9, 4, 15, 4, 15, 7), False),
               ("line", (6, 7, 7, 20, 17, 20, 18, 7), False)],
    "video": [("rect", (3, 6, 16, 18), False), ("poly", (16, 10, 21, 7, 21, 17, 16, 14), False)],
    "clock": [("oval", (3, 3, 21, 21), False), ("line", (12, 7, 12, 12, 15, 14), False)],
    "queue": [("line", (4, 6, 16, 6), False), ("line", (4, 12, 16, 12), False),
              ("line", (4, 18, 12, 18), False), ("poly", (16, 14, 21, 17, 16, 20), True)],
    "options": [("line", (4, 6, 20, 6), False), ("line", (4, 12, 20, 12), False),
                ("line", (4, 18, 20, 18), False), ("dot", (14, 6), True),
                ("dot", (8, 12), True), ("dot", (16, 18), True)],
    "grid": [("rect", (4, 4, 11, 11), False), ("rect", (13, 4, 20, 11), False),
             ("rect", (4, 13, 11, 20), False), ("rect", (13, 13, 20, 20), False)],
    "down": [("line", (12, 4, 12, 16), False), ("line", (7, 11, 12, 16, 17, 11), False),
             ("line", (5, 20, 19, 20), False)],
    "check": [("line", (5, 12, 10, 17, 19, 7), False)],
    "warn": [("poly", (12, 3, 22, 20, 2, 20), False), ("line", (12, 9, 12, 14), False),
             ("dot0", (12, 17), True)],
    "box": [("poly", (4, 7, 12, 3, 20, 7, 20, 17, 12, 21, 4, 17), False),
            ("line", (4, 7, 12, 11, 20, 7), False), ("line", (12, 11, 12, 21), False)],
    "app": [("rect", (4, 5, 20, 16), False), ("line", (8, 20, 16, 20), False),
            ("line", (12, 16, 12, 20), False)],
    "film": [("rect", (3, 5, 21, 19), False), ("line", (7, 5, 7, 19), False),
             ("line", (17, 5, 17, 19), False)],
    "music": [("line", (9, 17, 9, 5, 20, 3, 20, 15), False), ("oval", (3, 14, 9, 20), False),
              ("oval", (14, 12, 20, 18), False)],
    "doc": [("poly", (6, 3, 15, 3, 19, 7, 19, 21, 6, 21), False), ("line", (9, 12, 16, 12), False),
            ("line", (9, 16, 14, 16), False)],
    "file": [("poly", (6, 3, 15, 3, 19, 7, 19, 21, 6, 21), False)],
    "moon": [("crescent", (4, 4, 20, 20), True)],
    "sun": [("oval", (8, 8, 16, 16), False), ("line", (12, 2, 12, 4), False),
            ("line", (12, 20, 12, 22), False), ("line", (2, 12, 4, 12), False),
            ("line", (20, 12, 22, 12), False), ("line", (5, 5, 6.5, 6.5), False),
            ("line", (17.5, 17.5, 19, 19), False), ("line", (5, 19, 6.5, 17.5), False),
            ("line", (17.5, 6.5, 19, 5), False)],
    "search": [("oval", (4, 4, 17, 17), False), ("line", (15, 15, 20, 20), False)],
    "folder": [("line", (3, 7, 3, 19, 21, 19, 21, 9, 11, 9, 9, 6, 3, 6, 3, 7), False)],
    "copy": [("rect", (8, 8, 20, 20), False), ("line", (16, 4, 4, 4, 4, 16), False)],
    "retry": [("arc", (4, 4, 20, 20, 40, 290), False), ("line", (17, 3, 18, 8, 13, 9), False)],
    "more": [("dot", (5, 12), True), ("dot", (12, 12), True), ("dot", (19, 12), True)],
    "chevron": [("line", (6, 9, 12, 15, 18, 9), False)],
    "gear": [("oval", (8, 8, 16, 16), False), ("oval", (3, 3, 21, 21), False)],
    "hourglass": [("line", (7, 3, 17, 3), False), ("line", (7, 21, 17, 21), False),
                  ("line", (8, 3, 8, 7, 16, 17, 16, 21), False),
                  ("line", (16, 3, 16, 7, 8, 17, 8, 21), False)],
}
CATEGORY_ICON = {"Compressed": "box", "Programs": "app", "Videos": "film", "Music": "music",
                 "Documents": "doc", "Other": "file"}


def rounded_rect(c, x0, y0, x1, y1, r, **kw):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def draw_icon(c, name, x, y, size, color, bg="", width=None):
    k = size / 24.0
    w = width or max(1.4, 1.9 * k)
    for kind, pts, filled in ICONS[name]:
        p = [x + v * k if i % 2 == 0 else y + v * k for i, v in enumerate(pts)]
        if kind == "line":
            c.create_line(*p, fill=color, width=w, capstyle="round", joinstyle="round")
        elif kind == "poly":
            c.create_polygon(*p, fill=color if filled else "", outline=color, width=w,
                             joinstyle="round")
        elif kind == "rect":
            c.create_rectangle(*p, fill=color if filled else "", outline=color, width=w)
        elif kind == "oval":
            c.create_oval(*p, outline=color, width=w)
        elif kind == "arc":
            c.create_arc(p[0], p[1], p[2], p[3], start=pts[4], extent=pts[5],
                         style="arc", outline=color, width=w)
        elif kind == "crescent":
            c.create_oval(*p, fill=color, outline=color, width=w * 0.6)
            off = 5 * k
            c.create_oval(p[0] + off, p[1] - off * 0.8, p[2] + off, p[3] - off * 0.8,
                          fill=bg or "", outline=bg or "")
        elif kind in ("dot", "dot0"):
            r = (2.2 if kind == "dot" else 1.3) * k
            c.create_oval(p[0] - r, p[1] - r, p[0] + r, p[1] + r,
                          fill=color if kind == "dot0" or not bg else bg, outline=color, width=w)


_fit_cache = {}


def fit(font, text, width):
    """Text cut with … to fit `width` pixels."""
    text = text or ""
    key = (str(font), text, int(width))
    if key in _fit_cache:
        return _fit_cache[key]
    if width <= 0:
        out = ""
    elif font.measure(text) <= width:
        out = text
    else:
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + "…") <= width:
                lo = mid
            else:
                hi = mid - 1
        out = text[:lo] + "…"
    if len(_fit_cache) > 5000:
        _fit_cache.clear()
    _fit_cache[key] = out
    return out


def wrap(font, text, width, max_lines=3):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.measure(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit(font, lines[-1] + " …", width)
    return lines


class Tooltip:
    def __init__(self, widget, text, theme):
        self.w, self.text, self.theme, self.tip, self.job = widget, text, theme, None, None
        widget.bind("<Enter>", self._later, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _later(self, _e):
        self.job = self.w.after(600, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        t = self.theme.t
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=self.text, bg=t["text"], fg=t["surface"], font=self.theme.f["small"],
                 padx=8, pady=4).pack()
        x = self.w.winfo_rootx()
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class CommandButton(tk.Canvas):
    """Flat command-bar button: icon + label; `primary` = filled accent."""

    def __init__(self, master, theme, text, icon, command, ink="text", primary=False,
                 tip="", trailing=False):
        self.th, self.text, self.icon, self.command = theme, text, icon, command
        self.ink, self.primary, self.compact, self.trailing = ink, primary, False, trailing
        self.hover = self.down = False
        super().__init__(master, highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<KeyPress-space>", lambda e: self.command())
        self.bind("<KeyPress-Return>", lambda e: self.command())
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())
        Tooltip(self, tip or text, theme)
        theme.on_change(self.draw)
        self.draw()

    def natural_width(self, compact=False):
        S = self.th.S
        if compact and not self.primary:
            return int(36 * S)
        return int((12 + 16 + 7 + 12) * S) + self.th.f["bold" if self.primary else "body"].measure(
            self.text)

    def set_compact(self, compact):
        if compact != self.compact:
            self.compact = compact
            self.draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def _release(self, e):
        fire = self.down and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._set(down=False)
        if fire:
            self.command()

    def draw(self):
        t, S = self.th.t, self.th.S
        h = int(34 * S)
        w = self.natural_width(self.compact)
        self.configure(width=w, height=h, bg=self.master["bg"])
        self.delete("all")
        under = self.master["bg"]
        if self.primary:
            fill = t["accent_ink"] if self.down else t["accent"]
            rounded_rect(self, 1, 1, w - 1, h - 1, 8 * S, fill=fill, outline="")
            ink, under = t["on_accent"], fill
        else:
            if self.down or self.hover:
                under = t["btn_down"] if self.down else t["btn_hover"]
                rounded_rect(self, 1, 1, w - 1, h - 1, 8 * S, fill=under, outline="")
            ink = t.get(self.ink, self.ink)
        if self.focus_get() is self:
            rounded_rect(self, 1, 1, w - 1, h - 1, 8 * S, fill="", outline=t["accent"], width=2)
        ic = 16 * S
        if self.compact and not self.primary:
            draw_icon(self, self.icon, (w - ic) / 2, (h - ic) / 2, ic, ink, under)
            return
        font = self.th.f["bold" if self.primary else "body"]
        fg = t["on_accent"] if self.primary else t["text"]
        if self.trailing:                              # "Newest first ⌄"
            self.create_text(12 * S, h / 2, text=self.text, anchor="w", font=font, fill=fg)
            draw_icon(self, self.icon, w - 12 * S - 12 * S, (h - 12 * S) / 2, 12 * S, t["muted"],
                      under, width=2.2 * S)
            return
        draw_icon(self, self.icon, 12 * S, (h - ic) / 2, ic, ink, under)
        self.create_text((12 + 16 + 7) * S, h / 2, text=self.text, anchor="w", font=font, fill=fg)


class IconButton(CommandButton):
    def __init__(self, master, theme, icon, command, tip):
        super().__init__(master, theme, tip, icon, command, tip=tip)
        self.compact = True
        self.draw()

    def natural_width(self, compact=False):
        return int(36 * self.th.S)


class SpeedChip(tk.Canvas):
    """↓ 18.2 MB/s with a little graph of the last minute."""

    def __init__(self, master, theme, points=90):
        self.th, self.n = theme, points
        self.values = [0.0] * points
        self.text = "0 B/s"
        super().__init__(master, highlightthickness=0, bd=0)
        theme.on_change(self.draw)
        self.draw()

    def set(self, text, value):
        self.text = text
        self.values = (self.values + [float(value)])[-self.n:]
        self.draw()

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        tw = max(f["big"].measure("888.88 MB/s"), f["big"].measure(self.text))
        gw = int(96 * S)
        w = int((12 + 13 + 8) * S) + tw + int(10 * S) + gw + int(8 * S)
        h = int(34 * S)
        self.configure(width=w, height=h, bg=self.master["bg"])
        self.delete("all")
        rounded_rect(self, 1, 1, w - 1, h - 1, 8 * S, fill=t["surface"], outline=t["border"])
        draw_icon(self, "down", 11 * S, (h - 14 * S) / 2, 14 * S, t["accent"], width=2.4 * S)
        self.create_text((12 + 13 + 8) * S, h / 2, text=self.text, anchor="w", font=f["big"],
                         fill=t["text"])
        x0, x1 = w - gw - 8 * S, w - 8 * S
        y0, y1 = 7 * S, h - 7 * S
        top = max(self.values) or 1.0
        step = (x1 - x0) / (self.n - 1)
        pts = []
        for i, v in enumerate(self.values):
            pts += [x0 + i * step, y1 - (y1 - y0) * v / top]
        self.create_polygon(x0, y1, *pts, x1, y1, fill=t["accent_soft"], outline="")
        self.create_line(*pts, fill=t["accent"], width=max(1.5, 1.6 * S))


class NavList(tk.Canvas):
    """Sidebar: section heads and items with an icon tile, label, sub-label and count."""

    def __init__(self, master, theme, on_select, on_menu=None):
        self.th, self.on_select, self.on_menu = theme, on_select, on_menu
        self.items, self.selected, self.hover = [], "all", None
        self._hits = []
        super().__init__(master, highlightthickness=0, bd=0, takefocus=1)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._hover(None))
        self.bind("<Button-1>", self._click)
        self.bind("<Button-3>", self._menu)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(int(-e.delta / 120), "units"))
        self.bind("<Up>", lambda e: self._step(-1))
        self.bind("<Down>", lambda e: self._step(1))
        theme.on_change(self.draw)

    def set_items(self, items):
        if items != self.items:
            self.items = items
            self.draw()

    def select(self, iid):
        self.selected = iid
        self.draw()

    def _at(self, y):
        y = self.canvasy(y)
        for y0, y1, iid in self._hits:
            if y0 <= y < y1:
                return iid
        return None

    def _motion(self, e):
        self._hover(self._at(e.y))

    def _hover(self, iid):
        if iid != self.hover:
            self.hover = iid
            self.configure(cursor="hand2" if iid else "")
            self.draw()

    def _click(self, e):
        self.focus_set()
        iid = self._at(e.y)
        if iid:
            self.selected = iid
            self.draw()
            self.on_select(iid)

    def _menu(self, e):
        iid = self._at(e.y)
        if iid and self.on_menu:
            self.on_menu(iid, e)

    def _step(self, d):
        ids = [i["id"] for i in self.items if not i.get("head")]
        if self.selected in ids:
            j = max(0, min(len(ids) - 1, ids.index(self.selected) + d))
            self.selected = ids[j]
            self.draw()
            self.on_select(self.selected)

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        w = max(1, self.winfo_width())
        self.configure(bg=t["bg"])
        self.delete("all")
        self._hits = []
        y = 4 * S
        for it in self.items:
            if it.get("head"):
                y += 12 * S
                self.create_text(12 * S, y + 8 * S, text=it["label"], anchor="w",
                                 font=f["small_bold"], fill=t["muted"])
                y += 22 * S
                continue
            h = (44 if it.get("sub") else 34) * S
            sel = it["id"] == self.selected
            if sel or it["id"] == self.hover:
                rounded_rect(self, 2 * S, y, w - 4 * S, y + h, 7 * S,
                             fill=t["nav_sel"] if sel else t["nav_hover"],
                             outline=t["border"] if sel else "")
            if sel:
                rounded_rect(self, 2 * S, y + h / 2 - 8 * S, 5 * S, y + h / 2 + 8 * S, 1.5 * S,
                             fill=t["accent"], outline="")
            ts = 22 * S
            tx, ty = 12 * S, y + (h - ts) / 2
            rounded_rect(self, tx, ty, tx + ts, ty + ts, 6 * S, fill=it["tint"], outline="")
            draw_icon(self, it["icon"], tx + 4.5 * S, ty + 4.5 * S, 13 * S, it["ink"], it["tint"],
                      width=2 * S)
            cx = tx + ts + 10 * S
            count = str(it.get("count") or "")
            cw = f["small_bold"].measure(count) + 12 * S if count else 0
            room = w - cx - cw - 14 * S
            font = f["bold"] if sel else f["body"]
            if it.get("sub"):
                self.create_text(cx, y + 15 * S, text=fit(font, it["label"], room), anchor="w",
                                 font=font, fill=t["text"])
                self.create_text(cx, y + 31 * S, text=fit(f["small"], it["sub"], room), anchor="w",
                                 font=f["small"], fill=it.get("sub_ink") or t["muted"])
            else:
                self.create_text(cx, y + h / 2, text=fit(font, it["label"], room), anchor="w",
                                 font=font, fill=t["text"])
            if count:
                bx1 = w - 10 * S
                if it.get("alert"):
                    rounded_rect(self, bx1 - cw, y + h / 2 - 9 * S, bx1, y + h / 2 + 9 * S, 9 * S,
                                 fill=t["bad"], outline="")
                self.create_text(bx1 - cw / 2, y + h / 2, text=count, font=f["small_bold"],
                                 fill="#ffffff" if it.get("alert") else t["muted"])
            self._hits.append((y, y + h, it["id"]))
            y += h + 1 * S
        self.configure(scrollregion=(0, 0, w, y + 8 * S))


class DownloadList(tk.Frame):
    """The download list: header + rows with a file tile, name and sub-line,
    a progress bar or state, speed, time left, size, queue and a ⋯ button.
    Only the visible rows are drawn, so long playlists stay fast."""
    COLS = [("progress", "Progress", 190, "w"), ("speed", "Speed", 90, "e"),
            ("eta", "Time left", 76, "e"), ("size", "Size", 78, "e"),
            ("queue", "Queue", 124, "w"), ("more", "", 36, "e")]

    def __init__(self, master, theme, on_select, on_open, on_menu):
        self.th = theme
        self.on_select, self.on_open, self.on_menu = on_select, on_open, on_menu
        super().__init__(master, bd=0, highlightthickness=0)
        theme.paint(self, bg="surface")
        self.head = tk.Canvas(self, highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        body = tk.Frame(self, bd=0)
        theme.paint(body, bg="surface")
        body.pack(fill="both", expand=True)
        self.c = tk.Canvas(body, highlightthickness=0, bd=0, takefocus=1)
        from tkinter import ttk
        self.sb = ttk.Scrollbar(body, orient="vertical", command=self._scrollbar)
        self.c.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.rows, self.order = [], []
        self.selection, self.anchor, self.hover = [], None, None
        self.top = 0
        self.empty = ("", "")
        c = self.c
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", lambda e: self._set_hover(None))
        c.bind("<Button-1>", self._click)
        c.bind("<Control-Button-1>", lambda e: self._click(e, ctrl=True))
        c.bind("<Shift-Button-1>", lambda e: self._click(e, shift=True))
        c.bind("<Double-Button-1>", self._double)
        c.bind("<Button-3>", self._right)
        c.bind("<MouseWheel>", lambda e: self.scroll_px(-e.delta / 120 * 3 * self.rh))
        c.bind("<Button-4>", lambda e: self.scroll_px(-3 * self.rh))
        c.bind("<Button-5>", lambda e: self.scroll_px(3 * self.rh))
        c.bind("<Up>", lambda e: self._key_move(-1, e))
        c.bind("<Down>", lambda e: self._key_move(1, e))
        c.bind("<Prior>", lambda e: self._key_move(-10, e))
        c.bind("<Next>", lambda e: self._key_move(10, e))
        c.bind("<Home>", lambda e: self._key_move(-10 ** 6, e))
        c.bind("<End>", lambda e: self._key_move(10 ** 6, e))
        c.bind("<Return>", lambda e: self.on_open())
        c.bind("<Control-a>", lambda e: self.select_all())
        theme.on_change(self.draw)

    @property
    def rh(self):
        return int(50 * self.th.S)

    # ---- data -----------------------------------------------------------
    def set_rows(self, rows, empty=("", "")):
        self.rows = rows
        self.order = [r["id"] for r in rows]
        self.empty = empty
        keep = [i for i in self.selection if i in self.order]
        if keep != self.selection:
            self.selection = keep
            self.on_select()
        self.draw()

    def select(self, iid, see=True):
        if iid in self.order:
            self.selection, self.anchor = [iid], iid
            if see:
                self.see(iid)
            self.draw()
            self.on_select()

    def select_all(self):
        self.selection = list(self.order)
        self.draw()
        self.on_select()

    def see(self, iid):
        i = self.order.index(iid)
        h = self.c.winfo_height()
        if i * self.rh < self.top:
            self.top = i * self.rh
        elif (i + 1) * self.rh > self.top + h:
            self.top = (i + 1) * self.rh - h

    # ---- scrolling ------------------------------------------------------
    def _clamp(self):
        h = max(1, self.c.winfo_height())
        total = len(self.rows) * self.rh
        self.top = max(0, min(self.top, max(0, total - h)))

    def scroll_px(self, d):
        self.top += d
        self.draw()

    def _scrollbar(self, *a):
        total = max(1, len(self.rows) * self.rh)
        if a[0] == "moveto":
            self.top = float(a[1]) * total
        elif a[0] == "scroll":
            n = int(a[1])
            self.top += n * (self.c.winfo_height() if a[2] == "pages" else self.rh)
        self.draw()

    # ---- mouse/keys -----------------------------------------------------
    def _row_at(self, y):
        i = int((y + self.top) // self.rh)
        return self.order[i] if 0 <= i < len(self.order) else None

    def _set_hover(self, iid):
        if iid != self.hover:
            self.hover = iid
            self.draw()

    def _motion(self, e):
        self._set_hover(self._row_at(e.y))

    def _more_hit(self, e):
        return e.x >= self.c.winfo_width() - 40 * self.th.S

    def _click(self, e, ctrl=False, shift=False):
        self.c.focus_set()
        iid = self._row_at(e.y)
        if iid is None:
            if not ctrl and not shift:
                self.selection = []
                self.draw()
                self.on_select()
            return
        if shift and self.anchor in self.order:
            a, b = sorted((self.order.index(self.anchor), self.order.index(iid)))
            self.selection = self.order[a:b + 1]
        elif ctrl:
            if iid in self.selection:
                self.selection.remove(iid)
            else:
                self.selection.append(iid)
            self.anchor = iid
        else:
            self.selection, self.anchor = [iid], iid
        self.draw()
        self.on_select()
        if not ctrl and not shift and self._more_hit(e):
            self.on_menu(e)

    def _double(self, e):
        if self._row_at(e.y):
            self.on_open()

    def _right(self, e):
        iid = self._row_at(e.y)
        if iid and iid not in self.selection:
            self.selection, self.anchor = [iid], iid
            self.draw()
            self.on_select()
        if iid:
            self.on_menu(e)

    def _key_move(self, d, e):
        if not self.order:
            return
        cur = self.order.index(self.anchor) if self.anchor in self.order else -1
        j = max(0, min(len(self.order) - 1, cur + d))
        iid = self.order[j]
        if e.state & 0x1 and self.anchor in self.order:
            a, b = sorted((self.order.index(self.anchor), j))
            self.selection = self.order[a:b + 1]
        else:
            self.selection, self.anchor = [iid], iid
        self.see(iid)
        self.draw()
        self.on_select()
        return "break"

    # ---- drawing --------------------------------------------------------
    def _layout(self, w):
        S = self.th.S
        cols = [list(c) for c in self.COLS]
        pad = 18 * S
        fixed = sum(c[2] for c in cols) * S
        while w - fixed - 2 * pad < 220 * S and len(cols) > 3:
            drop = next((c for c in cols if c[0] in ("queue", "eta", "speed")), None)
            if not drop:
                break
            cols.remove(drop)
            fixed -= drop[2] * S
        x = w - pad
        pos = {}
        for key, _title, width, anchor in reversed(cols):
            pos[key] = (x - width * S, x, anchor)
            x -= width * S
        pos["name"] = (pad, x, "w")
        return pos

    def _draw_head(self, pos):
        t, S, f = self.th.t, self.th.S, self.th.f
        h = int(30 * S)
        hc = self.head
        hc.configure(height=h, bg=t["surface2"])
        hc.delete("all")
        w = hc.winfo_width()
        hc.create_line(0, 0, w, 0, fill=t["border"])
        hc.create_line(0, h - 1, w, h - 1, fill=t["border"])
        titles = {"name": "Name", **{c[0]: c[1] for c in self.COLS}}
        for key, (x0, x1, anchor) in pos.items():
            if not titles.get(key):
                continue
            x = x0 + (14 * S if key == "queue" else 0) if anchor == "w" else x1 - 8 * S
            hc.create_text(x, h / 2, text=titles[key], anchor=anchor, font=f["small"],
                           fill=t["muted"])

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        c = self.c
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        c.configure(bg=t["surface"])
        self._clamp()
        pos = self._layout(w)
        self.head.configure(width=w)
        self._draw_head(pos)
        c.delete("all")
        total = len(self.rows) * self.rh
        self.sb.set(self.top / max(1, total), min(1.0, (self.top + h) / max(1, total)))
        if not self.rows:
            title, sub = self.empty
            c.create_text(w / 2, h / 2 - 12 * S, text=title, font=f["big"], fill=t["text"])
            c.create_text(w / 2, h / 2 + 12 * S, text=sub, font=f["body"], fill=t["muted"])
            return
        first = int(self.top // self.rh)
        last = min(len(self.rows), int((self.top + h) // self.rh) + 1)
        for i in range(first, last):
            self._draw_row(self.rows[i], i * self.rh - self.top, w, pos)

    def _draw_row(self, r, y, w, pos):
        t, S, f = self.th.t, self.th.S, self.th.f
        c, rh = self.c, self.rh
        sel = r["id"] in self.selection
        if sel:
            c.create_rectangle(0, y, w, y + rh, fill=t["sel"], outline="")
            rounded_rect(c, 0, y + 10 * S, 3 * S, y + rh - 10 * S, 1.5 * S, fill=t["accent"],
                         outline="")
        elif r["id"] == self.hover:
            c.create_rectangle(0, y, w, y + rh, fill=t["hover"], outline="")
        c.create_line(0, y + rh - 1, w, y + rh - 1, fill=t["line"])
        mid = y + rh / 2
        # name: tile + two lines
        x0, x1, _ = pos["name"]
        ts = 32 * S
        tint, ink = r["tile"]
        rounded_rect(c, x0, mid - ts / 2, x0 + ts, mid + ts / 2, 8 * S, fill=tint, outline="")
        c.create_text(x0 + ts / 2, mid, text=r["ext"][:4], font=f["tiny_bold"], fill=ink)
        tx = x0 + ts + 12 * S
        room = x1 - tx - 14 * S
        c.create_text(tx, mid - 9 * S, text=fit(f["bold"], r["name"], room), anchor="w",
                      font=f["bold"], fill=t["text"])
        c.create_text(tx, mid + 10 * S, text=fit(f["small"], r["sub"], room), anchor="w",
                      font=f["small"], fill=t[r.get("sub_ink", "muted")])
        # progress or state
        if "progress" in pos:
            p0, p1, _ = pos["progress"]
            if r.get("pct") is not None:
                bw = p1 - p0 - 56 * S
                bar_fill = t[r.get("bar", "accent")]
                rounded_rect(c, p0, mid - 3 * S, p0 + bw, mid + 3 * S, 3 * S, fill=t["track"],
                             outline="")
                if r["pct"] > 0:
                    rounded_rect(c, p0, mid - 3 * S, p0 + max(6 * S, bw * min(1.0, r["pct"])),
                                 mid + 3 * S, 3 * S, fill=bar_fill, outline="")
                c.create_text(p1 - 8 * S, mid, text=r["pct_text"], anchor="e", font=f["small"],
                              fill=t["text"])
            else:
                ink = t[r.get("state_ink", "muted")]
                draw_icon(c, r["state_icon"], p0, mid - 7 * S, 14 * S, ink, t["surface"],
                          width=2.2 * S)
                c.create_text(p0 + 22 * S, mid, text=fit(f["small_bold"], r["state"],
                                                         p1 - p0 - 30 * S),
                              anchor="w", font=f["small_bold"], fill=ink)
        for key in ("speed", "eta", "size"):
            if key in pos:
                _a, b, _ = pos[key]
                c.create_text(b - 8 * S, mid, text=r.get(key, ""), anchor="e", font=f["body"],
                              fill=t["text"] if key == "speed" else t["muted"])
        if "queue" in pos and r.get("queue"):
            q0, q1, _ = pos["queue"]
            label = fit(f["small_bold"], r["queue"], q1 - q0 - 34 * S)
            qw = f["small_bold"].measure(label) + 16 * S
            qx = q0 + 14 * S
            qt, qi = (t["queue_tint"], t["queue_ink"]) if r.get("queue_alt") else (t["track"],
                                                                                   t["muted"])
            rounded_rect(c, qx, mid - 10 * S, qx + qw, mid + 10 * S, 10 * S, fill=qt, outline="")
            c.create_text(qx + qw / 2, mid, text=label, font=f["small_bold"], fill=qi)
        if "more" in pos:
            m0, m1, _ = pos["more"]
            draw_icon(c, "more", m1 - 22 * S, mid - 7 * S, 14 * S, t["muted"])


class Card(tk.Canvas):
    """A rounded surface with a border; subclasses draw their content."""

    def __init__(self, master, theme, height):
        self.th = theme
        super().__init__(master, highlightthickness=0, bd=0, height=int(height * theme.S))
        self.bind("<Configure>", lambda e: self.draw())
        theme.on_change(self.draw)

    def frame(self):
        t, S = self.th.t, self.th.S
        w, h = self.winfo_width(), self.winfo_height()
        self.configure(bg=t["bg"])
        self.delete("all")
        rounded_rect(self, 1, 1, w - 1, h - 1, 10 * S, fill=t["surface"], outline=t["border"])
        return w, h


class DetailsCard(Card):
    """The selected download: chips, actions, connection map and numbers —
    or the error and what to do about it."""

    def __init__(self, master, theme, actions):
        self.info = None
        self.actions = actions               # {"label": callable}
        self._buttons = []
        super().__init__(master, theme, 150)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)

    def show(self, info):
        if info != self.info:
            self.info = info
            self.draw()

    def _motion(self, e):
        hit = any(x0 <= e.x <= x1 and y0 <= e.y <= y1 for x0, y0, x1, y1, _ in self._buttons)
        self.configure(cursor="hand2" if hit else "")

    def _click(self, e):
        for x0, y0, x1, y1, fn in self._buttons:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                fn()
                return

    def _button(self, x1, y, label, fn, primary=False):
        t, S, f = self.th.t, self.th.S, self.th.f
        bw = f["small"].measure(label) + 24 * S
        x0 = x1 - bw
        rounded_rect(self, x0, y, x1, y + 28 * S, 7 * S,
                     fill=t["accent"] if primary else t["surface"],
                     outline="" if primary else t["border"])
        self.create_text((x0 + x1) / 2, y + 14 * S, text=label, font=f["small"],
                         fill=t["on_accent"] if primary else t["text"])
        self._buttons.append((x0, y, x1, y + 28 * S, fn))
        return x0 - 8 * S

    def _chip(self, x, y, text, tint, ink):
        f, S = self.th.f, self.th.S
        cw = f["small_bold"].measure(text) + 16 * S
        rounded_rect(self, x, y - 10 * S, x + cw, y + 10 * S, 10 * S, fill=tint, outline="")
        self.create_text(x + cw / 2, y, text=text, font=f["small_bold"], fill=ink)
        return x + cw + 6 * S

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        w, h = self.frame()
        self._buttons = []
        info = self.info
        pad = 18 * S
        if not info:
            self.create_text(w / 2, h / 2 - 9 * S, text="Select a download to see its details",
                             font=f["bold"], fill=t["text"])
            self.create_text(w / 2, h / 2 + 12 * S,
                             text="Connections, speed, mirrors — or what to do if it failed.",
                             font=f["small"], fill=t["muted"])
            return
        # actions (right), title + chips (left)
        bx = w - pad
        for label, key, primary in reversed(info["buttons"]):
            fn = self.actions.get(key)
            if fn:
                bx = self._button(bx, 12 * S, label, fn, primary)
        x = pad
        title_room = bx - x - 10 * S
        chips_w = sum(f["small_bold"].measure(c[0]) + 22 * S for c in info.get("chips", []))
        name = fit(f["big"], info["title"], max(80 * S, title_room - chips_w))
        self.create_text(x, 26 * S, text=name, anchor="w", font=f["big"], fill=t["text"])
        x += f["big"].measure(name) + 12 * S
        for text, kind in info.get("chips", []):
            tint, ink = {"accent": (t["accent_soft"], t["accent_ink"]),
                         "ok": (t["accent_soft"], t["ok"]),
                         "bad": (t["track"], t["bad"])}.get(kind, (t["track"], t["muted"]))
            if x + f["small_bold"].measure(text) + 16 * S < bx:
                x = self._chip(x, 26 * S, text, tint, ink)
        y = 52 * S
        if info.get("error"):
            lines = wrap(f["body"], "Error: " + info["error"], w - 2 * pad, 2)
            for ln in lines:
                self.create_text(pad, y + 9 * S, text=ln, anchor="w", font=f["body"],
                                 fill=t["bad"])
                y += 20 * S
            if info.get("hint"):
                for ln in wrap(f["body"], "What to do: " + info["hint"], w - 2 * pad, 2):
                    self.create_text(pad, y + 11 * S, text=ln, anchor="w", font=f["body"],
                                     fill=t["text"])
                    y += 20 * S
            return
        # connection map
        segs = info.get("segments") or []
        n = len(segs)
        if n:
            gap = 3 * S
            sw = (w - 2 * pad - gap * (n - 1)) / n
            for i, (frac, live) in enumerate(segs):
                sx = pad + i * (sw + gap)
                rounded_rect(self, sx, y, sx + sw, y + 12 * S, 3 * S, fill=t["track"], outline="")
                if frac > 0:
                    rounded_rect(self, sx, y, sx + max(3 * S, sw * frac), y + 12 * S, 3 * S,
                                 fill=t[info.get("seg_color", "accent")], outline="")
                if live and frac < 1:
                    lx = sx + sw * frac
                    self.create_line(lx, y - 1 * S, lx, y + 13 * S, fill=t["warn"],
                                     width=max(1, 1.5 * S))
        y += 26 * S
        stats = info.get("stats") or []
        if stats:
            cw = (w - 2 * pad) / len(stats)
            for i, (label, value) in enumerate(stats):
                sx = pad + i * cw
                self.create_text(sx, y + 8 * S, text=label, anchor="w", font=f["small"],
                                 fill=t["muted"])
                self.create_text(sx, y + 27 * S, text=fit(f["bold"], value, cw - 14 * S),
                                 anchor="w", font=f["bold"], fill=t["text"])


class StatusBar(tk.Canvas):
    def __init__(self, master, theme):
        self.th = theme
        self.left, self.right, self.ok = [], [], True
        super().__init__(master, highlightthickness=0, bd=0, height=int(28 * theme.S))
        self.bind("<Configure>", lambda e: self.draw())
        theme.on_change(self.draw)

    def set(self, left, right, ok=True):
        if (left, right, ok) != (self.left, self.right, self.ok):
            self.left, self.right, self.ok = left, right, ok
            self.draw()

    def draw(self):
        t, S, f = self.th.t, self.th.S, self.th.f
        w, h = self.winfo_width(), self.winfo_height()
        self.configure(bg=t["bg"])
        self.delete("all")
        self.create_line(0, 0, w, 0, fill=t["border"])
        x = 18 * S
        for i, part in enumerate(self.left):
            if i == 0:
                self.create_oval(x, h / 2 - 3.5 * S, x + 7 * S, h / 2 + 3.5 * S,
                                 fill=t["ok"] if self.ok else t["bad"], outline="")
                x += 13 * S
            self.create_text(x, h / 2, text=part, anchor="w", font=f["small"], fill=t["muted"])
            x += f["small"].measure(part) + 18 * S
        x = w - 18 * S
        for part in reversed(self.right):
            room = max(0, x - w * 0.55)
            part = fit(f["small"], part, room)
            self.create_text(x, h / 2, text=part, anchor="e", font=f["small"], fill=t["muted"])
            x -= f["small"].measure(part) + 18 * S
