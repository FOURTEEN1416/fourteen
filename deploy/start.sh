#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 唯一的你 — Production Start Script
# ═══════════════════════════════════════════════════════════
# Usage: sudo bash deploy/start.sh
# Run from: /opt/ai-girlfriend
# ═══════════════════════════════════════════════════════════

set -euo pipefail

APP_DIR="/opt/ai-girlfriend"
VENV="${APP_DIR}/.venv"
NGINX_SERVE="/opt/ai-girlfriend/frontend/dist"
ENV_FILE="${APP_DIR}/.env"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ── Prerequisites check ──
if [ ! -d "${VENV}" ]; then
    log "ERROR: Virtual environment not found at ${VENV}"
    log "Run deploy/setup.sh first, or create venv: python3 -m venv ${VENV}"
    exit 1
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "WARNING: .env file not found at ${ENV_FILE}"
    log "Copy deploy/.env.production to ${ENV_FILE} and fill in values."
    exit 1
fi

if [ ! -d "${APP_DIR}" ]; then
    log "ERROR: Application directory not found: ${APP_DIR}"
    exit 1
fi

# ── Source environment ──
set -a
source "${ENV_FILE}"
set +a

log "=== Starting 唯一的你 (Production) ==="

# ── Step 1: Verify database connection ──
log "[1/5] Checking database connection..."
source "${VENV}/bin/activate"
if python -c "
import asyncio
try:
    from api.database import AsyncSessionLocal
    print('Database module loaded successfully')
except Exception as e:
    print(f'Database not configured: {e}')
    exit(0)  # non-fatal, DB might be optional in dev mode
" 2>&1; then
    log "[1/5] Database check complete."
else
    log "[1/5] Database check skipped (non-fatal)."
fi

# ── Step 2: Run migrations ──
log "[2/5] Running database migrations..."
if python -c "import alembic" 2>/dev/null; then
    alembic upgrade head
    log "[2/5] Migrations applied."
else
    log "[2/5] Alembic not installed; attempting init_db..."
    python -c "
import asyncio
from api.database import init_db
asyncio.run(init_db())
" 2>/dev/null && log "[2/5] Tables synced." || log "[2/5] DB init skipped."
fi

# ── Step 3: Ensure Nginx serve directory exists ──
log "[3/5] Verifying frontend static files..."
if [ ! -d "${NGINX_SERVE}" ] || [ -z "$(ls -A "${NGINX_SERVE}" 2>/dev/null)" ]; then
    log "Frontend not deployed. Run deploy/deploy.sh first."
    log "Continuing with API-only mode..."
fi
log "[3/5] Frontend check complete."

# ── Step 4: Start backend with uvicorn ──
log "[4/5] Starting FastAPI backend..."
cd "${APP_DIR}"

# Start as background process (systemd should be used normally)
uvicorn api.run_api:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --log-level info \
    --proxy-headers \
    --forwarded-allow-ips="127.0.0.1" \
    >> "${APP_DIR}/logs/uvicorn.log" 2>&1 &

UVICORN_PID=$!
echo $UVICORN_PID > "${APP_DIR}/logs/uvicorn.pid"
log "Backend started (PID: ${UVICORN_PID})"

# ── Step 5: Health check loop ──
log "[5/5] Running health checks..."
HEALTHY=false
for i in $(seq 1 12); do
    sleep 2
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health 2>/dev/null | grep -q "200"; then
        HEALTHY=true
        log "Backend health check passed (attempt ${i})"
        break
    fi
    log "Waiting for backend... (attempt ${i}/12)"
done

if [ "${HEALTHY}" = true ]; then
    log "=========================================="
    log "唯一的你 is running!"
    log "  API:  http://127.0.0.1:8000"
    log "  Docs: http://127.0.0.1:8000/docs"
    log "  PID:  ${UVICORN_PID}"
    log "=========================================="
    log ""
    log "To check logs:  tail -f ${APP_DIR}/logs/uvicorn.log"
    log "To stop:        kill \$(cat ${APP_DIR}/logs/uvicorn.pid)"
else
    log "WARNING: Backend health check failed after 12 attempts"
    log "Check logs: tail -f ${APP_DIR}/logs/uvicorn.log"
    log "Check config: ${ENV_FILE}"
fi
