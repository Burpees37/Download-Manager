"""The extension's catch logic, run in Node against a fake browser."""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(os.path.dirname(HERE), "extension")


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
def test_extension_syntax():
    for f in ("background.js", "content.js", "popup.js"):
        subprocess.run(["node", "--check", os.path.join(EXT, f)], check=True)


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
def test_catch_rules():
    r = subprocess.run(["node", os.path.join(HERE, "extension_harness.mjs"),
                        os.path.join(EXT, "background.js")],
                       capture_output=True, text=True, timeout=60, check=True)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    taken = "add:true,cancel,erase"
    assert out == {"firefoxHookUnused": True, "zip": taken, "png": "suggest",
                   "cancelled": "add:true,suggest", "alt": "suggest", "excluded": "suggest",
                   "small": "suggest", "anyType": taken, "off": "suggest",
                   # new in 1.8.1
                   "pdf": taken, "split": taken, "nameless": taken, "namelessZip": taken,
                   "namelessHtml": "suggest", "drivePng": taken, "docsExport": taken,
                   "blob": "suggest", "cancelled2": "add:true,suggest",
                   "recent": ["cancelled:why", "skipped:why", "taken:-", "taken:-"],
                   "recentNames": ["game.zip", "game.zip", "Notes.txt"],
                   "driveButton": "Original file from Google Drive|false",
                   "appDown": "suggest",
                   # 1.10.0: next episode in the same tab gets the new stream
                   "ep1": ["ep1/master.m3u8"], "ep2": ["ep2/master.m3u8"],
                   "reloaded": ["ep.site/watch"], "ep3": ["ep3/master.m3u8"],
                   # 1.8.1: a feed video sends its own address, not the feed's
                   "fbFeed": "https://www.facebook.com/watch/?v=123|true|title="}
