@echo off
cd /d "%~dp0"
if not exist "%~dp0voice_clean.py" (echo ERROR: run this file from the EXTRACTED folder, not from inside the zip. & echo Right click the zip, Extract All, then open the extracted folder. & pause & exit /b 1)
echo === VoiceCleanKit install ===
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Installing via winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  echo.
  echo Python installed. CLOSE this window and run this file again.
  pause
  exit /b
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg not found. Installing via winget...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  echo.
  echo FFmpeg installed. CLOSE this window and run this file again.
  pause
  exit /b
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install faster-whisper numpy tkinterdnd2
echo.
echo Done. Now run 0_SETTINGS_AND_RUN.bat
pause
