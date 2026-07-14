#!/bin/bash
# Start ai-girlfriend backend service
set -e
cd /opt/ai-girlfriend
pkill -f 'uvicorn api.run_api' 2>/dev/null || true
sleep 1
echo "" > /var/log/ai-girlfriend.log
source .venv/bin/activate
nohup python -m uvicorn api.run_api:app --host 127.0.0.1 --port 8000 --workers 1 > /var/log/ai-girlfriend.log 2>&1 &
echo "PID=$!"
sleep 6
ps -ef | grep uvicorn | grep -v grep || echo "NO UVICORN PROCESS"
echo "--- LOG ---"
tail -40 /var/log/ai-girlfriend.log
echo "--- HEALTH ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8000/health || echo "curl failed"
