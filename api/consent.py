"""
使用即同意声明协议（W2-CONSENT）— 协议版本与同意状态查询

用户使用产品即视为同意《用户协议与隐私声明》（docs/legal/USER_AGREEMENT.md）。
协议文案更新时递增 CURRENT_AGREEMENT_VERSION，未同意新版本的用户登录后
needs_consent=True，前端弹全屏同意窗重新取得同意。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import ConsentRecord

# 当前协议版本 — 必须与 docs/legal/USER_AGREEMENT.md 头部版本、
# frontend/src/constants/agreement.ts 的 AGREEMENT_VERSION 三方一致。
CURRENT_AGREEMENT_VERSION = "1.0.0"


async def latest_consent(db: AsyncSession, user_id: int) -> ConsentRecord | None:
    """取用户最近一次同意记录"""
    result = await db.execute(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id)
        .order_by(ConsentRecord.agreed_at.desc(), ConsentRecord.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def has_consented(
    db: AsyncSession, user_id: int, version: str = CURRENT_AGREEMENT_VERSION
) -> bool:
    """用户是否已同意指定版本（默认当前版本）"""
    record = await latest_consent(db, user_id)
    return bool(record is not None and record.agreement_version == version)


async def record_consent(
    db: AsyncSession, user_id: int, version: str = CURRENT_AGREEMENT_VERSION
) -> ConsentRecord:
    """落一条同意记录（时间戳由服务端 UTC 生成，不信任客户端时钟）"""
    record = ConsentRecord(
        user_id=user_id,
        agreement_version=version,
        agreed_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record
