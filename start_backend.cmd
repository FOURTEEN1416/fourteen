@echo off
:: 十四 AI Girlfriend — 仅后端 API
cd /d %~dp0
python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000 --reload --log-level info
