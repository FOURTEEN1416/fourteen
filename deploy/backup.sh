#!/usr/bin/env bash
# =============================================================================
# 唯一的你 PostgreSQL Database Backup Script
# Environment: Linux (Ubuntu/Debian/CentOS)
# Features:
#   1. Parses DATABASE_URL or reads from .env file
#   2. Uses pg_dump with gzip compression
#   3. Auto-cleanup backups older than 30 days
#   4. Supports --test flag for connection testing only
# Usage:
#   ./backup.sh              # Run backup
#   ./backup.sh --test       # Test connection only
#   DB_BACKUP_DIR=/data/backups ./backup.sh  # Custom backup dir
# =============================================================================

set -euo pipefail

# -------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# -------------------------------------------------------------------------
# Backup directory, defaults to /opt/unique-you/backups
BACKUP_DIR="${DB_BACKUP_DIR:-/opt/unique-you/backups}"

# Backup retention in days (default 30)
RETENTION_DAYS="${DB_RETENTION_DAYS:-30}"

# Log file (same directory as backups)
LOG_FILE="${BACKUP_DIR}/backup.log"

# DATABASE_URL (prefer env var, fallback to .env file)
DATABASE_URL="${DATABASE_URL:-}"

# Test mode flag
TEST_MODE=false

# -------------------------------------------------------------------------
# Parse command-line arguments
# -------------------------------------------------------------------------
for arg in "$@"; do
    case $arg in
        --test)
            TEST_MODE=true
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [--test]"
            exit 1
            ;;
    esac
done

# -------------------------------------------------------------------------
# Log functions: timestamp + level + message
# -------------------------------------------------------------------------
log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2
}

log_warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] $*"
}

# -------------------------------------------------------------------------
# Initialize: create backup directory if not exists
# -------------------------------------------------------------------------
init_dirs() {
    if [ ! -d "$BACKUP_DIR" ]; then
        mkdir -p "$BACKUP_DIR"
        log_info "Created backup directory: $BACKUP_DIR"
    fi
}

# -------------------------------------------------------------------------
# Load DATABASE_URL from .env file
# Search order: script parent dir > BACKUP_DIR parent dir > current dir
# -------------------------------------------------------------------------
load_env() {
    # If env var is already set, use it directly
    if [ -n "$DATABASE_URL" ]; then
        return 0
    fi

    # Try to find .env in various locations
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local env_file=""

    for candidate in "${script_dir}/../.env" "${BACKUP_DIR}/../.env" ".env"; do
        if [ -f "$candidate" ]; then
            env_file="$candidate"
            break
        fi
    done

    if [ -z "$env_file" ]; then
        log_error "No .env file found and DATABASE_URL is not set."
        log_error "Please set DATABASE_URL or place a .env file in:"
        log_error "  - ${script_dir}/../.env"
        log_error "  - ${BACKUP_DIR}/../.env"
        log_error "  - current directory"
        exit 1
    fi

    log_info "Loading DATABASE_URL from $env_file"

    # Extract DATABASE_URL value (ignore comments, strip quotes)
    local raw_value
    raw_value="$(grep -E '^DATABASE_URL=' "$env_file" | head -1 | sed 's/^DATABASE_URL=//')"

    if [ -z "$raw_value" ]; then
        log_error "DATABASE_URL not found in $env_file"
        exit 1
    fi

    # Strip surrounding quotes (single or double)
    DATABASE_URL="$(echo "$raw_value" | sed -e "s/^'//" -e "s/'$//" -e 's/^"//' -e 's/"$//')"
}

# -------------------------------------------------------------------------
# Parse DATABASE_URL into pg_dump connection parameters
# Format: postgresql://user:password@host:port/database
# -------------------------------------------------------------------------
parse_db_url() {
    DB_USER="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://\([^:]*\):.*|\1|p')"
    DB_PASS="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^:]*:\([^@]*\)@.*|\1|p')"
    DB_HOST="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^@]*@\([^:]*\):.*|\1|p')"
    DB_PORT="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^@]*@[^:]*:\([^/]*\)/.*|\1|p')"
    DB_NAME="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^@]*@[^:]*:[^/]*/\(.*\)|\1|p')"

    # Default port to 5432 if not specified
    if [ -z "$DB_PORT" ]; then
        DB_PORT="5432"
    fi

    # Validate parsed components
    if [ -z "$DB_USER" ] || [ -z "$DB_HOST" ] || [ -z "$DB_NAME" ]; then
        log_error "Failed to parse DATABASE_URL. Please check the format."
        log_error "Expected format: postgresql://user:password@host:port/database"
        exit 1
    fi

    log_info "Database: $DB_NAME, Host: $DB_HOST:$DB_PORT, User: $DB_USER"
}

# -------------------------------------------------------------------------
# Test database connection (via pg_isready or SELECT 1)
# -------------------------------------------------------------------------
test_connection() {
    log_info "Testing database connection..."

    # Prefer pg_isready (lightweight, no query execution)
    if command -v pg_isready &>/dev/null; then
        export PGPASSWORD="$DB_PASS"
        if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t 10 &>/dev/null; then
            log_info "Database connection OK"
            return 0
        fi
    fi

    # Fallback: use psql to run SELECT 1
    if command -v psql &>/dev/null; then
        export PGPASSWORD="$DB_PASS"
        if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" -t -q &>/dev/null; then
            log_info "Database connection OK"
            return 0
        fi
    fi

    # If neither pg_isready nor psql is available, check pg_dump
    if ! command -v pg_dump &>/dev/null; then
        log_error "pg_dump not found. Please install postgresql-client."
        log_error "  Ubuntu/Debian: apt-get install postgresql-client"
        log_error "  CentOS/RHEL:   yum install postgresql"
        log_error "  Alpine:        apk add postgresql-client"
    fi

    log_error "Database connection failed"
    return 1
}

# -------------------------------------------------------------------------
# Perform the actual backup
# -------------------------------------------------------------------------
do_backup() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d_%H%M%S')"
    local backup_file="${BACKUP_DIR}/unique_you_${timestamp}.sql.gz"
    local temp_file="${backup_file}.tmp"

    log_info "Starting backup: $DB_NAME -> $backup_file"

    # Export password for pg_dump
    export PGPASSWORD="$DB_PASS"

    # Run pg_dump piped to gzip, writing to a temp file first
    # Flags:
    #   --no-owner       Skip owner restoration (portable across environments)
    #   --no-acl         Skip privilege restoration
    #   --clean          Include DROP statements in output
    #   --if-exists      Use IF EXISTS with DROP (safer restore)
    if pg_dump \
        --no-owner \
        --no-acl \
        --clean \
        --if-exists \
        -h "$DB_HOST" \
        -p "$DB_PORT" \
        -U "$DB_USER" \
        -d "$DB_NAME" \
        2>"${temp_file}.log" \
        | gzip > "$temp_file"; then

        # Success: rename temp to final
        mv "$temp_file" "$backup_file"
        local file_size
        file_size="$(du -h "$backup_file" | cut -f1)"
        rm -f "${temp_file}.log"

        log_info "Backup complete - File: $backup_file (${file_size})"
        return 0
    else
        # Failure: collect error details
        local err_msg
        err_msg="$(cat "${temp_file}.log" 2>/dev/null || echo 'unknown error')"
        rm -f "$temp_file" "${temp_file}.log"

        log_error "Backup failed"
        log_error "Error details: $err_msg"

        # Write alert log (can be caught by external monitoring)
        echo "[ALERT] [$(date '+%Y-%m-%d %H:%M:%S')] Backup failed: ${DB_NAME}@${DB_HOST}:${DB_PORT} - ${err_msg}" >> "$LOG_FILE"

        return 1
    fi
}

# -------------------------------------------------------------------------
# Clean up backups older than RETENTION_DAYS
# -------------------------------------------------------------------------
cleanup_old_backups() {
    log_info "Cleaning backups older than ${RETENTION_DAYS} days..."

    local deleted_count=0

    # Find files matching naming convention beyond retention period
    while IFS= read -r -d '' old_file; do
        local fsize
        fsize="$(du -h "$old_file" | cut -f1)"
        rm -f "$old_file"
        deleted_count=$((deleted_count + 1))
        log_info "  Removed: $old_file (${fsize})"
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "unique_you_*.sql.gz" -mtime "+${RETENTION_DAYS}" -print0)

    if [ "$deleted_count" -eq 0 ]; then
        log_info "  Nothing to clean"
    else
        log_info "Removed ${deleted_count} old backup files"
    fi

    # Also clean log archives older than 90 days
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "backup.log.*" -mtime "+90" -delete 2>/dev/null || true
}

# -------------------------------------------------------------------------
# Rotate log file when it exceeds 10MB
# -------------------------------------------------------------------------
rotate_log() {
    if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null)" -gt 10485760 ]; then
        mv "$LOG_FILE" "${LOG_FILE}.$(date '+%Y%m%d%H%M%S')"
        log_info "Log rotated"
    fi
}

# -------------------------------------------------------------------------
# Main execution flow
# -------------------------------------------------------------------------
main() {
    echo "=============================================="
    echo " 唯一的你 Database Backup Tool"
    echo " Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "=============================================="

    # 1. Initialize directories
    init_dirs

    # 2. Rotate log if needed
    rotate_log

    # 3. Load database connection info
    load_env
    parse_db_url

    # 4. Test connection
    if ! test_connection; then
        log_error "Connection test failed, aborting."
        exit 1
    fi

    # 5. If test mode, stop here
    if [ "$TEST_MODE" = true ]; then
        log_info "Test mode, skipping backup."
        exit 0
    fi

    # 6. Execute backup
    if ! do_backup; then
        log_error "Backup process failed."
        exit 1
    fi

    # 7. Clean up old backups
    cleanup_old_backups

    echo "=============================================="
    echo " Backup Complete"
    echo "=============================================="
}

main
