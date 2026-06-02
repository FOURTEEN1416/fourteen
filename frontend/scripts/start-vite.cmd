@echo off
setlocal
cd /d "%~dp0.."
set VITE_DEV_PORT=5199
set VITE_API_BASE=http://localhost:8000
start /B "" "%LOCALAPPDATA%\bun\bin\bun.exe" run dev
echo Vite starting on port 5199...
