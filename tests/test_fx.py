"""The light/dark animation's frames, drawn without a screen."""
import pytest

pytest.importorskip("tkinter")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from jmdm_app import gui_fx  # noqa: E402


def frames(to_dark):
    w, h = 400, 260
    dark, light = (22, 24, 28), (243, 245, 248)
    before = Image.new("RGB", (w, h), light if to_dark else dark)
    after = Image.new("RGB", (w, h), dark if to_dark else light)
    fx = gui_fx.ThemeSwitch(None, None, to_dark, lambda: None)
    fx.setup(before, after, (370, 30))
    return fx, [fx.render(t / 10) for t in range(11)], dark


@pytest.mark.parametrize("to_dark", [False, True])
def test_frames_never_go_black(to_dark):
    # The glow at the circle's edge must tint, never darken: an earlier version
    # drew a black band there going dark -> light.
    fx, imgs, dark = frames(to_dark)
    for img in imgs:
        lo = img.convert("L").getextrema()[0]
        assert lo >= sum(dark) / 3 - 2, (to_dark, lo)


@pytest.mark.parametrize("to_dark", [False, True])
def test_starts_old_ends_new(to_dark):
    fx, imgs, _ = frames(to_dark)
    far = (20, 240)                                   # far from the button
    assert imgs[0].getpixel(far) == fx.before.getpixel(far)
    assert imgs[-1].getpixel(far) == fx.after.getpixel(far)
    near = (365, 35)
    assert imgs[5].getpixel(near) != fx.before.getpixel(near)   # circle has passed here
