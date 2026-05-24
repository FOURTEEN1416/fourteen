#!/bin/bash
# scripts/backup_before_refactor.sh
# 重构前完整备份

set -e

BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "=== 开始完整备份 ==="

if [ -f "data/sqlite.db" ]; then
    cp data/sqlite.db "$BACKUP_DIR/sqlite.db.backup"
    sqlite3 data/sqlite.db ".dump" > "$BACKUP_DIR/sqlite_dump.sql" 2>/dev/null || true
    echo "✓ 数据库已备份"
fi

if [ -d "data/characters" ]; then
    cp -r data/characters "$BACKUP_DIR/characters_backup/"
    echo "✓ 角色数据已备份"
fi

if [ -d "config" ]; then
    cp -r config "$BACKUP_DIR/config_backup/"
    echo "✓ 配置文件已备份"
fi

git tag -a "pre-refactor-$(date +%Y%m%d)" -m "重构前代码状态" 2>/dev/null || true

cd "$BACKUP_DIR"
find . -type f -exec sha256sum {} \; > checksums.txt 2>/dev/null || true
cd -

echo "=== 备份完成: $BACKUP_DIR ==="
