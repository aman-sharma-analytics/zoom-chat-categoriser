@echo off
rem Zoom Chat Categoriser -- local only, nothing leaves this machine.
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
echo Starting Zoom Chat Categoriser at http://127.0.0.1:8010  (Ctrl+C to stop)
start "" http://127.0.0.1:8010
"%PY%" -m uvicorn app:app --host 127.0.0.1 --port 8010
