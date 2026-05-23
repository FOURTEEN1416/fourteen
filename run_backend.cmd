@echo off
cd /d "C:\Users\FOUR\Desktop\ai-girlfriend"
set PYTHONIOENCODING=utf-8
python main.py --no-wechat --no-scheduler > backend_output.log 2>&1
