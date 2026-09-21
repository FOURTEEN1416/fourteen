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

import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from shisi.config import get_config
from utils.local_time import now_local

from ._legacy_diary_summarizer import DiarySummarizer
from ._legacy_episodic_memory import EpisodicMemory
from ._legacy_importance_scorer import ImportanceScorer
from ._legacy_working_memory import WorkingMemory
from .conflict_detector import ConflictDetector
from .conversation_summarizer import ConversationSummarizer
from .fact_extractor import FactExtractor
from .forgetting_manager import ForgettingManager
from .reflection_engine import ReflectionEngine
from .semantic_memory import SemanticMemory
from .structured_memory import StructuredMemory
from .vector_memory import VectorMemory

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

# 系统错误占位回复：不得写入 chat_history / 工作记忆 / 向量库（LOG 遗留项）。
# 这些文本一旦被当成 assistant 真实发言入库，会污染后续上下文——模型会把
# 「处理超时」当成角色说过的台词继续演。只存用户原话，不存罐头错误语。
_SYSTEM_ERROR_REPLIES = frozenset({
    "抱歉，处理超时，请稍后重试",
    "（处理消息时出现异常, 请稍后重试）",
    "（生成回复时出现异常, 请稍后重试）",
    "系统初始化中, 请稍候...",
    "等下，我还没回完上一条",
})
_SYSTEM_ERROR_MARKERS = (
    "处理超时",
    "消息处理异常",
    "处理消息时出现异常",
)


def _is_system_error_reply(reply: str) -> bool:
    """是否为系统错误占位（非角色真实回复）。

    包 Q · A2：同步识别 utils.fallback_lines 的角色化兜底句，
    避免新的 timeout/empty/exception 旁路句污染 chat_history。
    """
    try:
        from utils.fallback_lines import is_system_fallback_line
        return is_system_fallback_line(reply)
    except Exception:  # noqa: BLE001
        text = (reply or "").strip()
        if not text:
            return True
        return text in _SYSTEM_ERROR_REPLIES or any(m in text for m in _SYSTEM_ERROR_MARKERS)

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
    # ── B3：config/shisi.yaml memory: 五键接线（2026-09-20 用户裁决「全面升级」）──
    extraction_enabled: bool = True
    fact_dedup_similarity: float = 0.85  # yaml similarity_threshold：同事实去重门槛
    short_term_retention_hours: int = 24
    long_term_threshold: int = 5  # 与 fact_extract_interval 同源（长期记忆触发间隔）


def _user_key_from_session(session_id: str) -> str:
    """session_id → user_facts 归属键（**完整会话键** `N:peer`，2026-09-21 隔离修复）。"""
    return StructuredMemory.user_key_from_session(session_id)


def _fact_age_days(updated_at: Any, now: datetime | None = None) -> float:
    """``user_facts.updated_at`` → 距今**天数**（遗忘模型的时间输入）。

    存储层该列由 SQLite ``CURRENT_TIMESTAMP`` 写入 —— **无时区标记的 UTC**
    墙钟串（``YYYY-MM-DD HH:MM:SS``）。旧实现拿它直接与
    ``datetime.now(tz=timezone.utc)`` 相减：naive − aware 必抛 ``TypeError``，
    被 except 吞成 ``days_old = 30.0`` 恒值 → 遗忘判定与真实记忆年龄完全脱钩
    （低置信度事实每晚误删、高置信度永不遗忘，按龄指数衰减整体失效）。

    本函数即修复：
    - naive 串按项目约定解释为 **UTC**（时间差运算统一 UTC，见 utils/local_time
      使用纪律），aware ISO 串原样保留时区；
    - 解析失败/缺失返回 ``0.0``（视为"刚写入"）—— 年龄未知时**不得**触发遗忘
      （fail-safe 保留数据；垃圾数据仍由低置信度清理兜底）；
    - 负年龄（未来时间戳，坏数据）钳到 0。
    """
    ref = now if now is not None else datetime.now(tz=timezone.utc)
    if not isinstance(updated_at, str) or not updated_at.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(updated_at.strip().replace("Z", "+00:00"))
    except ValueError:
        logger.debug("updated_at 无法解析，按 0 天龄处理: %r", updated_at)
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (ref - parsed).total_seconds() / 86400)


def _load_shisi_memory_config() -> dict:
    """从 config/shisi.yaml memory: 读取（B3 接线）；失败时回落硬编码默认。"""
    defaults = {
        "working_memory_capacity": 20,
        "short_term_retention_hours": 24,
        "long_term_threshold": 5,
        "similarity_threshold": 0.85,
        "extraction_enabled": True,
    }
    try:
        mem = get_config("memory") or {}
        if not isinstance(mem, dict):
            return defaults
        return {k: mem.get(k, v) for k, v in defaults.items()}
    except Exception:  # noqa: BLE001
        return defaults


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
        _mem_cfg = _load_shisi_memory_config()
        # yaml 优先；显式构造参数若非默认值则覆盖（保持单测可注入）
        _wl = int(_mem_cfg.get("working_memory_capacity", working_limit) or working_limit)
        if working_limit != 20:
            _wl = working_limit
        _fei = int(_mem_cfg.get("long_term_threshold", fact_extract_interval) or fact_extract_interval)
        if fact_extract_interval != 5:
            _fei = fact_extract_interval
        self._config = MemoryConfig(
            working_limit=_wl,
            retrieval_timeout=retrieval_timeout,
            fact_extract_interval=_fei,
            extraction_enabled=bool(_mem_cfg.get("extraction_enabled", True)),
            fact_dedup_similarity=float(_mem_cfg.get("similarity_threshold", 0.85) or 0.85),
            short_term_retention_hours=int(_mem_cfg.get("short_term_retention_hours", 24) or 24),
            long_term_threshold=_fei,
        )
        working_limit = _wl
        fact_extract_interval = _fei

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
        # 2026-09-21 重扫：`callable(self._llm)` 判据恒为 False（网关对象不可调用）
        # → LLM 事实抽取/日记摘要/记忆反思三条能力**从未启用**，事实只能由正则
        # 规则产出（生产实证的碎片「叫我」「上班」）。统一由 utils.llm_bridge 适配。
        from utils.llm_bridge import to_sync_callable

        llm_callable = to_sync_callable(self._llm)
        self.fe = fact_extractor or FactExtractor(llm_func=llm_callable)
        self.ds = diary_summarizer or DiarySummarizer(
            llm_func=llm_callable,
            structured_memory=self.sm,
        )
        self.emotion = emotion_engine

        # V2 组件
        self.scorer = ImportanceScorer()
        self.forgetting = ForgettingManager(lambda_low, lambda_high)
        self.conflict_detector = ConflictDetector(
            self.semantic, conflict_similarity_threshold
        )
        # 2026-09-22：CrossSessionReasoner/pending_events 死链整体拆除——
        # 每轮「提取→存储→检索」后 orchestrator 第一件事就是 pop 丢弃
        # （optimized_orchestrator「待办不进 system 当对话」），resolve 零调用，
        # 表无界增长；提醒语义由 set_reminder + reminder_delivery 确定性承载。
        # 详见 docs/DELETION_LOG.md。

        # 记忆反思引擎：将零散事实沉淀为洞察
        self.reflection = ReflectionEngine(
            llm_func=llm_callable,
            vector_memory=self.vm,
            structured_memory=self.sm,
            reflection_interval=max(1, fact_extract_interval * 2),
        )

        # 对话摘要器（方案二：摘要+滑动窗口）
        self.summarizer = ConversationSummarizer(llm_gateway)

        # 2026-09-21：启动时一次性把「剥 owner 的裸 peer」迁到完整会话键
        try:
            if hasattr(self.sm, "migrate_legacy_isolation_keys"):
                self.sm.migrate_legacy_isolation_keys()
        except Exception as e:  # noqa: BLE001
            logger.warning("isolation key migration skipped: %s", e)

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

    def write_chat_history_sync(
        self,
        user_msg: str,
        reply: str,
        emotion_tag: str = "",
        session_id: str = "",
        character_id: str = "",
        turn_id: str = "",
        importance: float = 0.0,
        channel: str = "",
    ) -> bool:
        """包 Q · B-a：同步轻写 chat_history 两行 + 工作记忆。

        orchestrator 在返回回复**之前**调用本方法，保证下一轮能读到刚说的内容；
        向量/事实抽取/日记等重活仍走 after_chat 异步路径。

        2026-09-21 重扫：两行都写入**归属**（``character_id``/``user_key``/
        ``turn_id``/``importance``/``channel``）—— 旧实现只有 role + session_id，
        assistant 行没有身份，同会话切换角色后新角色会把上一角色的回复当成
        自己说过的（"分不清谁说的"的存储层根因）。
        """
        effective_session = session_id or self.session_id
        user_key = _user_key_from_session(effective_session)
        store_assistant = not _is_system_error_reply(reply)
        common = dict(
            character_id=str(character_id or ""),
            user_key=user_key,
            turn_id=str(turn_id or ""),
            importance=float(importance or 0.0),
            channel=str(channel or ""),
        )
        try:
            self.sm.add_chat("user", user_msg, emotion_tag=emotion_tag,
                             session_id=effective_session, **common)
            if store_assistant:
                self.sm.add_chat("assistant", reply, emotion_tag=emotion_tag,
                                 session_id=effective_session, **common)
            self.working.add("user", user_msg, emotion_tag, 0.5, session_id=effective_session)
            if store_assistant:
                self.working.add("assistant", reply, emotion_tag, 0.5, session_id=effective_session)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("write_chat_history_sync failed: %s", e)
            return False

    def record_outbound_message(
        self,
        message: str,
        session_id: str = "",
        emotion_tag: str = "",
    ) -> bool:
        """角色**主动发出**的消息（追问 / 主动消息 / 到期提醒）回写对话历史。

        2026-09-21 自问自答根因：这三类消息只发送、从不入库，下一轮 prompt 里
        没有她自己问过的那句 → 重复发同一个问题，并把用户对追问的回应误当成
        用户新起的话题（生产实证 1032 行「我确实提过，但具体是什么事…」）。
        与 write_chat_history_sync 的区别就是**只有 assistant 一行**。
        """
        text = str(message or "").strip()
        effective_session = session_id or self.session_id
        if not text or not effective_session or _is_system_error_reply(text):
            return False
        try:
            self.sm.add_chat(
                "assistant", text, emotion_tag=emotion_tag,
                session_id=effective_session,
            )
            self.working.add(
                "assistant", text, emotion_tag, 0.5,
                session_id=effective_session,
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("record_outbound_message failed: %s", e)
            return False

    def after_chat(
        self,
        user_msg: str,
        reply: str,
        emotion_tag: str = "",
        session_id: str = "",
        history_already_written: bool = False,
        character_id: str = "",
        turn_id: str = "",
        channel: str = "",
    ) -> dict[str, Any]:
        effective_session = session_id or self.session_id
        user_key = _user_key_from_session(effective_session)
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
        # 2026-09-20 修复：原用 datetime.now(tz=timezone.utc)，对 UTC+8 主机使
        # _is_late_night（23:00–05:00）实际落在本地 07:00–13:59 —— 深夜加权错位。
        local_now = now_local()
        try:
            if self._is_late_night(local_now):
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

        # 2. 存储到结构化记忆（B-a：若 orchestrator 已同步写过则跳过，防双插）
        store_assistant = not _is_system_error_reply(reply)
        if not history_already_written:
            try:
                common = dict(
                    character_id=str(character_id or ""),
                    user_key=user_key,
                    turn_id=str(turn_id or ""),
                    importance=float(importance or 0.0),
                    channel=str(channel or ""),
                )
                self.sm.add_chat("user", user_msg, emotion_tag=emotion_tag,
                                 session_id=effective_session, **common)
                if store_assistant:
                    self.sm.add_chat("assistant", reply, emotion_tag=emotion_tag,
                                     session_id=effective_session, **common)
                result["stored_chat"] = True
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to store chat: %s", e)

            # 3. 存储到工作记忆（P0-4-4：按会话分桶，禁止全员混写一桶）
            self.working.add("user", user_msg, emotion_tag, importance, session_id=effective_session)
            if store_assistant:
                self.working.add("assistant", reply, emotion_tag, importance, session_id=effective_session)
        else:
            result["stored_chat"] = True

        # B-a：chat_history 两行已在上方**同步**写入（即使 after_chat 整体被
        # 提交到后台线程，orchestrator 会先调 write_chat_history_sync 保证返回前可见）。

        # 4. 存储到向量库（线程池异步执行，不阻塞主流程）
        if store_assistant:
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

        # 6. 情绪记录
        if emotion_tag:
            try:
                self.vm.store_emotion_log_sync(
                    emotion_tag, importance, trigger="chat"
                )
                result["emotion_updated"] = True
            except Exception as e:  # noqa: BLE001
                logger.warning("Emotion log failed: %s", e)

        # 8. 归档检查（只归档本会话桶）
        if self.working.should_archive(
            self._config.episodic_archive_trigger, session_id=effective_session
        ):
            self._archive_working_memory(session_id=effective_session)
            result["archived"] = True

        return result

    def retrieve_context(
        self,
        query: str,
        session_id: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        检索记忆上下文 — 并行检索三层记忆，**全部按会话/user_key 隔离**。

        2026-09-21 生产串台修复：旧实现 working/episodic/semantic/pending/
        reflections 均无用户过滤，多用户并发时会把他人事实/回忆注入当前 prompt。
        """
        uk = _user_key_from_session(session_id) if session_id else ""
        context = {  # type: ignore[var-annotated]
            "working": [],
            "episodic": [],
            "semantic": [],
            "facts": [],
            "reflections": [],
        }

        # 1. 工作记忆：分桶后直接按会话取；桶空回落 DB 会话历史
        try:
            if session_id:
                bucket = self.working.get_recent(n=10, session_id=session_id)
                context["working"] = bucket or self._load_session_history(session_id, limit=10)
            else:
                context["working"] = []
        except Exception as e:  # noqa: BLE001
            logger.debug("Working memory retrieve failed: %s", e)
            context["working"] = []

        # 2. 情景记忆 — 按 session_id 过滤向量元数据
        start_episodic = time.perf_counter()
        try:
            episodic_results = self.episodic.search(
                query, top_k=top_k, session_id=session_id or None
            )
            context["episodic"] = episodic_results
        except Exception as e:  # noqa: BLE001
            logger.warning("Episodic retrieval failed, degraded: %s", e)
        elapsed_episodic = time.perf_counter() - start_episodic

        # 3. 语义检索 — 强制 user_key（有会话时禁止全库 search_facts）
        start_semantic = time.perf_counter()
        try:
            semantic_results = self.semantic.search(
                query,
                top_k=top_k,
                user_key=uk if session_id else None,
            )
            context["semantic"] = semantic_results.get("structured", [])
            context["facts"] = [
                s.get("fact", "") for s in context["semantic"]
                if isinstance(s, dict)
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("Semantic retrieval failed, degraded: %s", e)
        elapsed_semantic = time.perf_counter() - start_semantic

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

        # 3b. 结构化事实补充（降级回退）— 只认当前会话 user_key
        if not context["facts"] and session_id:
            try:
                facts = self.sm.get_facts(min_confidence=0.3, user_key=uk, limit=top_k)
                context["facts"] = [f["fact"] for f in facts[:top_k]]
            except Exception as e:  # noqa: BLE001
                logger.debug("Structured fact fallback failed: %s", e)

        # 4. 记忆反思洞察 — 按会话过滤
        try:
            context["reflections"] = self.reflection.get_insights(
                query=query, top_k=3, session_id=session_id or None
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to get reflections: %s", e)
            context["reflections"] = []

        return context

    def get_recent_context(self, n: int = 3, session_id: str = "") -> str:
        """获取最近对话上下文文本（会话隔离，2026-09-20：改读 DB 真源）"""
        sess = session_id or self.working.session_id
        recent = self._load_session_history(sess, limit=max(n, 6))[-n:] if sess else []
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
        """
        try:
            # 1. 遗忘
            self._apply_forgetting()

            # 2. 生成每日摘要 — 按会话分桶，禁止全员混写（2026-09-21 隔离）
            date_str = now_local().strftime("%Y-%m-%d")
            today_chats = self.sm.get_chats_today()
            if not today_chats:
                logger.info("No chats today, skipping daily maintenance")
                return None
            buckets: dict[str, list[dict[str, Any]]] = {}
            for c in today_chats:
                sid = str(c.get("session_id") or "")
                buckets.setdefault(sid, []).append(c)
            last_summary = None
            for sid, chats in buckets.items():
                uk = _user_key_from_session(sid) if sid else ""
                summary = self.ds.summarize_day(chats)
                diary_key = f"{uk}|{date_str}" if uk else date_str
                self.ds.save_summary(diary_key, summary)
                last_summary = summary
            self._last_daily_summary = last_summary

            # 3. 清理低置信度事实
            self._cleanup_low_confidence_facts()

            logger.info("Daily maintenance complete: %s", date_str)
            return last_summary

        except Exception as e:  # noqa: BLE001
            logger.error("Daily maintenance failed: %s", e)
            return None

    def get_chat_context(
        self,
        session_id: str = "",
        keep_recent: int = 50,
        summary_trigger: int = 80,
        character_id: str = "",
    ):
        # 2026-09-20 根因修复：对话上下文的唯一真源改为**持久化 chat_history 表**，
        # 不再读全局 RAM deque。旧实现三重缺陷（生产实证 2026-09-20）：
        # ① 重启即失忆——deque 是进程内存，服务重启后历史为空，用户上午聊的
        #   「答应提醒起床」下午全忘（日志 hist_msgs 归零实证）；
        # ② 跨会话串扰——deque 不带 session 标签，两个好友同时聊天时消息互相
        #   混入对方上下文（违反多用户隔离硬约束）；上下文供给已改读 DB 会话真源。
        # ③ 历史裸形态遗留：owner 会话不再并入裸 peer 历史（2026-09-21）。
        # working deque 保留给后台归档/情景记忆任务，不再承担上下文供给。
        sess = session_id or self.working.session_id
        messages = self._load_session_history(sess, limit=keep_recent + 40, character_id=character_id)
        if not messages:
            return [], ""
        return self.summarizer.get_chat_context(
            messages,
            session_id=sess,
            keep_recent=keep_recent,
            summary_trigger=summary_trigger,
        )

    def get_cross_session_tail(self, session_id: str = "", limit: int = 8) -> list[str]:
        """B-d：跨会话尾巴（delegate structured_memory）。"""
        sess = session_id or self.working.session_id
        sm = self.sm
        if sm is None or not hasattr(sm, "get_cross_session_tail"):
            return []
        try:
            return sm.get_cross_session_tail(sess, limit=limit) or []
        except Exception as e:  # noqa: BLE001
            logger.debug("get_cross_session_tail failed: %s", e)
            return []

    def _load_session_history(
        self, session_id: str, limit: int, character_id: str = ""
    ) -> list[dict[str, Any]]:
        """从 chat_history 表按会话加载最近对话（**严格会话隔离 + 归属完整**）。

        2026-09-21 重扫（与「模型分不清谁说的」直接相关，三处根治）：

        ① **删除内容去重**：旧实现把结果塞进以 ``(created_at, content)`` 为键的
           dict —— 秒级时间戳下，**内容相同的两条消息被静默折叠成一条**
           （连续两条"嗯"、"好的"只剩一条），上下文缺句。
        ② **排序改用 `id`**：`created_at` 只有秒精度，用户与角色的同秒消息
           相对次序不保证（"谁先说的"取决于扫描方向）→ 现由存储层按
           AUTOINCREMENT 的 `id`（= 写入序）判序。
        ③ **不再丢弃归属**：返回体带 ``character_id`` / ``user_key`` /
           ``turn_id`` / ``channel`` / **真实 importance**（旧实现硬编码 0.5，
           属假指标）；下游据此可把每句归到具体角色，切换角色不再继承他人台词。

        owner 形态 `N:peer@im.wechat` **只读自己的 session_id**，禁止并入裸 peer
        遗留历史（旧双形态合并会让同一 peer 下多个 owner 看见同一批消息）。
        """
        if not session_id:
            return []
        try:
            if character_id:
                rows = self.sm.get_chats_by_session_limit(
                    session_id, limit, character_id=character_id,
                )
            else:
                # 不传角色时保持旧签名调用（兼容未升级的 StructuredMemory 替身）
                rows = self.sm.get_chats_by_session_limit(session_id, limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("按会话加载历史失败 session=%s: %s", session_id, e)
            return []
        out: list[dict[str, Any]] = []
        for r in rows or []:
            content = str(r.get("content") or "").strip()
            if not content:
                continue
            out.append({
                "id": int(r.get("id") or 0),
                "role": r.get("role", "user"),
                "content": content,
                "emotion": r.get("emotion_tag", ""),
                "importance": float(r.get("importance") or 0.0),
                "timestamp": r.get("created_at", ""),
                "character_id": str(r.get("character_id") or ""),
                "user_key": str(r.get("user_key") or "") or str(r.get("session_id") or ""),
                "turn_id": str(r.get("turn_id") or ""),
                "channel": str(r.get("channel") or ""),
            })
        return out

    # ── V1 兼容接口 ──────────────────────────────────────

    def get_memory_context(
        self,
        n_chats: int = 10,
        affinity_level: int = 0,
        session_id: str = "",
    ) -> dict[str, Any]:
        """V1兼容：获取当前对话需要的记忆上下文

        包 Q · B-c：注入条数 k = min(4 + ceil(level/2), 10)；
        优先 relationship/commitment > preference > 普通 fact。

        2026-09-20 全仓复核：`recent_chats` 与事实归属必须按会话隔离——
        旧实现 `get_recent_chats` 读全局表，多用户并发时会把他人聊天
        注入当前用户的记忆上下文（违反隔离硬约束）。
        """
        import math

        sess = session_id or self.session_id
        k = min(4 + math.ceil(max(0, int(affinity_level)) / 2), 10)
        context = {
            "recent_chats": [],
            "user_facts": [],
            "user_fact_rows": [],
            "topics": [],
            "relationship_facts": [],
            "today_summary": "",
            "emotion_trend": {},
            "injection_k": k,
        }

        try:
            if sess:
                context["recent_chats"] = self._load_session_history(sess, limit=n_chats)
            else:
                context["recent_chats"] = []
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get recent chats: %s", e)

        try:
            # 完整隔离：只注入当前会话归属的事实（user_key 从 session 澄清）
            uk = _user_key_from_session(sess)
            facts = self.sm.get_facts(min_confidence=0.3, user_key=uk, limit=50)

            def _prio(row: dict) -> int:
                cat = str(row.get("category") or "")
                if cat in ("relationship", "commitment"):
                    return 0
                if cat == "preference":
                    return 1
                return 2

            facts_sorted = sorted(facts, key=lambda r: (_prio(r), -(r.get("confidence") or 0)))
            selected = facts_sorted[:k]
            try:
                from utils.prompt_sanitize import is_injectable_fact

                selected = [f for f in selected if is_injectable_fact(str(f.get("fact") or ""))][:k]
            except Exception:  # noqa: BLE001
                pass
            context["user_facts"] = [f["fact"] for f in selected]
            context["user_fact_rows"] = selected
            topics: list[str] = []
            rel: list[str] = []
            for f in selected:
                if f.get("category") in ("relationship", "commitment"):
                    rel.append(str(f.get("fact") or ""))
                tp = str(f.get("topics") or "")
                for t in tp.split(","):
                    t = t.strip()
                    if t and t not in topics:
                        topics.append(t)
            context["topics"] = topics[:3]
            context["relationship_facts"] = rel[:2]
            # B4：被注入上下文即算一次「回忆」→ access_count+1
            ids = [f.get("id") for f in selected if f.get("id") is not None]
            if ids and hasattr(self.sm, "increment_fact_access"):
                try:
                    self.sm.increment_fact_access(ids)
                except Exception as ie:  # noqa: BLE001
                    logger.debug("increment_fact_access failed: %s", ie)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to get facts: %s", e)

        try:
            # 2026-09-21：日记/情绪趋势按会话隔离写读（键 = user_key|本地日）
            uk = _user_key_from_session(sess) if sess else ""
            date_str = now_local().strftime("%Y-%m-%d")
            summaries = self.ds.get_all_summaries()
            if summaries:
                diary_key = f"{uk}|{date_str}" if uk else date_str
                # 同时兼容未加前缀的旧全局键（仅当无会话归属时）
                context["today_summary"] = summaries.get(diary_key, "") or (
                    summaries.get(date_str, "") if not uk else ""
                )
                trend = self.ds.detect_mood_trend(summaries)
                context["emotion_trend"] = trend
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to detect trend: %s", e)

        return context

    def get_formatted_context(
        self,
        n_chats: int = 6,
        affinity_level: int = 0,
        session_id: str = "",
    ) -> str:
        """V1兼容：获取格式化的记忆上下文文本（用于注入 prompt）

        包 Q · B-c 标题：# 关于用户 / # 最近话题 / # 我们之间
        """
        ctx = self.get_memory_context(
            n_chats, affinity_level=affinity_level, session_id=session_id,
        )
        parts = []

        if ctx["user_facts"]:
            parts.append("# 关于用户")
            for fact in ctx["user_facts"]:
                parts.append(f"- {fact}")

        if ctx.get("topics"):
            parts.append("# 最近话题")
            parts.append("、".join(ctx["topics"][:3]))

        if ctx.get("relationship_facts"):
            parts.append("# 我们之间")
            for fact in ctx["relationship_facts"]:
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
            facts = self.sm.get_facts(min_confidence=0.0, limit=1000, user_key=None)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load facts for forgetting: %s", e)
            return 0

        for fact in facts:
            # 2026-09-22 修复：时间输入统一经 _fact_age_days —— 旧实现 naive/aware
            # 相减恒抛 TypeError 被 except 吞成 30.0，按龄遗忘整体失效（见其 docstring）。
            days_old = _fact_age_days(fact.get("updated_at"))

            importance = fact.get("confidence", 0.5)
            access_count = fact.get("access_count", 0)

            should_remove = False
            if self._forgetting_model == "exponential":
                # B4：access_count 进入权重（越回忆越牢）
                should_remove = self.forgetting.should_delete(
                    importance, days_old, access_count=access_count
                )
            elif self._forgetting_model == "threshold":
                should_remove = not self.scorer.should_retain(
                    importance, days_old, access_count
                )

            if should_remove:
                try:
                    # B5：进回收站再删主表行（数据只增不减，可 restore）
                    uk = fact.get("user_key", "") or ""
                    self.sm.delete_fact(fact["id"], recycle=True, user_key=uk)
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
        B3：`extraction_enabled=false` 时整段跳过（配置可关）。
        """
        if not getattr(self._config, "extraction_enabled", True):
            logger.debug("Fact extraction disabled by config memory.extraction_enabled")
            return 0
        count = 0
        _uk = _user_key_from_session(session_id)
        try:
            # 隔离：只抽取**本会话**消息。旧实现读全局 get_recent_chats(10)，
            # 多用户并发时会把他人消息提取后写进当前 user_key（违反隔离硬约束）。
            recent = self._load_session_history(session_id, limit=10) if session_id else []
            # 选择性记忆：过滤掉敷衍且无情感的消息
            # 2026-09-20 修复：该 now 会被传入 should_store_as_fact → _is_late_night，
            # 属墙钟判定，须用本地时间（原先 UTC 使深夜规则整体错位 8 小时）。
            now = now_local()
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

                # 冲突检测（P1-13：按本人 user_key 隔离，禁止跨用户判冲突）
                conflict = self.conflict_detector.check_conflict(
                    fact["fact"], fact.get("category", "general"),
                    user_key=_uk,
                )
                if conflict:
                    existing_fact = conflict.get("existing_fact", "")
                    logger.debug(
                        "Fact conflict detected: new=%s vs existing=%s",
                        fact["fact"][:30], existing_fact[:30],
                    )
                    continue

                # B-b：near-dup 由 add_fact 内部 UPDATE 强化，不再「查到就 continue」
                # （旧逻辑 search_facts 任一命中即跳过 → 重复事实永不 reinforce）。
                topics = fact.get("topics")
                if isinstance(topics, str):
                    topics_list: list[str] | str | None = topics
                elif isinstance(topics, (list, tuple)):
                    topics_list = list(topics)
                else:
                    topics_list = None
                if self.semantic.add_fact(
                    fact["fact"],
                    fact.get("category", "general"),
                    fact.get("confidence", 0.5),
                    fact.get("source", ""),
                    user_key=_uk,
                    topics=topics_list,
                ):
                    count += 1

        except Exception as e:  # noqa: BLE001
            logger.warning("Fact extraction failed: %s", e)

        # 记忆反思：事实足够时生成更高层洞察
        if count > 0:
            try:
                facts = self.semantic.get_facts(limit=20, user_key=_user_key_from_session(session_id))
                episodes = self.episodic.search(
                    "", top_k=3, session_id=session_id or None
                )
                self.reflection.maybe_reflect(
                    facts=[{"fact": f.get("fact", ""), "category": f.get("category", "general")} for f in facts],
                    episodes=episodes,
                    session_id=session_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Reflection trigger failed: %s", e)

        return count

    def _archive_working_memory(self, session_id: str = "") -> None:
        """归档工作记忆到情景记忆（P0-4-4：只取本会话桶，不再全员混档）"""
        sid = str(session_id or "") or self.working.session_id
        messages = self.working.get_for_archive(session_id=sid)
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
                session_id=sid,
            )
            # 仅在归档成功后清空工作记忆（防止数据丢失）
            self.working.clear(session_id=sid)
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
