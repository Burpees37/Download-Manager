"""The light/dark switch animation: the new theme spreads out in a circle from
the sun/moon button — a warm golden glow when going light ("sunrise"), an
indigo glow with twinkling stars when going dark ("nightfall").

How: picture the window before and after the switch, then show frames that
blend the two through a growing circle (Pillow). On Windows the "after"
picture is taken straight from the window (PrintWindow) while a cover shows
the "before" one, so nothing flickers. If anything is missing (no Pillow, a
capture fails) or Windows' animation effects are off, the switch is instant."""
import math
import os
import random
import time
import tkinter as tk

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageGrab, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

DURATION = 0.85            # seconds
SUN = (255, 196, 84)
NIGHT = (110, 128, 255)


def animations_enabled():
    """Windows: Settings → Accessibility → Visual effects → Animation effects."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        on = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)
        return bool(on.value)
    except Exception:
        return True


def _print_window(widget):
    """The widget's window as drawn, even under other windows (Windows only)."""
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = widget.winfo_id()
    w, h = widget.winfo_width(), widget.winfo_height()
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 2):          # PW_RENDERFULLCONTENT
            return None

        class BMI(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            return None
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
        return img if img.getextrema() != ((0, 0), (0, 0), (0, 0)) else None
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def capture(widget, direct=False):
    """A picture of the widget's area: straight from the window when `direct`
    (Windows), else from the screen."""
    widget.update_idletasks()
    if direct and os.name == "nt":
        try:
            img = _print_window(widget)
            if img is not None:
                return img
        except Exception:
            pass
        return None
    try:
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        img = ImageGrab.grab(bbox=(x, y, x + widget.winfo_width(), y + widget.winfo_height()),
                             all_screens=os.name == "nt")
        return img.convert("RGB")
    except Exception:
        return None


def _glow_sprite(color, size=256):
    """A soft round glow: bright middle fading to nothing."""
    img = Image.new("RGBA", (size, size), color + (0,))
    px = img.load()
    c = (size - 1) / 2
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - c, y - c) / c
            if d < 1:
                a = (1 - d) ** 2.2
                px[x, y] = color + (int(255 * a),)
    return img


class ThemeSwitch:
    """Run one animated switch. `apply()` changes the theme; `origin` is the
    widget the circle starts from."""
    running = False

    def __init__(self, root, origin, to_dark, apply, scale=1.0):
        self.root, self.origin, self.to_dark, self.apply, self.S = root, origin, to_dark, apply, scale

    def start(self):
        if ThemeSwitch.running:
            return
        if not (HAVE_PIL and animations_enabled()) or self.root.winfo_width() < 50:
            self.apply()
            return
        ThemeSwitch.running = True
        try:
            self._prepare()
        except Exception:
            self._finish(apply_if_needed=True)

    # ---- set-up -----------------------------------------------------------
    def _prepare(self):
        root = self.root
        self.applied = False
        before = capture(root)
        if before is None:
            self._finish(apply_if_needed=True)
            return
        w, h = before.size
        self.cover = None
        if os.name == "nt":
            # cover the window with the "before" picture, switch underneath,
            # then take the "after" picture straight from the window
            self.cover = tk.Toplevel(root)
            self.cover.overrideredirect(True)
            self.cover.geometry(f"{w}x{h}+{root.winfo_rootx()}+{root.winfo_rooty()}")
            self.cover.attributes("-topmost", True)
            self.canvas = tk.Canvas(self.cover, width=w, height=h, highlightthickness=0, bd=0)
            self.canvas.pack()
            self.photo = ImageTk.PhotoImage(before, master=self.canvas)
            self.item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
            self.cover.update()
            self.apply()
            self.applied = True
            root.update()
            after = capture(root, direct=True)
        else:
            self.apply()
            self.applied = True
            root.update()
            after = capture(root)
        if after is None or after.size != before.size:
            self._finish()
            return
        if self.cover is None:
            self.canvas = tk.Canvas(root, highlightthickness=0, bd=0)
            self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
            self.photo = ImageTk.PhotoImage(before, master=self.canvas)
            self.item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        ox = self.origin.winfo_rootx() - root.winfo_rootx() + self.origin.winfo_width() / 2
        oy = self.origin.winfo_rooty() - root.winfo_rooty() + self.origin.winfo_height() / 2
        self.setup(before, after, (ox, oy))
        self.t0 = time.perf_counter()
        self._frame()

    def setup(self, before, after, origin):
        """Everything render() needs: the two pictures and where the circle starts."""
        self.before, self.after = before, after
        w, h = before.size
        ox, oy = origin
        self.o = (ox, oy)
        self.rmax = max(math.hypot(ox - cx, oy - cy) for cx in (0, w) for cy in (0, h)) + 30
        color = NIGHT if self.to_dark else SUN
        self.color = color
        self.glow = _glow_sprite(color)
        self.q = 4                                     # masks are worked out at 1/4 size
        self.small = (max(1, w // self.q), max(1, h // self.q))
        rnd = random.Random(7)
        self.stars = []
        if self.to_dark:                               # where the stars appear
            for _ in range(int(26 * self.S)):
                ang = rnd.uniform(math.pi * 0.5, math.pi * 1.0)   # down-left of a top-right button
                dist = rnd.uniform(0.08, 0.9) * self.rmax
                sx, sy = ox + math.cos(ang) * dist, oy + math.sin(ang) * dist
                if 0 <= sx < w and 0 <= sy < h:
                    self.stars.append((sx, sy, dist, rnd.uniform(1.2, 2.4) * self.S,
                                       rnd.uniform(0, 0.25)))

    # ---- frames -----------------------------------------------------------
    @staticmethod
    def _ease(t):
        return 1 - (1 - t) ** 3 if t < 1 else 1.0          # fast start, soft landing

    def render(self, t):
        """Frame at time t (0..1) as an RGB image."""
        e = self._ease(t)
        r = e * self.rmax
        w, h = self.before.size
        q = self.q
        ox, oy = self.o
        rs = r / q
        box = [ox / q - rs, oy / q - rs, ox / q + rs, oy / q + rs]
        # the circle, with a soft edge
        m = Image.new("L", self.small, 0)
        ImageDraw.Draw(m).ellipse(box, fill=255)
        mask = m.filter(ImageFilter.GaussianBlur(1.2)).resize((w, h), Image.Resampling.BILINEAR)
        frame = Image.composite(self.after, self.before, mask).convert("RGBA")
        # glowing edge of the circle
        ring = Image.new("L", self.small, 0)
        band = max(2.0, 9 * self.S / q)
        ImageDraw.Draw(ring).ellipse(box, outline=255, width=int(band))
        ring = ring.filter(ImageFilter.GaussianBlur(band)).resize((w, h), Image.Resampling.BILINEAR)
        strength = 0.75 * (1 - t) ** 0.8
        amount = ring.point(lambda v: int(v * strength))
        if self.to_dark:
            glow_layer = Image.new("RGBA", (w, h), self.color + (255,))
            glow_layer.putalpha(amount)
            frame.alpha_composite(glow_layer)
        else:
            # sunrise: light is added (screen blend), so over the dark side the
            # edge shines gold instead of turning a muddy brown
            light = Image.merge("RGB", [amount.point(lambda v, c=c: v * c // 255) for c in self.color])
            frame = ImageChops.screen(frame.convert("RGB"), light).convert("RGBA")
        # the glow at the button: blooms, then fades
        size = int((60 + 700 * e) * self.S)
        alpha = (min(1.0, t / 0.18)) * (1 - t) ** 1.4
        if size > 4 and alpha > 0.01:
            g = self.glow.resize((size, size), Image.Resampling.BILINEAR)
            if alpha < 1:
                g.putalpha(g.getchannel("A").point(lambda v: int(v * alpha)))
            self._paste_clipped(frame, g, ox - size / 2, oy - size / 2)
        # stars twinkle on as night falls
        if self.stars:
            sd = ImageDraw.Draw(frame)
            for sx, sy, dist, rad, delay in self.stars:
                if dist > r:
                    continue
                k = max(0.0, min(1.0, (t - delay) / 0.3))
                a = k * (0.6 + 0.4 * math.sin((t - delay) * 30 + sx)) * (1 - max(0, t - 0.8) / 0.2)
                if a > 0.02:
                    for grow, aa in ((rad * 2.6, 0.25), (rad, 1.0)):
                        sd.ellipse([sx - grow, sy - grow, sx + grow, sy + grow],
                                   fill=(235, 240, 255, int(255 * a * aa)))
        return frame.convert("RGB")

    def _frame(self):
        try:
            t = min(1.0, (time.perf_counter() - self.t0) / DURATION)
            self.photo = ImageTk.PhotoImage(self.render(t), master=self.canvas)
            self.canvas.itemconfigure(self.item, image=self.photo)
            if t < 1:
                self.canvas.after(1, self._frame)
            else:
                self.canvas.after(40, self._finish)
        except Exception:
            self._finish(apply_if_needed=True)

    @staticmethod
    def _paste_clipped(frame, sprite, x, y):
        """alpha_composite with a sprite that hangs over the frame's edge."""
        x, y = int(x), int(y)
        sx, sy = max(0, -x), max(0, -y)
        crop = sprite.crop((sx, sy, sprite.width, sprite.height))
        frame.alpha_composite(crop, (max(0, x), max(0, y)))

    def _finish(self, apply_if_needed=False):
        if apply_if_needed and not getattr(self, "applied", False):
            try:
                self.apply()
            except Exception:
                pass
        for w in (getattr(self, "cover", None), getattr(self, "canvas", None)):
            try:
                if w is not None:
                    w.destroy()
            except tk.TclError:
                pass
        ThemeSwitch.running = False
