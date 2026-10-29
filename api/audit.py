"""操作员审计（NEW-21）+ 全局运维视图开关（V2-1）唯一 owner。

2026-10-29 前端体验批 3（D20-⑤/⑥ 用户裁决）：

- **NEW-21 操作员审计**：admin 特权读写统一落 `operator_audit` 表（users.db）。
  cal.com 教训（调研结论，见 docs/reports/2026-10-28-frontend-ux-audit.md）：
  特权辅助功能无审计闭环容易被整体退役——审计先行，再开全局视图。
  🔴 隐私红线：**不存用户消息内容**，target 只落归属标识，detail 只落结构化摘要。
- **V2-1 全局运维视图开关**：admin 默认收窄到本人数据前缀；显式开启后
  `memory_scope` 才放行全库。开关读侧 `is_global_view_enabled` 由
  `resolve_principal_from_request` 在解析 admin 主体时调用并落
  `request.state.admin_global_view`（消费方：`misc_routes._memory_scope_prefix`）。

审计写入**尽力而为**：失败只告警，绝不阻断被审计的业务读/写。
"""

from __future__ import annotations

import contextlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import OperatorAudit, OperatorPref

logger = logging.getLogger("api.audit")


async def record_operator_audit(
    db: AsyncSession,
    *,
    admin_id: int,
    action: str,
    target: str = "",
    detail: dict | None = None,
) -> None:
    """落一条操作员审计（尽力而为，不抛出）。"""
    try:
        db.add(
            OperatorAudit(
                admin_id=int(admin_id),
                action=str(action)[:64],
                target=str(target or "")[:255],
                detail=detail or {},
            )
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001 — 审计失败不得阻断业务路径
        logger.warning("operator_audit 写入失败（忽略）: action=%s err=%s", action, e)
        with contextlib.suppress(Exception):
            await db.rollback()


async def is_global_view_enabled(db: AsyncSession, user_id: int) -> bool:
    """读操作员的全局运维视图开关（无行 = False，默认收窄）。"""
    result = await db.execute(
        select(OperatorPref.global_view_enabled).where(OperatorPref.user_id == int(user_id))
    )
    return bool(result.scalar_one_or_none() or False)


async def set_global_view_enabled(db: AsyncSession, user_id: int, enabled: bool) -> None:
    """写操作员的全局运维视图开关（upsert）。"""
    row = (
        await db.execute(select(OperatorPref).where(OperatorPref.user_id == int(user_id)))
    ).scalar_one_or_none()
    if row is None:
        db.add(OperatorPref(user_id=int(user_id), global_view_enabled=bool(enabled)))
    else:
        row.global_view_enabled = bool(enabled)
    await db.commit()


async def recent_operator_audit(db: AsyncSession, limit: int = 50) -> list[dict]:
    """最近操作员审计行（admin 控制台展示用，新在前）。"""
    result = await db.execute(
        select(OperatorAudit).order_by(OperatorAudit.id.desc()).limit(max(1, min(limit, 200)))
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "admin_id": r.admin_id,
            "action": r.action,
            "target": r.target,
            "detail": r.detail or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
