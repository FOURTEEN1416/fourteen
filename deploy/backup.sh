#!/usr/bin/env bash
# =============================================================================
# AI Girlfriend 应用数据一致备份（W10 重构 · 唯一样板委托层）
#
# 真正的备份逻辑在 deploy/backup_manager.py（Python 标准库，跨平台、可测试）：
#   - users 库按 APP_DATABASE_URL > DATABASE_URL 优先级解析（与 api/runtime_config
#     平价，有契约测试钉死），默认 data/users.db
#   - 记忆库 data/sqlite.db、事件账本 data/agent_plane.db、向量源 data/chroma_db、
#     角色卡 config/characters（gitignore，不入 git）、知识索引、运行时状态与
#     机密配置一并纳入，产出 manifest.json（组件/版本/sha256/表行数/业务行归属）
#   - SQLite 一律走备份 API 在线一致快照（WAL 已提交内容必然包含），不再依赖
#     sqlite3 CLI，也没有裸 cp 主文件的退化路径
#   - 退出码语义：0=complete / 2=partial（缺项或降级，明确不完整）/ 1=failed
#
# Environment:
#   DB_BACKUP_DIR      备份输出目录（默认 <PROJECT_ROOT>/backups）
#   DB_RETENTION_DAYS  保留天数（默认 30）
#   PROJECT_ROOT       项目根（默认 /opt/ai-girlfriend）
# Usage:
#   ./backup.sh              # 执行备份
#   ./backup.sh --test       # 仅测试数据库连接
#   ./backup.sh --dry-run    # 只读探测，输出计划 JSON，不写任何文件
# 恢复：deploy/restore.sh <backup-dir> --target <独立目录>（绝不原地覆盖）
# =============================================================================
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/opt/ai-girlfriend}"

# 解析 Python 解释器：优先项目 venv
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON=python3
else
    echo "[ERROR] python3 not found" >&2
    exit 1
fi

exec "${PYTHON}" "${PROJECT_ROOT}/deploy/backup_manager.py" \
    --project-root "${PROJECT_ROOT}" \
    --output-dir "${DB_BACKUP_DIR:-${PROJECT_ROOT}/backups}" \
    --retention-days "${DB_RETENTION_DAYS:-30}" \
    "$@"
