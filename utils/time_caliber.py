"""时间口径迁移版本闸（2026-11-21 时间口径统一批）。

背景：``reminders`` / ``pending_intents`` / ``topic_state`` 等列的读写已统一
为 UTC 语义（唯一写入口径见 :mod:`utils.local_time`），代码**不再感知**旧
北京墙钟行。若 ``scripts/migrate_time_columns_to_utc.py --apply`` 未在停机
窗口执行，存量行会被当 UTC 读取——典型后果是提醒**延后 8 小时**触发、
话题 TTL 偏移。本模块提供轻量只读版本闸：检查迁移脚本落下的
``time_caliber_migrations`` 幂等标记，未迁移时按环境分级处置：

- **生产**（复用 :func:`utils.runtime_env.is_production`）：启动即 fail-closed
  拒绝启动（半迁移态不可上线）；
- **开发**：打印 CRITICAL 警告后放行，不阻塞本地调试。

本闸**只读**、不自动迁移——迁移必须由脚本显式执行（备份 + 幂等标记 +
失败整体恢复），避免启动期隐式写库。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from utils.project_paths import project_path

# L8 修复（2026-11-22）：环境判定走 utils.runtime_env 轻量真源。
# 旧实现运行时 ``from api.runtime_config import is_production`` 会经
# ``api/__init__ → app_factory → auth_jwt`` 拉起重链——裸环境（无 JWT_SECRET
# 且非显式 dev）import 即抛 RuntimeError，本闸的检查根本执行不到。
from utils.runtime_env import is_production

logger = logging.getLogger("utils.time_caliber")

#: 与 ``scripts/migrate_time_columns_to_utc.py::MIGRATION_NAME`` 保持一致
#:（脚本成功后写入该标记；两处改名必须同步）。
MIGRATION_NAME = "time_caliber_utc_v1"

__all__ = ["MIGRATION_NAME", "ensure_time_caliber_migrated"]


def _has_migration_marker(db_path: Path) -> bool:
    """只读探测迁移标记。

    - 库文件不存在 → 视为已满足（新装环境没有旧墙钟行，无需迁移）；
    - 标记表不存在 / 无该迁移记录 / 库被锁或损坏 → 视为未迁移（fail-closed）。
    """
    if not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM time_caliber_migrations WHERE migration = ?",
            (MIGRATION_NAME,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def ensure_time_caliber_migrated(
    sqlite_db: Path | str | None = None,
    agent_plane_db: Path | str | None = None,
) -> bool:
    """启动期版本闸：UTC 迁移未落标记时，生产拒绝启动、开发告警放行。

    Args:
        sqlite_db: ``data/sqlite.db`` 路径（缺省取项目默认，测试可注入 tmp 路径）。
        agent_plane_db: ``data/agent_plane.db`` 路径（同上）。

    Returns:
        ``True`` = 两库均已有迁移标记（或库不存在，无需迁移）；
        ``False`` = 非生产环境检测到未迁移（已打印 CRITICAL，放行）。

    Raises:
        RuntimeError: 生产环境（``AI_GF_ENV/APP_ENV/ENV`` 为 prod/production）
            检测到任一库缺迁移标记 —— fail-closed，拒绝启动。
    """
    sqlite_path = (
        Path(sqlite_db) if sqlite_db else Path(project_path("data", "sqlite.db"))
    )
    plane_path = (
        Path(agent_plane_db)
        if agent_plane_db
        else Path(project_path("data", "agent_plane.db"))
    )
    missing = [
        str(p)
        for p in (sqlite_path, plane_path)
        if not _has_migration_marker(p)
    ]
    if not missing:
        return True
    detail = f"时间口径 UTC 迁移未完成（缺标记: {', '.join(missing)}）"
    if is_production():
        logger.critical(
            "%s —— 生产环境拒绝启动；请先在停机窗口执行 "
            "scripts/migrate_time_columns_to_utc.py --apply",
            detail,
        )
        raise RuntimeError(
            f"{detail}；生产环境必须先执行 scripts/migrate_time_columns_to_utc.py --apply"
        )
    logger.critical(
        "%s —— 开发环境放行继续启动；旧北京墙钟行会被当 UTC 读取"
        "（提醒可能延后 8 小时），上线前必须完成迁移",
        detail,
    )
    return False
