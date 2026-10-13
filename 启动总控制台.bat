@echo off
cd /d "%~dp0"
python tools\local_server.py %*
if errorlevel 1 pause
