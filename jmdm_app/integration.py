"""Lets the browser start the app (native messaging registration on Windows)."""
import os
import sys
import json
from .config import CHROME_EXTENSION_ID, FIREFOX_EXTENSION_ID, NATIVE_HOST
from .util import app_dir

def register_browser_integration():
    """Register the native messaging helper with Chrome, Edge, Brave and
    Firefox (current user only, no admin needed) so the extension can start
    the app. Safe to run on every launch; it also fixes paths if the app moved."""
    if os.name != "nt":
        return False, "Only needed on Windows"
    import winreg
    data = os.path.join(app_dir(), "jmdm_data")
    os.makedirs(data, exist_ok=True)
    if getattr(sys, "frozen", False):
        host = os.path.join(app_dir(), "jmdm-host.exe")
        if not os.path.isfile(host):
            return False, "jmdm-host.exe is missing next to the app"
    else:
        host = os.path.join(data, "jmdm-host.bat")
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            py = py[:-11] + "python.exe"
        with open(host, "w", encoding="utf-8") as fh:
            fh.write(f'@echo off\n"{py}" "{os.path.join(app_dir(), "native_host.py")}" %*\n')
    base = {"name": NATIVE_HOST, "description": "Starts JM Download Manager",
            "path": host, "type": "stdio"}
    chrome_json = os.path.join(data, "native_host_chrome.json")
    firefox_json = os.path.join(data, "native_host_firefox.json")
    with open(chrome_json, "w", encoding="utf-8") as fh:
        json.dump(dict(base, allowed_origins=[f"chrome-extension://{CHROME_EXTENSION_ID}/"]),
                  fh, indent=2)
    with open(firefox_json, "w", encoding="utf-8") as fh:
        json.dump(dict(base, allowed_extensions=[FIREFOX_EXTENSION_ID]), fh, indent=2)
    keys = [(r"Software\Google\Chrome\NativeMessagingHosts", chrome_json),
            (r"Software\Microsoft\Edge\NativeMessagingHosts", chrome_json),
            (r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts", chrome_json),
            (r"Software\Mozilla\NativeMessagingHosts", firefox_json)]
    done = 0
    for key, path in keys:
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + "\\" + NATIVE_HOST) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, path)
            done += 1
        except OSError:
            pass
    return done > 0, f"Registered with {done} browser profile location(s)"
