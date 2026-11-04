"""Add download window helpers that need no window."""
import pytest

pytest.importorskip("tkinter")
from jmdm_app import gui_add  # noqa: E402


def test_page_like():
    assert gui_add.page_like("https://anime.example/season/frieren-s2")
    assert not gui_add.page_like("https://h.com/game.rar")
    assert not gui_add.page_like("https://h.com/game.7z.001")
    assert not gui_add.page_like("https://www.youtube.com/watch?v=x")
    assert not gui_add.page_like("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view")
    assert not gui_add.page_like("not a link")


def test_folder_memory_per_type():
    s = {"download_dir": "D:/Downloads"}
    gui_add.remember_folder(s, "Videos", "D:/Anime")
    gui_add.remember_folder(s, "Compressed", "E:/Games")
    assert gui_add.remembered_folder(s, "Videos") == "D:/Anime"
    assert gui_add.remembered_folder(s, "Compressed") == "E:/Games"
    assert gui_add.remembered_folder(s, "Programs") == ""
    gui_add.remember_folder(s, "Videos", "D:/Downloads")      # back to the default
    assert gui_add.remembered_folder(s, "Videos") == ""


def test_labels(tmp_path):
    assert gui_add.height_label(2160) == "4K" and gui_add.height_label(1080) == "1080p"
    assert gui_add.clock(754) == "12:34" and gui_add.clock(3725) == "1:02:05"
    assert "free on" in gui_add.free_space(str(tmp_path / "not" / "made" / "yet"))


def test_probe_reports_errors_instead_of_raising():
    r = gui_add.probe_link("http://127.0.0.1:9/nothing.zip")
    assert r["kind"] == "error" and r["error"]
