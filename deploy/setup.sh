#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# AI Girlfriend — Ubuntu Server Initial Setup
# ═══════════════════════════════════════════════════════════
# Usage: sudo bash deploy/setup.sh
# Run on: Fresh Ubuntu 22.04+ server
# Idempotent: Safe to re-run
# ═══════════════════════════════════════════════════════════

set -euo pipefail

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

if [ "$EUID" -ne 0 ]; then
    error "Please run as root (sudo bash deploy/setup.sh)"
    exit 1
fi

# ═══════════════════════════════════════════════════════════════
# Configuration (edit these for your environment)
# ═══════════════════════════════════════════════════════════════

APP_DIR="/opt/ai-girlfriend"
APP_USER="www-data"  # matches Nginx user; change if needed
DB_NAME="ai_girlfriend"
DB_USER="ai_girlfriend"
DB_PASS="$(openssl rand -base64 24)"  # auto-generated, save this
DOMAIN="ai-girlfriend.example.com"     # CHANGE THIS

# ═══════════════════════════════════════════════════════════════
# Step 1: System Packages
# ═══════════════════════════════════════════════════════════════

info "=== Step 1/7: Updating system packages ==="
apt-get update -qq
apt-get upgrade -y -qq

info "Installing required packages..."
apt-get install -y -qq \
    python3.12-venv \
    python3.12-dev \
    nginx \
    postgresql \
    postgresql-client \
    certbot \
    python3-certbot-nginx \
    git \
    curl \
    wget \
    rsync \
    ufw

# Install Node.js 22 LTS
if ! command -v node &> /dev/null; then
    info "Installing Node.js 22 LTS..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs
else
    info "Node.js already installed: $(node --version)"
fi

info "Step 1/7 complete."

# ═══════════════════════════════════════════════════════════════
# Step 2: Application Directory
# ═══════════════════════════════════════════════════════════════

info "=== Step 2/7: Setting up application directory ==="

if [ ! -d "${APP_DIR}" ]; then
    mkdir -p "${APP_DIR}"
    info "Created ${APP_DIR}"
else
    info "${APP_DIR} already exists."
fi

# Create data subdirectories
mkdir -p "${APP_DIR}/data" "${APP_DIR}/cache" "${APP_DIR}/logs"

# Clone repo if empty
if [ -z "$(ls -A "${APP_DIR}" 2>/dev/null)" ]; then
    info "Cloning repository..."
    git clone https://github.com/FOURTEEN1416/ai-girlfriend.git "${APP_DIR}"
else
    info "App directory not empty, assuming repository already cloned."
fi

info "Setting permissions for ${APP_USER}..."
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod -R 755 "${APP_DIR}"
info "Step 2/7 complete."

# ═══════════════════════════════════════════════════════════════
# Step 3: Python Virtual Environment
# ═══════════════════════════════════════════════════════════════

info "=== Step 3/7: Setting up Python virtual environment ==="

VENV="${APP_DIR}/.venv"
if [ ! -d "${VENV}" ]; then
    python3 -m venv "${VENV}"
    info "Created virtual environment at ${VENV}"
else
    info "Virtual environment already exists."
fi

source "${VENV}/bin/activate"
pip install --upgrade pip -q
pip install -e "${APP_DIR}" -q
info "Python dependencies installed."
info "Step 3/7 complete."

# ═══════════════════════════════════════════════════════════════
# Step 4: PostgreSQL Setup
# ═══════════════════════════════════════════════════════════════

info "=== Step 4/7: Setting up PostgreSQL ==="

# Start PostgreSQL if not running
if ! pg_isready -q 2>/dev/null; then
    systemctl start postgresql
    systemctl enable postgresql
    info "PostgreSQL started."
else
    info "PostgreSQL already running."
fi

# Create database user
if sudo -u postgres psql -t -c "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
    info "Database user '${DB_USER}' already exists."
else
    sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"
    info "Database user '${DB_USER}' created."
fi

# Create database
if sudo -u postgres psql -t -c "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    info "Database '${DB_NAME}' already exists."
else
    sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
    info "Database '${DB_NAME}' created."
fi

# Grant privileges
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"

info "PostgreSQL setup complete."
info "Database: ${DB_NAME}, User: ${DB_USER}, Password: ${DB_PASS}"
info "Step 4/7 complete."

# ═══════════════════════════════════════════════════════════════
# Step 5: Nginx Configuration
# ═══════════════════════════════════════════════════════════════

info "=== Step 5/7: Configuring Nginx ==="

# Create frontend serve directory
mkdir -p /var/www/ai-girlfriend

# Copy nginx config if present
if [ -f "${APP_DIR}/deploy/nginx.conf" ]; then
    cp "${APP_DIR}/deploy/nginx.conf" /etc/nginx/sites-available/ai-girlfriend
    # Replace example domain placeholder
    sed -i "s/ai-girlfriend\.example\.com/${DOMAIN}/g" /etc/nginx/sites-available/ai-girlfriend
else
    warn "deploy/nginx.conf not found, skipping."
fi

# Enable site
if [ ! -L /etc/nginx/sites-enabled/ai-girlfriend ]; then
    ln -sf /etc/nginx/sites-available/ai-girlfriend /etc/nginx/sites-enabled/ai-girlfriend
    # Remove default if it conflicts
    rm -f /etc/nginx/sites-enabled/default
fi

# Test and reload
if nginx -t; then
    systemctl reload nginx || systemctl start nginx
    info "Nginx configured and running."
else
    error "Nginx config test failed. Check manually."
fi
info "Step 5/7 complete."

# ═══════════════════════════════════════════════════════════════
# Step 6: Systemd Service
# ═══════════════════════════════════════════════════════════════

info "=== Step 6/7: Installing systemd service ==="

if [ -f "${APP_DIR}/deploy/ai-girlfriend-backend.service" ]; then
    cp "${APP_DIR}/deploy/ai-girlfriend-backend.service" /etc/systemd/system/ai-girlfriend-backend.service
    systemctl daemon-reload
    systemctl enable ai-girlfriend-backend
    systemctl start ai-girlfriend-backend
    info "systemd service installed and started."
else
    warn "deploy/ai-girlfriend-backend.service not found, skipping."
fi
info "Step 6/7 complete."

# ═══════════════════════════════════════════════════════════════
# Step 7: Firewall & SSL Placeholder
# ═══════════════════════════════════════════════════════════════

info "=== Step 7/7: Configuring firewall ==="

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable
info "UFW configured: SSH, HTTP, HTTPS allowed."

info ""
info "=== SSL Setup (run after DNS is pointed) ==="
info "  sudo certbot --nginx -d ${DOMAIN}"
info ""
info "=== Setup Complete! ==="
info ""
info "Next steps:"
info "  1. Edit ${APP_DIR}/.env with production values"
info "     (see deploy/.env.production for template)"
info "  2. Set DATABASE_URL: postgresql://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}"
info "  3. Point DNS: ${DOMAIN} → this server's IP"
info "  4. Run certbot for SSL: sudo certbot --nginx -d ${DOMAIN}"
info "  5. Run deployment: sudo bash ${APP_DIR}/deploy/deploy.sh"
info ""
info "PostgreSQL password: ${DB_PASS}"
info "Save this password securely!"
