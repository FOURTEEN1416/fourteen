#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 唯一的你 — Remote Build & Restart Script
# ═══════════════════════════════════════════════════════════
# Usage (after code is synced to server):
#   ssh "${DEPLOY_USER:-deploy}@${DEPLOY_HOST:?set DEPLOY_HOST}" \
#     "bash ${APP_DIR:-/opt/ai-girlfriend}/deploy/remote_deploy.sh"
# 安全建议:
#   - 使用专用部署用户（如 deploy）而非 root 进行 SSH 登录
#   - 配置 SSH 密钥认证，禁用 root SSH 登录
#   - 公开仓库勿写死真实生产 IP/账号/端口；部署目标用 DEPLOY_HOST/DEPLOY_USER 注入
#   - 若历史版本曾暴露主机 IP 或凭据，请轮换相关凭据并复查暴露面
# ═══════════════════════════════════════════════════════════

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ai-girlfriend}"
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