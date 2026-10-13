@echo off
cd /d "%~dp0"
python tools\preview_server.py %*
if errorlevel 1 pause
