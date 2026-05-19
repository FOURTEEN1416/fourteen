"""
记忆管线编排器 — 每次对话后的处理流程

每次聊天后自动触发：
1. 存储原始对话到 ChromaDB（向量记忆）
2. 提取事实 → SQLite（结构化记忆）
3. 更新情感状态机
4. （如果是午夜）触发日记摘要
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any, Callable, Dict, List, Optional

from .vector_memory import VectorMemory
from .structured_memory import StructuredMemory
from .fact_extractor import FactExtractor
from .diary_summarizer import DiarySummarizer

logger = logging.getLogger("memory_pipeline")


class MemoryPipeline:
    """
    记忆管线编排器

    连接各记忆模块，定义每次对话后的处理流程。
    """

    def __init__(
        self,
        vector_memory: Optional[VectorMemory] = None,
        structured_memory: Optional[StructuredMemory] = None,
        fact_extractor: Optional[FactExtractor] = None,
        diary_summarizer: Optional[DiarySummarizer] = None,
        emotion_engine: Optional[Any] = None,
    ):
        self.vm = vector_memory or VectorMemory()
        self.sm = structured_memory or StructuredMemory()
        self.fe = fact_extractor or FactExtractor()
        self.ds = diary_summarizer or DiarySummarizer()
        self.emotion = emotion_engine

        # 会话跟踪
        self._session_id: str = ""
        self._last_daily_summary: Optional[str] = None

        logger.info("MemoryPipeline initialized")

    @property
    def session_id(self) -> str:
        if not self._session_id:
            self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._session_id

    # ── 核心接口 ──────────────────────────────────────────

    def after_chat(
        self,
        user_msg: str,
        reply: str,
        emotion_tag: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        effective_session = session_id or self.session_id
        emotion_state = {"emotion": emotion_tag} if emotion_tag else None
        result = {
            "stored_chat": False,
            "facts_extracted": 0,
            "emotion_updated": False,
        }

        try:
            self.sm.add_chat("user", user_msg, emotion_tag=emotion_tag,
                             session_id=effective_session)
            self.sm.add_chat("assistant", reply, emotion_tag=emotion_tag,
                             session_id=effective_session)
            result["stored_chat"] = True
        except Exception as e:
            logger.warning("Failed to store chat: %s", e)

        # 2. 存储对话到 ChromaDB
        try:
            self.vm.store_chat(user_msg, reply, {
                "emotion": emotion_tag,
                "session_id": effective_session,
            })
        except Exception as e:
            logger.warning("Failed to store vector chat: %s", e)

        # 3. 提取用户事实（每隔几条消息）
        if self._should_extract_facts():
            try:
                recent = self.sm.get_recent_chats(10)
                user_msgs = [c["content"] for c in recent if c["role"] == "user"]
                if user_msgs:
                    facts = self.fe.extract_facts(user_msgs)
                    deduped = FactExtractor.deduplicate(facts)

                    for fact in deduped:
                        # 检查是否已存在
                        existing = self.sm.search_facts(fact["fact"])
                        if not existing:
                            self.sm.add_fact(
                                fact=fact["fact"],
                                category=fact.get("category", "general"),
                                confidence=fact.get("confidence", 0.5),
                            )
                            result["facts_extracted"] += 1
            except Exception as e:
                logger.warning("Fact extraction failed: %s", e)

        # 4. 记录情绪
        if emotion_state and self.vm.health_check().get("collections", {}).get("emotion_logs"):
            try:
                self.vm.store_emotion_log(
                    emotion_state.get("emotion", "unknown"),
                    emotion_state.get("intensity", 0.5),
                    trigger="chat",
                )
                result["emotion_updated"] = True
            except Exception as e:
                logger.warning("Emotion log failed: %s", e)

        return result

    def daily_maintenance(self) -> Optional[str]:
        """
        每日维护（午夜自省时调用）

        流程：
        1. 获取今天聊天
        2. 生成每日摘要
        3. 保存摘要
        4. 清理低置信度事实

        Returns:
            每日摘要文本
        """
        try:
            today_chats = self.sm.get_chats_today()
            if not today_chats:
                logger.info("No chats today, skipping daily maintenance")
                return None

            # 生成摘要
            summary = self.ds.summarize_day(today_chats)
            date_str = datetime.now().strftime("%Y-%m-%d")
            self.ds.save_summary(date_str, summary)
            self._last_daily_summary = summary

            # 清理低置信度事实
            self._cleanup_low_confidence_facts()

            logger.info("Daily maintenance complete: %s", date_str)
            return summary

        except Exception as e:
            logger.error("Daily maintenance failed: %s", e)
            return None

    def get_memory_context(self, n_chats: int = 10) -> Dict[str, Any]:
        """
        获取当前对话需要的记忆上下文

        Returns:
            {
                "recent_chats": [...],
                "user_facts": [...],
                "today_summary": str,
                "emotion_trend": {...},
            }
        """
        context = {
            "recent_chats": [],
            "user_facts": [],
            "today_summary": "",
            "emotion_trend": {},
        }

        try:
            context["recent_chats"] = self.sm.get_recent_chats(n_chats)
        except Exception as e:
            logger.warning("Failed to get recent chats: %s", e)

        try:
            facts = self.sm.get_facts(min_confidence=0.3)
            context["user_facts"] = [f["fact"] for f in facts]
        except Exception as e:
            logger.warning("Failed to get facts: %s", e)

        try:
            summaries = self.ds.get_all_summaries()
            if summaries:
                trend = self.ds.detect_mood_trend(summaries)
                context["emotion_trend"] = trend
        except Exception as e:
            logger.warning("Failed to detect trend: %s", e)

        return context

    def get_formatted_context(self, n_chats: int = 6) -> str:
        """
        获取格式化的记忆上下文文本（用于注入 prompt）

        Returns:
            格式化文本
        """
        ctx = self.get_memory_context(n_chats)
        parts = []

        # 用户事实
        if ctx["user_facts"]:
            parts.append("[我记得的你]")
            for fact in ctx["user_facts"][:5]:
                parts.append(f"- {fact}")

        # 今日摘要
        if ctx["today_summary"]:
            parts.append(f"[今日回顾] {ctx['today_summary']}")

        # 最近对话
        if ctx["recent_chats"]:
            parts.append("[最近聊天]")
            for c in ctx["recent_chats"][-6:]:
                role = "你" if c["role"] == "user" else "我"
                parts.append(f"{role}: {c['content']}")

        return "\n".join(parts)

    # ── 内部方法 ──────────────────────────────────────────

    def _should_extract_facts(self) -> bool:
        """判断是否该提取事实（每5条消息提取一次）"""
        try:
            count = self.sm.count_chats_today()
            return count % 10 < 2  # 每5-10条消息检查一次
        except Exception:
            return False

    def _cleanup_low_confidence_facts(self) -> None:
        """清理低置信度事实"""
        try:
            facts = self.sm.get_facts(min_confidence=0.0)
            for f in facts:
                if f.get("confidence", 0) < 0.2:
                    self.sm.delete_fact(f["id"])
            logger.debug("Cleaned up low confidence facts")
        except Exception as e:
            logger.warning("Cleanup failed: %s", e)

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "vector_memory": self.vm.health_check(),
            "structured_memory": self.sm.health_check(),
            "fact_extractor": self.fe.health_check(),
            "diary_summarizer": self.ds.health_check(),
        }
