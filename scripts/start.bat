@echo off
chcp 65001 >nul
title 十四 - AI虚拟伴侣

echo ============================================
echo   十四 — AI 虚拟伴侣 启动脚本
echo   Windows 11 / Python 版
echo ============================================
echo.

:: 切换到项目目录
cd /d "%~dp0.."

:: 检查虚拟环境
if exist venv\Scripts\activate.bat (
    echo [1/3] 激活虚拟环境...
    call venv\Scripts\activate.bat
) else (
    echo [1/3] 未找到虚拟环境，使用系统 Python
)

:: 检查 .env 中的 API Key
if exist .env (
    echo [2/3] 发现 .env 文件，加载环境变量...
    for /f "usebackq tokens=*" %%a in (.env) do set "%%a"
)

:: 检查依赖
echo [3/3] 检查依赖...
python -c "import chromadb, yaml, apscheduler, httpx" 2>nul
if %errorlevel% neq 0 (
    echo   部分依赖未安装，正在安装...
    pip install chromadb pyyaml apscheduler httpx
)

echo.
echo ============================================
echo   请选择启动模式:
echo   1. 控制台聊天模式（默认）
echo   2. 微信模式（扫码登录）
echo ============================================
echo.

set /p MODE="选择 (1/2): "

if "%MODE%"=="2" (
    echo.
    echo   正在启动微信模式...
    echo   请用微信扫码登录
    timeout /t 3 >nul
    python main.py
) else (
    echo.
    echo   启动控制台聊天模式...
    echo   输入 /quit 退出  /reset 重置记忆  /status 查看状态
    echo.
    python main.py --console
)

pause
