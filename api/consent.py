"""
使用即同意声明协议（W2-CONSENT）— 协议版本与同意状态查询

用户使用产品即视为同意《用户协议与隐私声明》（docs/legal/USER_AGREEMENT.md）。
协议文案更新时递增 CURRENT_AGREEMENT_VERSION，未同意新版本的用户登录后
needs_consent=True，前端弹全屏同意窗重新取得同意。

W9（D13 裁决）：同意从「登录提示」升级为**服务端四通道同源强制**——
HTTP 主聊天、WS 聊天、微信入站回复、后台外发（主动消息/提醒/追问）在
未同意 / 同意旧版本 / 已撤回 三种状态下统一拒绝或停发；撤回经
``withdraw_consent`` 落档，重同意即恢复。判定入口唯一：
``require_current_consent``（HTTP 依赖）与 ``outbound_allowed_for_session``
（后台/微信/WS 共源），失败一律 fail-closed（读库异常=不允许消费/外发）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Security
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import get_current_user_id
from api.database import ConsentRecord, get_db

logger = logging.getLogger("api.consent")

# 当前协议版本 — 必须与 docs/legal/USER_AGREEMENT.md 头部版本、
# frontend/src/constants/agreement.ts 的 AGREEMENT_VERSION 三方一致。
CURRENT_AGREEMENT_VERSION = "1.0.0"

#: 撤回记录的版本哨兵（ConsentRecord.agreement_version=WITHDRAWN）
CONSENT_WITHDRAWN = "WITHDRAWN"

#: 外发/消费判定的短 TTL 缓存（秒）——避免调度器每 tick / 每条微信消息直查库
_OUTBOUND_CACHE_TTL = 5.0
_outbound_cache: dict[int, tuple[float, bool]] = {}


def outbound_owner_db():
    """外发判定用的 users.db 会话工厂（模块级以便测试沙箱重定向）。"""
    from api.database import _async_session

    return _async_session


async def _maybe_session(db_or_factory):
    """兼容 AsyncSession 与 sessionmaker 两种入参。"""
    if isinstance(db_or_factory, AsyncSession):
        return None, db_or_factory
    return db_or_factory(), None


async def latest_consent(db: AsyncSession, user_id: int) -> ConsentRecord | None:
    """取用户最近一次同意/撤回记录"""
    result = await db.execute(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id)
        .order_by(ConsentRecord.agreed_at.desc(), ConsentRecord.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def consent_state_of(db_or_factory, user_id: int) -> str:
    """当前同意状态：granted / missing / withdrawn / outdated（D13 判定真源）。"""
    own, db = await _maybe_session(db_or_factory)
    if own is not None:
        async with own as db:
            return await consent_state_of(db, user_id)
    record = await latest_consent(db, user_id)
    if record is None:
        return "missing"
    if record.agreement_version == CONSENT_WITHDRAWN:
        return "withdrawn"
    if record.agreement_version != CURRENT_AGREEMENT_VERSION:
        return "outdated"
    return "granted"


async def has_consented(
    db: AsyncSession, user_id: int, version: str = CURRENT_AGREEMENT_VERSION
) -> bool:
    """用户是否已同意指定版本（默认当前版本）；撤回/旧版本均视为未同意"""
    record = await latest_consent(db, user_id)
    if record is None:
        return False
    if record.agreement_version == CONSENT_WITHDRAWN:
        return False
    return bool(record.agreement_version == version)


async def withdraw_consent(
    db: AsyncSession, user_id: int
) -> ConsentRecord:
    """撤回同意（D13）：落 WITHDRAWN 哨兵档；四通道外发/消费即刻停止。"""
    record = ConsentRecord(
        user_id=user_id,
        agreement_version=CONSENT_WITHDRAWN,
        agreed_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    _outbound_cache.pop(int(user_id), None)
    logger.info("用户 %s 撤回协议同意", user_id)
    return record


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
    _outbound_cache.pop(int(user_id), None)
    return record


# ═══════════════════════════════════════════════════════
# D13 四通道同源判定
# ═══════════════════════════════════════════════════════


async def consumption_allowed(db_or_factory, user_id: int) -> bool:
    """该账号当前是否允许消费服务（HTTP/WS 聊天、微信入站回复）。

    granted → True；missing / withdrawn / outdated / 读库失败 → False（fail-closed）。
    """
    try:
        state = await consent_state_of(db_or_factory, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("同意状态读取失败 uid=%s（fail-closed）: %s", user_id, e)
        return False
    return state == "granted"


async def outbound_allowed_for_session(
    session_key: str, db_factory=None
) -> bool:
    """会话键归属账号是否允许外发（主动消息/提醒/追问的统一判定入口）。

    外发语义与消费门禁分层（协议 §0「使用即同意」+ §7 撤回/更新）：
    - withdrawn（明确撤回）/ outdated（同意旧版本未更新）→ **停发**；
    - missing（从未记录，如存量微信直连用户）→ **放行**——「使用即同意」
      即同意载体；拦 missing 会把生产存量用户的外发全部静默（行为回退）；
    - 读库失败 → fail-closed 停发；
    - 无主会话（遗留裸键 / 匿名 web）没有账号可撤，恒放行。
    """
    from utils.session_key import owner_of

    owner = owner_of(session_key)
    if owner is None:
        return True
    factory = db_factory or outbound_owner_db()
    try:
        state = await consent_state_of(factory, int(owner))
    except Exception as e:  # noqa: BLE001
        logger.warning("外发同意判定读取失败 uid=%s（fail-closed）: %s", owner, e)
        state = "withdrawn"
    allowed = state in ("granted", "missing")
    _outbound_cache[int(owner)] = (time.monotonic(), allowed)
    return allowed


def outbound_allowed_for_session_sync(session_key: str) -> bool:
    """``outbound_allowed_for_session`` 的同步包装（调度器/提醒线程用）。

    带短 TTL 缓存；共享循环不可用时 fail-closed。
    """
    from utils.session_key import owner_of

    owner = owner_of(session_key)
    if owner is None:
        return True
    cached = _outbound_cache.get(int(owner))
    if cached and time.monotonic() - cached[0] < _OUTBOUND_CACHE_TTL:
        return cached[1]
    try:
        from utils.async_utils import run_on_shared_loop

        allowed = run_on_shared_loop(outbound_allowed_for_session(session_key))
    except Exception as e:  # noqa: BLE001
        logger.warning("外发同意判定失败 session=%s（fail-closed）: %s", session_key, e)
        allowed = False
    _outbound_cache[int(owner)] = (time.monotonic(), allowed)
    return allowed


def invalidate_outbound_cache(user_id: int | None = None) -> None:
    """撤回/同意后清缓存（同进程立即生效）。"""
    if user_id is None:
        _outbound_cache.clear()
    else:
        _outbound_cache.pop(int(user_id), None)


async def require_current_consent(
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> int:
    """HTTP 消费门禁（D13）：未同意 / 同意旧版本 / 已撤回 → 403 CONSENT_REQUIRED。

    用于「可消费/可触达」操作（主聊天、会话创建）。登录/注册/同意记录本身
    不设门禁——那是取得同意的通道。挂载方式与 ``get_current_user_id`` 相同：
    ``user_id: int = Security(require_current_consent)``。
    """
    state = await consent_state_of(db, user_id)
    if state != "granted":
        raise HTTPException(
            status_code=403,
            detail={
                "message": "CONSENT_REQUIRED",
                "needs_consent": True,
                "consent_state": state,
            },
        )
    return user_id
