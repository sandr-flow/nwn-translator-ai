@echo off
REM Launch API (8000) + Vite dev server (5173) and open browser.
REM No auto-reload: a reload restart would kill a translation in progress.
cd /d "%~dp0"

start "NWN API" .venv\Scripts\python.exe -m nwn_translator.web
start "NWN UI" cmd /c "cd /d "%~dp0frontend" && npm run dev"

REM Wait for the API before opening the browser: cold imports take 10-15s,
REM and a page opened earlier shows an empty UI until it is reloaded.
echo Waiting for the API on http://127.0.0.1:8000 ...
for /l %%i in (1,1,30) do (
  curl -s -o nul --max-time 2 http://127.0.0.1:8000/api/health >nul 2>&1 && goto api_up
  timeout /t 1 /nobreak >nul
)
echo API did not answer within 30s, opening the UI anyway.
:api_up
start http://localhost:5173
