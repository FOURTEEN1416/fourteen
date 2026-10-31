"""话题状态机管理端路由 —— ``/api/topic-state/*``（2026-10-27 实施计划 Task 5）。

三个端点：

- ``GET  /api/topic-state/config``：纯热读 ``data/scheduler_config.json`` 的
  ``topic_state`` 块；不依赖 ``orch._ase``，4 个 worker 里谁都能答。
- ``POST /api/topic-state/config``：partial merge 热写；越界 422（Pydantic 先行），
  合并后跨字段冲突/未知字段 400，其余顶层配置块逐字保留。
- ``GET  /api/topic-state/{session_key}``：只读快照（active/parked/closed +
  next_plan）；不写库、不初始化调度器。**admin 可查任意键；普通用户只能查
  自己的会话**（白名单从 token 派生：全部微信绑定 + 活跃会话 + 本人 owner
  前缀，见 ``_owned_session_keys`` / ``_is_owned_session_key``）。

路由注册顺序是硬约束：``/config`` 必须在 ``/{session_key}`` 之前，否则
FastAPI 会把 ``config`` 当成会话键。

会话键口径沿用 R7 P0 修复后的 ``_resolve_admin_target_key``：含 ``:`` 的完整键
原样使用；纯数字仅当该用户存在微信绑定时还原为 ``N:wxid``，否则 400（不猜键，
web/WS 的 ``N:web:8hex`` 必须传完整键）。该还原只服务 admin 分支——普通用户
分支**不**解析客户端自报的键，只做白名单比对（不猜键也就不存在猜错人的可能）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal, require_role
from api.database import get_db
from api.main_routes import TopicStateConfigRequest

logger = logging.getLogger("api.routers.topic_routes")

router = APIRouter(tags=["topic-state"])


@router.get("/api/topic-state/config")
async def get_topic_state_config(
    _auth: bool = Security(verify_api_key_dep),
    _admin=Depends(require_role("admin")),
):
    """读取话题状态机配置（热读，不缓存、不依赖调度器）。"""
    from utils.topic_state import read_topic_config

    return {"status": "ok", "config": read_topic_config()}


@router.post("/api/topic-state/config")
async def update_topic_state_config(
    req: TopicStateConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin=Depends(require_role("admin")),
):
    """partial merge 写入话题状态机配置（锁内读改写，只触碰 ``topic_state`` 块）。"""
    from utils.topic_state import write_topic_config

    payload = req.model_dump(exclude_none=True)
    try:
        config = write_topic_config(**payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    logger.info("话题状态配置已更新: %s", payload)
    return {"status": "ok", "config": config}


async def _bound_session_keys(user_id: int, db) -> set[str]:
    """本人**全部**微信绑定键（R4-2：不走 ``training_routes`` 的单条 helper）。

    ``_user_session_key_for`` 用 ``order_by(bound_at.desc()).first()`` 只取最新
    一条绑定 → 历史上多条 ``N:wxid`` 的话题状态对本人不可读。绑定表每一行
    都是合法归属，这里按 ``user_id`` 全量取，不依赖单条 helper。
    """
    from sqlalchemy import select

    from api.database import WechatBinding

    if db is None:
        return set()
    uid = int(user_id)
    try:
        result = await db.execute(
            select(WechatBinding.wxid).where(WechatBinding.user_id == uid)
        )
        wxids = list(result.scalars())
    except Exception as exc:  # noqa: BLE001 — 查询失败按收窄处理
        logger.debug("查询用户微信绑定失败 user=%s: %s", uid, exc)
        return set()
    return {
        f"{uid}:{wxid}"
        for wxid in (str(value or "").strip() for value in wxids)
        if wxid
    }


async def _owned_session_keys(user_id: int, db) -> set[str]:
    """普通用户可读的会话键白名单（三支都从 token 的 ``user_id`` 派生）。

    1. **全部**微信绑定键 ``N:wxid``（``_bound_session_keys``，R4-2）；
    2. 进程内本人活跃会话（``deps.sessions.get_active_sessions``）；
    3. token 派生的 owner 前缀（见 ``_is_owned_session_key``）——web/WS 会话
       由 ``resolve_owned_session``（``chat_routes.py`` /
       ``api/websocket_server.py``）现场铸造 ``N:web:hex``，既不登记
       SessionManager 也不落绑定表，不按前缀认领就永远读不到（R4-1）。

    客户端传入的 ``session_key`` 仅作为**待比对**的候选，永不作为归属依据。
    会话管理器缺席或抛错 → 只丢第 2 支，绑定/前缀支仍生效（收窄而非放宽，
    fail-closed，不向客户端回 500）。
    """
    from api.deps import deps

    uid = int(user_id)
    keys = await _bound_session_keys(uid, db)
    sessions = deps.sessions
    if sessions is not None:
        try:
            active = sessions.get_active_sessions(str(uid))
        except Exception as exc:  # noqa: BLE001 — 读活跃会话失败按收窄处理
            logger.debug("读取用户活跃会话失败 user=%s: %s", uid, exc)
            active = []
        keys.update(str(sid).strip() for sid in active)
    keys.discard("")
    return keys


def _is_owned_session_key(session_key: str, user_id: int, owned: set[str]) -> bool:
    """候选键是否属于 ``user_id``（白名单三支见 ``_owned_session_keys``）。"""
    from utils.session_key import parse

    key = str(session_key or "").strip()
    if not key:
        return False
    if key in owned:
        return True
    # 第 3 支：token 派生的 owner 前缀。只认**三段及以上**的 web/WS 键
    # （``N:web:hex`` / ``N:N:web:hex``，``utils/session_key`` 判据）——它们由
    # ``resolve_owned_session`` 按同一 ``owner_of`` 规则现场铸造，chat 写路径
    # 与读快照必须同一归属口径。二段式微信键 ``N:wxid`` 仍要求真实绑定
    # （D15 不猜键）。
    parsed = parse(key)
    return parsed.owner == int(user_id) and parsed.segments >= 3


@router.get("/api/topic-state/{session_key}")
async def get_topic_state_snapshot(
    session_key: str = Path(..., min_length=1, max_length=200),
    character_id: str = Query("default", max_length=128),
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    db=Depends(get_db),
):
    """只读快照：admin 查任意会话键；普通用户只能查自己的会话。

    归属口径（本批统一模式）：服务端从 token 派生身份键，客户端自报归属
    一律忽略或校验后退回 404 —— 与 ``require_character_access``
    （``api/routers/character_routes.py:102``）同款「不区分不存在与无权限」，
    防用状态码枚举他人会话键。

    无 Bearer → 401（本面原本就是 admin-only，机器面从未取得访问权，
    因此不退化成 ``character_routes`` 的「机器面无干预」语义）。
    """
    if principal is None:
        raise HTTPException(
            401, "Missing Authorization header (Bearer token)"
        )
    from api.routers.training_routes import _resolve_admin_target_key
    from utils.topic_state import get_topic_snapshot

    if principal.role == "admin":
        key = await _resolve_admin_target_key(session_key, db)
    else:
        # 普通用户：不解析、不还原，只做白名单比对（含数字 id 形态一律 404）
        key = str(session_key or "").strip()
        owned = await _owned_session_keys(principal.user_id, db)
        if not _is_owned_session_key(key, principal.user_id, owned):
            raise HTTPException(404, f"会话不存在: {key}")
    snapshot = get_topic_snapshot(key, character_id)
    snapshot.pop("text", None)  # 只读结构；prompt 渲染文本不对任何角色回传
    return {
        "status": "ok",
        "session_key": key,
        "character_id": character_id,
        **snapshot,
    }
