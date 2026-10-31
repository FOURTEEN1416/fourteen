"""
人格画像路由 — emotion / persona / psych

来源：原 api.main_routes.py L231/245/254/266/286/294/303/315/333 共 9 端点

依赖：
- deps.orch（emotion / persona / persona_extractor）
- EmotionStateResponse 模型来自 api.main_routes

归属（2026-10-30 psych 域「画像还给本人」）：
- `/api/psych/*` 五端点是**双分支**：admin 保持改前的全量语义，普通用户只在
  **本人**会话的 scope 上读写。
- 归属只在**路由层**强制：`profile_scope()` 的键 `{character_id}:{session_id}`
  已含 owner 段（v1.28 完整会话键），生成主链与 persona_extractor 零改动。
- 允许的 session_id 一律由服务端从认证主体派生，客户端自报他人键 → 404。

读面口径注记（R5-3，2026-10-31）：域规格原文是「未传 session_id 时在白名单各
scope 上聚合（`pe.profile_scopes` 逐键）」，**当前实现比规格更窄**——只做
`owner` + 可选 `character_id` / `session_id` 的 **exact 匹配**，取本人桶中
**最近的一份**（`list_persona_scopes()` 已按 `last_updated` 新→旧排序），
`scope_count` 是匹配桶数；**不在各 scope 之间做聚合**。exact 匹配比
`profile_scopes` 的 `endswith` 更严格，属安全侧收窄（宁窄勿宽），本批保持
实现、不改读面语义。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.database import get_db
from api.deps import deps
from api.main_routes import EmotionStateResponse
from persona_extractor.persona_bank import scope_owner_uid

logger = logging.getLogger("api.routers.personality_routes")

router = APIRouter(tags=["personality"])


# ═══════════════════════════════════════════════════════
# Emotion API
# ═══════════════════════════════════════════════════════


@router.get("/api/emotion/state", response_model=EmotionStateResponse)
async def emotion_state(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._emotion:
        health_data = orch._emotion.health_check()
        return EmotionStateResponse(
            current_emotion=health_data.get("current_emotion", ""),
            intensity=health_data.get("intensity", 0.0),
            energy=health_data.get("energy", 0.0),
            affinity=health_data.get("affinity", 0.0),
        )
    return EmotionStateResponse()


@router.get("/api/emotion/trend")
async def emotion_trend(
    days: int = Query(default=7, ge=1, le=30),
    _auth: bool = Security(verify_api_key_dep),
):
    """情绪趋势（会话内存态）：按时间窗返回历史记录（旧→新）。

    2026-09-01 修复：旧实现读不存在的 `_emotion_history` 属性，端点自创建起
    恒返回空数组；现统一走 EmotionEngine.get_history()。历史为环形缓冲
    （500 条上限，重启清零），days 仅作时间窗近似截断。
    """
    orch = deps.orch
    if not orch or not orch._emotion:
        return {"trend": [], "days": days}
    history = orch._emotion.get_history()
    return {"trend": history[-days * 20:], "days": days}


@router.get("/api/emotion/distribution")
async def emotion_distribution(
    days: int = Query(default=7, ge=1, le=30),
    _auth: bool = Security(verify_api_key_dep),
):
    """情绪分布（会话内存态）：聚合历史中各主情绪的占比。

    SP-1 补齐：与 trend 同源（EmotionEngine 环形缓冲），无持久化——
    重启后从零累计，前端需诚实标注"会话内"。
    """
    orch = deps.orch
    if not orch or not orch._emotion:
        return {"distribution": [], "total": 0, "days": days}
    history = orch._emotion.get_history()
    cutoff = time.time() - days * 86400
    counts: dict[str, int] = {}
    for item in history:
        ts = item.get("timestamp", 0) or 0
        if ts and ts < cutoff:
            continue
        emotion = item.get("primary_emotion", "平常")
        counts[emotion] = counts.get(emotion, 0) + 1
    distribution = [
        {"emotion": emotion, "count": count}
        for emotion, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {"distribution": distribution, "total": sum(counts.values()), "days": days}


# ═══════════════════════════════════════════════════════
# Persona API
# ═══════════════════════════════════════════════════════


@router.get("/api/persona/profile")
async def persona_profile(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._persona:
        return {
            "core_character": orch._persona.profile.core_character,
            "speaking_style": orch._persona.profile.speaking_style,
            "emotional_preference": orch._persona.profile.emotional_preference,
        }
    return {}


@router.get("/api/persona/evolution-log")
async def persona_evolution_log(
    limit: int = Query(default=50, ge=1, le=500),
    _auth: bool = Security(verify_api_key_dep),
):
    orch = deps.orch
    if orch and orch._persona:
        return {"log": orch._persona.get_evolution_log(limit)}
    return {"log": []}


# ═══════════════════════════════════════════════════════
# 用户心理画像 API
# ═══════════════════════════════════════════════════════


def _get_pe():
    """从 orch.components 或 _persona_extractor 兜底取出 PersonaExtractor"""
    orch = deps.orch
    if orch and hasattr(orch, "components"):
        return orch.components.get("persona_extractor")
    return None


# ── 归属收窄（普通用户只看自己）─────────────────────────────
#
# 判据真源：scope 键 = `{character_id}:{session_id}`，剥掉角色段后的 session_id
# 是完整会话键（微信 `N:wxid@im.wechat` / web `N:web:hex`），**owner 段就是注册
# 用户 id** —— `persona_bank.scope_owner_uid` 正是据此解析，账号删号级联已在用
# 它（persona_bank.py:108-120 / :453-463）。因此「这份画像属于谁」不需要第二套
# 归属表，路由层收窄即可，生成主链与 persona_extractor 零改动。
#
# 另两条服务端派生来源只负责「承认一个尚无画像的本人会话键」，好让「本人新会话」
# 返回 200 insufficient_data 而不是 404（客户端传入的 session_id 必须在集合内）：
#   ① 微信：`_user_session_key_for`（training_routes 同一实现，只读复用）
#   ② web ：`deps.sessions.get_active_sessions(str(user_id))`
# 客户端自报的归属一律不采信；不在集合内 → 404（不区分「不存在」与「无权限」，
# 与 require_character_access 同口径，防状态码枚举）。
#
# 读键只来自归属过滤后的 scope：普通用户没有命中桶时返回 `scope=""` 且**不触
# bank**（`_owned_scope` + 各端点的空列表短路），杜绝「请求参数被当成读键」——
# 旧 fallback 曾把 `character_id`（或 `profile_scope(cid, '')`）直接喂给
# `bank.get_persona`，`?character_id=<他人完整 scope 键>` 即可读到他人画像。
#
# 已知边界（登记待后续批次，本次不改）：一段式遗留键（`wxid@im.wechat`）、
# owner 段非数字的历史 scope 与历史全局 `default` 桶对普通用户一律不可见
# （fail-closed，只有 admin 可见）；`/api/emotion/*` 与 `/api/persona/*` 读的
# 仍是全局 orch 内存态、无 owner 维度（本域规格外）。


def _scope_session_id(scope: str) -> str:
    """`{character_id}:{session_id}` → session_id 段；无角色前缀时返回空串。"""
    _char, sep, session = str(scope or "").partition(":")
    return session if sep else ""


async def _declared_session_ids(user_id: int, db: AsyncSession | None) -> set[str]:
    """从认证主体派生的会话键集合（微信绑定 + 本进程登记的 web 会话）。"""
    ids: set[str] = set()
    if db is not None:
        # 延迟导入：与本模块同属 api.routers，模块级互引会成环
        from api.routers.training_routes import _user_session_key_for

        wx_key = await _user_session_key_for(int(user_id), db)
        if wx_key:
            ids.add(wx_key)
    sessions = deps.sessions
    if sessions is not None:
        try:
            ids.update(str(s) for s in sessions.get_active_sessions(str(user_id)))
        except Exception as exc:  # noqa: BLE001 — 会话登记读失败不得放宽归属
            logger.debug("读取本人活跃会话失败 user=%s: %s", user_id, exc)
    return ids


async def _owned_targets(
    pe,
    uid: int,
    db: AsyncSession | None,
    character_id: str | None,
    session_id: str | None,
) -> list[str]:
    """普通用户的读写目标 scope（新→旧），只由**归属过滤后的已存 scope** 构成。

    两个入参只当**过滤条件**，任何情况下都不被当作读键：
    - `session_id` 必须属于服务端派生的允许集合，否则 404；
    - `character_id` 含 `:` 视为把完整 scope 键塞进请求（`{char}:{session}` 形状），
      同样 404 —— 否则它会在「无本人桶」时原样成为 `bank` 的读键，读到他人工信
      （角色名是文件名派生的标识，不可能含冒号）。
    """
    cid = (character_id or "").strip()
    if ":" in cid:
        raise HTTPException(status_code=404, detail="画像不存在")
    owned = [
        scope
        for scope in pe.bank.list_persona_scopes()
        if scope_owner_uid(scope) == int(uid)
    ]
    allowed = await _declared_session_ids(int(uid), db)
    allowed.update(sid for sid in (_scope_session_id(s) for s in owned) if sid)
    sid = (session_id or "").strip()
    if sid and sid not in allowed:
        raise HTTPException(status_code=404, detail="画像不存在")
    targets: list[str] = []
    for scope in owned:
        if cid and not (scope == cid or scope.startswith(f"{cid}:")):
            continue
        if sid and _scope_session_id(scope) != sid:
            continue
        targets.append(scope)
    return targets


def _owned_scope(targets: list[str]) -> tuple[str, int]:
    """非 admin 的读目标：只从本人 scope 列表取值，空即 `("", 0)`（不读库）。

    实现比规格更窄（R5-3）：`targets` 已按 `list_persona_scopes()` 的
    `last_updated` 新→旧排序，这里只取**最近的一份**（`last_updated` 最新）
    并附带匹配桶数，不跨 scope / 跨字段聚合；匹配本身是 `owner` + 可选
    `character_id` / `session_id` 的 exact 形态，比 `profile_scopes` 的
    `endswith` 更严格。
    """
    return (targets[0], len(targets)) if targets else ("", 0)


async def _actor(
    principal: AuthPrincipal | None = Depends(get_optional_principal),
) -> tuple[int, bool]:
    """认证主体 → `(主体 id, 是否 admin)`；无主体一律 401（fail-closed）。

    唯一身份依赖是 `get_optional_principal`：它负责验签 / 存在性 / is_active
    / 撤销版本并落 `request.state.auth_principal`，**本依赖只查一次 users**。

    旧实现另挂 `get_current_user` 并在 `principal is None` 时回落到该 User——
    该分支生产不可达（无 Bearer 时 `get_current_user` 必 401），却让同一请求
    重复查 users（R5-1 / R5-2）。现删除死回落：无主体直接 401，与改前
    `get_current_user` 的 fail-closed 语义保持一致。
    """
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header (Bearer token)",
        )
    return int(principal.user_id), principal.role == "admin"


@router.get("/api/psych/profile")
async def psych_profile(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    actor: tuple[int, bool] = Depends(_actor),
    db: AsyncSession = Depends(get_db),
):
    """画像摘要：按生成侧写入的 scope 取数（`{character_id}:{session_id}`）。

    旧实现固定读 `pe.user_id`（默认 `default`，请求链早已不再切换），与生成侧
    键空间错位，页面恒报"画像尚未生成"。

    分支：admin = 全量（显式过滤或「全部」）；普通用户 = 只在本人会话的 scope
    上取数，请求他人 session_id / 把完整 scope 键塞进 character_id 一律 404。
    """
    pe = _get_pe()
    if pe is None:
        return {
            "user_id": "default",
            "scope": "default",
            "scope_count": 0,
            "status": "unavailable",
            "snapshots": 0,
        }
    actor_id, is_admin = actor
    if is_admin:
        scope, scope_count = pe.resolve_profile_scope(character_id, session_id)
        return pe.profile_summary(scope, scope_count)
    targets = await _owned_targets(pe, actor_id, db, character_id, session_id)
    if not targets:
        # 无本人桶：不回显客户端可控键、不读库（与 profile_summary 缺数据分支同形）
        return {
            "user_id": "",
            "scope": "",
            "scope_count": 0,
            "status": "insufficient_data",
            "snapshots": 0,
        }
    return pe.profile_summary(targets[0], len(targets))


@router.get("/api/psych/snapshots")
async def psych_snapshots(
    limit: int = Query(default=20, ge=1, le=200),
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    actor: tuple[int, bool] = Depends(_actor),
    db: AsyncSession = Depends(get_db),
):
    pe = _get_pe()
    if pe is None:
        return {"snapshots": []}
    actor_id, is_admin = actor
    if is_admin:
        scope, _count = pe.resolve_profile_scope(character_id, session_id)
    else:
        targets = await _owned_targets(pe, actor_id, db, character_id, session_id)
        if not targets:
            return {"scope": "", "snapshots": []}
        scope, _count = _owned_scope(targets)
    snaps = pe.bank.get_recent_snapshots(user_id=scope, limit=limit)
    return {"scope": scope, "snapshots": [s.to_dict() for s in snaps]}


@router.delete("/api/psych/profile")
async def reset_psych_profile(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    actor: tuple[int, bool] = Depends(_actor),
    db: AsyncSession = Depends(get_db),
):
    """清除画像。

    admin：无过滤条件时清除**全部**已学 scope（与页面文案一致）。

    普通用户：目标一律先由 token 派生为 scope 列表再清，**绝不**调用
    `clear_profiles()` 的无过滤形态（它在 `self.user_id` 命中时还会追加默认桶，
    且无过滤即全表）——这是本端点最高危的一面。
    """
    pe = _get_pe()
    if pe is None:
        raise HTTPException(503, "PersonaExtractor未初始化")
    actor_id, is_admin = actor
    if is_admin:
        res = pe.clear_profiles(character_id, session_id)
        cleared = int(res["cleared"])
        targets = list(res["targets"])
    else:
        targets = await _owned_targets(pe, actor_id, db, character_id, session_id)
        cleared = pe.bank.clear_users(targets) if targets else 0
    if len(targets) - cleared > 0:
        raise HTTPException(
            500,
            f"心理画像未全部清除：{cleared}/{len(targets)}，请重试",
        )
    return {"status": "reset", "cleared": cleared, "scopes": targets}


@router.get("/api/psych/mental-health")
async def psych_mental_health(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    actor: tuple[int, bool] = Depends(_actor),
    db: AsyncSession = Depends(get_db),
):
    pe = _get_pe()
    if pe is None:
        return {"available": False}
    actor_id, is_admin = actor
    if is_admin:
        scope, _count = pe.resolve_profile_scope(character_id, session_id)
    else:
        targets = await _owned_targets(pe, actor_id, db, character_id, session_id)
        if not targets:
            return {"available": True, "scope": "", "data": None}
        scope, _count = _owned_scope(targets)
    persona = pe.bank.get_persona(scope)
    if persona is None:
        return {"available": True, "scope": scope, "data": None}
    return {
        "available": True,
        "scope": scope,
        "mental_health": persona.mental_health,
        "cognitive": persona.cognitive,
        "liwc": persona.liwc,
        "dark_triad": persona.dark_triad,
        "hexaco": persona.hexaco,
    }


@router.get("/api/psych/liwc")
async def psych_liwc(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    actor: tuple[int, bool] = Depends(_actor),
    db: AsyncSession = Depends(get_db),
):
    pe = _get_pe()
    if pe is None or not pe.liwc:
        return {"available": False}
    actor_id, is_admin = actor
    if is_admin:
        scope, _count = pe.resolve_profile_scope(character_id, session_id)
    else:
        targets = await _owned_targets(pe, actor_id, db, character_id, session_id)
        if not targets:
            return {"available": True, "scope": "", "data": None}
        scope, _count = _owned_scope(targets)
    persona = pe.bank.get_persona(scope)
    if persona is None or not persona.liwc:
        return {"available": True, "scope": scope, "data": None}
    return {"available": True, "scope": scope, "data": persona.liwc}
