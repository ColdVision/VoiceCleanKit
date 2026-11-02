@echo off
cd /d "%~dp0"
if not exist "%~dp0voice_clean.py" (echo ERROR: run this file from the EXTRACTED folder, not from inside the zip. & echo Right click the zip, Extract All, then open the extracted folder. & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (echo Run 1_INSTALL.bat first. & pause & exit /b 1)
if "%~1"=="" (echo Drag an audio or video file onto this file. & pause & exit /b 1)
set PYTHONUTF8=1
".venv\Scripts\python.exe" make_srt.py "%~1" --model small --lang ru
echo.
echo Subtitles are in the projects folder.
pause
