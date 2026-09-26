#!/usr/bin/env bash
# =============================================================================
# AI Girlfriend 隔离恢复（W10 · 委托层）
#
# 恢复逻辑在 deploy/restore_manager.py：
#   - 只恢复到独立目标目录；目标含应用检出标记（main.py/pyproject.toml/.git）
#     无条件拒绝——不原地覆盖生产
#   - 恢复后自动校验：每库 integrity_check、表清单、全表行数、按会话键的
#     业务行归属、角色卡/向量源文件数与字节数，任何不一致退出非 0
#   - 校验通过也只打印计划切换步骤，切换永远是人工动作
#
# Usage:
#   ./restore.sh backups/backup-YYYYMMDD-HHMMSS --target /srv/restore-verify
#   ./restore.sh <backup-dir> --target <dir> --force        # 覆盖非空目标（仍拒 live root）
#   ./restore.sh <backup-dir> --target <dir> --verify-only  # 只校验既有恢复产物
# =============================================================================
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/opt/ai-girlfriend}"

if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON=python3
else
    echo "[ERROR] python3 not found" >&2
    exit 1
fi

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <backup-dir> --target <dir> [--force] [--verify-only] [--skip-secrets]" >&2
    exit 1
fi

exec "${PYTHON}" "${PROJECT_ROOT}/deploy/restore_manager.py" "$@"
