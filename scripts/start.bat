@echo off
chcp 65001 >nul
title 小暖 - AI伴侣女友

echo ============================================
echo   💕 小暖 — AI 伴侣女友 启动脚本
echo   Windows 11 / Python 版
echo ============================================
echo.

:: 切换到项目目录
cd /d "%~dp0.."

:: 检查虚拟环境
if exist venv\Scripts\activate.bat (
    echo [1/5] 激活虚拟环境...
    call venv\Scripts\activate.bat
) else (
    echo [1/5] 未找到虚拟环境，使用系统 Python
)

:: 检查 .env 中的 API Key
if exist .env (
    echo [2/5] 发现 .env 文件，加载环境变量...
    for /f "usebackq tokens=*" %%a in (.env) do set "%%a"
)

:: 检查依赖
echo [3/5] 检查依赖...
python -c "import chromadb, yaml, apscheduler, httpx" 2>nul
if %errorlevel% neq 0 (
    echo   ⚠️ 部分依赖未安装，正在安装...
    pip install chromadb pyyaml apscheduler httpx
)

:: 运行验证
echo [4/5] 运行验证...
python scripts\verify_all.py
if %errorlevel% neq 0 (
    echo.
    echo   ⚠️ 部分验证未通过，继续启动...
)

echo [5/5] 选择启动模式...
echo.
echo ============================================
echo   请选择启动模式:
echo   1. 控制台聊天模式（默认）
echo   2. 微信模式（需要配置 CowAgent）
echo   3. 运行全量验证后退出
echo   4. 风格克隆训练（从聊天记录学习风格）
echo ============================================
echo.

set /p MODE="选择 (1/2/3): "

if "%MODE%"=="2" (
    echo.
    echo   正在启动微信模式...
    echo   请确保已配置 config\cowagent_config.json 中的 API KEY
    timeout /t 3 >nul
    python main.py
) else if "%MODE%"=="3" (
    echo.
    echo   全量验证完成，按任意键退出
    pause
    exit /b 0
) else (
    echo.
    echo   启动控制台聊天模式...
    echo   输入 /quit 退出  /reset 重置记忆  /status 查看状态
    echo.
    python main.py --no-wechat
)

pause
