@echo off
:: 十四 AI Girlfriend — 仅后端 API
:: 输出经过 _filter_log.py 过滤，扣掉 onnxruntime EP Error 噪声
cd /d %~dp0
python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000 --reload --log-level info 2>&1 | python scripts\_filter_log.py
