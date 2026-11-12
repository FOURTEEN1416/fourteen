"""话题线程状态 facade（Speaker 第 1 刀，2026-11-12）。

本类**只做转发**：三张话题表（``topic_state`` / ``topic_plan`` / ``topic_meta``）
的唯一 SQL owner 仍是 ``utils.topic_state``。此处不复制任何 SQL、不缓存快照、
不替调用方补归属键——所有 API 都显式传 ``(session_key, character_id)``，
从结构上杜绝「A 会话的计划被 B 会话消费」。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("speaker.thread_state")


class ThreadState:
    """一个 ``(session_key, character_id)`` 归属上的话题状态视图。"""

    def __init__(self, session_key: str, character_id: str = ""):
        # 2026-11-12 复核修复：必须 strip——空白串 "   " 在 if not 判断里为假，
        # 会穿透 no-op 防线，被底层 topic_state 归一成 default 角色。
        self.session_key = str(session_key or "").strip()
        self.character_id = str(character_id or "").strip()

    def snapshot(self) -> dict[str, Any]:
        """读话题快照；失败返回空 dict（Speaker 全链 fail-open）。

        2026-11-12 复核修复：空 character_id 不做任何读写——底层
        ``utils.topic_state`` 会把空串归一成 ``default``，那会把内置十四的
        话题块注入给「本意是不带角色」的调用方，造成同用户不同角色上下文串味。
        """
        if not self.character_id:
            return {}
        try:
            from utils import topic_state

            return topic_state.get_topic_snapshot(
                self.session_key, self.character_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][thread] snapshot 失败 session=%s: %s", self.session_key, e)
            return {}

    def text_block(self) -> str:
        """渲染状态块；无 active/parked 或异常时返回空串。"""
        if not self.character_id:
            return ""
        try:
            from utils import topic_state

            return topic_state.render_topic_state_block(self.snapshot()) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][thread] text_block 失败 session=%s: %s", self.session_key, e)
            return ""

    def record_turn(
        self,
        user_message: str,
        assistant_reply: str = "",
        *,
        turn_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """委托 ``update_after_turn``；失败返回空 dict。"""
        if not self.character_id:
            return {}
        try:
            from utils import topic_state

            return topic_state.update_after_turn(
                self.session_key,
                self.character_id,
                user_message,
                assistant_reply,
                turn_id=turn_id,
                now=now,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][thread] record_turn 失败 session=%s: %s", self.session_key, e)
            return {}

    def take_plan(
        self, now: datetime | None = None, consume: bool = False
    ) -> dict[str, Any] | None:
        """委托 ``take_next_plan``；失败返回 None。"""
        if not self.character_id:
            return None
        try:
            from utils import topic_state

            return topic_state.take_next_plan(
                self.session_key,
                self.character_id,
                now=now,
                consume=consume,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][thread] take_plan 失败 session=%s: %s", self.session_key, e)
            return None

    def save_plan(
        self,
        topic: str,
        intent: str,
        wait_minutes: int = 0,
        *,
        planned_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """委托 ``save_next_plan``；失败返回 None。

        ``planned_at`` 与底层同名 keyword 直通（``next_at = planned_at + wait_minutes``），
        调度器必须传本拍 ``now``，否则等待窗会按落盘时刻漂移。
        """
        if not self.character_id:
            return None
        try:
            from utils import topic_state

            return topic_state.save_next_plan(
                self.session_key,
                self.character_id,
                topic,
                intent,
                wait_minutes=wait_minutes,
                planned_at=planned_at,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][thread] save_plan 失败 session=%s: %s", self.session_key, e)
            return None

    def mark_recalled(self, topic_id: str, *, now: datetime | None = None) -> bool:
        """委托 ``mark_topic_recalled``；失败返回 False。"""
        if not self.character_id:
            return False
        try:
            from utils import topic_state

            return bool(
                topic_state.mark_topic_recalled(
                    self.session_key, self.character_id, topic_id, now=now
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][thread] mark_recalled 失败 session=%s: %s", self.session_key, e)
            return False
