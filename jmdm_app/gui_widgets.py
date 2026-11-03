"""Drawn pieces of the Classic window: toolbar buttons with vector icons, the
speed graph and small file-type badges. Everything is drawn with Tk itself, so
no image files are needed and it stays sharp at any Windows display scaling."""
import tkinter as tk

TOOLBAR_BG = "#fbfcfd"
FONT = ("Segoe UI", 9)

# icon = list of drawing steps on a 24x24 grid: (kind, coords, filled)
ICONS = {
    "add": [("line", (12, 5, 12, 19), False), ("line", (5, 12, 19, 12), False)],
    "resume": [("poly", (8, 5, 19, 12, 8, 19), True)],
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
    "folder": [("line", (3, 7, 3, 19, 21, 19, 21, 9, 11, 9, 9, 6, 3, 6, 3, 7), False)],
}


def ui_scale(root):
    """1.0 at 100 % Windows scaling, 1.5 at 150 % …"""
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def rounded_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def draw_icon(c, name, x, y, size, color, bg):
    k = size / 24.0
    w = max(1.5, 2 * k)
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
        elif kind == "dot":
            r = 2.4 * k
            c.create_oval(p[0] - r, p[1] - r, p[0] + r, p[1] + r, fill=bg, outline=color,
                          width=w)


class ToolButton(tk.Canvas):
    """A big IDM-style toolbar button: tinted icon tile with a label under it."""

    def __init__(self, master, text, icon, tint, ink, command, scale=1.0):
        self.s = scale
        w, h = int(80 * scale), int(68 * scale)
        super().__init__(master, width=w, height=h, bg=TOOLBAR_BG, highlightthickness=0,
                         cursor="hand2", takefocus=1)
        self.text, self.icon, self.tint, self.ink, self.command = text, icon, tint, ink, command
        self.hover = self.down = False
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<KeyPress-space>", lambda e: self.command())
        self.bind("<KeyPress-Return>", lambda e: self.command())
        self._draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self._draw()

    def _release(self, e):
        fire = self.down and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._set(down=False)
        if fire:
            self.command()

    def _draw(self):
        s = self.s
        self.delete("all")
        w, h = int(80 * s), int(68 * s)
        if self.hover:
            rounded_rect(self, 2, 2, w - 2, h - 2, 6 * s,
                         fill="#e3ecf8" if self.down else "#eef3fa", outline="#c7d6ea")
        t = 34 * s
        x0, y0 = (w - t) / 2, 7 * s
        rounded_rect(self, x0, y0, x0 + t, y0 + t, 8 * s, fill=self.tint, outline="")
        ic = 20 * s
        draw_icon(self, self.icon, x0 + (t - ic) / 2, y0 + (t - ic) / 2, ic, self.ink, self.tint)
        self.create_text(w / 2, y0 + t + 13 * s, text=self.text, font=FONT, fill="#202124")


class SpeedGraph(tk.Canvas):
    """Total speed over the last minute or so, like IDM's graph."""

    def __init__(self, master, color, scale=1.0, points=90):
        self.w, self.h = int(180 * scale), int(56 * scale)
        super().__init__(master, width=self.w, height=self.h, bg="#f1f5fb",
                         highlightthickness=1, highlightbackground="#dde3ec")
        self.color, self.n = color, points
        self.values = [0.0] * points

    def push(self, v):
        self.values = (self.values + [float(v)])[-self.n:]
        self.redraw()

    def redraw(self):
        self.delete("all")
        top = max(self.values) or 1.0
        w, h = self.w, self.h
        step = w / (self.n - 1)
        pts = []
        for i, v in enumerate(self.values):
            pts += [i * step, h - 3 - (h - 8) * v / top]
        self.create_polygon(0, h, *pts, w, h, fill="#d6e4f7", outline="")
        self.create_line(*pts, fill=self.color, width=2, smooth=True)


# file-type badges in the list and sidebar
CATEGORY_COLORS = {
    "Compressed": ("#fdf0dc", "#b7791f"), "Programs": ("#e3eefb", "#2a6ab5"),
    "Videos": ("#fbe3e6", "#b4233a"), "Music": ("#e0f3f4", "#127580"),
    "Documents": ("#fdeceb", "#b3392f"), "Other": ("#eef0f3", "#6b7480"),
}


def badge_image(root, fill, edge, size):
    """A small rounded colour tile (PhotoImage, no files needed)."""
    img = tk.PhotoImage(width=size, height=size)
    m = max(1, size // 8)
    img.put(edge, to=(m, m, size - m, size - m))
    img.put(fill, to=(m + 1, m + 1, size - m - 1, size - m - 1))
    img.put(edge, to=(m + 2, size // 2 - 1, size - m - 2, size // 2 + 1))
    for x, y in ((m, m), (size - m - 1, m), (m, size - m - 1), (size - m - 1, size - m - 1)):
        img.put("#ffffff", to=(x, y, x + 1, y + 1))       # soften the corners
    return img


def folder_image(root, fill, edge, size):
    img = tk.PhotoImage(width=size, height=size)
    t = size // 4
    img.put(edge, to=(1, t - 1, size // 2, t + 1))           # tab
    img.put(edge, to=(1, t, size - 1, size - 2))
    img.put(fill, to=(2, t + 2, size - 2, size - 3))
    return img
