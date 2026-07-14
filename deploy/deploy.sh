#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 唯一的你 — Deployment Script
# ═══════════════════════════════════════════════════════════
# Usage: sudo bash deploy/deploy.sh
# Run from: /opt/ai-girlfriend
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
log "[1/6] Pulling latest code from git..."
cd "${APP_DIR}"
# 安全建议: 配置 Git 提交签名验证（GPG），确保代码来源可信
# git config commit.gpgsign true
git fetch origin
git checkout main
git pull origin main
log "[1/6] Git pull complete."

# ── Step 2: Install/update Python dependencies ──
log "[2/6] Installing Python dependencies..."
if [ ! -d "${VENV}" ]; then
    log "Creating virtual environment..."
    python3 -m venv "${VENV}"
fi
source "${VENV}/bin/activate"
pip install --upgrade pip
pip install -e "${APP_DIR}"
log "[2/6] Python dependencies installed."

# ── Step 3: Build frontend ──
log "[3/6] Building frontend..."
if [ -d "${FRONTEND_SRC}" ]; then
    cd "${FRONTEND_SRC}"
    npm ci 2>/dev/null || npm install
    npm run build
    log "[3/6] Frontend build complete."
else
    log "[3/6] WARNING: Frontend source not found at ${FRONTEND_SRC}, skipping."
fi

# ── Step 4: Copy frontend to Nginx serve directory ──
log "[4/6] Copying frontend build to Nginx serve directory..."
if [ -d "${FRONTEND_DIST}" ]; then
    mkdir -p "${NGINX_SERVE}"
    rsync -a --delete "${FRONTEND_DIST}/" "${NGINX_SERVE}/"
    log "[4/6] Frontend copied to ${NGINX_SERVE}."
else
    log "[4/6] WARNING: Frontend dist not found, skipping copy."
fi

# ── Step 5: Run database migrations (if any) ──
log "[5/6] Running database migrations..."
source "${VENV}/bin/activate"
if python -c "import alembic" 2>/dev/null; then
    alembic upgrade head
    log "[5/6] Alembic migrations complete."
else
    log "[5/6] Alembic not installed, running table sync if available..."
    python -c "from api.database import init_db; import asyncio; asyncio.run(init_db())" 2>/dev/null || true
    log "[5/6] DB sync attempted."
fi

# ── Step 6: Restart services ──
log "[6/6] Restarting services..."
systemctl daemon-reload
systemctl restart ai-girlfriend
log "[6/6] Backend service restarted."

# Reload Nginx (test config first)
if nginx -t 2>/dev/null; then
    nginx -s reload
    log "[6/6] Nginx reloaded."
else
    log "[6/6] WARNING: Nginx config test failed, skipping reload."
fi

log "=== 唯一的你 Deployment Completed Successfully ==="
log "Check status: systemctl status ai-girlfriend"
log "Check logs:   journalctl -u ai-girlfriend -f"
