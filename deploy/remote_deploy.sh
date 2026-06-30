#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 唯一的你 — Remote Build & Restart Script
# ═══════════════════════════════════════════════════════════
# Usage (after code is synced to server):
#   ssh root@139.199.199.174 "bash /opt/ai-girlfriend/deploy/remote_deploy.sh"
# ═══════════════════════════════════════════════════════════

set -euo pipefail

APP_DIR="/opt/ai-girlfriend"
FRONTEND_DIR="${APP_DIR}/frontend"
VENV="${APP_DIR}/.venv"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== Remote build & restart started ==="

cd "${APP_DIR}"

log "[1/4] Installing / updating Python package..."
source "${VENV}/bin/activate"
pip install -e "${APP_DIR}"

log "[2/4] Installing frontend dependencies..."
cd "${FRONTEND_DIR}"
npm ci

log "[3/4] Building frontend..."
npm run build

log "[4/4] Restarting services..."
systemctl restart ai-girlfriend

if nginx -t >/dev/null 2>&1; then
    nginx -s reload
    log "Nginx reloaded."
else
    log "WARNING: nginx config test failed, skipping reload."
fi

log "=== Remote build & restart completed ==="
