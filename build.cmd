@echo off
cd /d "%~dp0"
set "SZU_RUNTIME=%LOCALAPPDATA%\SZU-Runtime"
if not exist "%SZU_RUNTIME%\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
"%SZU_RUNTIME%\Scripts\python.exe" -m pip install pyinstaller==6.22.3
if errorlevel 1 exit /b 1
"%SZU_RUNTIME%\Scripts\python.exe" tools\build_release.py
pause
