#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 唯一的你 — Production Start Script
# ═══════════════════════════════════════════════════════════
# Usage: sudo bash deploy/start.sh
# Run from: /opt/ai-girlfriend
#
# W10 失败门禁（2026-09-27）：
#   - DB 检查用真实引擎（api.database._engine）执行 SELECT 1，失败退出非 0——
#     旧脚本导入不存在的会话工厂符号，异常后 exit(0)，检查形同虚设；
#   - 迁移失败（alembic / init_db）直接中止，不再吞错；
#   - 启动探针打 /api/ready（旧脚本探测不存在的 /health，失败也只 warning
#     仍以 0 退出）——发布成功判据 = ready 200。
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
    log "ERROR: .env file not found at ${ENV_FILE}"
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
# 真实连接检查：经生产同款异步引擎执行 SELECT 1；失败退出非 0。
log "[1/5] Checking database connection..."
source "${VENV}/bin/activate"
if ! python -c "
import asyncio
from sqlalchemy import text
from api.database import _engine

async def _probe():
    async with _engine.connect() as conn:
        await conn.execute(text('SELECT 1'))

asyncio.run(_probe())
print('Database connection OK')
"; then
    log "ERROR: Database connection check failed (engine: \${APP_DATABASE_URL:-DATABASE_URL:-default sqlite})"
    log "Fix DATABASE_URL / APP_DATABASE_URL in ${ENV_FILE} before starting."
    exit 1
fi
log "[1/5] Database check complete."

# ── Step 2: Run migrations ──
log "[2/5] Running database migrations..."
if python -c "import alembic" 2>/dev/null; then
    alembic upgrade head
    log "[2/5] Migrations applied."
else
    log "[2/5] Alembic not installed; running init_db (table sync)..."
    # 迁移失败必须阻断启动（旧实现吞错误后照常起服务）
    python -c "
import asyncio
from api.database import init_db
asyncio.run(init_db())
"
    log "[2/5] Tables synced."
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

# ── Step 5: Readiness gate ──
# 发布成功判据 = /api/ready 200（含真实 DB/记忆/模型配置探测与关键路由组存在性）。
# 失败退出非 0——绝不带病宣布启动成功。
log "[5/5] Waiting for /api/ready..."
READY=false
for i in $(seq 1 30); do
    sleep 2
    code="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/ready 2>/dev/null || true)"
    if [ "${code}" = "200" ]; then
        READY=true
        log "Backend ready (attempt ${i})"
        break
    fi
    log "Waiting for backend /api/ready... (attempt ${i}/30, last code=${code})"
done

if [ "${READY}" = true ]; then
    log "=========================================="
    log "唯一的你 is running!"
    log "  API:   http://127.0.0.1:8000"
    log "  Ready: http://127.0.0.1:8000/api/ready"
    log "  Docs:  http://127.0.0.1:8000/docs"
    log "  PID:   ${UVICORN_PID}"
    log "=========================================="
    log ""
    log "To check logs:  tail -f ${APP_DIR}/logs/uvicorn.log"
    log "To stop:        kill \$(cat ${APP_DIR}/logs/uvicorn.pid)"
else
    log "ERROR: /api/ready did not return 200 within the wait window."
    log "Check logs: tail -f ${APP_DIR}/logs/uvicorn.log"
    log "Check config: ${ENV_FILE}"
    exit 1
fi
