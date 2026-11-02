@echo off
cd /d "%~dp0"
if not exist "%~dp0voice_clean.py" (echo ERROR: run this file from the EXTRACTED folder, not from inside the zip. & echo Right click the zip, Extract All, then open the extracted folder. & pause & exit /b 1)
if not exist ".venv\Scripts\pythonw.exe" (echo Run 1_INSTALL.bat first. & pause & exit /b 1)
start "" ".venv\Scripts\pythonw.exe" "%~dp0launcher.py"
