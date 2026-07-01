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
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from my_character.emotion_engine import AffinityLevel, EmotionEngine

logger = logging.getLogger("user_scheduler")


@dataclass
class UserInstance:
    """单个用户的会话实例数据"""

    user_id: str
    nickname: str = ""
    character_card_id: str = "default"
    session_id: str = ""

    # 用户专属的情感引擎
    emotion_engine: EmotionEngine | None = None

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

    # ── 核心入口 ──────────────────────────────────────────

    async def process_message(
        self, user_id: str, text: str, message_type: str = "text"
    ) -> dict[str, Any]:
        """处理某个用户的消息

        注意: 不再使用全局锁，依赖 Orchestrator 的 per-session 锁保证并发安全。
        Orchestrator._get_session_lock(session_id) 为每个 session 提供独立的锁，
        不同用户可并行处理，同一用户消息串行处理，避免情感引擎状态串扰。
        """
        return await self._process_message_inner(user_id, text, message_type)

    async def _process_message_inner(
        self, user_id: str, text: str, message_type: str = "text"
    ) -> dict[str, Any]:
        """实际消息处理：委托给 orchestrator，并追加语音合成逻辑"""
        instance = self._get_or_create(user_id)
        session_id = instance.session_id

        # 修复：传入 character_id，否则多用户角色隔离失效
        # 使用关键字参数以兼容 Orchestrator（character_id 为第 5 参）和
        # OptimizedOrchestrator（character_id 为第 4 参）两种签名
        result = await self._orch.process_message(
            text, session_id, message_type, character_id=instance.character_card_id
        )

        # 统计
        instance.total_chats += 1
        instance.last_active = time.time()

        return result

    # ── 用户管理 ─────────────────────────────────────────

    def _get_or_create(self, user_id: str) -> UserInstance:
        """获取或创建用户实例（线程安全）

        注意：此方法为同步方法，内部不包含任何 await 操作。
        threading.Lock 足以保护简短的字典操作。

        竞态条件分析：
        - 每个用户有独立的 EmotionEngine 实例，情感状态完全隔离
        - _users 字典操作在锁保护下是原子的（dict __contains__ / __setitem__ / __getitem__）
        - EmotionEngine 内部状态变更仅在 process_message 中发生，
          而 process_message 由 Orchestrator 的 per-session 锁串行化
        - 因此 _users_lock + per-session 锁两层保护足以防止所有竞态条件
        """
        with self._users_lock:
            if user_id not in self._users:
                engine = self._create_user_engine()
                # 查绑定缓存，用绑定的角色和昵称
                binding = self._bindings.get(user_id)
                character_card_id = (binding.get("character_card_id") or "default") if binding else "default"
                nickname = binding.get("nickname") if binding else ""
                instance = UserInstance(
                    user_id=user_id,
                    nickname=nickname,
                    character_card_id=character_card_id,
                    session_id=user_id,
                    emotion_engine=engine,
                )
                self._users[user_id] = instance
                logger.info("✨ 新用户接入: %s → 角色 %s (总用户数: %d)", user_id, character_card_id, len(self._users))
            return self._users[user_id]

    def remove_user(self, user_id: str) -> bool:
        """移除用户（线程安全）"""
        with self._users_lock:
            if user_id in self._users:
                instance = self._users.pop(user_id)
                # 关闭用户的情感引擎，释放线程池资源
                if instance.emotion_engine:
                    try:  # noqa: BLE001
                        instance.emotion_engine.close()
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
            if instance.emotion_engine:
                instance.emotion_engine.reset()
            instance.total_chats = 0
            logger.info("用户重置: %s", user_id)
            return True

    # ── 角色卡分配 ───────────────────────────────────────

    def set_user_character(self, user_id: str, card_id: str) -> bool:
        if user_id not in self._users:
            return False
        self._users[user_id].character_card_id = card_id
        logger.info("用户 %s 角色卡 → %s", user_id, card_id)
        return True

    def get_user_character(self, user_id: str) -> str:
        instance = self._users.get(user_id)
        return instance.character_card_id if instance else "default"

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
                    logger.info("用户 %s 实时角色切换 → %s", wxid, data["character_card_id"])
                if "nickname" in data:
                    self._users[wxid].nickname = data["nickname"]

    async def remove_binding(self, wxid: str) -> None:
        """解除绑定时清理缓存"""
        async with self._bindings_lock:
            self._bindings.pop(wxid, None)

    def health_check(self) -> dict[str, Any]:
        return {
            "active_users": self.active_user_count,
            "users": list(self._users.keys()),
        }
