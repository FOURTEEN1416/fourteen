#!/usr/bin/env bash
# =============================================================================
# AI Girlfriend Database Backup Script
# 支持 SQLite（默认）和 PostgreSQL，同时备份 ChromaDB 向量数据
#
# Environment: Linux (Ubuntu/Debian/CentOS)
# Features:
#   1. 自动检测数据库类型（SQLite / PostgreSQL）
#   2. SQLite: 用 .backup 命令安全导出（支持 WAL 模式）
#   3. PostgreSQL: 用 pg_dump + gzip 压缩
#   4. 同时备份 ChromaDB 向量数据目录
#   5. 自动清理超过保留期的备份
#   6. 支持 --test 测试连接
# Usage:
#   ./backup.sh              # 执行备份
#   ./backup.sh --test       # 仅测试连接
#   DB_BACKUP_DIR=/data/backups ./backup.sh  # 自定义备份目录
# =============================================================================

set -euo pipefail

# -------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# -------------------------------------------------------------------------
BACKUP_DIR="${DB_BACKUP_DIR:-/opt/ai-girlfriend/backups}"
RETENTION_DAYS="${DB_RETENTION_DAYS:-30}"
LOG_FILE="${BACKUP_DIR}/backup.log"
DATABASE_URL="${DATABASE_URL:-}"
# SQLite 数据库文件路径（默认值，可被 DATABASE_URL 覆盖）
SQLITE_DB_PATH="${SQLITE_DB_PATH:-/opt/ai-girlfriend/data/users.db}"
# ChromaDB 数据目录
CHROMA_DB_DIR="${CHROMA_DB_DIR:-/opt/ai-girlfriend/data/chroma}"
# 项目根目录（用于查找 .env 和 data/ 目录）
PROJECT_ROOT="${PROJECT_ROOT:-/opt/ai-girlfriend}"

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
# Log functions
# -------------------------------------------------------------------------
log_info()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $*"; }
log_error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; }
log_warn()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] $*"; }

# -------------------------------------------------------------------------
# Initialize: create backup directory
# -------------------------------------------------------------------------
init_dirs() {
    if [ ! -d "$BACKUP_DIR" ]; then
        mkdir -p "$BACKUP_DIR"
        log_info "Created backup directory: $BACKUP_DIR"
    fi
}

# -------------------------------------------------------------------------
# Load DATABASE_URL from .env file
# -------------------------------------------------------------------------
load_env() {
    if [ -n "$DATABASE_URL" ]; then
        return 0
    fi

    local env_file="${PROJECT_ROOT}/.env"
    if [ ! -f "$env_file" ]; then
        # 尝试其他位置
        for candidate in "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/.env" ".env"; do
            if [ -f "$candidate" ]; then
                env_file="$candidate"
                break
            fi
        done
    fi

    if [ ! -f "$env_file" ]; then
        log_warn "No .env file found, assuming SQLite at $SQLITE_DB_PATH"
        DATABASE_URL=""
        return 0
    fi

    log_info "Loading DATABASE_URL from $env_file"
    local raw_value
    raw_value="$(grep -E '^DATABASE_URL=' "$env_file" 2>/dev/null | head -1 | sed 's/^DATABASE_URL=//' || true)"

    if [ -n "$raw_value" ]; then
        DATABASE_URL="$(echo "$raw_value" | sed -e "s/^'//" -e "s/'$//" -e 's/^"//' -e 's/"$//')"
    fi
}

# -------------------------------------------------------------------------
# 检测数据库类型
# 返回: "sqlite" 或 "postgresql"
# -------------------------------------------------------------------------
detect_db_type() {
    if [ -z "$DATABASE_URL" ]; then
        echo "sqlite"
        return
    fi

    case "$DATABASE_URL" in
        sqlite*|sqlite3*)
            echo "sqlite"
            ;;
        postgresql*|postgres*)
            echo "postgresql"
            ;;
        *)
            # 默认假设 SQLite
            log_warn "Unknown DATABASE_URL format, assuming SQLite"
            echo "sqlite"
            ;;
    esac
}

# -------------------------------------------------------------------------
# 从 DATABASE_URL 提取 SQLite 文件路径
# 格式: sqlite+aiosqlite:///path/to/db.sqlite
# -------------------------------------------------------------------------
parse_sqlite_path() {
    # 移除 sqlite+aiosqlite:/// 或 sqlite:/// 前缀
    local path="${DATABASE_URL#sqlite+aiosqlite:///}"
    path="${path#sqlite:///}"
    # 如果是相对路径，基于 PROJECT_ROOT 解析
    if [ "${path:0:1}" != "/" ]; then
        path="${PROJECT_ROOT}/${path}"
    fi
    echo "$path"
}

# -------------------------------------------------------------------------
# 从 DATABASE_URL 提取 PostgreSQL 连接参数
# -------------------------------------------------------------------------
parse_pg_url() {
    DB_USER="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://\([^:]*\):.*|\1|p')"
    DB_PASS="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^:]*:\([^@]*\)@.*|\1|p')"
    DB_HOST="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^@]*@\([^:]*\):.*|\1|p')"
    DB_PORT="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^@]*@[^:]*:\([^/]*\)/.*|\1|p')"
    DB_NAME="$(echo "$DATABASE_URL" | sed -n 's|^postgresql://[^@]*@[^:]*:[^/]*/\(.*\)|\1|p')"

    if [ -z "$DB_PORT" ]; then
        DB_PORT="5432"
    fi

    if [ -z "$DB_USER" ] || [ -z "$DB_HOST" ] || [ -z "$DB_NAME" ]; then
        log_error "Failed to parse PostgreSQL DATABASE_URL"
        log_error "Expected format: postgresql://user:password@host:port/database"
        exit 1
    fi
}

# -------------------------------------------------------------------------
# 测试数据库连接
# -------------------------------------------------------------------------
test_connection() {
    log_info "Testing database connection..."
    local db_type
    db_type="$(detect_db_type)"

    if [ "$db_type" = "sqlite" ]; then
        local db_path
        if [ -n "$DATABASE_URL" ]; then
            db_path="$(parse_sqlite_path)"
        else
            db_path="$SQLITE_DB_PATH"
        fi

        if [ -f "$db_path" ]; then
            if command -v sqlite3 &>/dev/null; then
                if sqlite3 "$db_path" "SELECT 1" &>/dev/null; then
                    log_info "SQLite connection OK: $db_path"
                    return 0
                else
                    log_error "SQLite connection failed: $db_path"
                    return 1
                fi
            else
                log_warn "sqlite3 not installed, but database file exists: $db_path"
                return 0
            fi
        else
            log_error "SQLite database file not found: $db_path"
            return 1
        fi
    else
        # PostgreSQL
        parse_pg_url
        export PGPASSWORD="$DB_PASS"
        if command -v pg_isready &>/dev/null; then
            if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t 10 &>/dev/null; then
                log_info "PostgreSQL connection OK"
                return 0
            fi
        fi
        if command -v psql &>/dev/null; then
            if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" -t -q &>/dev/null; then
                log_info "PostgreSQL connection OK"
                return 0
            fi
        fi
        log_error "PostgreSQL connection failed"
        return 1
    fi
}

# -------------------------------------------------------------------------
# 执行 SQLite 备份
# 使用 .backup 命令（安全，支持 WAL 模式，不阻塞写入）
# -------------------------------------------------------------------------
backup_sqlite() {
    local db_path
    if [ -n "$DATABASE_URL" ]; then
        db_path="$(parse_sqlite_path)"
    else
        db_path="$SQLITE_DB_PATH"
    fi

    local timestamp
    timestamp="$(date '+%Y-%m-%d_%H%M%S')"
    local backup_file="${BACKUP_DIR}/ai_girlfriend_${timestamp}.db"
    local temp_file="${backup_file}.tmp"

    log_info "Starting SQLite backup: $db_path -> $backup_file"

    if command -v sqlite3 &>/dev/null; then
        # 使用 sqlite3 .backup 命令（原子性，支持 WAL）
        if sqlite3 "$db_path" ".backup '$temp_file'" 2>&1; then
            mv "$temp_file" "$backup_file"
            chmod 600 "$backup_file"
            local file_size
            file_size="$(du -h "$backup_file" | cut -f1)"
            log_info "SQLite backup complete: $backup_file (${file_size})"
            return 0
        else
            rm -f "$temp_file"
            log_error "SQLite backup failed"
            return 1
        fi
    else
        # 没有 sqlite3 命令，直接复制文件（可能不一致，但比没有备份好）
        log_warn "sqlite3 not installed, using file copy (may be inconsistent under WAL)"
        if cp "$db_path" "$temp_file" 2>&1; then
            # 同时复制 WAL 和 SHM 文件（如果存在）
            cp "${db_path}-wal" "${temp_file}-wal" 2>/dev/null || true
            cp "${db_path}-shm" "${temp_file}-shm" 2>/dev/null || true
            mv "$temp_file" "$backup_file"
            chmod 600 "$backup_file"
            log_info "SQLite backup complete (file copy): $backup_file"
            return 0
        else
            rm -f "$temp_file"
            log_error "SQLite file copy failed"
            return 1
        fi
    fi
}

# -------------------------------------------------------------------------
# 执行 PostgreSQL 备份
# -------------------------------------------------------------------------
backup_postgresql() {
    parse_pg_url

    local timestamp
    timestamp="$(date '+%Y-%m-%d_%H%M%S')"
    local backup_file="${BACKUP_DIR}/ai_girlfriend_${timestamp}.sql.gz"
    local temp_file="${backup_file}.tmp"

    log_info "Starting PostgreSQL backup: $DB_NAME -> $backup_file"

    export PGPASSWORD="$DB_PASS"
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

        mv "$temp_file" "$backup_file"
        chmod 600 "$backup_file"
        local file_size
        file_size="$(du -h "$backup_file" | cut -f1)"
        rm -f "${temp_file}.log"
        log_info "PostgreSQL backup complete: $backup_file (${file_size})"
        return 0
    else
        local err_msg
        err_msg="$(cat "${temp_file}.log" 2>/dev/null || echo 'unknown error')"
        rm -f "$temp_file" "${temp_file}.log"
        log_error "PostgreSQL backup failed: $err_msg"
        return 1
    fi
}

# -------------------------------------------------------------------------
# 备份 ChromaDB 向量数据目录
# -------------------------------------------------------------------------
backup_chromadb() {
    if [ ! -d "$CHROMA_DB_DIR" ]; then
        log_info "ChromaDB directory not found, skipping: $CHROMA_DB_DIR"
        return 0
    fi

    local timestamp
    timestamp="$(date '+%Y-%m-%d_%H%M%S')"
    local backup_file="${BACKUP_DIR}/chromadb_${timestamp}.tar.gz"

    log_info "Starting ChromaDB backup: $CHROMA_DB_DIR -> $backup_file"

    if tar czf "$backup_file" -C "$(dirname "$CHROMA_DB_DIR")" "$(basename "$CHROMA_DB_DIR")" 2>&1; then
        chmod 600 "$backup_file"
        local file_size
        file_size="$(du -h "$backup_file" | cut -f1)"
        log_info "ChromaDB backup complete: $backup_file (${file_size})"
        return 0
    else
        log_error "ChromaDB backup failed"
        return 1
    fi
}

# -------------------------------------------------------------------------
# 清理旧备份
# -------------------------------------------------------------------------
cleanup_old_backups() {
    log_info "Cleaning backups older than ${RETENTION_DAYS} days..."
    local deleted_count=0

    while IFS= read -r -d '' old_file; do
        local fsize
        fsize="$(du -h "$old_file" | cut -f1)"
        rm -f "$old_file"
        deleted_count=$((deleted_count + 1))
        log_info "  Removed: $old_file (${fsize})"
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name "ai_girlfriend_*.db" -o -name "ai_girlfriend_*.sql.gz" -o -name "chromadb_*.tar.gz" \) -mtime "+${RETENTION_DAYS}" -print0)

    if [ "$deleted_count" -eq 0 ]; then
        log_info "  Nothing to clean"
    else
        log_info "Removed ${deleted_count} old backup files"
    fi
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
# Main
# -------------------------------------------------------------------------
main() {
    echo "=============================================="
    echo " AI Girlfriend Database Backup Tool"
    echo " Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "=============================================="

    init_dirs
    rotate_log
    load_env

    local db_type
    db_type="$(detect_db_type)"
    log_info "Detected database type: $db_type"

    if ! test_connection; then
        log_error "Connection test failed, aborting."
        exit 1
    fi

    if [ "$TEST_MODE" = true ]; then
        log_info "Test mode, skipping backup."
        exit 0
    fi

    # 主数据库备份
    if [ "$db_type" = "sqlite" ]; then
        backup_sqlite || { log_error "SQLite backup failed."; exit 1; }
    else
        backup_postgresql || { log_error "PostgreSQL backup failed."; exit 1; }
    fi

    # ChromaDB 向量数据备份
    backup_chromadb || log_warn "ChromaDB backup failed, continuing"

    # 清理旧备份
    cleanup_old_backups

    echo "=============================================="
    echo " Backup Complete"
    echo "=============================================="
}

main
