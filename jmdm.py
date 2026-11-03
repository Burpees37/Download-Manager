#!/usr/bin/env python3
"""JM Download Manager — starts the app. The code lives in the jmdm_app package.

Run:   python jmdm.py                -> the app window
       python jmdm.py <url>          -> the window with the Add dialog filled in
       python jmdm.py --cli <url> [-o folder] [-n connections] [--limit KB/s]
"""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from jmdm_app.cli import run_cli
        return run_cli(sys.argv[2:])
    from jmdm_app.gui import run_gui
    return run_gui(sys.argv[1] if len(sys.argv) > 1 else None)


if __name__ == "__main__":
    sys.exit(main())
