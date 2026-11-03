@echo off
rem Builds "JM Download Manager.exe" and installs it to D:\JM-Download-Manager
cd /d "%~dp0"
echo Installing build tools...
python -m pip install --upgrade pyinstaller yt-dlp certifi || goto :nopython
python -m PyInstaller --noconfirm --onefile --windowed --name "JM Download Manager" ^
  --icon jmdm.ico --collect-submodules yt_dlp jmdm.py || goto :fail
python -m PyInstaller --noconfirm --onefile --windowed --name jmdm-host native_host.py || goto :fail
set DEST=D:\JM-Download-Manager
if not exist D:\ set DEST=%USERPROFILE%\JM-Download-Manager
if not exist "%DEST%" mkdir "%DEST%"
copy /y "dist\JM Download Manager.exe" "%DEST%\" >nul
copy /y "dist\jmdm-host.exe" "%DEST%\" >nul
xcopy /e /i /y extension "%DEST%\extension" >nul
echo.
echo Done! App installed to %DEST%
echo For HD YouTube/Facebook video, put ffmpeg.exe in that folder (see README).
start "" "%DEST%"
pause
exit /b 0
:nopython
echo Python is not installed. Get it from https://www.python.org/downloads/ (tick "Add to PATH").
pause
exit /b 1
:fail
echo Build failed - see the messages above.
pause
exit /b 1
