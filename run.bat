@echo off
rem Run without building an .exe (needs Python + yt-dlp)
cd /d "%~dp0"
python -m pip install --quiet --upgrade yt-dlp
start "" pythonw jmdm.py %*
