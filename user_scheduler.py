"""
用户调度器 — 多微信用户核心调度器

核心设计：
- 每个用户有独立的 EmotionEngine 实例（情感完全隔离）
- 所有用户共享 LLM/安全层/工具/RAG（省资源）
- 消息进来 → 换入用户专属 engine → 处理 → 换出
- 控制台前端 API 管理用户（查聊天、换角色、重置）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from my_character.emotion_engine import AffinityLevel, EmotionEngine
from shisi.affinity import scale as affinity_scale
from utils import affinity_state, session_key
from utils.project_paths import resolve_project_path

logger = logging.getLogger("user_scheduler")

# 用户专属 LLM 配置（BYOK）缓存 TTL：配置变更罕见，避免每条微信消息都查库；
# 改完最多 30s 生效。
_LLM_CFG_CACHE_TTL = 30.0

# 绑定缓存向 SQLite 真源回灌的节流窗（缺陷 D：跨 worker 换角色/新绑定）。
_BINDING_REFRESH_SECONDS = 30.0


@dataclass
class UserInstance:
    """单个用户的会话实例数据"""

    user_id: str
    nickname: str = ""
    character_card_id: str = "default"
    session_id: str = ""

    # 用户专属的情感引擎
    emotion_engine: EmotionEngine | None = None
    emotion_engines: dict[str, EmotionEngine] = field(default_factory=dict)

    # 统计
    total_chats: int = 0
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    is_active: bool = True

    @property
    def affinity_level(self) -> int:
        if self.emotion_engine:
            return self.emotion_engine.state.affinity
        return 0

    @property
    def affinity_name(self) -> str:
        return AffinityLevel.get_name(self.affinity_level)

    @property
    def primary_emotion(self) -> str:
        if self.emotion_engine:
            return self.emotion_engine.state.primary_emotion.value
        return "平常"


class UserManager:
    """
    用户调度器

    用法:
        mgr = UserManager(orchestrator)
        result = await mgr.process_message("wx_小明", "你好呀")
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator
        self._users: dict[str, UserInstance] = {}
        # ── 微信绑定缓存（wxid → binding dict） ──
        # 在 API 操作绑定或启动时预加载，让 _get_or_create 能查询角色信息
        self._bindings: dict[str, dict] = {}
        # ── 锁策略说明 ──────────────────────────────────────────────
        # 两个独立锁，分别保护 _users 和 _bindings：
        #
        # _users_lock (threading.Lock)：
        #   保护 _get_or_create / remove_user / reset_user
        #   这些方法可能从同步或异步上下文调用，且操作 O(1) dict，
        #   用 threading.Lock 足以（锁持有时间 < 1μs，不阻塞事件循环）。
        #
        # _bindings_lock (asyncio.Lock)：
        #   保护 load_bindings / upsert_binding / remove_binding
        #   这些方法只从异步 API handler 调用，用 asyncio.Lock
        #   避免在长时间无响应场景下阻塞事件循环。
        #
        # _get_or_create 读 _bindings 时持有 _users_lock 但不持有
        # _bindings_lock。CPython GIL 保证 dict.get() 是原子的，
        # 最坏情况读到旧值，下一条消息即更新。
        # ────────────────────────────────────────────────────────────
        self._users_lock = threading.Lock()
        self._bindings_lock = asyncio.Lock()
        # 跨 worker 真源回灌的节流簿（缺陷 D）。用独立 threading.Lock：
        # 回灌由 APScheduler 工作线程发起，_bindings_lock 是 asyncio.Lock，
        # 在别的线程 await 它没有意义。
        self._binding_refresh_lock = threading.Lock()
        self._last_binding_refresh = 0.0

        # 从 orchestrator 的共享情感引擎提取配置
        self._engine_template = None
        if hasattr(orchestrator, "components") and "emotion" in orchestrator.components:
            self._engine_template = orchestrator.components["emotion"]

    def _create_user_engine(self) -> EmotionEngine:
        """基于共享模板创建用户专属情感引擎"""
        if self._engine_template:
            return EmotionEngine(
                llm_gateway=getattr(self._engine_template, "_llm", None),
                use_llm=getattr(self._engine_template, "_classifier", None) is not None,
                classifier_mode=getattr(self._engine_template, "_classifier_mode", "rule"),
            )
        return EmotionEngine()

    def _get_character_engine(self, instance: UserInstance, character_id: str) -> EmotionEngine:
        """获取“用户 × 角色”专属情绪引擎，并同步兼容属性 emotion_engine。

        容量控制（2026-09-17 修复）：每个 (用户 × 角色) 组合都会创建一个
        EmotionEngine（内部持有状态与可选线程池）。旧实现只增不删，用户反复
        切换角色时引擎数量无界增长。现按 LRU 上限淘汰，且**永不淘汰当前活跃角色**。

        B6 持久化：引擎创建/切换时从 `data/affinity_state.json` 恢复
        affection_points（进程重启不丢亲密度）。
        """
        engine = instance.emotion_engines.get(character_id)
        if engine is None:
            engine = self._create_user_engine()
            self._restore_affinity(instance.user_id, character_id, engine)
        # 先发布活跃指针，再做淘汰：否则淘汰逻辑无法区分"待回收的旧活跃引擎"
        # 与"新活跃引擎"，会把旧活跃引擎从字典移除却不 close（资源泄漏）。
        instance.emotion_engine = engine
        # 记录最近使用顺序（dict 保序，重插即移到末尾）
        instance.emotion_engines.pop(character_id, None)
        instance.emotion_engines[character_id] = engine
        self._evict_stale_engines(instance, keep=character_id)
        return engine

    @staticmethod
    def _restore_affinity(user_id: str, character_id: str, engine: EmotionEngine) -> None:
        try:
            points = affinity_state.load_points(user_id, character_id)
            if points <= 0:
                return
            level = affinity_scale.points_to_level(points)
            state = getattr(engine, "state", None) or getattr(engine, "_state", None)
            if state is None:
                return
            state.affection_points = points
            state.affinity = level
            logger.debug(
                "恢复亲密度 %s/%s → points=%.1f level=%d",
                user_id, character_id, points, level,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复亲密度失败 %s/%s: %s", user_id, character_id, e)

    def apply_time_decay_all(self, hours: float) -> int:
        """对所有存活的 用户×角色 情绪引擎应用时间衰减（审计 item45）。

        旧实现的每日衰减只打在 orchestrator 注入的**模板引擎**上——对话/调度
        各自用请求级与用户级引擎，模板 state 无人读取，「情绪自然冷却」名存实亡。
        """
        with self._users_lock:
            instances = list(self._users.values())
        applied = 0
        for inst in instances:
            for engine in list(inst.emotion_engines.values()):
                try:
                    engine.apply_time_decay(hours)
                    applied += 1
                except Exception as e:  # noqa: BLE001
                    logger.debug("用户 %s 情绪引擎时间衰减失败: %s", inst.user_id, e)
        return applied

    @staticmethod
    def _persist_affinity(user_id: str, character_id: str, engine: EmotionEngine) -> None:
        """持久化亲密度：**点存 + 审计日志双写**（块E，2026-09-22）。

        为什么要双写：`shisi.affinity.enhancer.AffinityEnhancer` 重启后从
        `affinity_records`（它自己的审计日志）**回放**恢复 `_values`，而本类
        只往 `data/affinity_state.json` 写点存 —— 两份键空间由 09-21 隔离批
        引入 user×character 后出现口径分叉（详见 orchestrator 内同批注释）。
        生产实证两份的键**零交集**，导致 enhancer 的"重启归零根治"（v1.36
        块2）在生产拿不到任何可回放数据。

        现同时写审计日志，使**回放源覆盖写入源**。注意审计写入失败必须只告警：
        点存是既有真源，不能因新路径失败而丢点存。
        """
        try:
            state = getattr(engine, "state", None) or getattr(engine, "_state", None)
            if state is None:
                return
            points = float(getattr(state, "affection_points", 0.0) or 0.0)
            affinity_state.save_points(user_id, character_id, points)
            # 审计镜像：enhancer 的恢复真源（`_values` 是 **shisi 0–100**）。
            # 🔴 刻度根治：旧镜像直接写 affection_points（0–500）到 new_value，
            # 与 enhancer.update 写入的 shisi 值混在同一列 —— 回放时两种刻度
            # 互相污染。现镜像写 points_to_shisi(points)，与 enhancer 同一刻度；
            # reason 同步换用 MIRROR_REASON_SHISI 标记，使读侧能区分旧 points 行
            # （判据错位会二次换算 250→50→10）。
            try:
                from shisi.affinity import scale as affinity_scale
                from shisi.affinity.enhancer import (
                    MIRROR_REASON_SHISI,
                    affinity_key,
                    default_db_path,
                )

                key = affinity_key(character_id, user_id)
                shisi_val = affinity_scale.points_to_shisi(points)
                with closing(sqlite3.connect(str(default_db_path()))) as conn, conn:
                    conn.execute(
                        "INSERT INTO affinity_records "
                        "(character_id, old_value, new_value, delta, reason, source) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            key,
                            0.0,
                            shisi_val,
                            0.0,
                            MIRROR_REASON_SHISI,
                            "emotion",
                        ),
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("好感度审计镜像写入失败（点存已成功）: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.debug("持久化亲密度失败 %s/%s: %s", user_id, character_id, e)

    #: 单个用户最多保留的情绪引擎数（用户 × 角色 组合数上限）
    _MAX_ENGINES_PER_USER = 8

    def _evict_stale_engines(self, instance: UserInstance, keep: str) -> None:
        """超出上限时按 LRU 关闭并移除最久未使用的角色引擎。

        dict 保序 + 每次访问重插到末尾 ⇒ 队首即最久未使用（LRU）。
        """
        while len(instance.emotion_engines) > self._MAX_ENGINES_PER_USER:
            victim = next((cid for cid in instance.emotion_engines if cid != keep), None)
            if victim is None:
                break
            stale = instance.emotion_engines.pop(victim)
            try:
                stale.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("回收角色 %s 情绪引擎失败: %s", victim, e)

    # ── 核心入口 ──────────────────────────────────────────

    async def process_message(
        self, user_id: str, text: str, message_type: str = "text",
        attachments: list | None = None,
        reply_sender=None,
    ) -> dict[str, Any]:
        """处理某个用户的消息

        注意: 不再使用全局锁，依赖 Orchestrator 的 per-session 锁保证并发安全。
        Orchestrator._get_session_lock(session_id) 为每个 session 提供独立的锁，
        不同用户可并行处理，同一用户消息串行处理，避免情感引擎状态串扰。

        attachments: 多模态附件（图片 content part 列表），由微信通道传入。
        """
        return await self._process_message_inner(
            user_id, text, message_type, attachments, reply_sender
        )

    async def _get_user_llm_config(self, wxid: str) -> tuple[int | None, dict | None]:
        """取该 wxid 对应用户的**专属 LLM 配置**（BYOK）。

        ⚠️ 2026-09-19 修复：微信路径此前**从不传 user_llm_config** ——
        用户在 web 控制端填的自己的 API Key 在微信聊天里**完全不生效**，
        一直用全局 key；而 web 聊天路径（chat_routes）是传的，两边行为不对称。
        用户问「用户使用自己的 API key 能不能顺利用上」时，答案在微信端是「不能」。

        缓存 30s：配置变更罕见，避免每条消息都查库；改完最多 30s 生效。
        """
        # owner:peer 由通道构造，费用与凭证归通道 owner，不能剥 owner 借用
        # 同一裸 peer 在另一个账号下的绑定。裸键仅保留显式绑定兼容路径。
        numeric_id = session_key.owner_of(wxid)
        if numeric_id is None:
            binding = self._bindings.get(wxid) or {}
            numeric_id = binding.get("user_id")
        if not numeric_id:
            return None, None

        cache = getattr(self, "_llm_cfg_cache", None)
        if cache is None:
            cache = self._llm_cfg_cache = {}
        now = time.time()
        hit = cache.get(int(numeric_id))
        if hit and now - hit[0] < _LLM_CFG_CACHE_TTL:
            return int(numeric_id), hit[1]

        from api.byok import load_user_llm_config
        from api.database import _async_session

        async with _async_session() as db:
            cfg = await load_user_llm_config(int(numeric_id), getattr(self, "_orch", None), db)

        # 变更时打一条日志，便于排查「用户配了 key 却没生效」
        prev = hit[1] if hit else None
        if cfg != prev:
            logger.info(
                "用户 %s 专属 LLM 配置%s", wxid,
                "已加载（BYOK 生效）" if cfg else "为空（使用全局 key）",
            )
        cache[int(numeric_id)] = (now, cfg)
        return int(numeric_id), cfg

    async def _process_message_inner(
        self, user_id: str, text: str, message_type: str = "text",
        attachments: list | None = None,
        reply_sender=None,
    ) -> dict[str, Any]:
        """实际消息处理：委托给 orchestrator，并追加语音合成逻辑"""
        instance = self._get_or_create(user_id)
        session_id = instance.session_id
        character_id = instance.character_card_id
        emotion_engine = self._get_character_engine(instance, character_id)

        # BYOK：把用户专属 LLM 配置传下去（此前漏传 → 用户的 key 在微信端不生效）
        numeric_uid, user_llm_cfg = await self._get_user_llm_config(user_id)

        # 修复：传入 character_id，否则多用户角色隔离失效
        # 使用关键字参数以兼容 Orchestrator（character_id 为第 5 参）和
        # OptimizedOrchestrator（character_id 为第 4 参）两种签名
        delivery_kwargs = {"reply_sender": reply_sender} if reply_sender is not None else {}
        result = await self._orch.process_message(
            text,
            session_id,
            message_type,
            character_id=character_id,
            emotion_engine=emotion_engine,
            user_llm_config=user_llm_cfg,
            user_id=numeric_uid,
            attachments=attachments,
            **delivery_kwargs,
        )
        result["character_id"] = character_id

        # 统计
        instance.total_chats += 1
        instance.last_active = time.time()
        # B6：对话后持久化 affection_points（重启不丢）
        self._persist_affinity(user_id, character_id, emotion_engine)

        return result

    # ── 用户管理 ─────────────────────────────────────────

    def _get_or_create(self, user_id: str) -> UserInstance:
        """获取或创建用户实例（线程安全）

        2026-09-19：微信通道会话键为 `owner:peer`；角色解析优先级：
        1) peer 偏好（wechat_peer_preferences，好友自选）
        2) wechat_bindings 中该 peer 的绑定
        3) 通道 owner 的默认角色 / default
        """
        with self._users_lock:
            if user_id not in self._users:
                engine = self._create_user_engine()
                character_card_id = self._resolve_character_id(user_id)
                nickname = ""
                binding = self._bindings.get(user_id)
                if not binding:
                    # 2026-09-22 收口：拆 owner 走唯一 owner（禁手写 split）
                    _owner, peer = session_key.split_owner(user_id)
                    if peer and peer != user_id:
                        binding = self._bindings.get(peer) or self._bindings.get(user_id)
                if binding:
                    nickname = binding.get("nickname") or ""
                    if binding.get("character_card_id") and user_id in self._bindings:
                        character_card_id = binding.get("character_card_id")
                instance = UserInstance(
                    user_id=user_id,
                    nickname=nickname,
                    character_card_id=character_card_id,
                    session_id=user_id,
                    emotion_engine=engine,
                    emotion_engines={character_card_id: engine},
                )
                self._users[user_id] = instance
                logger.info("✨ 新用户接入: %s → 角色 %s (总用户数: %d)", user_id, character_card_id, len(self._users))
            return self._users[user_id]

    def _resolve_character_id(self, user_id: str) -> str:
        """按优先级解析会话键对应的角色卡。"""
        binding = self._bindings.get(user_id)
        if binding and binding.get("character_card_id"):
            return str(binding["character_card_id"])
        # 2026-09-22 收口：拆 owner 走唯一 owner（禁手写 split）
        owner, peer = session_key.split_owner(user_id)
        if owner:
            # peer 偏好缓存键：owner:peer 已在 bindings 中则上面已命中
            pref = self._bindings.get(f"pref:{user_id}")
            if pref and pref.get("character_card_id"):
                return str(pref["character_card_id"])
            peer_bind = self._bindings.get(peer)
            if peer_bind and peer_bind.get("character_card_id"):
                return str(peer_bind["character_card_id"])
            owner_bind = self._bindings.get(owner)
            if owner_bind and owner_bind.get("character_card_id"):
                return str(owner_bind["character_card_id"])
        return "default"

    def remove_user(self, user_id: str) -> bool:
        """移除用户（线程安全）"""
        with self._users_lock:
            if user_id in self._users:
                instance = self._users.pop(user_id)
                # 关闭用户的情感引擎，释放线程池资源
                for engine in set(instance.emotion_engines.values()):
                    try:  # noqa: BLE001
                        engine.close()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("关闭用户 %s 情感引擎时出错: %s", user_id, e)
                logger.info("用户移除: %s", user_id)
                return True
            return False

    def reset_user(self, user_id: str) -> bool:
        """重置用户（情感归零）（线程安全）"""
        with self._users_lock:
            if user_id not in self._users:
                return False
            instance = self._users[user_id]
            for engine in instance.emotion_engines.values():
                engine.reset()
            instance.total_chats = 0
            try:
                affinity_state.clear(user_id, instance.character_card_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("清除亲密度持久化失败: %s", e)
            logger.info("用户重置: %s", user_id)
            return True

    # ── 角色卡分配 ───────────────────────────────────────

    def set_user_character(self, user_id: str, card_id: str) -> bool:
        """为用户切换角色卡。

        线程安全（2026-09-17 修复）：旧实现直接读写 `self._users[...]` 与
        `instance.emotion_engines`，**未持 `_users_lock`** —— 与 `_get_or_create` /
        `remove_user` / `upsert_binding` 并发时可能出现"刚移除又被写回"、
        字典在迭代中被修改等竞态。现与其他写路径统一加锁。
        """
        with self._users_lock:
            instance = self._users.get(user_id)
            if instance is None:
                return False
            instance.character_card_id = card_id
            self._get_character_engine(instance, card_id)
        logger.info("用户 %s 角色卡 → %s", user_id, card_id)
        return True

    def get_user_character(self, user_id: str) -> str:
        """会话键 → 当前绑定角色 id。

        实例存活时以实例为准；**实例未建**（主动消息/提醒/祝福可能先于
        首条聊天，或 remove_user 后）必须回落绑定表 `_resolve_character_id`。
        旧实现实例缺失即 `"default"`，使 character_resolver / 亲密度 / 祝福
        口吻在「绑定已存在但用户还没聊过」时全部错绑内置角色。
        """
        instance = self._users.get(user_id)
        if instance is not None:
            return instance.character_card_id
        return self._resolve_character_id(str(user_id or ""))

    # ── 查询接口 ─────────────────────────────────────────

    def get_all_users(self) -> list[dict[str, Any]]:
        return [
            {
                "user_id": u.user_id,
                "nickname": u.nickname,
                "character_card_id": u.character_card_id,
                "affinity_level": u.affinity_level,
                "affinity_name": u.affinity_name,
                "primary_emotion": u.primary_emotion,
                "total_chats": u.total_chats,
                "created_at": u.created_at,
                "last_active": u.last_active,
                "is_active": u.is_active,
            }
            for u in self._users.values()
        ]

    def get_user_info(self, user_id: str) -> dict[str, Any] | None:
        instance = self._users.get(user_id)
        if not instance:
            return None
        emotion_dict = instance.emotion_engine.state.to_dict() if instance.emotion_engine else {}
        return {
            "user_id": instance.user_id,
            "nickname": instance.nickname,
            "character_card_id": instance.character_card_id,
            "emotion": emotion_dict,
            "total_chats": instance.total_chats,
            "created_at": instance.created_at,
            "last_active": instance.last_active,
            "is_active": instance.is_active,
        }

    @property
    def active_user_count(self) -> int:
        return len(self._users)

    def get_bound_wxids(self) -> list[str]:
        """当前已绑定微信的 wxid 列表（主动消息投递目标）。

        只读 dict.keys() 在 GIL 下原子；_bindings 的写路径均持有
        _bindings_lock 整体替换/更新键值，读侧快照足够安全。
        """
        return list(self._bindings.keys())

    # ── 绑定缓存管理 ───────────────────────────────────
    #
    # 绑定缓存是 SQLite wechat_bindings 表的内存镜像，
    # 使 _get_or_create 能在不查 DB（异步）的情况下获取角色信息。
    #
    # 三种更新路径：
    #   1. 启动预加载: load_bindings()
    #   2. API 创建绑定: upsert_binding()
    #   3. API 切换角色: upsert_binding()
    # ──────────────────────────────────────────────────

    async def load_bindings(self, bindings: list[dict]) -> None:
        """启动时预加载所有绑定到缓存"""
        async with self._bindings_lock:
            self._bindings.clear()
            for b in bindings:
                wxid = b.get("wxid", "")
                if wxid:
                    self._bindings[wxid] = b
            logger.info("已加载 %d 条绑定到缓存", len(self._bindings))

    async def upsert_binding(self, wxid: str, data: dict) -> None:
        """API 操作绑定后同步更新缓存和实时用户实例"""
        async with self._bindings_lock:
            # 更新缓存
            if wxid in self._bindings:
                self._bindings[wxid].update(data)
            else:
                self._bindings[wxid] = data
        # 同步更新已在内存中的用户实例（用 threading.Lock，不阻塞事件循环）
        with self._users_lock:
            if wxid in self._users:
                if "character_card_id" in data:
                    self._users[wxid].character_card_id = data["character_card_id"]
                    self._get_character_engine(self._users[wxid], data["character_card_id"])
                    logger.info("用户 %s 实时角色切换 → %s", wxid, data["character_card_id"])
                if "nickname" in data:
                    self._users[wxid].nickname = data["nickname"]

    async def remove_binding(self, wxid: str) -> None:
        """解除绑定时清理缓存"""
        async with self._bindings_lock:
            self._bindings.pop(wxid, None)

    # ── 跨 worker 真源回灌（块4，缺陷 D）─────────────────────────
    #
    # 缓存是 SQLite 的**内存镜像**，但写路径只热更本进程：
    # `wechat_channel_routes.py` 换角色、`wechat_routes.py` 绑定、连接器自选
    # 都发生在处理该 HTTP 请求的那个 worker，而生产 4 uvicorn worker 里只有
    # master 跑调度器（`api/run_api.py` 的 flock 选主）。旧实现除此之外仅在
    # lifespan 启动时预载一次 → master 的视图永远停在启动快照：
    #   · 换角色后主动消息/祝福/提醒文案仍按**旧角色**口吻开口
    #     （`get_user_character` 又优先信活实例，实例值同样陈旧，切角永不生效）；
    #   · 新绑定/新自选偏好在 master 不可见 → 该会话的角色解析回落旧卡或内置十四。
    #
    # 用**同步 sqlite3 只读**回灌，而不是复用异步 session：调用方是 APScheduler
    # 工作线程，那里没有可用事件循环，`asyncio.run` / `run_coroutine_threadsafe
    # ().result()` 属本项目「谎言家族 asyncio 桥」的死法（见 utils/async_utils）。
    # 只读连接（mode=ro）不写库、不参与 ORM 会话，读的是已提交行。

    @staticmethod
    def _resolve_users_db_path(db_path: str | Path | None = None) -> Path | None:
        """users.db 的文件路径；非 SQLite 部署返回 None（不猜测、不误读）。"""
        if db_path is not None:
            return resolve_project_path(str(db_path))
        url = (
            os.environ.get("APP_DATABASE_URL", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        )
        if not url:
            # 与 api.runtime_config.get_database_url() 的默认口径一致，
            # 但锚定项目根（相对路径按 CWD 解析会静默读到另一个库）。
            return resolve_project_path("data/users.db")
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if url.startswith(prefix):
                raw = url[len(prefix):].split("?")[0]
                if not raw or raw.startswith(":memory:"):
                    return None
                return resolve_project_path(raw)
        return None

    @staticmethod
    def _read_binding_rows(path: Path) -> tuple[dict[str, dict], bool]:
        """(绑定行, 偏好表是否可读)。偏好表缺失/旧库无表 → 第二项 False。"""
        rows: dict[str, dict] = {}
        prefs_ok = True
        with closing(sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0
        )) as conn:
            conn.execute("PRAGMA busy_timeout=2000")
            try:
                for wxid, uid, nickname, card in conn.execute(
                    "SELECT wxid, user_id, nickname, character_card_id "
                    "FROM wechat_bindings"
                ):
                    key = str(wxid or "").strip()
                    if not key:
                        continue
                    rows[key] = {
                        "wxid": key,
                        "user_id": uid,
                        "nickname": str(nickname or ""),
                        "character_card_id": str(card or "default"),
                    }
            except sqlite3.Error as e:  # noqa: BLE001
                logger.debug("绑定表回读失败: %s", e)
            try:
                for owner, peer, card in conn.execute(
                    "SELECT owner_user_id, peer_wxid, character_card_id "
                    "FROM wechat_peer_preferences"
                ):
                    if not owner or not peer:
                        continue
                    rows[f"pref:{int(owner)}:{peer}"] = {
                        "wxid": str(peer),
                        "user_id": int(owner),
                        "character_card_id": str(card or "default"),
                    }
            except sqlite3.Error as e:  # noqa: BLE001
                prefs_ok = False
                logger.debug("好友角色偏好回读失败: %s", e)
        return rows, prefs_ok

    def refresh_bindings_from_db(
        self,
        *,
        db_path: str | Path | None = None,
        force: bool = False,
        min_interval_seconds: float = _BINDING_REFRESH_SECONDS,
    ) -> int:
        """把绑定缓存回灌到 SQLite 真源（节流默认 30s）。返回角色变更的键数。

        语义与启动预载一致：**DB 为准**。但整体替换只在两张表都读成功时生效，
        单表读失败则保留该表的本地键，避免旧库/迁移中把偏好清空。
        """
        with self._binding_refresh_lock:
            now = time.monotonic()
            if not force and now - self._last_binding_refresh < min_interval_seconds:
                return 0
            self._last_binding_refresh = now

        path = self._resolve_users_db_path(db_path)
        if path is None or not path.exists():
            return 0
        try:
            rows, prefs_ok = self._read_binding_rows(path)
        except Exception as e:  # noqa: BLE001
            logger.warning("绑定真源回灌失败（保留本进程缓存）: %s", e)
            return 0
        if not rows and not prefs_ok:
            return 0

        with self._users_lock:
            old = self._bindings
            if not prefs_ok:
                for k, v in old.items():
                    rows.setdefault(k, v)
            touched = {
                k for k, v in rows.items()
                if str((old.get(k) or {}).get("character_card_id") or "")
                != str(v.get("character_card_id") or "")
            }
            self._bindings = rows

        # 活实例同步：`get_user_character` 在实例存活时**优先信实例**，
        # 不同步则换卡永不生效。按会话键重新解析（而不是把变更键直接当实例键）——
        # 裸 wxid 的变更要落到 `owner:peer` 形态的实例上，且当该 (owner,peer)
        # 有好友自选偏好时偏好优先，不能把偏好覆盖成绑定卡。
        for uid, inst in self._affected_instances(touched):
            resolved = self._resolve_character_id(uid)
            if resolved and resolved != inst.character_card_id:
                # set_user_character 自身持锁、建角色引擎并打日志
                self.set_user_character(uid, resolved)
                logger.info("跨 worker 换卡生效: %s → %s", uid, resolved)
        return len(touched)

    def _affected_instances(self, touched_keys: set[str]) -> list[tuple[str, Any]]:
        """缓存变更后可能受影响的活实例快照（键 + 实例）。"""
        if not touched_keys:
            return []
        with self._users_lock:
            return [
                (uid, inst) for uid, inst in self._users.items()
                if self._binding_keys_for_session(uid) & touched_keys
            ]

    @staticmethod
    def _binding_keys_for_session(uid: str) -> set[str]:
        """该会话键在 `_resolve_character_id` 里会读到的全部缓存键。"""
        keys = {uid, f"pref:{uid}"}
        owner, peer = session_key.split_owner(uid)
        if peer:
            keys.add(peer)
            keys.add(f"pref:{owner}:{peer}")
        if owner:
            keys.add(owner)
        return keys

    def health_check(self) -> dict[str, Any]:
        return {
            "active_users": self.active_user_count,
            "users": list(self._users.keys()),
        }
