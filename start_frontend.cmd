@echo off
:: 唯一的你 — 仅前端
cd /d %~dp0frontend
D:\node.exe .\node_modules\vite\bin\vite.js --port 5173 --host
