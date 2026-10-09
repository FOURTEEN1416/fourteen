"""Shisi 记忆服务适配层 — 兼容 MemoryPipeline 接口。

shisi/memory 提供收藏/转发增强能力（FavoriteManager/ForwardManager），
核心记忆存储/检索复用 shisi/memory/legacy/（迁移自原根 memory/）。
适配层统一暴露 OptimizedOrchestrator 所需的方法，使 main.py 可以通过
配置开关在 shisi 适配层与 _legacy 兼容行为之间切换。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# shisi 自有 pipeline（迁移自原根 memory.memory_pipeline）。
# 根目录 memory/ 已物理删除，所有引用统一指向 shisi 自有位置。
# StructuredMemory / VectorMemory 改为在 _build_default_memory_backends 内 lazy import，
# 避免顶级导入未使用触发 ruff F401。
from shisi.core.conversation_turn import HISTORY_RECENT_LIMIT
from shisi.memory.favorite_manager import FavoriteManager
from shisi.memory.forward_manager import ForwardManager
from shisi.memory.legacy.memory_pipeline import MemoryPipeline  # noqa: E402

logger = logging.getLogger("shisi.application.memory_service")


def _build_default_memory_backends(chroma_path, db_path, structured_memory, vector_memory):
    """Build defaults."""
    if vector_memory is None and chroma_path is not None:
        from shisi.memory.legacy.vector_memory import VectorMemory
        vector_memory = VectorMemory(chroma_path=str(chroma_path))
    if structured_memory is None and db_path is not None:
        from shisi.memory.legacy.structured_memory import StructuredMemory
        structured_memory = StructuredMemory(db_path=str(db_path))
    return structured_memory, vector_memory


class ShisiMemoryService:
    """兼容 MemoryPipeline 的十四记忆服务。

    对外暴露 MemoryPipeline 的核心接口，同时提供 shisi 的收藏/转发能力。
    默认行为与 MemoryPipeline 保持一致，仅在收藏/转发等增强功能上体现
    shisi 模块的差异。
    """

    def __init__(
        self,
        structured_memory=None,
        vector_memory=None,
        fact_extractor=None,
        diary_summarizer=None,
        emotion_engine=None,
        llm_gateway=None,
        db_path: str | Path | None = None,
        chroma_path: str | Path | None = None,
        working_limit: int = 20,
        retrieval_timeout: float = 1.0,
        forgetting_model: str = "exponential",
        lambda_low: float = 0.1,
        lambda_high: float = 0.01,
        conflict_similarity_threshold: float = 0.3,
        fact_extract_interval: int = 5,
        *,
        favorite_mgr=None,
        forward_mgr=None,
    ):
        structured_memory, vector_memory = _build_default_memory_backends(
            chroma_path=chroma_path,
            db_path=db_path,
            structured_memory=structured_memory,
            vector_memory=vector_memory,
        )
        self._pipeline = MemoryPipeline(
            vector_memory=vector_memory,
            structured_memory=structured_memory,
            fact_extractor=fact_extractor,
            diary_summarizer=diary_summarizer,
            emotion_engine=emotion_engine,
            llm_gateway=llm_gateway,
            working_limit=working_limit,
            retrieval_timeout=retrieval_timeout,
            forgetting_model=forgetting_model,
            lambda_low=lambda_low,
            lambda_high=lambda_high,
            conflict_similarity_threshold=conflict_similarity_threshold,
            fact_extract_interval=fact_extract_interval,
        )

        # shisi 增强：收藏/转发（支持外部注入，避免重复实例化）
        self._favorite_manager = favorite_mgr if favorite_mgr is not None else FavoriteManager(db_path=db_path)
        self._forward_manager = forward_mgr if forward_mgr is not None else ForwardManager()

        logger.info(
            "ShisiMemoryService initialized (forgetting=%s, working_limit=%d)",
            forgetting_model, working_limit,
        )

    # ── 属性代理（保持与 MemoryPipeline 兼容）────────────────

    @property
    def session_id(self) -> str:
        return self._pipeline.session_id

    @property
    def vm(self):
        return self._pipeline.vm

    @property
    def sm(self):
        return self._pipeline.sm

    @property
    def vector_memory(self):
        return self._pipeline.vm

    @property
    def structured_memory(self):
        return self._pipeline.sm

    @property
    def working(self):
        return self._pipeline.working

    @property
    def episodic(self):
        return self._pipeline.episodic

    @property
    def semantic(self):
        return self._pipeline.semantic

    @property
    def fe(self):
        return self._pipeline.fe

    @property
    def ds(self):
        return self._pipeline.ds

    @property
    def reflection(self):
        return self._pipeline.reflection

    @property
    def summarizer(self):
        return self._pipeline.summarizer

    @property
    def favorite_manager(self) -> FavoriteManager:
        return self._favorite_manager

    @property
    def forward_manager(self) -> ForwardManager:
        return self._forward_manager

    # ── 核心兼容接口 ──────────────────────────────────────

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
        # P1-16（2026-09-21 审查修复）：pipeline 早已支持 history_already_written
        # 与 write_chat_history_sync，但服务壳没转发——orchestrator 的 hasattr/
        # 签名探测恒 False，B-a 同步轻写整层失效（慢工具轮次表现为"吞消息"竞态）。
        # 2026-09-21 重扫：归属参数（character_id/turn_id/channel）同样必须转发，
        # 否则管道收到的仍是"无身份"的两行。
        return self._pipeline.after_chat(
            user_msg=user_msg,
            reply=reply,
            emotion_tag=emotion_tag,
            session_id=session_id,
            history_already_written=history_already_written,
            character_id=character_id,
            turn_id=turn_id,
            channel=channel,
        )

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
        return self._pipeline.write_chat_history_sync(
            user_msg=user_msg,
            reply=reply,
            emotion_tag=emotion_tag,
            session_id=session_id,
            character_id=character_id,
            turn_id=turn_id,
            importance=importance,
            channel=channel,
        )

    def record_outbound_message(
        self,
        message: str,
        session_id: str = "",
        emotion_tag: str = "",
        character_id: str = "",
        channel: str = "",
        importance: float = 0.0,
        turn_id: str = "",
    ) -> bool:
        """角色主动发出的消息（追问/主动消息/提醒）回写历史——唯一 owner。

        🔴 2026-09-22：补 `character_id` 等归属参数（此前签名根本没有该参数，
        出站 assistant 行永久无归属 → 切角色后继承他人台词）。
        """
        return self._pipeline.record_outbound_message(
            message=message,
            session_id=session_id,
            emotion_tag=emotion_tag,
            character_id=character_id,
            channel=channel,
            importance=importance,
            turn_id=turn_id,
        )

    def retrieve_context(
        self,
        query: str,
        session_id: str = "",
        top_k: int = 5,
        character_id: str = "",
    ) -> dict[str, Any]:
        context = self._pipeline.retrieve_context(
            query=query, session_id=session_id, top_k=top_k, character_id=character_id,
        )
        # 缺陷 F：目标侧派生记录进入唯一检索合并点——
        # context 已挂载 forwarded_notes（含 forward_id/from 可追溯字段）；
        # ⚠️ prompt 渲染层尚未消费（PRIV-2：写入面已在 API 层按目标卡归属收口），
        # 接线消费前不得按「已进 prompt」的假设直接渲染该字段。
        try:
            notes = self._forward_notes_for(character_id)
            if notes and isinstance(context, dict):
                context["forwarded_notes"] = notes
        except Exception as e:  # noqa: BLE001
            logger.debug("forward notes inject failed: %s", e)
        return context

    def _forward_notes_for(self, character_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """取目标角色最近转发便签（含 forward_id/from 可追溯字段）。"""
        cid = str(character_id or "").strip()
        if not cid:
            return []
        rows = self._forward_manager.get_forwards(cid)[:limit]
        notes = []
        for r in rows:
            content = str(r.get("content") or "").strip()
            if not content:
                continue
            notes.append({
                "forward_id": r.get("id"),
                "from": r.get("from"),
                "memory_id": r.get("memory_id"),
                "note": content[:300],
            })
        return notes

    def get_recent_context(self, n: int = 3, session_id: str = "", character_id: str = "") -> str:
        return self._pipeline.get_recent_context(n=n, session_id=session_id, character_id=character_id)

    def get_chat_context(
        self,
        session_id: str = "",
        keep_recent: int = HISTORY_RECENT_LIMIT,
        character_id: str = "",
        summarize: bool = True,
    ) -> tuple[list, str]:
        return self._pipeline.get_chat_context(
            session_id=session_id,
            keep_recent=keep_recent,
            character_id=character_id,
            summarize=summarize,
        )

    def get_cross_session_tail(self, session_id: str = "", limit: int = 8, character_id: str = "") -> list[str]:
        return self._pipeline.get_cross_session_tail(session_id=session_id, limit=limit, character_id=character_id)

    def get_memory_context(
        self, n_chats: int = 10, session_id: str = "",
    ) -> dict[str, Any]:
        return self._pipeline.get_memory_context(n_chats=n_chats, session_id=session_id)

    def get_formatted_context(
        self, n_chats: int = 6, session_id: str = "",
    ) -> str:
        return self._pipeline.get_formatted_context(n_chats=n_chats, session_id=session_id)

    def store_episode(
        self,
        messages: list[dict],
        summary: str = "",
        importance: float = 0.5,
        session_id: str = "",
    ) -> str:
        return self._pipeline.episodic.store_episode(
            messages=messages,
            summary=summary,
            importance=importance,
            session_id=session_id,
        )

    def add_fact(
        self,
        fact: str,
        category: str = "general",
        confidence: float = 0.5,
        source: str = "",
        user_key: str = "",
    ) -> bool:
        # 隔离：调用方应传会话归属 user_key；缺省 '' 表示无主/内部维护路径
        return self._pipeline.semantic.add_fact(
            fact=fact,
            category=category,
            confidence=confidence,
            source=source,
            user_key=user_key,
        )

    # ── W4 统一事实写入口（2026-09-27）────────────────────
    # 归属、来源水位、向量派生、EventLedger 记账一次完成；返回真实回执。
    # 工具/API/后台维护的事实增删一律经本入口，不再各自直写 StructuredMemory
    # （缺陷 B：旧 remember_facts 直写 sm.add_fact，既不派生向量也不带
    # source_last_id → 迟到的旧来源写入可复活已删事实）。

    def record_fact(
        self,
        fact: str,
        *,
        session_key: str,
        category: str = "general",
        confidence: float = 0.85,
        source: str = "agent",
        importance: float = 0.5,
        topics: list[str] | None = None,
        turn_id: str = "",
    ) -> dict[str, Any]:
        """写入/强化用户事实，返回 `{fact_id, action, source_last_id, turn_id, user_key, vector_stored, ok}`。

        来源水位取本轮已落库历史的最后 id（无 turn_id 时退本会话最新 id）——
        与 `delete_fact` 写入的 `fact_deletion_watermarks` 同钟，故「迟到旧
        来源被跳过、用户新一轮重述可重新记住」由同一 id 序自动成立。
        """
        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = self._pipeline.sm
        user_key = StructuredMemory.user_key_from_session(session_key)
        source_last_id = self._source_last_id(sm, session_key or user_key, turn_id)
        receipt = self._pipeline.semantic.write_fact(
            fact=fact,
            category=category,
            confidence=confidence,
            source=source,
            importance=importance,
            user_key=user_key,
            topics=topics,
            source_last_id=source_last_id,
        )
        receipt = dict(receipt)
        action = str(receipt.get("action") or "")
        receipt.update({
            "user_key": user_key,
            "turn_id": str(turn_id or ""),
            "source_last_id": int(receipt.get("source_last_id") or 0),
            "fact_id": int(receipt.get("fact_id") or -1),
            "ok": action in ("inserted", "reinforced", "skipped_deleted_source"),
        })
        if action in ("inserted", "reinforced", "skipped_deleted_source"):
            self._append_memory_event(
                session_key=user_key,
                facts=[{
                    "fact": fact,
                    "category": category,
                    "fact_id": receipt["fact_id"],
                    "action": action,
                    "source_last_id": receipt["source_last_id"],
                    "turn_id": receipt["turn_id"],
                }],
                action="reinforce" if action == "reinforced" else "write",
                turn_id=str(turn_id or ""),
            )
        return receipt

    def forget_fact(
        self,
        fact_id: int,
        *,
        session_key: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """删除事实（进回收站 + 写删除水位 + 失效派生召回），返回真实回执。"""
        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = self._pipeline.sm
        user_key = StructuredMemory.user_key_from_session(session_key)
        removed = False
        try:
            removed = bool(sm.delete_fact(int(fact_id), recycle=True, user_key=user_key))
        except Exception as e:  # noqa: BLE001
            logger.warning("forget_fact 删除失败 id=%s: %s", fact_id, e)
        receipt: dict[str, Any] = {
            "fact_id": int(fact_id),
            "user_key": user_key,
            "turn_id": "",
            "action": "deleted" if removed else "not_found",
            "ok": removed,
        }
        if removed:
            self._append_memory_event(
                session_key=user_key,
                facts=[{
                    "fact_id": receipt["fact_id"], "action": "deleted", "reason": reason,
                }],
                action="forget",
                turn_id=str(receipt.get("turn_id") or ""),
            )
        return receipt

    def forget_facts_by_text(self, texts: list[str], *, session_key: str) -> list[int]:
        """按原文删除（仅精确同文，绝不做子串匹配——否则一句话能删光他人事实）。"""
        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = self._pipeline.sm
        user_key = StructuredMemory.user_key_from_session(session_key)
        wanted = {str(t or "").strip() for t in texts or [] if str(t or "").strip()}
        if not wanted:
            return []
        removed: list[int] = []
        try:
            rows = sm.get_facts(user_key=user_key, min_confidence=0.0, limit=-1)
        except Exception as e:  # noqa: BLE001
            logger.warning("forget_facts_by_text 读取失败: %s", e)
            return []
        for row in rows:
            if str(row.get("fact") or "") in wanted:
                fid = int(row.get("id") or 0)
                if fid and self.forget_fact(fid, session_key=session_key)["ok"]:
                    removed.append(fid)
        return removed

    @staticmethod
    def _source_last_id(sm: Any, session_key: str, turn_id: str) -> int | None:
        """本轮来源水位：优先 turn_id 精确匹配，退化为会话最新历史 id。"""
        sid = 0
        try:
            fn = getattr(sm, "chat_turn_last_id", None)
            if callable(fn) and turn_id:
                sid = int(fn(session_key, turn_id) or 0)
            if not sid:
                fn2 = getattr(sm, "chat_last_id", None)
                if callable(fn2):
                    sid = int(fn2(session_key) or 0)
        except Exception as e:  # noqa: BLE001
            logger.debug("来源水位解析失败，按无水位写入: %s", e)
            return None
        return sid or None

    @staticmethod
    def _append_memory_event(
        *,
        session_key: str,
        facts: list[dict],
        action: str,
        turn_id: str = "",
        reply_id: str = "",
        character_id: str = "",
    ) -> None:
        """EventLedger 是记忆写入的审计真源；记账失败绝不反噬已落库的写入。"""
        if not session_key or not facts:
            return
        try:
            from shisi.agent_plane import runtime as apruntime

            apruntime.append_memory_write_event(
                session_key=session_key,
                facts=facts,
                action=action,
                turn_id=turn_id,
                reply_id=reply_id,
                character_id=character_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("memory ledger event failed: %s", e)

    def reset_session(self) -> None:
        self._pipeline.reset_session()

    def daily_maintenance(self) -> str | None:
        return self._pipeline.daily_maintenance()

    def health_check(self) -> dict:
        return {
            **self._pipeline.health_check(),
            "favorite_manager": {"available": True},
            "forward_manager": {"available": True},
        }

    def close(self) -> None:
        self._pipeline.close()

    # ── shisi 增强接口 ────────────────────────────────────

    def favorite(self, character_id: str, memory_id: str) -> bool:
        return self._favorite_manager.favorite(character_id, memory_id)

    def unfavorite(self, character_id: str, memory_id: str) -> bool:
        return self._favorite_manager.unfavorite(character_id, memory_id)

    def list_favorites(self, character_id: str) -> list[dict[str, Any]]:
        return self._favorite_manager.list_favorites(character_id)

    def is_favorite(self, character_id: str, memory_id: str) -> bool:
        return self._favorite_manager.is_favorite(character_id, memory_id)

    def forward(
        self,
        from_character: str,
        to_character: str,
        memory_id: str,
        memory_content: str = "",
    ) -> int:
        """转发；返回 forward_id（0=失败）。完整回执见 :meth:`forward_receipt`。"""
        return self._forward_manager.forward(
            from_character=from_character,
            to_character=to_character,
            memory_id=memory_id,
            memory_content=memory_content,
        )

    def forward_receipt(
        self,
        from_character: str,
        to_character: str,
        memory_id: str,
        memory_content: str = "",
    ) -> dict[str, Any]:
        """目标侧派生记录回执（缺陷 F）：含 forward_id，可供 get_forwards 消费。"""
        return self._forward_manager.forward_receipt(
            from_character=from_character,
            to_character=to_character,
            memory_id=memory_id,
            memory_content=memory_content,
        )

    def get_forwards(self, character_id: str) -> list[dict[str, Any]]:
        return self._forward_manager.get_forwards(character_id)
