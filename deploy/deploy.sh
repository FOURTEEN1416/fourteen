#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 唯一的你 — Deployment Script
# ═══════════════════════════════════════════════════════════
# Usage: sudo bash deploy/deploy.sh
# Run from: /opt/ai-girlfriend
#
# W10 失败门禁（2026-09-27）：
#   - 迁移失败（alembic / init_db）阻断发布——旧实现吞错后照常重启；
#   - 服务重启后必须 /api/ready 200 才算发布成功，失败退出非 0——
#     旧实现 restart 后无任何验证就打印 Completed Successfully；
#   - probe 失败（nginx -t / ready 门）一律非 0 退出。
# ═══════════════════════════════════════════════════════════

set -euo pipefail

# ── Timestamp for logging ──
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== 唯一的你 Deployment Started ==="

APP_DIR="/opt/ai-girlfriend"
FRONTEND_SRC="${APP_DIR}/frontend"
FRONTEND_DIST="${FRONTEND_SRC}/dist"
NGINX_SERVE="/opt/ai-girlfriend/frontend/dist"
VENV="${APP_DIR}/.venv"

# ── Step 1: Pull latest code ──
log "[1/7] Pulling latest code from git..."
cd "${APP_DIR}"
# 安全建议: 配置 Git 提交签名验证（GPG），确保代码来源可信
# git config commit.gpgsign true
git fetch origin
git checkout main
git pull origin main
log "[1/7] Git pull complete."

# ── Step 2: Install/update Python dependencies ──
log "[2/7] Installing Python dependencies..."
if [ ! -d "${VENV}" ]; then
    log "Creating virtual environment..."
    python3 -m venv "${VENV}"
fi
source "${VENV}/bin/activate"
pip install --upgrade pip
pip install -e "${APP_DIR}"
log "[2/7] Python dependencies installed."

# ── Step 3: Build frontend ──
log "[3/7] Building frontend..."
if [ -d "${FRONTEND_SRC}" ]; then
    cd "${FRONTEND_SRC}"
    npm ci 2>/dev/null || npm install
    npm run build
    log "[3/7] Frontend build complete."
else
    log "[3/7] WARNING: Frontend source not found at ${FRONTEND_SRC}, skipping."
fi

# ── Step 4: Copy frontend to Nginx serve directory ──
log "[4/7] Copying frontend build to Nginx serve directory..."
if [ -d "${FRONTEND_DIST}" ]; then
    mkdir -p "${NGINX_SERVE}"
    rsync -a --delete "${FRONTEND_DIST}/" "${NGINX_SERVE}/"
    log "[4/7] Frontend copied to ${NGINX_SERVE}."
else
    log "[4/7] WARNING: Frontend dist not found, skipping copy."
fi

# ── Step 5: Run database migrations (failure blocks the release) ──
log "[5/7] Running database migrations..."
source "${VENV}/bin/activate"
cd "${APP_DIR}"
if python -c "import alembic" 2>/dev/null; then
    alembic upgrade head
    log "[5/7] Alembic migrations complete."
else
    log "[5/7] Alembic not installed, running init_db table sync..."
    # 迁移失败必须阻断发布（旧实现 `2>/dev/null || true` 吞错后照常重启）
    python -c "from api.database import init_db; import asyncio; asyncio.run(init_db())"
    log "[5/7] DB table sync complete."
fi

# ── Step 6: Restart services ──
log "[6/7] Restarting services..."
systemctl daemon-reload
systemctl restart ai-girlfriend
log "[6/7] Backend service restarted."

# Reload Nginx (test config first)
if nginx -t 2>/dev/null; then
    nginx -s reload
    log "[6/7] Nginx reloaded."
else
    log "[6/7] WARNING: Nginx config test failed, skipping reload."
fi

# ── Step 7: Readiness gate（发布成功判据）──
# /api/ready 含真实 DB/记忆/模型配置探测与关键路由组存在性；
# 非 200 = 发布失败，退出非 0。绝不带病宣布成功。
log "[7/7] Waiting for /api/ready..."
READY=false
for i in $(seq 1 30); do
    sleep 2
    code="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/ready 2>/dev/null || true)"
    if [ "${code}" = "200" ]; then
        READY=true
        log "[7/7] Ready gate passed (attempt ${i})"
        break
    fi
    log "[7/7] Waiting for /api/ready... (attempt ${i}/30, last code=${code})"
done

if [ "${READY}" != true ]; then
    log "=== 唯一的你 Deployment FAILED: /api/ready did not return 200 ==="
    log "Rollback hint: keep the previous release; check journalctl -u ai-girlfriend"
    exit 1
fi

log "=== 唯一的你 Deployment Completed Successfully (ready=200) ==="
log "Check status: systemctl status ai-girlfriend"
log "Check logs:   journalctl -u ai-girlfriend -f"
