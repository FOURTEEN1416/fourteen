"""统一账号生命周期作业（W9）：账号删除 = 跨存储遗忘。

对应承诺：``docs/legal/USER_AGREEMENT.md`` §2.5「删除即真正删除：删除账号…
对应聊天记录与记忆随之删除」。缺陷 A 的根治入口。

五阶段模型（任务书口径）::

    preview 归属 → 冻结新写入并撤销会话 → 逐 owner purge → 验证 → 审计回执

设计约束：
- **键形态经各 owner 解析**：会话键空间、裸 peer 遗留、数字 uid 直列、
  组合键（``{user_key}::{character_id}``）、账本键、ASE 索引键——各存储的
  归属判定都在自己的 owner 模块内完成，这里只下发 uid + 已知会话键；
- **幂等、可恢复、部分失败可查询**：作业账（job journal）持久化在
  ``data/lifecycle_jobs/``，owner 步骤失败即标 failed 且**不得宣称 deleted**；
  重入续跑只补未完成步骤；
- **回执只含计数**：不含 wxid、消息正文、邮箱等私密内容；
- **恢复备份治理**：删除前写入坟场（graveyard）；备份恢复把已删账号行带回
  时，``reconcile_graveyard`` 重新清除；
- **迟到写入防护**：冻结阶段经 ``utils.deletion_guard`` 封禁该 owner 的
  写入与外发（在途生成在 purge 后落地的一律丢弃）。

本模块只做编排；存储副作用都在各 owner（structured_memory / vector_memory /
event_ledger / ase_hub / affinity_state / channel_paths / connector_registry /
proactive.scheduler）新增的 purge 接口里。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update

from api.database import (
    ConsentRecord,
    InviteCode,
    User,
    UserActiveCharacter,
    UserSession,
    WechatBinding,
    WechatChannelSession,
    WechatPeerPreference,
)
from utils.deletion_guard import block_owner, read_graveyard, write_graveyard
from utils.project_paths import project_path
from utils.session_key import owner_of

logger = logging.getLogger("api.lifecycle")


def job_root() -> Path:
    """作业账目录（模块函数以便测试沙箱重定向）。"""
    return project_path("data", "lifecycle_jobs")


# ═══════════════════════════════════════════════════════
# 归属范围（Scope）解析
# ═══════════════════════════════════════════════════════


@dataclass
class AccountScope:
    """一次删除作业的归属范围。私密标识只在进程内流转，不入回执。"""

    user_id: int
    wxids: list[str] = field(default_factory=list)          # 绑定的裸 peer（含 @im.wechat）
    session_keys: list[str] = field(default_factory=list)   # 已知会话键（构造自绑定/偏好）
    character_ids: list[str] = field(default_factory=list)  # 私有角色实例 id
    email: str = ""                                          # 仅内部日志用，不入回执
    characters_dir: Path | None = None
    knowledge_dir: Path | None = None
    sqlite_db: Path | None = None
    agent_db: Path | None = None
    chroma_dir: Path | None = None


async def _resolve_scope(
    user_id: int,
    db_factory: Callable[[], Any],
    *,
    characters_dir: Path | None = None,
    knowledge_dir: Path | None = None,
    sqlite_db: Path | None = None,
    agent_db: Path | None = None,
    chroma_dir: Path | None = None,
) -> AccountScope:
    wxids: list[str] = []
    session_keys: list[str] = []
    email = ""
    async with db_factory() as db:
        user = await db.get(User, int(user_id))
        if user is not None:
            email = user.email
        rows = (
            await db.execute(
                select(WechatBinding).where(WechatBinding.user_id == int(user_id))
            )
        ).scalars().all()
        for b in rows:
            wxids.append(b.wxid)
            session_keys.append(f"{int(user_id)}:{b.wxid}")
        prefs = (
            await db.execute(
                select(WechatPeerPreference).where(
                    WechatPeerPreference.owner_user_id == int(user_id)
                )
            )
        ).scalars().all()
        for p in prefs:
            session_keys.append(f"{int(user_id)}:{p.peer_wxid}")
    return AccountScope(
        user_id=int(user_id),
        wxids=sorted(set(wxids)),
        session_keys=sorted(set(session_keys)),
        email=email,
        characters_dir=characters_dir,
        knowledge_dir=knowledge_dir,
        sqlite_db=sqlite_db,
        agent_db=agent_db,
        chroma_dir=chroma_dir,
    )


def _default_paths(
    characters_dir: Path | None, knowledge_dir: Path | None,
    sqlite_db: Path | None, agent_db: Path | None, chroma_dir: Path | None,
) -> tuple[Path, Path, Path, Path, Path]:
    from shisi.agent_plane.event_ledger import EventLedger
    from shisi.memory.legacy import structured_memory as _sm_mod

    return (
        Path(characters_dir) if characters_dir else project_path("config", "characters"),
        Path(knowledge_dir) if knowledge_dir else project_path("data", "knowledge"),
        Path(sqlite_db) if sqlite_db else _sm_mod.default_db_path(),
        Path(agent_db) if agent_db else EventLedger.default_path(),
        Path(chroma_dir) if chroma_dir else project_path("data", "chroma_db"),
    )


# ═══════════════════════════════════════════════════════
# Owner 步骤
# ═══════════════════════════════════════════════════════


@dataclass
class OwnerStep:
    """单个存储 owner 的生命周期动作（preview/purge/verify 均返回纯计数）。"""

    name: str
    preview: Callable[..., Any]
    purge: Callable[..., Any]
    verify: Callable[..., Any]


def _is_wxid_like(value: str) -> bool:
    # 裸 peer 形态：自带 @im.wechat 或不含 "数字:" owner 前缀
    from utils.session_key import parse

    return parse(value).owner is None


async def _freeze_revoke(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    """冻结新写入 + 撤销会话（第一步，先于一切清除）。

    - is_active=False：BYOK/会话模型按现值拒绝该账号的一切新消费；
    - token_version 自增（W1 撤销语义）：存量 access/refresh 立即失效，
      且备份恢复带回的旧 token_version 同样对不上；
    - refresh 会话行吊销；
    - deletion_guard 封禁 + 坟场登记（迟到写入/外发防护 + 恢复治理）。
    """
    from api.auth_jwt import bump_token_version

    revoked = 0
    async with db_factory() as db:
        user = await db.get(User, scope.user_id)
        frozen = False
        if user is not None:
            user.is_active = False
            bump_token_version(user)
            frozen = True
        result = await db.execute(
            delete(UserSession).where(UserSession.user_id == scope.user_id)
        )
        revoked = int(result.rowcount or 0)
        await db.commit()
    block_owner(scope.user_id)
    _graveyard_register(scope.user_id)
    return {"frozen": int(frozen), "sessions_revoked": revoked}


async def _freeze_verify(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    async with db_factory() as db:
        n = len((
            await db.execute(
                select(UserSession.id).where(UserSession.user_id == scope.user_id)
            )
        ).scalars().all())
    return {"sessions_revoked": n}


async def _users_db_rows(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    """users.db 全部归属行删除（最后一步；账号主体随本步出库）。"""
    counts: dict[str, int] = {}
    async with db_factory() as db:
        for name, model, col in (
            ("wechat_peer_preferences", WechatPeerPreference, "owner_user_id"),
            ("user_active_characters", UserActiveCharacter, "user_id"),
            ("wechat_bindings", WechatBinding, "user_id"),
            ("wechat_channel_sessions", WechatChannelSession, "user_id"),
            ("consent_records", ConsentRecord, "user_id"),
        ):
            result = await db.execute(delete(model).where(getattr(model, col) == scope.user_id))
            counts[name] = int(result.rowcount or 0)
        # 邀请码引用置空（FK SET NULL 语义；在未开 pragma 的引擎上同样成立）
        for col in ("created_by", "used_by"):
            await db.execute(
                update(InviteCode)
                .where(getattr(InviteCode, col) == scope.user_id)
                .values(**{col: None})
            )
        user = await db.get(User, scope.user_id)
        counts["user"] = 0
        if user is not None:
            await db.delete(user)
            counts["user"] = 1
        await db.commit()
    return counts


async def _users_db_verify(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    residual: dict[str, int] = {}
    async with db_factory() as db:
        user = await db.get(User, scope.user_id)
        residual["user"] = int(user is not None)
        for name, model, col in (
            ("wechat_bindings", WechatBinding, "user_id"),
            ("wechat_channel_sessions", WechatChannelSession, "user_id"),
            ("wechat_peer_preferences", WechatPeerPreference, "owner_user_id"),
            ("consent_records", ConsentRecord, "user_id"),
            ("user_active_characters", UserActiveCharacter, "user_id"),
        ):
            residual[name] = int(await _count_rows(db, model, col, scope.user_id))
    return residual


async def _count_rows(db: Any, model: Any, col: str, uid: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(model).where(getattr(model, col) == uid)
    )
    return int(result.scalar() or 0)


async def _users_db_preview(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    counts: dict[str, int] = {}
    async with db_factory() as db:
        for name, model, col in (
            ("wechat_bindings", WechatBinding, "user_id"),
            ("wechat_channel_sessions", WechatChannelSession, "user_id"),
            ("wechat_peer_preferences", WechatPeerPreference, "owner_user_id"),
            ("consent_records", ConsentRecord, "user_id"),
            ("user_active_characters", UserActiveCharacter, "user_id"),
            ("user_sessions", UserSession, "user_id"),
        ):
            counts[name] = await _count_rows(db, model, col, scope.user_id)
    user = await db.get(User, scope.user_id)
    counts["user"] = int(user is not None)
    return counts


def _character_instances_purge(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    """私有角色实例清除（D2：模板只读、私有实例随账号出库）。

    归属判定复用 W1 的 ``card_owner_key``（公共模板/无主卡一律保留）；
    卡删除连带知识索引、成就行、绑定与偏好引用重置、个人激活行清除、
    向量 character_id 派生。
    """
    from api.routers.character_routes import card_owner_key
    from shisi.memory.legacy.vector_memory import VectorMemory

    characters_dir, knowledge_dir, _sm, _agent, chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    owner = str(scope.user_id)
    removed_cards = 0
    removed_knowledge = 0
    cids: list[str] = []
    if characters_dir.exists():
        for path in sorted(characters_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if card_owner_key(card) != owner:
                continue
            cid = str(card.get("id") or path.stem)
            cids.append(cid)
            path.unlink(missing_ok=True)
            removed_cards += 1
            kpath = knowledge_dir / f"{cid}.json"
            if kpath.exists():
                kpath.unlink(missing_ok=True)
                removed_knowledge += 1
    scope.character_ids.extend(cids)

    async def _db_part() -> dict:
        counts = {"achievements": 0, "refs_reset": 0, "active_rows": 0}
        from api.database import CharacterAchievement

        async with db_factory() as db:
            for cid in cids:
                r = await db.execute(
                    delete(CharacterAchievement).where(
                        CharacterAchievement.character_id == cid
                    )
                )
                counts["achievements"] += int(r.rowcount or 0)
                r = await db.execute(
                    update(UserActiveCharacter)
                    .where(UserActiveCharacter.character_id == cid)
                    .values(character_id="default")
                )
                counts["active_rows"] += int(r.rowcount or 0)
            r = await db.execute(
                update(WechatBinding)
                .where(WechatBinding.character_card_id.in_(cids))
                .values(character_card_id="default")
            )
            counts["refs_reset"] += int(r.rowcount or 0)
            r = await db.execute(
                update(WechatPeerPreference)
                .where(WechatPeerPreference.character_card_id.in_(cids))
                .values(character_card_id="default")
            )
            counts["refs_reset"] += int(r.rowcount or 0)
            await db.commit()
        return counts

    async def _vectors() -> int:
        if not cids:
            return 0
        try:
            vm = VectorMemory(str(chroma))
        except Exception:  # noqa: BLE001
            return 0
        receipt = vm.purge_owner_data([], [], character_ids=cids)
        return int(sum(receipt.values()))

    async def _go() -> dict:
        counts = await _db_part()
        counts["knowledge"] = removed_knowledge
        counts["vector_refs"] = await _vectors()
        counts["cards"] = removed_cards
        return counts

    return _go()


def _character_instances_verify(scope: AccountScope, db_factory: Callable[[], Any]) -> dict:
    from api.routers.character_routes import card_owner_key

    async def _go() -> dict:
        from api.database import CharacterAchievement

        residual: dict[str, int] = {"cards": 0, "achievements": 0}
        characters_dir, knowledge_dir, _sm, _agent, _chroma = _default_paths(
            scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
            scope.agent_db, scope.chroma_dir,
        )
        if characters_dir.exists():
            for path in characters_dir.glob("*.json"):
                try:
                    card = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if card_owner_key(card) == str(scope.user_id):
                    residual["cards"] += 1
        async with db_factory() as db:
            residual["achievements"] = len((
                await db.execute(
                    select(CharacterAchievement.id).where(
                        CharacterAchievement.character_id.in_(
                            scope.character_ids or ["__none__"]
                        )
                    )
                )
            ).scalars().all())
        return residual

    return _go()


def _memory_sqlite_purge(scope: AccountScope) -> dict:
    from shisi.memory.legacy.structured_memory import StructuredMemory

    _chars, _kn, sqlite_db, _agent, _chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    sm = StructuredMemory(str(sqlite_db))
    return sm.purge_owner_data(
        scope.user_id, session_keys=scope.session_keys, bare_peers=scope.wxids
    )


def _memory_sqlite_verify(scope: AccountScope) -> dict:
    from shisi.memory.legacy.structured_memory import StructuredMemory

    _chars, _kn, sqlite_db, _agent, _chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    sm = StructuredMemory(str(sqlite_db))
    return sm.verify_owner_purged(scope.user_id, session_keys=scope.session_keys,
                                  bare_peers=scope.wxids)


def _memory_sqlite_preview(scope: AccountScope) -> dict:
    from shisi.memory.legacy.structured_memory import StructuredMemory

    _chars, _kn, sqlite_db, _agent, _chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    sm = StructuredMemory(str(sqlite_db))
    return sm.count_owner_rows(scope.user_id, session_keys=scope.session_keys,
                               bare_peers=scope.wxids)


def _owned_keys_of(scope: AccountScope, keys: list[str]) -> list[str]:
    """按 owner 段筛选（uid 前缀或显式键），供索引型存储解析。"""
    uid = scope.user_id
    out = []
    known = set(scope.session_keys) | set(scope.wxids)
    for k in keys:
        if k in known:
            out.append(k)
            continue
        o = owner_of(k)
        if o is None:
            left, sep, _ = str(k).partition(":")
            if sep and left.isdigit() and int(left) == uid:
                out.append(k)
        elif o == uid:
            out.append(k)
    return out


def _persona_profile_purge(scope: AccountScope) -> dict:
    """W13 · D11：user_persona / user_persona_snapshots 归属清除。

    归属判定在 owner（persona_bank.scope_owner_uid）内完成：scope 剥
    character 前缀后 ``owner_of(session_id) == uid``；他人与无主 scope 保留。
    """
    bank = _persona_profile_bank(scope)
    try:
        return bank.purge_owner_scopes(scope.user_id)
    finally:
        _close_persona_bank(bank)


def _persona_profile_verify(scope: AccountScope) -> dict:
    bank = _persona_profile_bank(scope)
    try:
        return bank.count_owner_scopes(scope.user_id)
    finally:
        _close_persona_bank(bank)


def _persona_profile_bank(scope: AccountScope):
    from persona_extractor.persona_bank import UserPersonaBank

    _chars, _kn, sqlite_db, _agent, _chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    return UserPersonaBank(str(sqlite_db))


def _close_persona_bank(bank: Any) -> None:
    conn = bank.get_connection()
    if conn is not None:
        conn.close()


def _memory_vectors_purge(scope: AccountScope) -> dict:
    from shisi.memory.legacy.vector_memory import VectorMemory

    _chars, _kn, _sm, _agent, chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    vm = VectorMemory(str(chroma))
    keys = sorted(set(scope.session_keys) | set(scope.wxids))
    return vm.purge_owner_data(
        user_keys=keys, session_ids=keys, character_ids=scope.character_ids
    )


def _memory_vectors_verify(scope: AccountScope) -> dict:
    from shisi.memory.legacy.vector_memory import VectorMemory

    _chars, _kn, _sm, _agent, chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    vm = VectorMemory(str(chroma))
    keys = sorted(set(scope.session_keys) | set(scope.wxids))
    return vm.count_owner_data(
        user_keys=keys, session_ids=keys, character_ids=scope.character_ids
    )


def _event_ledger_purge(scope: AccountScope) -> dict:
    from shisi.agent_plane.event_ledger import EventLedger

    _chars, _kn, _sm, agent_db, _chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    ledger = EventLedger(str(agent_db))
    removed = ledger.purge_owner_sessions(
        scope.user_id, [*scope.session_keys, *scope.wxids]
    )
    return {"events": removed}


def _event_ledger_verify(scope: AccountScope) -> dict:
    from shisi.agent_plane.event_ledger import EventLedger

    _chars, _kn, _sm, agent_db, _chroma = _default_paths(
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    )
    ledger = EventLedger(str(agent_db))
    return {"events": ledger.count_owner_sessions(
        scope.user_id, [*scope.session_keys, *scope.wxids]
    )}


def _ase_states_purge(scope: AccountScope) -> dict:
    import proactive.ase_hub as ase_hub

    raw = ase_hub._read_index_raw()
    keys = _owned_keys_of(scope, list(raw))
    removed = ase_hub.purge_user_states(keys)
    return {"states": removed}


def _ase_states_verify(scope: AccountScope) -> dict:
    import proactive.ase_hub as ase_hub

    raw = ase_hub._read_index_raw()
    return {"states": len(_owned_keys_of(scope, list(raw)))}


def _affinity_purge(scope: AccountScope) -> dict:
    from utils import affinity_state

    keys = sorted(set(scope.session_keys) | {str(scope.user_id)} | set(scope.wxids))
    # owner_uid 前缀兜底：覆盖枚举不到的 web hex 等会话键的点存条目
    removed = affinity_state.purge_owner(keys, owner_uid=scope.user_id)
    return {"points": removed}


def _affinity_verify(scope: AccountScope) -> dict:
    from utils import affinity_state

    keys = sorted(set(scope.session_keys) | {str(scope.user_id)} | set(scope.wxids))
    return {"points": affinity_state.count_owner(keys, owner_uid=scope.user_id)}


def _wechat_channels_purge(scope: AccountScope) -> dict:

    from wechat_direct import channel_paths
    from wechat_direct.connector_registry import get_registry

    removed = 0
    try:
        removed = get_registry().purge_user(scope.user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("通道连接器停用失败 uid=%s: %s", scope.user_id, e)
    disk = channel_paths.remove_user_sessions(scope.user_id)
    return {"connectors": removed, "disk_dirs": int(bool(disk))}


def _wechat_channels_verify(scope: AccountScope) -> dict:
    from wechat_direct import channel_paths
    from wechat_direct.connector_registry import get_registry

    try:
        live = len(get_registry().list_for_user(scope.user_id))
    except Exception:  # noqa: BLE001
        live = 0
    root = channel_paths.sessions_root() / str(scope.user_id)
    return {"connectors": live, "disk_dirs": int(root.exists())}


def _purge_throttle_file(
    owner_id: int, session_keys: list[str], bare_peers: list[str] | None = None,
) -> dict:
    """无调度器实例时（脚本/测试）直接对跨 worker 配置文件做同等清除。"""
    from proactive.scheduler import ProactiveScheduler
    from utils import json_state

    config_path = ProactiveScheduler._CONFIG_PATH
    explicit = {str(k) for k in list(session_keys) + list(bare_peers or [])}

    def _owned(key: str) -> bool:
        if key in explicit:
            return True
        left, sep, _rest = key.partition(":")
        return bool(sep) and left.isdigit() and int(left) == owner_id

    removed = 0

    def _mutate(data: dict) -> None:
        nonlocal removed
        throttle = data.get("throttle")
        if not isinstance(throttle, dict):
            return
        for section in ("llm_proactive_next_ok", "deliver_fail_counts",
                        "disabled_event_day"):
            seg = throttle.get(section)
            if isinstance(seg, dict):
                victims = [k for k in list(seg) if _owned(str(k))]
                for k in victims:
                    seg.pop(k, None)
                    removed += 1
        sent = throttle.get("important_dates_sent")
        if isinstance(sent, list):
            keep = [
                x for x in sent
                if not (len(str(x).split("|")) >= 2 and _owned(str(x).split("|")[1]))
            ]
            removed += len(sent) - len(keep)
            throttle["important_dates_sent"] = keep

    try:
        json_state.update_json(config_path, _mutate)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"节流账本文件清除失败: {e}") from e
    return {"entries": removed}


def _scheduler_throttle_purge(scope: AccountScope) -> dict:
    sched = None
    try:
        from api.deps import deps

        sched = (getattr(deps.orch, "components", None) or {}).get("scheduler")
    except Exception:  # noqa: BLE001
        sched = None
    if sched is not None and hasattr(sched, "purge_throttle_for"):
        removed = sched.purge_throttle_for(scope.user_id, scope.session_keys)
        return {"entries": int(removed)}
    return _purge_throttle_file(scope.user_id, scope.session_keys, scope.wxids)


def _scheduler_throttle_verify(scope: AccountScope) -> dict:
    from proactive.scheduler import ProactiveScheduler
    from utils import json_state

    explicit = {str(k) for k in [*scope.session_keys, *scope.wxids]}

    def _owned(key: str) -> bool:
        if key in explicit:
            return True
        left, sep, _rest = key.partition(":")
        return bool(sep) and left.isdigit() and int(left) == scope.user_id

    data = json_state.read_json(ProactiveScheduler._CONFIG_PATH, default={}) or {}
    throttle = data.get("throttle") or {}
    n = 0
    for section in ("llm_proactive_next_ok", "deliver_fail_counts",
                    "disabled_event_day"):
        seg = throttle.get(section)
        if isinstance(seg, dict):
            n += sum(1 for k in seg if _owned(str(k)))
    sent = throttle.get("important_dates_sent")
    if isinstance(sent, list):
        n += sum(
            1 for x in sent
            if len(str(x).split("|")) >= 2 and _owned(str(x).split("|")[1])
        )
    return {"entries": n}


def _process_caches_purge(scope: AccountScope) -> dict:
    """进程内缓存驱逐（尽力而为；跨 worker 的剩余进程随 TTL/门禁失效）。"""
    removed = 0
    try:
        from api.deps import deps

        gf = getattr(deps, "gf", None)
        if gf is not None and hasattr(gf, "remove_user"):
            victims = [str(scope.user_id), *scope.session_keys]
            for key in victims:
                try:
                    if gf.remove_user(key):
                        removed += 1
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return {"user_instances": removed}


def _process_caches_verify(scope: AccountScope) -> dict:
    # 进程缓存非持久存储，验证阶段无可查真源；返回占位计数。
    return {"user_instances": 0}


_OWNER_STEPS: dict[str, OwnerStep] = {
    name: OwnerStep(name=name, preview=preview, purge=purge, verify=verify)
    for name, preview, purge, verify in (
        ("freeze_revoke", None, _freeze_revoke, _freeze_verify),
        ("character_instances", None, _character_instances_purge,
         _character_instances_verify),
        ("memory_sqlite", _memory_sqlite_preview, _memory_sqlite_purge,
         _memory_sqlite_verify),
        # W13 · D11：心理画像（user_persona/user_persona_snapshots，同
        # sqlite.db）——插在 memory_sqlite 之后同库清理；verify residual
        # 机制提供「目标 owner 删光、他人保留」的行数断言。
        ("persona_profile", None, _persona_profile_purge, _persona_profile_verify),
        ("memory_vectors", None, _memory_vectors_purge, _memory_vectors_verify),
        ("event_ledger", None, _event_ledger_purge, _event_ledger_verify),
        ("ase_states", None, _ase_states_purge, _ase_states_verify),
        ("affinity_points", None, _affinity_purge, _affinity_verify),
        ("wechat_channels", None, _wechat_channels_purge, _wechat_channels_verify),
        ("scheduler_throttle", None, _scheduler_throttle_purge,
         _scheduler_throttle_verify),
        ("process_caches", None, _process_caches_purge, _process_caches_verify),
        ("users_db", _users_db_preview, _users_db_rows, _users_db_verify),
    )
}

# 冻结步骤必须先于一切清除；users_db 行删除必须最后。
_STEP_ORDER = [
    "freeze_revoke", "character_instances", "memory_sqlite", "persona_profile",
    "memory_vectors", "event_ledger", "ase_states", "affinity_points",
    "wechat_channels", "scheduler_throttle", "process_caches", "users_db",
]


# ═══════════════════════════════════════════════════════
# 作业账（journal）
# ═══════════════════════════════════════════════════════


def _job_path(user_id: int, job_dir: Path | None = None) -> Path:
    root = Path(job_dir) if job_dir else job_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"acct-{int(user_id)}.json"


def _load_job(user_id: int, job_dir: Path | None = None) -> dict | None:
    try:
        return json.loads(_job_path(user_id, job_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_job(job: dict, job_dir: Path | None = None) -> None:
    job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _job_path(job["user_id"], job_dir).write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _graveyard_register(user_id: int) -> None:
    def _mutate(data: dict) -> None:
        data[str(int(user_id))] = {
            "deleted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    write_graveyard(_mutate)


def graveyard_contains(user_id: int) -> bool:
    return str(int(user_id)) in read_graveyard()


# ═══════════════════════════════════════════════════════
# 编排入口
# ═══════════════════════════════════════════════════════


async def _run_step(step: OwnerStep, fn: Callable[..., Any], scope: AccountScope,
                    db_factory: Callable[[], Any]) -> Any:
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "db_factory" in params:
        result = fn(scope, db_factory=db_factory)
    else:
        # 纯同步 owner（磁盘/SQLite IO）放到线程，避免阻塞事件循环
        result = await asyncio.to_thread(fn, scope)
    if inspect.isawaitable(result):
        result = await result
    return result


async def delete_account_everywhere(
    user_id: int,
    *,
    db_factory: Callable[[], Any] | None = None,
    characters_dir: Path | None = None,
    knowledge_dir: Path | None = None,
    sqlite_db: Path | None = None,
    agent_db: Path | None = None,
    chroma_dir: Path | None = None,
    job_dir: Path | None = None,
) -> dict:
    """删除账号的全部跨存储数据；完成前不得宣称 deleted。

    重入语义：该账号存在未完成作业账时续跑（跳过已完成步骤）；账号已不存在
    （或再次调用）时仍执行全部清除与验证（幂等）。
    """
    if db_factory is None:
        db_factory = _default_db_factory()

    scope = await _resolve_scope(
        int(user_id), db_factory,
        characters_dir=characters_dir, knowledge_dir=knowledge_dir,
        sqlite_db=sqlite_db, agent_db=agent_db, chroma_dir=chroma_dir,
    )

    # 存储路径解析：显式传参优先；续跑未显式传参者沿用首次作业记录的路径
    # （恢复备份场景的 reconcile 不传路径也能落到原作业的同一批存储）。
    store_names = ("characters_dir", "knowledge_dir", "sqlite_db", "agent_db", "chroma_dir")
    passed = (characters_dir, knowledge_dir, sqlite_db, agent_db, chroma_dir)
    job = _load_job(int(user_id), job_dir)
    resume_stores = (job or {}).get("stores") or {}
    resolved = list(_default_paths(*passed))
    for i, (name, val) in enumerate(zip(store_names, passed, strict=True)):
        if val is not None:
            resolved[i] = Path(val)
        elif resume_stores.get(name):
            resolved[i] = Path(resume_stores[name])
    (
        scope.characters_dir, scope.knowledge_dir, scope.sqlite_db,
        scope.agent_db, scope.chroma_dir,
    ) = resolved

    if job is None or job.get("status") == "completed":
        job = {
            "job_id": f"acct-{int(user_id)}",
            "user_id": int(user_id),
            "status": "running",
            "completed": False,
            "steps": {},
        }
    job["stores"] = {
        name: str(val) for name, val in zip(store_names, resolved, strict=True)
    }
    job["status"] = "running"
    _save_job(job, job_dir)

    for name in _STEP_ORDER:
        step = _OWNER_STEPS[name]
        record = job["steps"].setdefault(name, {"status": "pending"})
        if record.get("status") == "done" and record.get("verified") is True:
            continue
        try:
            receipt = await _run_step(step, step.purge, scope, db_factory)
            record["status"] = "done"
            record["receipt"] = receipt
            record.pop("error", None)
            verification = await _run_step(step, step.verify, scope, db_factory)
            residual = sum(
                int(v) for v in (verification or {}).values() if isinstance(v, (int, float))
            )
            record["verified"] = residual == 0
            record["verify_residual"] = int(residual)
            if residual != 0:
                # 验证不过 = 未清干净：作业不得宣称完成
                record["status"] = "failed"
                record["error"] = "VerifyResidual"
                job["status"] = "failed"
                job["completed"] = False
                _save_job(job, job_dir)
                logger.error(
                    "账号清除验证有残留 uid=%s step=%s residual=%s",
                    user_id, name, residual,
                )
                return _public_job(job)
        except Exception as e:  # noqa: BLE001
            record["status"] = "failed"
            record["error"] = f"{type(e).__name__}"
            record["verified"] = False
            job["status"] = "failed"
            job["completed"] = False
            _save_job(job, job_dir)
            logger.error("账号清除步骤失败 uid=%s step=%s: %s", user_id, name, e)
            return _public_job(job)
        _save_job(job, job_dir)

    job["status"] = "completed"
    job["completed"] = True
    _save_job(job, job_dir)
    logger.info("账号跨存储清除完成 uid=%s", user_id)
    return _public_job(job)


def _public_job(job: dict) -> dict:
    """回执对外形态：只含状态与计数，剔除任何私密内容。"""
    return {
        "job_id": job.get("job_id"),
        "user_id": job.get("user_id"),
        "status": job.get("status"),
        "completed": bool(job.get("completed")),
        "steps": job.get("steps", {}),
    }


async def job_status(job_id: str, job_dir: Path | None = None) -> dict:
    path = (Path(job_dir) if job_dir else job_root()) / f"{job_id}.json"
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"job_id": job_id, "status": "unknown", "completed": False, "steps": {}}
    return _public_job(job)


async def preview_account_deletion(
    user_id: int,
    *,
    db_factory: Callable[[], Any] | None = None,
    characters_dir: Path | None = None,
    knowledge_dir: Path | None = None,
    sqlite_db: Path | None = None,
    agent_db: Path | None = None,
    chroma_dir: Path | None = None,
    job_dir: Path | None = None,
) -> dict:
    """删除前归属清单：逐 owner 计数（只读，不含私密内容）。"""
    if db_factory is None:
        db_factory = _default_db_factory()

    scope = await _resolve_scope(
        int(user_id), db_factory,
        characters_dir=characters_dir, knowledge_dir=knowledge_dir,
        sqlite_db=sqlite_db, agent_db=agent_db, chroma_dir=chroma_dir,
    )
    owners: dict[str, dict] = {}
    for name in _STEP_ORDER:
        step = _OWNER_STEPS[name]
        if name == "freeze_revoke":
            continue
        fn = step.preview or step.verify  # verify 均为只读计数
        owners[name] = await _run_step(step, fn, scope, db_factory)
    return {
        "user_id": int(user_id),
        "owners": owners,
        "graveyard": graveyard_contains(int(user_id)),
    }


async def reconcile_graveyard(
    *, db_factory: Callable[[], Any] | None = None, job_dir: Path | None = None
) -> list[int]:
    """坟场对账：备份恢复把已删账号带回来时再次清除（幂等）。

    在登录/注册/刷新等会话获取路径调用。返回本次重新清除的 uid 列表。
    """
    repurged: list[int] = []
    for key in read_graveyard():
        try:
            uid = int(key)
        except (TypeError, ValueError):
            continue
        if db_factory is None:
            db_factory = _default_db_factory()
        async with db_factory() as db:
            user = await db.get(User, uid)
            exists = user is not None
        if not exists:
            continue
        logger.warning("坟场对账：已删账号 uid=%s 重新出现（疑似备份恢复），再次清除", uid)
        await delete_account_everywhere(uid, db_factory=db_factory, job_dir=job_dir)
        repurged.append(uid)
    return repurged


# 后台注销任务的强引用集（防 Task 被 GC 半途丢弃）
_BG_TASKS: set[asyncio.Task[None]] = set()


def _dispatch_background_delete(user_id: int, **kwargs: Any) -> None:
    """把 queued 注销作业真实派发到当前事件循环的后台任务。

    P0 根治：旧实现只写 ``status=queued`` 作业账后 return，无任何消费者，
    自助注销实际永不执行。``delete_account_everywhere`` 自带幂等/续跑/
    逐步骤失败落账（status=failed + 步骤原因）；本包装只兜其外的顶层
    异常（scope 解析等），保证作业账同样落 ``failed`` + 原因——按回执
    纪律只记异常类型名，不落异常文本（防私密路径外泄）。
    """

    async def _run() -> None:
        try:
            await delete_account_everywhere(int(user_id), **kwargs)
        except Exception as e:  # noqa: BLE001
            logger.error("后台注销作业失败 uid=%s: %s", user_id, e)
            try:
                job = _load_job(int(user_id)) or {
                    "job_id": f"acct-{int(user_id)}",
                    "user_id": int(user_id),
                    "steps": {},
                }
                job["status"] = "failed"
                job["completed"] = False
                job["error"] = f"{type(e).__name__}"
                _save_job(job)
            except Exception:  # noqa: BLE001
                logger.exception("后台注销作业失败原因落账失败 uid=%s", user_id)

    task = asyncio.create_task(_run())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def self_service_delete(
    user_id: int,
    *,
    db_factory: Callable[[], Any] | None = None,
    background: bool = True,
    characters_dir: Path | None = None,
    knowledge_dir: Path | None = None,
    sqlite_db: Path | None = None,
    agent_db: Path | None = None,
    chroma_dir: Path | None = None,
) -> dict:
    """自助注销：立即冻结（停用 + 撤销会话 + 坟场 + 写入封禁），清除异步进行。

    background=True 时冻结仍同步完成，随后经 ``_dispatch_background_delete``
    真实派发后台清除（queued 不再悬挂）；返回作业受理状态，
    **清除完成前响应不含任何「已删除」宣称**。
    """
    scope = await _resolve_scope(
        int(user_id), db_factory or _default_db_factory(),
        characters_dir=characters_dir, knowledge_dir=knowledge_dir,
        sqlite_db=sqlite_db, agent_db=agent_db, chroma_dir=chroma_dir,
    )
    await _freeze_revoke(scope, db_factory or _default_db_factory())
    if background:
        job = {
            "job_id": f"acct-{int(user_id)}",
            "user_id": int(user_id),
            "status": "queued",
            "completed": False,
            "steps": {},
        }
        _save_job(job)
        # P0：queued 不再悬挂 —— 真实派发后台清除（幂等语义在
        # delete_account_everywhere 内保证，失败同样落作业账）
        _dispatch_background_delete(
            int(user_id),
            db_factory=db_factory or _default_db_factory(),
            characters_dir=characters_dir, knowledge_dir=knowledge_dir,
            sqlite_db=sqlite_db, agent_db=agent_db, chroma_dir=chroma_dir,
        )
        return _public_job(job)
    return await delete_account_everywhere(
        int(user_id),
        db_factory=db_factory or _default_db_factory(),
        characters_dir=characters_dir, knowledge_dir=knowledge_dir,
        sqlite_db=sqlite_db, agent_db=agent_db, chroma_dir=chroma_dir,
    )


def _default_db_factory() -> Callable[[], Any]:
    from api.database import _async_session

    return _async_session


# ═══════════════════════════════════════════════════════
# 账号全量导出（storage manifest + 分页）
# ═══════════════════════════════════════════════════════


def _export_sm(sm: Any, sqlite_db: Path | None) -> Any:
    if sm is not None:
        return sm
    from shisi.memory.legacy.structured_memory import StructuredMemory

    path = Path(sqlite_db) if sqlite_db else None
    if path is not None:
        return StructuredMemory(str(path))
    from shisi.memory.legacy import structured_memory as _sm_mod

    return StructuredMemory(str(_sm_mod.default_db_path()))


def export_chats_page(
    sm: Any, session_key: str, before_id: int = 0, limit: int = 500,
) -> dict:
    return sm.export_owner_chats_page(session_key, before_id=before_id, limit=limit)


async def export_account_manifest(
    user_id: int,
    *,
    db_factory: Callable[[], Any] | None = None,
    sm: Any = None,
    sqlite_db: Path | None = None,
) -> dict:
    """全量导出清单：协议 §2.1 各类别计数 + 会话键清单（本人数据，含键）。"""
    if db_factory is None:
        from api.database import _async_session as db_factory

    sm = _export_sm(sm, sqlite_db)
    session_keys = sorted(
        await asyncio.to_thread(_owned_session_keys_sync, sm, int(user_id))
    )
    counts = await asyncio.to_thread(
        lambda: sm.count_owner_rows(int(user_id), session_keys=session_keys,
                                    bare_peers=[])
    )
    async with db_factory() as db:
        consents = len((
            await db.execute(
                select(ConsentRecord.id).where(ConsentRecord.user_id == int(user_id))
            )
        ).scalars().all())
        bindings = len((
            await db.execute(
                select(WechatBinding.id).where(WechatBinding.user_id == int(user_id))
            )
        ).scalars().all())
        channels = len((
            await db.execute(
                select(WechatChannelSession.id).where(
                    WechatChannelSession.user_id == int(user_id)
                )
            )
        ).scalars().all())
    return {
        "user_id": int(user_id),
        "session_keys": session_keys,
        "categories": {
            "chats": int(counts.get("chat_history", 0)),
            "facts": int(counts.get("user_facts", 0)),
            "reflections": int(counts.get("reflections", 0)),
            "reminders": int(counts.get("reminders", 0)),
            "profile": int(counts.get("user_profile", 0)),
            "diary": int(counts.get("daily_summaries", 0)),
            "consents": consents,
            "wechat_bindings": bindings,
            "wechat_channels": channels,
        },
    }


def _owned_session_keys_sync(sm: Any, user_id: int) -> list[str]:
    """导出用会话键枚举：owner 前缀 + user_key 列去重（本人数据，供分页遍历）。"""
    uid_prefix = f"{int(user_id)}:"
    keys: set[str] = set()
    with sm.get_connection() as conn:
        for r in conn.execute(
            "SELECT DISTINCT session_id FROM chat_history WHERE session_id LIKE ? "
            "OR user_key LIKE ?",
            (uid_prefix + "%", uid_prefix + "%"),
        ):
            if r["session_id"]:
                keys.add(str(r["session_id"]))
    return sorted(keys)
