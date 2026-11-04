"""The launcher and package as a whole."""
import importlib
import os
import subprocess
import sys

from conftest import ROOT, content

MODULES = ["config", "util", "net", "gates", "updates", "integration", "series",
           "downloader", "video", "browser", "manager", "schedule", "gdrive", "gui_theme", "gui_widgets", "gui_popups", "gui_fx", "gui_add", "icon_map", "gui", "cli"]


def test_every_module_imports():
    for m in MODULES:
        importlib.import_module("jmdm_app." + m)


def test_app_dir_is_the_app_folder():
    from jmdm_app import util
    assert os.path.isfile(os.path.join(util.app_dir(), "jmdm.py"))


def test_command_line_download(server, tmp_path):
    url = server.url("/file/cli.bin?size=600000&seed=c")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "jmdm.py"), "--cli", url,
                        "-o", str(tmp_path), "-n", "4"],
                       capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    with open(tmp_path / "cli.bin", "rb") as fh:
        assert fh.read() == content(600000, "c")
