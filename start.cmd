@echo off
cd /d "%~dp0"
set "SZU_RUNTIME=%LOCALAPPDATA%\SZU-Runtime"
if not exist "%SZU_RUNTIME%\Scripts\pythonw.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
start "" "%SZU_RUNTIME%\Scripts\pythonw.exe" run.py
