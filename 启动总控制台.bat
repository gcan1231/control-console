@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B tools\local_server.py %*
) else (
  python -B tools\local_server.py %*
)
if errorlevel 1 pause
