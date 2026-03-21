@echo off
REM Запуск API (8000) + Vite (5173). Браузер: http://localhost:5173
cd /d "%~dp0"
python scripts\run_web_ui.py
if errorlevel 1 pause
