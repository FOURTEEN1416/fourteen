from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("reflection_engine")

REFLECTION_PROMPT = """你是一位善于观察的陪伴者。请根据以下关于用户的事实和最近对话片段，生成1-3条更高层次的"反思"——即对用户性格、偏好、关系或潜在需求的洞察。

要求：
- 每条反思用一句话表达
- 基于已有事实，不要编造
- 语气自然，像默默记在心里
- 如果信息不足，直接返回空数组

已知事实：
{facts}

最近片段：
{episodes}

请输出 JSON 数组：
[{{"insight": "用户其实更在意被理解，而不是被建议"}}]"""


class ReflectionEngine:
    """记忆反思引擎。

    定期将零散事实/片段沉淀为更高层洞察，并在检索时注入上下文。
    """

    def __init__(
        self,
        llm_func: Callable[..., str] | None = None,
        vector_memory: Any | None = None,
        structured_memory: Any | None = None,
        reflection_interval: int = 10,
    ):
        self._llm = llm_func
        self._vm = vector_memory
        self._sm = structured_memory
        self._reflection_interval = reflection_interval
        self._fact_count_since_reflection = 0
        self._lock = threading.Lock()
        self._insights_cache: list[str] = []
        self._cache_ts = 0.0

    def maybe_reflect(
        self,
        facts: list[dict[str, Any]],
        episodes: list[dict[str, Any]] | None = None,
        session_id: str = "",
    ) -> list[str]:
        """根据新增事实数量触发反思。"""
        with self._lock:
            self._fact_count_since_reflection += len(facts)
            if self._fact_count_since_reflection < self._reflection_interval:
                return []
            self._fact_count_since_reflection = 0

        insights = self.reflect(facts, episodes)
        if insights:
            self.store_insights(insights, session_id=session_id)
        return insights

    def reflect(
        self,
        facts: list[dict[str, Any]],
        episodes: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        """生成反思洞察。优先使用 LLM，失败时回退到规则模板。"""
        if not facts:
            return []

        if self._llm:
            try:
                return self._reflect_with_llm(facts, episodes or [])
            except Exception as e:  # noqa: BLE001
                logger.debug("LLM reflection failed, fallback to rules: %s", e)

        return self._reflect_with_rules(facts)

    def _reflect_with_llm(
        self,
        facts: list[dict[str, Any]],
        episodes: list[dict[str, Any]],
    ) -> list[str]:
        fact_lines = []
        for f in facts[-20:]:
            text = f.get("fact") or f.get("content", "")
            cat = f.get("category", "general")
            if text:
                fact_lines.append(f"- [{cat}] {text}")
        if not fact_lines:
            return []

        episode_lines = []
        for ep in episodes[-5:]:
            content = ep.get("summary") or ep.get("content", "")
            if content:
                episode_lines.append(f"- {content}")

        prompt = REFLECTION_PROMPT.format(
            facts="\n".join(fact_lines),
            episodes="\n".join(episode_lines) or "无",
        )
        if self._llm is None:
            return []
        result = self._llm(prompt)
        insights = self._parse_json(result)
        return [i.get("insight", "").strip() for i in insights if i.get("insight")]

    def _reflect_with_rules(self, facts: list[dict[str, Any]]) -> list[str]:
        """规则化反思：根据事实类别组合生成简单洞察。"""
        insights = []
        categories: dict[str, list[str]] = {}
        for f in facts:
            cat = f.get("category", "general")
            text = f.get("fact") or f.get("content", "")
            categories.setdefault(cat, []).append(text)

        prefs = categories.get("preference", [])
        if prefs:
            insights.append(f"你似乎对{prefs[-1]}挺上心。")

        habits = categories.get("habit", [])
        if habits:
            insights.append(f"你经常{habits[-1]}，这已经成了你的习惯。")

        emotions = [f for f in facts if f.get("category") in ("emotion", "mood")]
        if emotions:
            insights.append("你最近情绪起伏不小，我会多留意。")

        events = categories.get("event", [])
        if events:
            insights.append(f"你提过{events[-1]}，这件事对你来说应该比较重要。")

        return insights[:3]

    def store_insights(
        self,
        insights: list[str],
        session_id: str = "",
    ) -> None:
        if not insights:
            return
        timestamp = time.time()
        for idx, text in enumerate(insights):
            if not text:
                continue
            try:
                insight_id = f"ref_{int(timestamp)}_{idx}_{hashlib.md5(text.encode()).hexdigest()[:6]}"
                metadata = {
                    "type": "reflection",
                    "insight_id": insight_id,
                    "session_id": session_id,
                    "timestamp": timestamp,
                }
                if self._vm:
                    self._vm.store_text_sync(text, metadata)
                if self._sm and hasattr(self._sm, "add_reflection"):
                    self._sm.add_reflection(text, session_id=session_id)
                logger.info("Reflection stored: %s", text[:40])
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to store reflection: %s", e)
        self._insights_cache = insights
        self._cache_ts = timestamp

    def get_insights(
        self,
        query: str = "",
        top_k: int = 3,
        use_cache: bool = True,
        session_id: str | None = None,
    ) -> list[str]:
        """检索反思洞察。session_id 非 None 时按会话隔离（缓存键含 session）。"""
        cache_key = f"{session_id or ''}|{query[:32]}"
        if (
            use_cache
            and self._insights_cache
            and time.time() - self._cache_ts < 300
            and getattr(self, "_insights_cache_key", "") == cache_key
        ):
            return self._insights_cache[:top_k]

        results: list[str] = []
        if self._vm and query:
            try:
                vector_results = self._vm.search_sync(
                    query, top_k=top_k * 3 if session_id else top_k,
                    filter_dict={"type": "reflection"},
                )
                for r in vector_results:
                    content = (r.get("content") or "").strip()
                    if not content:
                        continue
                    from utils.prompt_sanitize import looks_like_dialogue

                    if looks_like_dialogue(content):
                        continue
                    meta = r.get("metadata") or {}
                    r_sid = str(meta.get("session_id") or r.get("session_id") or "")
                    if session_id is not None:
                        if r_sid and r_sid == str(session_id):
                            results.append(content)
                        continue
                    results.append(content)
            except Exception as e:  # noqa: BLE001
                logger.debug("Vector reflection search failed: %s", e)

        if not results and self._sm and hasattr(self._sm, "get_reflections"):
            try:
                from utils.prompt_sanitize import looks_like_dialogue

                rows = self._sm.get_reflections(
                    limit=top_k, session_id=session_id
                ) if session_id is not None else self._sm.get_reflections(limit=top_k)
                results = [
                    r.get("content", "").strip()
                    for r in rows
                    if r.get("content") and not looks_like_dialogue(str(r.get("content")))
                ]
            except TypeError:
                try:
                    from utils.prompt_sanitize import looks_like_dialogue

                    rows = self._sm.get_reflections(limit=top_k)
                    if session_id is not None:
                        rows = [
                            r for r in rows
                            if str(r.get("session_id") or "") == str(session_id)
                        ]
                    results = [
                        r.get("content", "").strip()
                        for r in rows
                        if r.get("content") and not looks_like_dialogue(str(r.get("content")))
                    ]
                except Exception as e:  # noqa: BLE001
                    logger.debug("Structured reflection search failed: %s", e)
            except Exception as e:  # noqa: BLE001
                logger.debug("Structured reflection search failed: %s", e)

        self._insights_cache = results[:top_k]
        self._insights_cache_key = cache_key
        self._cache_ts = time.time()
        return results[:top_k]

    @staticmethod
    def _parse_json(text: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "insights" in data:
                return data["insights"]
        except json.JSONDecodeError:
            pass
        match = re.search(r"\[.*?\]", text, re.DOTALL)  # noqa: F405
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return []

    def health_check(self) -> dict[str, Any]:
        return {
            "llm_available": self._llm is not None,
            "vector_available": self._vm is not None,
            "structured_available": self._sm is not None,
            "reflection_interval": self._reflection_interval,
        }
