@echo off
:: 唯一的你 — 一键部署入口（调用 PowerShell 脚本）
:: 用法：双击运行 或 在命令行执行 deploy_ai_girlfriend.bat
powershell -ExecutionPolicy Bypass -File "%~dp0deploy_ai_girlfriend.ps1" %*
pause
