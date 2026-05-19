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

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("memory_pipeline")


# ═══════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
#  三层记忆 — 自包含实现
# ═══════════════════════════════════════════════════════════════

class WorkingMemory:
    """工作记忆 — 短期对话上下文（deque O(1) + 自动归档触发）"""

    def __init__(self, limit: int = 20):
        self._messages: deque = deque(maxlen=limit)
        self._session_id: str = ""
        self._lock = threading.Lock()
        self._total_count = 0

    @property
    def session_id(self) -> str:
        if not self._session_id:
            self._session_id = f"session_{int(time.time())}"
        return self._session_id

    def start_session(self, session_id: str = "") -> None:
        with self._lock:
            self._messages.clear()
            self._session_id = session_id or f"session_{int(time.time())}"
            self._total_count = 0

    def add(self, role: str, content: str, emotion: str = "",
            importance: float = 0.5) -> None:
        with self._lock:
            self._messages.append({
                "role": role,
                "content": content,
                "emotion": emotion,
                "importance": importance,
                "timestamp": time.time(),
            })
            self._total_count += 1

    def get_recent(self, n: int = 10) -> List[Dict]:
        with self._lock:
            return list(self._messages)[-n:]

    def get_for_archive(self) -> List[Dict]:
        with self._lock:
            return list(self._messages)

    def should_archive(self, trigger_count: int = 20) -> bool:
        return self._total_count >= trigger_count

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._total_count = 0

    def count(self) -> int:
        return len(self._messages)


class EpisodicMemory:
    """情景记忆 — 对话片段归档（向量检索 + 摘要压缩 + 分层存储）"""

    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()

    def store_episode(self, messages: List[Dict], summary: str = "",
                      importance: float = 0.5, session_id: str = "") -> str:
        if not messages:
            return ""
        episode_id = f"ep_{int(time.time())}_{hash(str(messages)) % 10000}"
        if not summary:
            summary = self._generate_summary(messages)
        content = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages
        )
        metadata = {
            "type": "episode",
            "episode_id": episode_id,
            "session_id": session_id,
            "summary": summary,
            "importance": importance,
            "message_count": len(messages),
            "timestamp": time.time(),
        }
        try:
            self._vm.store_text(content, metadata)
            self._sm.add_episode(episode_id, summary, importance, metadata)
            logger.debug("Episode stored: %s", episode_id)
            return episode_id
        except Exception as e:
            logger.warning("Failed to store episode: %s", e)
            return ""

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        try:
            return self._vm.search(query, top_k=top_k,
                                   filter_dict={"type": "episode"})
        except Exception as e:
            logger.warning("Episode search failed: %s", e)
            return []

    def _generate_summary(self, messages: List[Dict]) -> str:
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        if len(user_msgs[-1]) > 10:
            return user_msgs[-1][:100]
        longest = max(user_msgs, key=len, default="")
        return longest[:100] if longest else ""


class SemanticMemory:
    """语义记忆 — 提取的事实和知识（去重 + 冲突检测 + 置信度管理）"""

    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory
        self._fact_cache: Set[int] = set()

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "") -> bool:
        fact_hash = hash(fact) % 10000000
        if fact_hash in self._fact_cache:
            return False
        try:
            similar = self._sm.search_facts(fact)
            if similar and len(similar) > 0:
                first = similar[0]
                if isinstance(first, dict) and first.get("similarity", 0) > 0.9:
                    logger.debug("Similar fact exists, skipping: %s...", fact[:30])
                    return False
        except Exception:
            pass
        try:
            self._sm.add_fact(fact, category, confidence, source)
            self._fact_cache.add(fact_hash)
            try:
                self._vm.store_text(fact, {
                    "type": "fact",
                    "category": category,
                    "confidence": confidence,
                })
            except Exception:
                pass
            return True
        except Exception as e:
            logger.warning("Failed to add fact: %s", e)
            return False

    def search(self, query: str, top_k: int = 5) -> Dict[str, List]:
        results = {"vector": [], "structured": []}
        try:
            vector_results = self._vm.search(query, top_k=top_k,
                                             filter_dict={"type": "fact"})
            results["vector"] = vector_results
            structured_results = self._sm.search_facts(query)
            results["structured"] = structured_results
        except Exception as e:
            logger.warning("Fact search failed: %s", e)
        return results

    def extract_facts_from_message(self, message: str) -> List[Dict]:
        facts = []
        patterns = [
            (r"我喜欢(.+)", "preference"),
            (r"我讨厌(.+)", "dislike"),
            (r"我是(.+)", "identity"),
            (r"我在(.+)(工作|上学)", "occupation"),
            (r"我的(.+)是(.+)", "attribute"),
        ]
        for pattern, category in patterns:
            matches = re.findall(pattern, message)
            for match in matches:
                fact_text = match if isinstance(match, str) else match[-1]
                facts.append({
                    "fact": fact_text.strip(),
                    "category": category,
                    "confidence": 0.6,
                })
        return facts


# ═══════════════════════════════════════════════════════════════
#  重要性评分器（Optimized 版）
# ═══════════════════════════════════════════════════════════════

class ImportanceScorer:
    """重要性评分器 — 关键词 + 情感 + 信息密度 + 时间衰减"""

    KEYWORD_WEIGHTS = {
        "喜欢": 0.3, "爱": 0.4, "想": 0.2,
        "重要": 0.3, "记住": 0.3, "别忘": 0.3,
        "生日": 0.5, "纪念日": 0.5, "约定": 0.4,
        "生气": 0.3, "难过": 0.3, "开心": 0.2,
    }

    EMOTION_WEIGHTS = {
        "生气": 0.3, "难过": 0.3, "开心": 0.1,
        "撒娇": 0.2, "吃醋": 0.25, "傲娇": 0.15,
    }

    def score(self, content: str, emotion: str = "",
              context: Dict = None) -> float:
        s = 0.3
        for keyword, weight in self.KEYWORD_WEIGHTS.items():
            if keyword in content:
                s += weight
        s += self.EMOTION_WEIGHTS.get(emotion, 0.1)
        s += min(0.2, len(content) / 500)
        if context and context.get("is_response_to_question"):
            s += 0.1
        return min(1.0, s)

    def should_retain(self, importance: float, days_old: float,
                      access_count: int = 0) -> bool:
        if importance >= 0.8:
            return True
        time_decay = max(0, 1 - days_old / 30)
        access_bonus = min(0.3, access_count * 0.05)
        final_score = importance * time_decay + access_bonus
        return final_score >= 0.2


# ═══════════════════════════════════════════════════════════════
#  V2 遗忘管理器 — 指数衰减模型
# ═══════════════════════════════════════════════════════════════

class ForgettingManager:
    """指数衰减遗忘模型 — 重要性分层衰减率"""

    def __init__(self, lambda_low: float = 0.1, lambda_high: float = 0.01):
        self.lambda_low = lambda_low
        self.lambda_high = lambda_high

    def retrieval_weight(self, importance: float,
                         days_since_access: float) -> float:
        lam = self.lambda_high if importance >= 0.7 else self.lambda_low
        weight = importance * math.exp(-lam * days_since_access)
        return max(0.0, min(1.0, weight))

    def should_delete(self, importance: float, days_since_access: float,
                      threshold: float = 0.05) -> bool:
        return self.retrieval_weight(importance, days_since_access) < threshold


# ═══════════════════════════════════════════════════════════════
#  V2 冲突检测器 — 相似度冲突检测
# ═══════════════════════════════════════════════════════════════

class ConflictDetector:
    """基于向量相似度的事实冲突检测"""

    def __init__(self, semantic_memory: SemanticMemory,
                 similarity_threshold: float = 0.3):
        self._sem = semantic_memory
        self._threshold = similarity_threshold

    def check_conflict(self, new_fact: str, category: str) -> Optional[Dict]:
        try:
            search_results = self._sem.search(new_fact, top_k=3)
            vector_results = search_results.get("vector", [])
            for result in vector_results:
                existing = result.get("content", "")
                distance = result.get("distance", 1.0)
                if distance < self._threshold and existing != new_fact:
                    return {
                        "new_fact": new_fact,
                        "existing_fact": existing,
                        "similarity": 1.0 - distance,
                        "category": category,
                        "status": "pending",
                    }
        except Exception as e:
            logger.debug("Conflict detection failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════
#  V2 跨会话推理器
# ═══════════════════════════════════════════════════════════════

class CrossSessionReasoner:
    """跨会话推理 — 提取未来事件并跟踪"""

    FUTURE_KEYWORDS = ["明天", "下周", "周末", "之后", "以后", "即将", "将要"]

    def __init__(self, structured_memory):
        self._sm = structured_memory

    def extract_pending_event(self, fact: str) -> Optional[Dict]:
        for kw in self.FUTURE_KEYWORDS:
            if kw in fact:
                return {"event_desc": fact, "keyword": kw}
        return None

    def store_pending_event(self, event_desc: str,
                            expected_time: Optional[str] = None,
                            session_id: str = ""):
        try:
            with self._sm._conn() as conn:
                conn.execute(
                    "INSERT INTO pending_events "
                    "(event_desc, expected_time, source_session_id) "
                    "VALUES (?, ?, ?)",
                    (event_desc, expected_time, session_id),
                )
                conn.commit()
        except Exception as e:
            logger.warning("store_pending_event failed: %s", e)

    def get_pending_events(self) -> List[Dict]:
        try:
            with self._sm._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM pending_events WHERE is_resolved = 0 "
                    "ORDER BY created_at ASC"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.debug("get_pending_events failed: %s", e)
            return []

    def resolve_event(self, event_id: int):
        try:
            with self._sm._conn() as conn:
                conn.execute(
                    "UPDATE pending_events SET is_resolved = 1 WHERE id = ?",
                    (event_id,),
                )
                conn.commit()
        except Exception as e:
            logger.warning("resolve_event failed: %s", e)


# ═══════════════════════════════════════════════════════════════
#  V1 FactExtractor — 事实提取器
# ═══════════════════════════════════════════════════════════════

FACT_CATEGORIES = [
    "preference", "habit", "event", "personal",
    "relationship", "work", "health", "general",
]

PATTERNS = {
    "preference": [
        r"(?:喜欢|爱[好]?|最[爱喜]|超[级]?[喜爱]).{0,20}(?:吃|喝|玩|去|看|听|做|打)",
        r"(?:不[喜爱]|讨厌|受不了).{0,20}",
    ],
    "habit": [
        r"(?:每天|平时|经常|总是|习惯).{0,30}(?:睡觉|起床|吃饭|运动|工作|学习|打游戏)",
        r"(?:熬夜|早起|午睡|健身|跑步)",
    ],
    "event": [
        r"(?:下周|明天|后天|下个月|这周末|周末).{0,30}(?:去|要|打算|准备|计划)",
        r"(?:出差|旅行|搬家|考试|面试|约会|聚会)",
    ],
    "personal": [
        r"(?:我[叫是]|名字).{0,10}",
        r"(?:住在|来自|家在).{0,15}",
        r"(?:今年|岁|出生).{0,10}",
    ],
    "work": [
        r"(?:上班|工作|公司|同事|老板|项目|加班)",
        r"(?:学生|上学|考试|专业|毕业|学校)",
    ],
    "health": [
        r"(?:生病|感冒|发烧|头疼|肚子疼|不舒服|医院|吃药)",
        r"(?:失眠|睡不着|累|疲惫|乏力)",
    ],
}


class FactExtractor:
    """事实提取器 — LLM模式 / 规则模式"""

    def __init__(self, llm_func: Optional[Callable] = None):
        self.llm_func = llm_func

    def extract_facts(self, user_messages: List[str]) -> List[Dict[str, Any]]:
        if not user_messages:
            return []
        if self.llm_func:
            return self._extract_with_llm(user_messages)
        return self._extract_with_rules(user_messages)

    def _extract_with_llm(self, messages: List[str]) -> List[Dict[str, Any]]:
        text = "\n".join(messages[-20:])
        prompt = f"""从以下对话中提取关于用户的事实信息。
只提取明确提到的、有具体内容的事实。
对每个事实给出类别和置信度(0~1)。

输出 JSON 数组格式：
[
  {{"fact": "用户喜欢吃火锅", "category": "preference", "confidence": 0.9}},
  {{"fact": "用户下周去北京出差", "category": "event", "confidence": 0.8}}
]

类别: {', '.join(FACT_CATEGORIES)}

对话内容:
{text}

JSON:"""
        try:
            result = self.llm_func(prompt)
            facts = self._parse_json_result(result)
            if facts:
                for f in facts:
                    f["source"] = "llm"
                return facts
        except Exception as e:
            logger.warning("LLM fact extraction failed: %s", e)
        return []

    def _extract_with_rules(self, messages: List[str]) -> List[Dict[str, Any]]:
        facts = []
        for msg in messages:
            for category, patterns in PATTERNS.items():
                for pattern in patterns:
                    matches = re.findall(pattern, msg)
                    for m in matches:
                        fact_text = m.strip()
                        if len(fact_text) < 2:
                            continue
                        if not any(f["fact"] == fact_text for f in facts):
                            facts.append({
                                "fact": fact_text,
                                "category": category,
                                "confidence": 0.5,
                                "source": "rule",
                            })
        return facts

    @staticmethod
    def deduplicate(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not facts:
            return []
        by_category: Dict[str, list] = {}
        for f in facts:
            cat = f.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(f)
        result = []
        for cat, items in by_category.items():
            items.sort(key=lambda x: x.get("confidence", 0), reverse=True)
            seen_texts = set()
            for item in items:
                text = item["fact"].strip()
                if text not in seen_texts:
                    seen_texts.add(text)
                    result.append(item)
        return result

    @staticmethod
    def _parse_json_result(text: str) -> Optional[List[Dict[str, Any]]]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    def health_check(self) -> dict:
        return {
            "llm_available": self.llm_func is not None,
            "rule_patterns": sum(len(p) for p in PATTERNS.values()),
        }


# ═══════════════════════════════════════════════════════════════
#  V1 DiarySummarizer — 日记摘要器
# ═══════════════════════════════════════════════════════════════

class DiarySummarizer:
    """日记摘要器 — 每日对话总结 + 情绪趋势分析"""

    def __init__(self, llm_func: Optional[Callable] = None):
        self.llm_func = llm_func
        self._daily_summaries: Dict[str, str] = {}

    def summarize_day(self, chats: List[Dict[str, Any]]) -> str:
        if not chats:
            return "今天没有聊天记录。"
        if self.llm_func:
            return self._summarize_with_llm(chats)
        return self._summarize_with_template(chats)

    def summarize_week(self, daily_summaries: List[str]) -> str:
        if not daily_summaries:
            return "本周没有记录。"
        if self.llm_func:
            text = "\n".join(daily_summaries)
            prompt = f"""以下是本周每日摘要，请生成周报：

{text}

周报格式：
## 本周概览
- 聊天频率:
- 主要话题:
- 用户状态:

## 新发现
- ...

## 情绪趋势
- ..."""
            try:
                return self.llm_func(prompt)
            except Exception as e:
                logger.warning("Weekly LLM summary failed: %s", e)
        return "\n".join(daily_summaries)

    def detect_mood_trend(self, daily_summaries: Dict[str, str]) -> Dict[str, Any]:
        if not daily_summaries:
            return {"trend": "无数据", "avg_mood": "未知", "notable_days": []}
        mood_keywords = {
            "positive": ["开心", "高兴", "不错", "愉快", "好", "甜", "暖", "感动"],
            "negative": ["难过", "生气", "累", "不好", "烦", "伤心", "郁闷", "焦虑"],
        }
        mood_scores = {}
        for date_str, summary in daily_summaries.items():
            score = 0
            for kw in mood_keywords["positive"]:
                if kw in summary:
                    score += 1
            for kw in mood_keywords["negative"]:
                if kw in summary:
                    score -= 1
            mood_scores[date_str] = score
        scores = list(mood_scores.values())
        if len(scores) >= 2:
            if scores[-1] > scores[0]:
                trend = "上升趋势，关系在变好"
            elif scores[-1] < scores[0]:
                trend = "下降趋势，需要注意"
            else:
                trend = "稳定"
        else:
            trend = "数据不足"
        if scores:
            avg = sum(scores) / len(scores)
            if avg > 1:
                avg_mood = "积极"
            elif avg < -1:
                avg_mood = "消极"
            else:
                avg_mood = "中性"
        else:
            avg_mood = "未知"
        notable = [d for d, s in mood_scores.items() if abs(s) >= 2]
        return {"trend": trend, "avg_mood": avg_mood, "notable_days": notable}

    def save_summary(self, date_str: str, summary: str) -> None:
        self._daily_summaries[date_str] = summary
        logger.info("Diary summary saved for %s", date_str)

    def get_summary(self, date_str: str) -> Optional[str]:
        return self._daily_summaries.get(date_str)

    def get_all_summaries(self) -> Dict[str, str]:
        return dict(self._daily_summaries)

    def _summarize_with_llm(self, chats: List[Dict[str, Any]]) -> str:
        chat_text = "\n".join(
            f"{'用户' if c['role'] == 'user' else '小暖'}: {c['content']}"
            for c in chats[-30:]
        )
        prompt = f"""以下是今天的对话记录，请生成简洁的每日摘要。

要求：
1. 今天聊了什么话题
2. 用户的情绪状态
3. 有什么新发现或值得记住的事
4. 关系进展

对话记录：
{chat_text}

每日摘要（100字以内）："""
        try:
            return self.llm_func(prompt)
        except Exception as e:
            logger.warning("LLM summary failed: %s", e)
            return self._summarize_with_template(chats)

    def _summarize_with_template(self, chats: List[Dict[str, Any]]) -> str:
        user_msgs = [c for c in chats if c.get("role") == "user"]
        assistant_msgs = [c for c in chats if c.get("role") == "assistant"]
        user_count = len(user_msgs)
        assistant_count = len(assistant_msgs)
        total = user_count + assistant_count
        all_content = " ".join(c["content"] for c in chats)
        topic_keywords = [
            "工作", "学习", "吃饭", "睡觉", "游戏", "电影",
            "音乐", "运动", "旅行", "家人", "朋友", "心情",
            "天气", "购物", "计划",
        ]
        mentioned = [kw for kw in topic_keywords if kw in all_content]
        emotions = [c.get("emotion_tag", "") for c in assistant_msgs
                    if c.get("emotion_tag")]
        emotion_summary = ", ".join(set(emotions)) if emotions else "未记录"
        summary = f"今日共 {total} 条消息（用户 {user_count} 条，小暖 {assistant_count} 条）。"
        if mentioned:
            summary += f" 提到话题：{'、'.join(mentioned)}。"
        if emotion_summary != "未记录":
            summary += f" 情绪状态：{emotion_summary}。"
        return summary

    def health_check(self) -> dict:
        return {
            "llm_available": self.llm_func is not None,
            "summaries_count": len(self._daily_summaries),
        }


# ═══════════════════════════════════════════════════════════════
#  融合记忆管线
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
        fact_extractor: Optional[FactExtractor] = None,
        diary_summarizer: Optional[DiarySummarizer] = None,
        emotion_engine: Optional[Any] = None,
        llm_gateway: Optional[Any] = None,
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
            from .vector_memory import VectorMemory
            vector_memory = VectorMemory()
        if structured_memory is None:
            from .structured_memory import StructuredMemory
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
            llm_func=self._llm if callable(self._llm) else None
        )
        self.emotion = emotion_engine

        # V2 组件
        self.scorer = ImportanceScorer()
        self.forgetting = ForgettingManager(lambda_low, lambda_high)
        self.conflict_detector = ConflictDetector(
            self.semantic, conflict_similarity_threshold
        )
        self.cross_session = CrossSessionReasoner(self.sm)

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
        self._last_daily_summary: Optional[str] = None
        self._chat_count_since_extract: int = 0

        # 缓存
        self._context_cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()

        logger.info(
            "MemoryPipeline initialized (forgetting=%s, working_limit=%d)",
            self._forgetting_model, working_limit,
        )

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

        # 2. 存储到结构化记忆
        try:
            self.sm.add_chat("user", user_msg, emotion_tag=emotion_tag,
                             session_id=effective_session)
            self.sm.add_chat("assistant", reply, emotion_tag=emotion_tag,
                             session_id=effective_session)
            result["stored_chat"] = True
        except Exception as e:
            logger.warning("Failed to store chat: %s", e)

        # 3. 存储到工作记忆
        self.working.add("user", user_msg, emotion_tag, importance)
        self.working.add("assistant", reply, emotion_tag, importance)

        # 4. 存储到向量库
        try:
            self.vm.store_chat(user_msg, reply, {
                "emotion": emotion_tag,
                "session_id": effective_session,
                "importance": importance,
            })
            result["stored_vector"] = True
        except Exception as e:
            logger.debug("Vector store failed: %s", e)

        # 5. 事实提取（每 N 条对话触发）
        self._chat_count_since_extract += 1
        if self._chat_count_since_extract >= self._config.fact_extract_interval:
            self._chat_count_since_extract = 0
            result["facts_extracted"] = self._do_fact_extraction(
                effective_session
            )

        # 6. 跨会话推理：检测未来事件
        try:
            pending = self.cross_session.extract_pending_event(user_msg)
            if pending:
                self.cross_session.store_pending_event(
                    pending["event_desc"], session_id=effective_session
                )
        except Exception as e:
            logger.debug("Cross-session reasoning failed: %s", e)

        # 7. 情绪记录
        if emotion_tag:
            try:
                self.vm.store_emotion_log(
                    emotion_tag, importance, trigger="chat"
                )
                result["emotion_updated"] = True
            except Exception as e:
                logger.debug("Emotion log failed: %s", e)

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
    ) -> Dict[str, Any]:
        """
        检索记忆上下文 — 并行检索三层记忆，向量检索超时降级

        Returns:
            {"working": [], "episodic": [], "semantic": [], "facts": []}
        """
        context = {
            "working": [],
            "episodic": [],
            "semantic": [],
            "facts": [],
        }

        # 1. 工作记忆（最快，无超时风险）
        context["working"] = self.working.get_recent(n=10)

        # 2. 向量检索（带超时降级）
        start = time.perf_counter()
        try:
            episodic_results = self.episodic.search(query, top_k=top_k)
            context["episodic"] = episodic_results
        except Exception as e:
            logger.warning("Episodic retrieval failed, degraded: %s", e)

        elapsed = time.perf_counter() - start
        if elapsed > self._config.retrieval_timeout:
            logger.warning(
                "Episodic retrieval slow (%.2fs), skipping semantic", elapsed
            )
        else:
            try:
                semantic_results = self.semantic.search(query, top_k=top_k)
                context["semantic"] = semantic_results.get("structured", [])
                context["facts"] = [
                    s.get("fact", "") for s in context["semantic"]
                    if isinstance(s, dict)
                ]
            except Exception as e:
                logger.warning("Semantic retrieval failed, degraded: %s", e)

        # 3. 结构化事实补充（降级回退）
        if not context["facts"]:
            try:
                facts = self.sm.get_facts(min_confidence=0.3)
                context["facts"] = [f["fact"] for f in facts[:top_k]]
            except Exception as e:
                logger.debug("Structured fact fallback failed: %s", e)

        # 4. 待处理事件
        try:
            context["pending_events"] = self.cross_session.get_pending_events()
        except Exception:
            context["pending_events"] = []

        return context

    def get_recent_context(self, n: int = 3) -> str:
        """获取最近对话上下文文本"""
        recent = self.working.get_recent(n=n)
        return "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in recent
        )

    def daily_maintenance(self) -> Optional[str]:
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
            date_str = datetime.now().strftime("%Y-%m-%d")
            self.ds.save_summary(date_str, summary)
            self._last_daily_summary = summary

            # 3. 清理低置信度事实
            self._cleanup_low_confidence_facts()

            # 4. 清理缓存
            with self._cache_lock:
                self._context_cache.clear()

            logger.info("Daily maintenance complete: %s", date_str)
            return summary

        except Exception as e:
            logger.error("Daily maintenance failed: %s", e)
            return None

    # ── V1 兼容接口 ──────────────────────────────────────

    def get_memory_context(self, n_chats: int = 10) -> Dict[str, Any]:
        """V1兼容：获取当前对话需要的记忆上下文"""
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
                context["today_summary"] = summaries.get(
                    datetime.now().strftime("%Y-%m-%d"), ""
                )
                trend = self.ds.detect_mood_trend(summaries)
                context["emotion_trend"] = trend
        except Exception as e:
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
        except Exception:
            return 0

        for fact in facts:
            try:
                updated_at_str = fact.get("updated_at", "")
                if updated_at_str:
                    updated_dt = datetime.fromisoformat(
                        updated_at_str.replace("Z", "+00:00")
                    ) if isinstance(updated_at_str, str) else datetime.now()
                    days_old = (datetime.now() - updated_dt).total_seconds() / 86400
                else:
                    days_old = 30.0
            except Exception:
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
                except Exception:
                    pass

        if forgotten:
            logger.info("Forgotten %d facts (model=%s)", forgotten,
                        self._forgetting_model)
        return forgotten

    # ── 内部方法 ──────────────────────────────────────────

    def _do_fact_extraction(self, session_id: str) -> int:
        """执行事实提取（V1 FactExtractor + V2 ConflictDetector）"""
        count = 0
        try:
            recent = self.sm.get_recent_chats(10)
            user_msgs = [c["content"] for c in recent if c["role"] == "user"]
            if not user_msgs:
                return 0

            facts = self.fe.extract_facts(user_msgs)
            deduped = FactExtractor.deduplicate(facts)

            for fact in deduped:
                # 冲突检测
                conflict = self.conflict_detector.check_conflict(
                    fact["fact"], fact.get("category", "general")
                )
                if conflict:
                    logger.debug(
                        "Fact conflict detected: new=%s vs existing=%s",
                        fact["fact"][:30], conflict["existing_fact"][:30],
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

        except Exception as e:
            logger.warning("Fact extraction failed: %s", e)

        return count

    def _archive_working_memory(self) -> None:
        """归档工作记忆到情景记忆"""
        messages = self.working.get_for_archive()
        if not messages:
            return

        summary = ""
        if self._llm and callable(self._llm):
            try:
                summary = self.ds._summarize_with_llm([
                    {"role": m.get("role", "user"),
                     "content": m.get("content", "")}
                    for m in messages
                ]) or ""
            except Exception as e:
                logger.debug("Summary generation failed: %s", e)

        avg_importance = (
            sum(m.get("importance", 0.5) for m in messages) / len(messages)
        )

        self.episodic.store_episode(
            messages,
            summary=summary,
            importance=avg_importance,
            session_id=self.working.session_id,
        )
        self.working.clear()
        logger.info("Working memory archived: %d messages", len(messages))

    def _cleanup_low_confidence_facts(self) -> None:
        """清理低置信度事实"""
        try:
            facts = self.sm.get_facts(min_confidence=0.0)
            for f in facts:
                if f.get("confidence", 0) < self._config.fact_min_confidence:
                    self.sm.delete_fact(f["id"])
            logger.debug("Cleaned up low confidence facts")
        except Exception as e:
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
