@echo off
:: 唯一的你 — 仅前端
cd /d %~dp0frontend
npm run dev -- --port 5173 --host
