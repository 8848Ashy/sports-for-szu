@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto failed
echo Setup complete. Run start.cmd.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.11 or newer and check your network.
pause
exit /b 1
