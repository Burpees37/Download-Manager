"""Colours, fonts and light/dark switching for the window (mockup D).

The main window is drawn with Tk canvases using the palettes below. Dialogs use
ttk widgets styled by the Sun Valley theme (sv-ttk, MIT), which gives them the
Windows 11 look in both light and dark. Without sv-ttk the app still works,
with the plain Windows look.
"""
import os
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

LIGHT = {
    "bg": "#f3f5f8", "surface": "#ffffff", "surface2": "#f8f9fb", "border": "#e2e5ea",
    "line": "#eef0f3", "text": "#1b1d21", "muted": "#5d6470", "accent": "#1f6feb",
    "accent_soft": "#e8f0fe", "accent_ink": "#174ea6", "sel": "#eaf2ff", "hover": "#f2f5f9",
    "nav_sel": "#ffffff", "nav_hover": "#e9edf2", "track": "#e6e9ee", "ok": "#1a7f37",
    "bad": "#c62828", "warn": "#9a5b00", "on_accent": "#ffffff", "btn_hover": "#e6eaf0",
    "btn_down": "#dce2ea", "paused": "#9aa1ac", "queue_tint": "#ede8fb", "queue_ink": "#5b3cc4",
}
DARK = {
    "bg": "#16181c", "surface": "#1e2126", "surface2": "#23262c", "border": "#30343c",
    "line": "#272a31", "text": "#e9ebef", "muted": "#a3a9b4", "accent": "#4c8dff",
    "accent_soft": "#1d2a40", "accent_ink": "#9cc0ff", "sel": "#1d2a40", "hover": "#24282e",
    "nav_sel": "#262a31", "nav_hover": "#22252b", "track": "#30353e", "ok": "#4cc38a",
    "bad": "#ff7b72", "warn": "#f2b04a", "on_accent": "#ffffff", "btn_hover": "#2a2e35",
    "btn_down": "#33373f", "paused": "#7d8592", "queue_tint": "#2b2040", "queue_ink": "#c9a7ff",
}
# file-type tiles: (light tint, light ink, dark tint, dark ink)
TILES = {
    "Compressed": ("#fdf0dc", "#8a5300", "#3a2e14", "#f4c56a"),
    "Programs": ("#e3eefb", "#0c4f8f", "#16253a", "#8fb8ff"),
    "Videos": ("#fbe3e6", "#a01a2c", "#3a1820", "#ff8fa3"),
    "Music": ("#e0f3f4", "#0b5f66", "#123236", "#6fd6df"),
    "Documents": ("#fdeceb", "#a3261e", "#3a1c1a", "#ff9b8f"),
    "Other": ("#eef0f3", "#4f5865", "#2a2e35", "#b9c0ca"),
}


ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def load_icon_fonts():
    """Make the app's icon fonts (Fluent UI System Icons) usable by this
    process only — nothing is installed. Call before creating the window."""
    if os.name != "nt":
        return                                   # elsewhere: installed fonts, or drawn icons
    try:
        import ctypes
        for f in ("jmdm-icons-regular.ttf", "jmdm-icons-filled.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(os.path.join(ICON_DIR, f), 0x10, 0)
    except Exception:
        pass


def ui_scale(root):
    """1.0 at 100 % Windows scaling, 1.5 at 150 % …"""
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def system_prefers_dark():
    """Windows' "Choose your app mode" setting."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class Theme:
    def __init__(self, root, mode="system", scale=1.0):
        self.root = root
        self.S = scale
        self.mode = mode if mode in ("light", "dark", "system") else "system"
        self.dark = False
        self.t = dict(LIGHT)
        self._listeners = []
        self._painted = []          # (widget, {option: token})
        self.sv = None
        try:
            import sv_ttk
            self.sv = sv_ttk
        except Exception:
            self.sv = None
        fams = set(tkfont.families(root))
        base = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans")
                     if f in fams), tkfont.nametofont("TkDefaultFont").actual("family"))
        disp = next((f for f in ("Segoe UI Variable Display", base) if f in fams), base)
        self.family = base
        from .icon_map import FAMILY, ICONS
        self.icon_codes = ICONS
        self.icon_family = FAMILY if all(f in fams for f in FAMILY.values()) else None
        self._icon_fonts = {}
        self.f = {
            "body": tkfont.Font(root, family=base, size=10),
            "bold": tkfont.Font(root, family=base, size=10, weight="bold"),
            "small": tkfont.Font(root, family=base, size=9),
            "small_bold": tkfont.Font(root, family=base, size=9, weight="bold"),
            "tiny_bold": tkfont.Font(root, family=base, size=7, weight="bold"),
            "title": tkfont.Font(root, family=disp, size=15, weight="bold"),
            "big": tkfont.Font(root, family=base, size=11, weight="bold"),
        }
        self.apply(self.wants_dark())

    def wants_dark(self):
        return self.mode == "dark" or (self.mode == "system" and system_prefers_dark())

    def icon(self, name, px):
        """(font, character) for an icon at px pixels, or None to draw it by hand."""
        if not self.icon_family or name not in self.icon_codes:
            return None
        style, cp = self.icon_codes[name]
        key = (style, int(round(px)))
        if key not in self._icon_fonts:
            self._icon_fonts[key] = tkfont.Font(self.root, family=self.icon_family[style],
                                                size=-max(8, key[1]))
        return self._icon_fonts[key], chr(cp)

    # ---- registration ---------------------------------------------------
    def paint(self, widget, **tokens):
        """widget.configure(option=palette[token]) now and on every switch."""
        self._painted.append((widget, tokens))
        widget.configure(**{k: self.t[v] for k, v in tokens.items()})
        return widget

    def on_change(self, fn):
        self._listeners.append(fn)

    def tile(self, category):
        lt, li, dt, di = TILES.get(category, TILES["Other"])
        return (dt, di) if self.dark else (lt, li)

    # ---- switching ------------------------------------------------------
    def set_mode(self, mode):
        self.mode = mode
        self.apply(self.wants_dark())

    def apply(self, dark):
        self.dark = dark
        self.t = dict(DARK if dark else LIGHT)
        if self.sv:
            try:
                self.sv.set_theme("dark" if dark else "light", self.root)
                # Sun Valley sets the dialog background/text colours when Tk
                # reports <<ThemeChanged>>. On Windows that report is lost while
                # the app is starting, so dialogs stayed Tk grey in dark mode:
                # run its colour step directly (doing it twice is harmless).
                try:
                    self.root.tk.call("configure_colors")
                except tk.TclError:
                    pass
            except Exception:
                self.sv = None
        if not self.sv:
            style = ttk.Style(self.root)
            for th in ("vista", "clam"):
                if th in style.theme_names():
                    style.theme_use(th)
                    break
        style = ttk.Style(self.root)
        style.configure("Hint.TLabel", foreground=self.t["muted"])
        style.configure("Bad.TLabel", foreground=self.t["bad"])
        style.configure("Head.TLabel", font=self.f["big"])
        self.root.configure(bg=self.t["bg"])
        # the lists that drop open from dropdowns (Queue, Quality, Appearance …)
        t = self.t
        for opt, val in (("background", t["surface"]), ("foreground", t["text"]),
                         ("selectBackground", t["accent"]), ("selectForeground", t["on_accent"]),
                         ("font", self.f["body"]), ("borderWidth", 0),
                         ("highlightThickness", 0), ("activeStyle", "none")):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)
        self.window_chrome(self.root)
        alive = []
        for w, tokens in self._painted:
            try:
                w.configure(**{k: self.t[v] for k, v in tokens.items()})
                alive.append((w, tokens))
            except tk.TclError:
                pass                                   # widget was closed
        self._painted = alive
        for fn in list(self._listeners):
            try:
                fn()
            except tk.TclError:
                pass

    def window_chrome(self, win):
        """Dark or light Windows title bar to match (Windows 10 1809+ / 11)."""
        if os.name != "nt":
            return
        try:
            import ctypes
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            val = ctypes.c_int(1 if self.dark else 0)
            for attr in (20, 19):                      # DWMWA_USE_IMMERSIVE_DARK_MODE
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
        except Exception:
            pass

    def style_text_widget(self, w):
        """Plain tk Text/Listbox inside dialogs."""
        wanted = {"bg": "surface", "fg": "text", "insertbackground": "text",
                  "selectbackground": "accent", "selectforeground": "on_accent",
                  "highlightbackground": "border", "highlightcolor": "accent"}
        have = set(w.keys())
        self.paint(w, **{k: v for k, v in wanted.items() if k in have})
