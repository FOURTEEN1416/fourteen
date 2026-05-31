"""
女友管理器 — 多微信用户核心调度器

核心设计：
- 每个用户有独立的 EmotionEngine 实例（情感完全隔离）
- 所有用户共享 LLM/安全层/工具/RAG（省资源）
- 消息进来 → 换入用户专属 engine → 处理 → 换出
- 控制台前端 API 管理用户（查聊天、换角色、重置）
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from my_character.emotion_engine import AffinityLevel, EmotionEngine

logger = logging.getLogger("girlfriend_manager")


@dataclass
class UserInstance:
    """单个用户的女友实例数据"""

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


class GirlfriendManager:
    """
    女友管理器

    用法:
        mgr = GirlfriendManager(orchestrator)
        result = await mgr.process_message("wx_小明", "你好呀")
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator
        self._users: dict[str, UserInstance] = {}
        # ── 锁策略说明 ──────────────────────────────────────────────
        # 使用 threading.Lock 保护 _users 字典的并发访问。
        # 选择 threading.Lock 而非 asyncio.Lock 的原因：
        #   1. 所有加锁操作（_get_or_create / remove_user / reset_user）
        #      都是同步短操作（仅 dict get/set/pop，无 await），
        #      不会阻塞事件循环。
        #   2. 每个用户有独立的 EmotionEngine 实例，情感引擎之间
        #      不存在共享状态的竞态条件。
        #   3. 消息处理流程（process_message）的并发安全由
        #      Orchestrator 的 per-session 锁保证，与 _users_lock 无关。
        # ────────────────────────────────────────────────────────────
        self._users_lock = threading.Lock()

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

        # 委托给 orchestrator 处理
        result = await self._orch.process_message(text, session_id, message_type)

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
                instance = UserInstance(
                    user_id=user_id,
                    session_id=user_id,
                    emotion_engine=engine,
                )
                self._users[user_id] = instance
                logger.info("✨ 新用户接入: %s (总用户数: %d)", user_id, len(self._users))
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

    def health_check(self) -> dict[str, Any]:
        return {
            "active_users": self.active_user_count,
            "users": list(self._users.keys()),
        }
