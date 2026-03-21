@echo off
REM Launch API (8000) + Vite dev server (5173). Browser: http://localhost:5173
cd /d "%~dp0"
start "NWN API" nwn-translate web --reload
cd frontend
npm run dev
