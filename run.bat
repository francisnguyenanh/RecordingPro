@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
start /b pythonw tray.py
timeout /t 10 >nul
start http://127.0.0.1:5012