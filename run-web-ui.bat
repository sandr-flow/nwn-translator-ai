@echo off
REM Launch API (8000) + Vite dev server (5173) and open browser.
cd /d "%~dp0"

start "NWN API" .venv\Scripts\nwn-translate.exe web --reload
timeout /t 2 /nobreak >nul
start http://localhost:5173
cd frontend
npm run dev
