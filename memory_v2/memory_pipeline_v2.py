from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from memory_v2.working_memory import WorkingMemory
from memory_v2.episodic_memory import EpisodicMemory
from memory_v2.semantic_memory import SemanticMemory
from memory_v2.importance_scorer import ImportanceScorer, ForgettingManager, ConflictDetector, CrossSessionReasoner

logger = logging.getLogger("memory_pipeline_v2")


class MemoryPipelineV2:
    def __init__(self, structured_memory, vector_memory, llm_gateway=None,
                 working_limit: int = 20, retrieval_timeout: float = 1.0):
        self._sm = structured_memory
        self._vm = vector_memory
        self._llm = llm_gateway
        self.working = WorkingMemory(structured_memory, limit=working_limit)
        self.episodic = EpisodicMemory(vector_memory, structured_memory)
        self.semantic = SemanticMemory(vector_memory, structured_memory)
        self.scorer = ImportanceScorer()
        self.forgetting = ForgettingManager()
        self.conflict_detector = ConflictDetector(self.semantic)
        self.cross_session = CrossSessionReasoner(structured_memory)
        self.retrieval_timeout = retrieval_timeout

    def retrieve_context(self, query: str, session_id: str = "",
                         top_k: int = 5) -> Dict[str, Any]:
        context = {"working": [], "episodic": [], "semantic": [], "conflicts": []}
        context["working"] = self.working.get_recent()

        start = time.perf_counter()
        try:
            context["episodic"] = self.episodic.search(query, top_k)
            context["semantic_vec"] = self.semantic.search(query, top_k).get("vector", [])
        except Exception as e:
            logger.warning("Memory retrieval degraded (vector fallback): %s", e)
        if time.perf_counter() - start > self.retrieval_timeout:
            logger.warning("Memory retrieval timeout, using working memory only")
            context["episodic"] = []
            context["semantic_vec"] = []

        return context

    def after_chat(self, user_msg: str, reply: str, emotion: str = "",
                   importance: float = 0.5, session_id: str = ""):
        self.working.add("user", user_msg, emotion, importance)
        self.working.add("assistant", reply, emotion, importance)
        self._vm.store_chat(user_msg, reply, {"emotion": emotion})

        if self.working.should_archive():
            self._archive_working_memory()

    def _archive_working_memory(self):
        messages = self.working.get_for_archive()
        if messages:
            summary = ""
            if self._llm:
                try:
                    from memory.diary_summarizer import DiarySummarizer
                    summarizer = DiarySummarizer(self._llm)
                    summary = summarizer._summarize_with_llm(
                        [m.get("content", "") for m in messages]
                    ) or ""
                except Exception as e:
                    logger.debug("Auto-summary failed: %s", e)
            self.episodic.store_episode(
                messages, summary=summary,
                importance=0.5, session_id=self.working.session_id,
            )
            self.working.clear()
            logger.info("Working memory archived to episodic memory")

    def daily_maintenance(self):
        logger.info("Running daily memory maintenance")
        try:
            self._apply_forgetting()
            self._check_pending_events()
        except Exception as e:
            logger.error("Daily maintenance error: %s", e)

    def _apply_forgetting(self):
        facts = self._sm.get_facts(limit=1000)
        for fact in facts:
            days = (time.time() - fact.get("updated_at", time.time())) / 86400
            importance = fact.get("confidence", 0.5)
            if self.forgetting.should_delete(importance, days):
                self._sm.delete_fact(fact["id"])
                logger.debug("Forgot low-importance fact: %s", fact.get("fact", "")[:30])

    def _check_pending_events(self):
        events = self.cross_session.get_pending_events()
        for event in events:
            logger.info("Pending event: %s", event.get("event_desc", ""))

    def health_check(self) -> dict:
        return {
            "working_session": self.working.session_id,
            "working_count": self.working.count(),
            "structured": self._sm.health_check(),
            "vector": self._vm.health_check(),
        }
