@echo off
:: 唯一的你 — 前后端一键启动
:: 前端 :5173 | 后端 API :8000

echo ============================================
echo   唯一的你 — 启动中...
echo ============================================
echo.

echo [1/2] 启动后端 API (端口 8000)...
start "唯一你-API" cmd /c "cd /d %~dp0 && python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000 --reload --log-level info 2>&1 | python scripts\_filter_log.py"

echo [2/2] 启动前端开发服务器 (端口 5173)...
start "唯一你-前端" cmd /c "cd /d %~dp0frontend && D:\node.exe .\node_modules\vite\bin\vite.js --port 5173 --host"

echo.
echo ============================================
echo   启动完成！
echo   前端: http://localhost:5173
echo   API : http://localhost:8000/docs
echo ============================================
echo.
pause
