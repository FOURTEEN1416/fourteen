"""
记忆管线编排器 — V1/V2/Optimized 深度融合统一实现

融合架构：
- 自包含三层记忆（工作/情景/语义）
- V1 FactExtractor + DiarySummarizer
- V2 ForgettingManager + ConflictDetector + CrossSessionReasoner
- 遗忘模型路由（exponential / threshold）
- 向量检索超时降级
- V1 兼容接口
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .conversation_summarizer import ConversationSummarizer
from .reflection_engine import ReflectionEngine
from .structured_memory import StructuredMemory
from .vector_memory import VectorMemory

from ._legacy_working_memory import WorkingMemory
from ._legacy_episodic_memory import EpisodicMemory
from ._legacy_semantic_memory import SemanticMemory
from ._legacy_importance_scorer import ImportanceScorer
from ._legacy_diary_summarizer import DiarySummarizer
from .forgetting_manager import ForgettingManager
from .conflict_detector import ConflictDetector
from .cross_session_reasoner import CrossSessionReasoner
from .fact_extractor import FactExtractor, FACT_CATEGORIES, PATTERNS

logger = logging.getLogger("memory_pipeline")


# ═══════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════

# ── 选择性记忆规则常量 ──────────────────────────────────────
# 敷衍词：用户用来回避真实情绪的表达
PERFUNCTORY_WORDS = ("没事", "我没事", "我很好", "还行", "还好", "无所谓", "算了")

# 情感关键词：表明用户在表达真实情感
EMOTION_KEYWORDS = (
    "难过", "伤心", "开心", "生气", "孤独", "寂寞", "怕", "害怕",
    "想", "想念", "爱", "讨厌", "焦虑", "烦躁", "崩溃", "绝望",
    "委屈", "心疼", "感动", "幸福",
)

# 深夜情感关键词：深夜时段需要特别关注的情感信号
LATE_NIGHT_EMOTION_WORDS = ("怕", "想", "孤独", "难过", "寂寞", "睡不着", "失眠", "崩溃")

# 深夜时段范围（24小时制，含两端）
LATE_NIGHT_START_HOUR = 23
LATE_NIGHT_END_HOUR = 5


@dataclass
class MemoryConfig:
    working_limit: int = 20
    episodic_archive_trigger: int = 20
    retrieval_timeout: float = 1.0
    importance_threshold: float = 0.3
    forgetting_days: int = 30
    cache_size: int = 100
    fact_extract_interval: int = 5
    fact_min_confidence: float = 0.2
    conflict_similarity_threshold: float = 0.3
    cache_ttl: int = 30  # 上下文缓存 TTL（秒），代替硬编码值


# ═══════════════════════════════════════════════════════════════
class MemoryPipeline:
    """
    融合记忆管线 — V1/V2/Optimized 统一实现

    自包含三层记忆架构：
    - Working Memory: 当前会话上下文（deque）
    - Episodic Memory: 历史对话归档（向量 + 结构化）
    - Semantic Memory: 提取的事实和知识（去重 + 冲突检测）

    内嵌组件：
    - V1 FactExtractor: 每5条对话触发事实提取
    - V1 DiarySummarizer: 日记摘要生成
    - V2 ForgettingManager: 指数衰减遗忘
    - V2 ConflictDetector: 相似度冲突检测
    - V2 CrossSessionReasoner: 跨会话推理

    遗忘模型路由：
    - "exponential" → ForgettingManager（指数衰减）
    - "threshold"  → ImportanceScorer.should_retain（阈值判断）
    """

    def __init__(
        self,
        vector_memory=None,
        structured_memory=None,
        fact_extractor: FactExtractor | None = None,
        diary_summarizer: DiarySummarizer | None = None,
        emotion_engine: Any | None = None,
        llm_gateway: Any | None = None,
        working_limit: int = 20,
        retrieval_timeout: float = 1.0,
        forgetting_model: str = "exponential",
        lambda_low: float = 0.1,
        lambda_high: float = 0.01,
        conflict_similarity_threshold: float = 0.3,
        fact_extract_interval: int = 5,
    ):
        self._config = MemoryConfig(
            working_limit=working_limit,
            retrieval_timeout=retrieval_timeout,
            fact_extract_interval=fact_extract_interval,
        )

        # 存储后端
        if vector_memory is None:
            vector_memory = VectorMemory()
        if structured_memory is None:
            structured_memory = StructuredMemory()
        self.vm = vector_memory
        self.sm = structured_memory

        # LLM 网关
        self._llm = llm_gateway

        # 三层记忆
        self.working = WorkingMemory(limit=working_limit)
        self.episodic = EpisodicMemory(self.vm, self.sm)
        self.semantic = SemanticMemory(self.vm, self.sm)

        # V1 组件
        self.fe = fact_extractor or FactExtractor(
            llm_func=self._llm if callable(self._llm) else None
        )
        self.ds = diary_summarizer or DiarySummarizer(
            llm_func=self._llm if callable(self._llm) else None,
            structured_memory=self.sm,
        )
        self.emotion = emotion_engine

        # V2 组件
        self.scorer = ImportanceScorer()
        self.forgetting = ForgettingManager(lambda_low, lambda_high)
        self.conflict_detector = ConflictDetector(
            self.semantic, conflict_similarity_threshold
        )
        self.cross_session = CrossSessionReasoner(self.sm)

        # 记忆反思引擎：将零散事实沉淀为洞察
        self.reflection = ReflectionEngine(
            llm_func=self._llm if callable(self._llm) else None,
            vector_memory=self.vm,
            structured_memory=self.sm,
            reflection_interval=max(1, fact_extract_interval * 2),
        )

        # 对话摘要器（方案二：摘要+滑动窗口）
        self.summarizer = ConversationSummarizer(llm_gateway)

        # 遗忘模型路由
        self._forgetting_model = forgetting_model
        if forgetting_model not in ("exponential", "threshold"):
            logger.warning(
                "Unknown forgetting_model '%s', fallback to 'exponential'",
                forgetting_model,
            )
            self._forgetting_model = "exponential"

        # 会话跟踪
        self._session_id: str = ""
        self._last_daily_summary: str | None = ""
        self._chat_count_since_extract: int = 0
        self._chat_count_lock = threading.Lock()  # 保护 _chat_count_since_extract 并发读写

        # 后台任务线程池：避免每条消息都创建/销毁线程
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="memory_pipeline"
        )

        # 缓存
        self._context_cache: dict[str, Any] = {}
        self._cache_lock = threading.Lock()

        logger.info(
            "MemoryPipeline initialized (forgetting=%s, working_limit=%d)",
            self._forgetting_model, working_limit,
        )

        # 从数据库恢复已持久化的日记摘要
        self.ds.load_summaries_from_db()

    @property
    def session_id(self) -> str:
        if not self._session_id:
            self._session_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        return self._session_id

    # ── 核心接口 ──────────────────────────────────────────

    @staticmethod
    def _is_late_night(timestamp: datetime) -> bool:
        """判断给定时间是否处于深夜时段（23:00-05:00，含两端）。

        Args:
            timestamp: 待判断的时间戳（建议带时区）

        Returns:
            True 表示处于深夜时段
        """
        hour = timestamp.hour
        # 23:00-23:59 或 00:00-05:00
        return hour >= LATE_NIGHT_START_HOUR or hour <= LATE_NIGHT_END_HOUR

    def should_store_as_fact(self, message: str, timestamp: datetime) -> bool:
        """选择性记忆判定：是否应将消息提取为语义记忆（事实）。

        规则：
        1. 包含敷衍词且不含情感关键词 → 不存为事实（降低重要性）
        2. 深夜（23:00-05:00）含情感关键词 → 提升重要性，存为事实
        3. 其他情况默认存为事实

        Args:
            message: 用户消息文本
            timestamp: 消息时间戳

        Returns:
            True 表示应存为事实，False 表示应跳过事实提取
        """
        if not message:
            return False

        has_perfunctory = any(w in message for w in PERFUNCTORY_WORDS)
        has_emotion = any(w in message for w in EMOTION_KEYWORDS)

        # 规则1：敷衍且无情感 → 不存为事实
        if has_perfunctory and not has_emotion:
            logger.debug("Skipping fact storage (perfunctory): %s", message[:30])
            return False

        # 规则2：深夜 + 情感词 → 强制存为事实（重要性在 after_chat 中提升）
        if self._is_late_night(timestamp):
            has_late_night_emotion = any(
                w in message for w in LATE_NIGHT_EMOTION_WORDS
            )
            if has_late_night_emotion:
                logger.debug(
                    "Late-night emotion detected, force store: %s", message[:30]
                )
                return True

        # 规则3：默认存为事实
        return True

    def after_chat(
        self,
        user_msg: str,
        reply: str,
        emotion_tag: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        effective_session = session_id or self.session_id
        result = {
            "stored_chat": False,
            "stored_vector": False,
            "facts_extracted": 0,
            "conflicts_detected": 0,
            "archived": False,
            "emotion_updated": False,
        }

        # 1. 重要性评分
        importance = self.scorer.score(user_msg, emotion_tag)

        # 1.1 选择性记忆：深夜情感词提升重要性
        now = datetime.now(tz=timezone.utc)
        try:
            if self._is_late_night(now):
                has_late_night_emotion = any(
                    w in user_msg for w in LATE_NIGHT_EMOTION_WORDS
                )
                if has_late_night_emotion:
                    importance = min(1.0, importance + 0.3)
                    logger.debug(
                        "Late-night emotion importance boost: %s", user_msg[:30]
                    )
        except Exception as e:  # noqa: BLE001
            logger.debug("Late-night importance boost failed: %s", e)

        # 2. 存储到结构化记忆
        try:
            self.sm.add_chat("user", user_msg, emotion_tag=emotion_tag,
                             session_id=effective_session)
            self.sm.add_chat("assistant", reply, emotion_tag=emotion_tag,
                             session_id=effective_session)
            result["stored_chat"] = True
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to store chat: %s", e)

        # 3. 存储到工作记忆
        self.working.add("user", user_msg, emotion_tag, importance)
        self.working.add("assistant", reply, emotion_tag, importance)

        # 4. 存储到向量库（线程池异步执行，不阻塞主流程）
        self._executor.submit(
            self.vm.store_chat_sync,
            user_msg,
            reply,
            {
                "emotion": emotion_tag,
                "session_id": effective_session,
                "importance": importance,
            },
        )
        result["stored_vector"] = True  # 乐观标记，错误在内部日志

        # 5. 事实提取（每 N 条对话触发）
        # 注意：所有对 _chat_count_since_extract 的操作必须在锁保护下完成
        # 包括读取、递增、重置，防止竞态条件导致计数不准确
        with self._chat_count_lock:
            self._chat_count_since_extract += 1
            should_extract = self._chat_count_since_extract >= self._config.fact_extract_interval
            if should_extract:
                self._chat_count_since_extract = 0
                # 事实提取操作也在锁保护下决定是否执行
                # 释放锁后再执行实际提取，避免长时间持有锁
                needs_extraction = True
            else:
                needs_extraction = False

        if needs_extraction:
            self._executor.submit(self._do_fact_extraction, effective_session)
            result["facts_extracted"] = 0  # 后台异步提取中，具体数量由线程日志记录

        # 6. 跨会话推理：检测未来事件
        try:
            pending = self.cross_session.extract_pending_event(user_msg)
            if pending:
                self.cross_session.store_pending_event(
                    pending["event_desc"], session_id=effective_session
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("Cross-session reasoning failed: %s", e)

        # 7. 情绪记录
        if emotion_tag:
            try:
                self.vm.store_emotion_log_sync(
                    emotion_tag, importance, trigger="chat"
                )
                result["emotion_updated"] = True
            except Exception as e:  # noqa: BLE001
                logger.warning("Emotion log failed: %s", e)

        # 8. 归档检查
        if self.working.should_archive(self._config.episodic_archive_trigger):
            self._archive_working_memory()
            result["archived"] = True

        return result

    def retrieve_context(
        self,
        query: str,
        session_id: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        检索记忆上下文 — 并行检索三层记忆，向量检索超时降级

        Returns:
            {"working": [], "episodic": [], "semantic": [], "facts": []}
        """
        context = {  # type: ignore[var-annotated]
            "working": [],
            "episodic": [],
            "semantic": [],
            "facts": [],
            "reflections": [],
        }

        # 1. 工作记忆（最快，无超时风险）
        context["working"] = self.working.get_recent(n=10)

        # 2. 向量检索 — 情景记忆（独立超时检测）
        start_episodic = time.perf_counter()
        try:
            episodic_results = self.episodic.search(query, top_k=top_k)
            context["episodic"] = episodic_results
        except Exception as e:  # noqa: BLE001
            logger.warning("Episodic retrieval failed, degraded: %s", e)
        elapsed_episodic = time.perf_counter() - start_episodic

        # 3. 语义检索（独立超时检测，不依赖 episodic 耗时）
        start_semantic = time.perf_counter()
        try:
            semantic_results = self.semantic.search(query, top_k=top_k)
            context["semantic"] = semantic_results.get("structured", [])
            context["facts"] = [
                s.get("fact", "") for s in context["semantic"]
                if isinstance(s, dict)
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("Semantic retrieval failed, degraded: %s", e)
        elapsed_semantic = time.perf_counter() - start_semantic

        # 超时日志（各自独立检测）
        if elapsed_episodic > self._config.retrieval_timeout:
            logger.warning(
                "Episodic retrieval slow (%.2fs > %.1fs)",
                elapsed_episodic, self._config.retrieval_timeout,
            )
        if elapsed_semantic > self._config.retrieval_timeout:
            logger.warning(
                "Semantic retrieval slow (%.2fs > %.1fs), facts may be degraded",
                elapsed_semantic, self._config.retrieval_timeout,
            )

        # 3. 结构化事实补充（降级回退）
        if not context["facts"]:
            try:
                facts = self.sm.get_facts(min_confidence=0.3)
                context["facts"] = [f["fact"] for f in facts[:top_k]]
            except Exception as e:  # noqa: BLE001
                logger.debug("Structured fact fallback failed: %s", e)

        # 4. 待处理事件
        try:
            context["pending_events"] = self.cross_session.get_pending_events()
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to get pending events: %s", e)
            context["pending_events"] = []

        # 5. 记忆反思洞察
        try:
            context["reflections"] = self.reflection.get_insights(query=query, top_k=3)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to get reflections: %s", e)
            context["reflections"] = []

        return context

    async def retrieve_context_async(
        self,
        query: str,
        session_id: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        异步检索记忆上下文 — 并行检索三层记忆，向量检索超时降级

        Returns:
            {"working": [], "episodic": [], "semantic": [], "facts": []}
        """
        # 使用稳定的 hash 函数（hashlib.md5）替代 Python 内置 hash()，
        # 避免 Python 3.3+ 的 hash randomization 导致缓存命中率低下
        import hashlib
        query_hash = hashlib.md5(query.encode()).hexdigest()[:16]
        cache_key = f"{session_id}:{query_hash}"
        with self._cache_lock:
            if cache_key in self._context_cache:
                cached = self._context_cache[cache_key]
                if time.time() - cached.get("_ts", 0) < self._config.cache_ttl:
                    logger.debug("retrieve_context_async cache hit")
                    return {k: v for k, v in cached.items() if k != "_ts"}

        context = {  # type: ignore[var-annotated]
            "working": [],
            "episodic": [],
            "semantic": [],
            "facts": [],
            "reflections": [],
        }

        start = time.perf_counter()

        async def _get_working():
            try:
                return self.working.get_recent(n=10)
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed to get working memory: %s", e)
                return []

        async def _search_episodic():
            try:
                return self.episodic.search(query, top_k=top_k)
            except Exception as e:  # noqa: BLE001
                logger.warning("Episodic retrieval failed, degraded: %s", e)
                return []

        async def _search_semantic():
            try:
                return self.semantic.search(query, top_k=top_k)
            except Exception as e:  # noqa: BLE001
                logger.warning("Semantic retrieval failed, degraded: %s", e)
                return {}

        results = await asyncio.gather(
            _get_working(),
            _search_episodic(),
            _search_semantic(),
            return_exceptions=True,
        )

        context["working"] = results[0] if not isinstance(results[0], Exception) else []  # type: ignore
        context["episodic"] = results[1] if not isinstance(results[1], Exception) else []  # type: ignore

        semantic_result = results[2] if not isinstance(results[2], Exception) else {}
        if isinstance(semantic_result, dict):
            context["semantic"] = semantic_result.get("structured", [])
            context["facts"] = [
                s.get("fact", "") for s in context["semantic"]
                if isinstance(s, dict)
            ]

        elapsed = time.perf_counter() - start
        logger.debug("Async retrieve_context completed in %.3fs", elapsed)

        if not context["facts"]:
            try:
                facts = self.sm.get_facts(min_confidence=0.3)
                context["facts"] = [f["fact"] for f in facts[:top_k]]
            except Exception as e:  # noqa: BLE001
                logger.debug("Structured fact fallback failed: %s", e)

        try:
            context["pending_events"] = self.cross_session.get_pending_events()
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to get pending events (async): %s", e)
            context["pending_events"] = []

        # 记忆反思洞察
        try:
            context["reflections"] = self.reflection.get_insights(query=query, top_k=3)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to get reflections (async): %s", e)
            context["reflections"] = []

        context["_ts"] = time.time()  # type: ignore
        with self._cache_lock:
            self._context_cache[cache_key] = context

        return {k: v for k, v in context.items() if k != "_ts"}

    def get_recent_context(self, n: int = 3) -> str:
        """获取最近对话上下文文本"""
        recent = self.working.get_recent(n=n)
        return "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in recent
        )

    def daily_maintenance(self) -> str | None:
        """
        每日维护

        流程：
        1. 应用遗忘模型（exponential / threshold）
        2. 生成每日摘要
        3. 清理低置信度事实
        4. 清理缓存
        """
        try:
            # 1. 遗忘
            self._apply_forgetting()

            # 2. 生成摘要
            today_chats = self.sm.get_chats_today()
            if not today_chats:
                logger.info("No chats today, skipping daily maintenance")
                return None
            summary = self.ds.summarize_day(today_chats)
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            self.ds.save_summary(date_str, summary)
            self._last_daily_summary = summary

            # 3. 清理低置信度事实
            self._cleanup_low_confidence_facts()

            # 4. 清理缓存
            with self._cache_lock:
                self._context_cache.clear()

            logger.info("Daily maintenance complete: %s", date_str)
            return summary

        except Exception as e:  # noqa: BLE001
            logger.error("Daily maintenance failed: %s", e)
            return None

    def get_chat_context(
        self,
        session_id: str = "",
        keep_recent: int = 50,
        summary_trigger: int = 80,
    ):
        working_messages = self.working.get_recent(n=200)
        if not working_messages:
            return [], ""
        return self.summarizer.get_chat_context(
            working_messages,
            session_id=session_id or self.working.session_id,
            keep_recent=keep_recent,
            summary_trigger=summary_trigger,
        )

    # ── V1 兼容接口 ──────────────────────────────────────

    def get_memory_context(self, n_chats: int = 10) -> dict[str, Any]:
        """V1兼容：获取当前对话需要的记忆上下文"""
        context = {
            "recent_chats": [],
            "user_facts": [],
            "today_summary": "",
            "emotion_trend": {},
        }

        try:
            context["recent_chats"] = self.sm.get_recent_chats(n_chats)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get recent chats: %s", e)

        try:
            facts = self.sm.get_facts(min_confidence=0.3)
            context["user_facts"] = [f["fact"] for f in facts]
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get facts: %s", e)

        try:
            summaries = self.ds.get_all_summaries()
            if summaries:
                context["today_summary"] = summaries.get(
                    datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"), ""
                )
                trend = self.ds.detect_mood_trend(summaries)
                context["emotion_trend"] = trend
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to detect trend: %s", e)

        return context

    def get_formatted_context(self, n_chats: int = 6) -> str:
        """V1兼容：获取格式化的记忆上下文文本（用于注入 prompt）"""
        ctx = self.get_memory_context(n_chats)
        parts = []

        if ctx["user_facts"]:
            parts.append("[我记得的你]")
            for fact in ctx["user_facts"][:5]:
                parts.append(f"- {fact}")

        if ctx["today_summary"]:
            parts.append(f"[今日回顾] {ctx['today_summary']}")

        if ctx["recent_chats"]:
            parts.append("[最近聊天]")
            for c in ctx["recent_chats"][-6:]:
                role = "你" if c["role"] == "user" else "我"
                parts.append(f"{role}: {c['content']}")

        return "\n".join(parts)

    # ── 遗忘模型路由 ─────────────────────────────────────

    def _apply_forgetting(self) -> int:
        """根据遗忘模型路由应用遗忘"""
        forgotten = 0
        try:
            facts = self.sm.get_facts(min_confidence=0.0, limit=1000)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load facts for forgetting: %s", e)
            return 0

        for fact in facts:
            try:
                updated_at_str = fact.get("updated_at", "")
                if updated_at_str:
                    updated_dt = datetime.fromisoformat(
                        updated_at_str.replace("Z", "+00:00")
                    ) if isinstance(updated_at_str, str) else datetime.now(tz=timezone.utc)
                    days_old = (datetime.now(tz=timezone.utc) - updated_dt).total_seconds() / 86400
                else:
                    days_old = 30.0
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed to parse fact updated_at, using default: %s", e)
                days_old = 30.0

            importance = fact.get("confidence", 0.5)
            access_count = fact.get("access_count", 0)

            should_remove = False
            if self._forgetting_model == "exponential":
                should_remove = self.forgetting.should_delete(
                    importance, days_old
                )
            elif self._forgetting_model == "threshold":
                should_remove = not self.scorer.should_retain(
                    importance, days_old, access_count
                )

            if should_remove:
                try:
                    self.sm.delete_fact(fact["id"])
                    forgotten += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to delete fact (id=%s): %s", fact.get("id"), e)

        if forgotten:
            logger.info("Forgotten %d facts (model=%s)", forgotten,
                        self._forgetting_model)
        return forgotten

    # ── 内部方法 ──────────────────────────────────────────

    def _do_fact_extraction(self, session_id: str) -> int:
        """执行事实提取（V1 FactExtractor + V2 ConflictDetector）

        集成选择性记忆：通过 should_store_as_fact 过滤敷衍消息，
        避免把"没事/还行"等无信息量内容提取为事实。
        """
        count = 0
        try:
            recent = self.sm.get_recent_chats(10)
            # 选择性记忆：过滤掉敷衍且无情感的消息
            now = datetime.now(tz=timezone.utc)
            user_msgs = [
                c["content"] for c in recent
                if c["role"] == "user"
                and self.should_store_as_fact(c.get("content", ""), now)
            ]
            if not user_msgs:
                return 0

            facts = self.fe.extract_facts(user_msgs)
            deduped = FactExtractor.deduplicate(facts)

            for fact in deduped:
                # 选择性记忆：对提取出的事实文本再次校验（防止规则模式误提取敷衍词）
                if not self.should_store_as_fact(fact.get("fact", ""), now):
                    continue

                # 冲突检测
                conflict = self.conflict_detector.check_conflict(
                    fact["fact"], fact.get("category", "general")
                )
                if conflict:
                    existing_fact = conflict.get("existing_fact", "")
                    logger.debug(
                        "Fact conflict detected: new=%s vs existing=%s",
                        fact["fact"][:30], existing_fact[:30],
                    )
                    continue

                # 去重检查
                existing = self.sm.search_facts(fact["fact"])
                if existing:
                    continue

                # 存储事实
                if self.semantic.add_fact(
                    fact["fact"],
                    fact.get("category", "general"),
                    fact.get("confidence", 0.5),
                    fact.get("source", ""),
                ):
                    count += 1

                    # 跨会话：检测未来事件
                    pending = self.cross_session.extract_pending_event(
                        fact["fact"]
                    )
                    if pending:
                        self.cross_session.store_pending_event(
                            pending["event_desc"], session_id=session_id
                        )

        except Exception as e:  # noqa: BLE001
            logger.warning("Fact extraction failed: %s", e)

        # 记忆反思：事实足够时生成更高层洞察
        if count > 0:
            try:
                facts = self.semantic.get_facts(limit=20)
                episodes = self.episodic.search("", top_k=3)
                self.reflection.maybe_reflect(
                    facts=[{"fact": f.get("fact", ""), "category": f.get("category", "general")} for f in facts],
                    episodes=episodes,
                    session_id=session_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Reflection trigger failed: %s", e)

        return count

    def _archive_working_memory(self) -> None:
        """归档工作记忆到情景记忆"""
        messages = self.working.get_for_archive()
        if not messages:
            return

        summary = ""
        if self._llm and callable(self._llm):
            try:
                messages_for_summary = [
                    {"role": m.get("role", "user"),
                     "content": m.get("content", "")}
                    for m in messages
                ]
                # 复用后台线程池执行 LLM 摘要，避免阻塞主线程
                future = self._executor.submit(
                    self.ds._summarize_with_llm, messages_for_summary
                )
                summary = future.result(timeout=30) or ""
            except Exception as e:  # noqa: BLE001
                logger.debug("Summary generation failed: %s", e)

        avg_importance = (
            sum(m.get("importance", 0.5) for m in messages) / len(messages)
        )

        try:
            self.episodic.store_episode(
                messages,
                summary=summary,
                importance=avg_importance,
                session_id=self.working.session_id,
            )
            # 仅在归档成功后清空工作记忆（防止数据丢失）
            self.working.clear()
            logger.info("Working memory archived: %d messages", len(messages))
        except Exception as e:  # noqa: BLE001
            logger.error("归档工作记忆失败，保留数据: %s", e)

    def _cleanup_low_confidence_facts(self) -> None:
        """分批清理低置信度事实（防止大量删除阻塞）"""
        try:
            batch_size = 50
            max_batches = 20  # 安全上限，防止无限循环
            total_deleted = 0
            for _ in range(max_batches):
                facts = self.sm.get_facts(min_confidence=0.0, limit=batch_size)
                if not facts:
                    break
                ids_to_delete = [
                    f["id"] for f in facts
                    if f.get("confidence", 0) < self._config.fact_min_confidence
                ]
                if not ids_to_delete:
                    break
                for fid in ids_to_delete:
                    self.sm.delete_fact(fid)
                total_deleted += len(ids_to_delete)
                if len(facts) < batch_size:
                    break
            if total_deleted:
                logger.info("Cleaned up %d low confidence facts", total_deleted)
        except Exception as e:  # noqa: BLE001
            logger.warning("Cleanup failed: %s", e)

    # ── 健康检查 ──────────────────────────────────────────

    def health_check(self) -> dict:
        return {
            "working_count": self.working.count(),
            "working_session": self.working.session_id,
            "forgetting_model": self._forgetting_model,
            "vector_memory": self.vm.health_check(),
            "structured_memory": self.sm.health_check(),
            "fact_extractor": self.fe.health_check(),
            "diary_summarizer": self.ds.health_check(),
        }

    def reset_session(self) -> None:
        self.working.start_session()
        logger.info("Memory session reset")

    def close(self) -> None:
        """关闭后台线程池，释放资源。"""
        if hasattr(self, "_executor") and self._executor is not None:
            try:
                self._executor.shutdown(wait=True)
                logger.info("MemoryPipeline 后台线程池已关闭")
            except Exception as e:  # noqa: BLE001
                logger.warning("MemoryPipeline 关闭线程池异常: %s", e)