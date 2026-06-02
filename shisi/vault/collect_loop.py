"""知识宝库收集循环 — 事件驱动 + 定时批量收集。

工作流程：
1. 角色知识请求入队
2. VaultCollector 定期从队列中消费
3. 对每个角色：从 CharaCardV2 提取 PersonaFeatures → 转 KnowledgeChunk → 索引到 CharacterKnowledgeService
4. 注入 LLM 上下文（在女友管理器中使用）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.character.models import CharaCardV2
from shisi.knowledge.character_knowledge_service import (
    CharacterKnowledgeService,
    get_knowledge_service,
)
from shisi.knowledge.retriever import KnowledgeChunk

from ._persona_adapter import PersonaAdapter

logger = logging.getLogger("shisi.vault.collect_loop")


class VaultCollector:
    """知识宝库收集器 — 负责将 CharaCardV2 知识提取并索引。

    支持：
    - collect_card(): 单角色卡知识提取 + 索引 + 持久化
    - collect_batch(): 批量收集（用于启动时重建全部索引）
    """

    def __init__(
        self,
        knowledge_service: CharacterKnowledgeService | None = None,
    ):
        self._service = knowledge_service or get_knowledge_service()

    # ── 核心收集方法 ──

    def collect_card(self, character_id: str, card: CharaCardV2) -> int:
        """收集单个角色的知识并索引。返回索引块数。"""
        # 1. 提取 PersonaFeatures
        features = PersonaAdapter.extract(card)

        # 2. 特征转 KnowledgeChunk
        chunks = self._features_to_chunks(character_id, features, card)

        # 3. 索引到 service
        if chunks:
            retriever = self._service._retrievers.get(character_id)
            if retriever is None:
                # 还没有检索器，先加载或建新的
                if not self._service.load_index(character_id):
                    from shisi.knowledge.retriever import BM25Retriever
                    retriever = BM25Retriever()
                    retriever.index(chunks)
                    self._service._retrievers[character_id] = retriever
                else:
                    # 已有磁盘索引，追加
                    self._service._retrievers[character_id].add_chunks(chunks)
            else:
                retriever.add_chunks(chunks)

            if character_id in self._service._chunk_counts:
                self._service._chunk_counts[character_id] += len(chunks)
            else:
                self._service._chunk_counts[character_id] = len(chunks)

            # 保存到磁盘
            self._service.save_index(character_id)
            logger.info(
                "知识宝库收集完成: %s → %d 知识块（特征）",
                character_id, len(chunks),
            )
        else:
            logger.warning("知识宝库收集无内容: %s", character_id)

        return len(chunks)

    def collect_batch(
        self, cards: dict[str, CharaCardV2]
    ) -> dict[str, int]:
        """批量收集多个角色的知识。返回 {character_id: 块数}。"""
        results: dict[str, int] = {}
        for cid, card in cards.items():
            results[cid] = self.collect_card(cid, card)
        return results

    def collect_from_json(
        self, character_id: str, json_path: str | Path
    ) -> int:
        """从 JSON 文件加载角色卡并收集知识。"""
        with open(json_path, encoding="utf-8") as f:
            import json
            data = json.load(f)
        card = CharaCardV2Parser.parse(data)
        return self.collect_card(character_id, card)

    # ── 辅助 ──

    def _features_to_chunks(
        self, character_id: str, features: Any, card: CharaCardV2
    ) -> list[KnowledgeChunk]:
        """将 PersonaFeatures 转换为检索用的 KnowledgeChunk。"""
        chunks: list[KnowledgeChunk] = []

        # 1. core_anchors — 每锚点一块
        for i, anchor in enumerate(features.core_anchors):
            chunks.append(KnowledgeChunk(
                content=anchor,
                source="vault.core_anchor",
                source_id=f"{character_id}_anchor_{i}",
            ))

        # 2. speaking_style
        for i, style in enumerate(features.speaking_style):
            chunks.append(KnowledgeChunk(
                content=style,
                source="vault.speaking_style",
                source_id=f"{character_id}_style_{i}",
            ))

        # 3. background — 整段
        for i, bg in enumerate(features.background):
            if bg.strip():
                chunks.append(KnowledgeChunk(
                    content=bg,
                    source="vault.background",
                    source_id=f"{character_id}_bg_{i}",
                ))

        # 4. relationship
        for i, rel in enumerate(features.relationship):
            chunks.append(KnowledgeChunk(
                content=rel,
                source="vault.relationship",
                source_id=f"{character_id}_rel_{i}",
            ))

        # 5. behavior_rules
        for i, rule in enumerate(features.behavior_rules):
            chunks.append(KnowledgeChunk(
                content=rule,
                source="vault.behavior_rule",
                source_id=f"{character_id}_rule_{i}",
            ))

        return chunks


class CollectLoop:
    """知识收集循环 — 异步队列驱动。

    用法：
        loop = CollectLoop(vault_collector)
        loop.start()  # 后台启动消费者
        await loop.enqueue("char_xxx", card)  # 入队收集请求
    """

    def __init__(
        self,
        collector: VaultCollector | None = None,
        poll_interval: float = 1.0,
    ):
        self._collector = collector or VaultCollector()
        self._queue: asyncio.Queue[tuple[str, CharaCardV2]] = asyncio.Queue()
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        """启动后台消费者循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("知识收集循环已启动")

    async def stop(self) -> None:
        """停止后台消费者循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("知识收集循环已停止")

    async def enqueue(self, character_id: str, card: CharaCardV2) -> None:
        """入队一个角色知识收集请求。"""
        await self._queue.put((character_id, card))

    async def _run_loop(self) -> None:
        """消费者循环。"""
        while self._running:
            try:
                # 等待队列有任务，带超时
                try:
                    character_id, card = await asyncio.wait_for(
                        self._queue.get(), timeout=self._poll_interval
                    )
                except TimeoutError:
                    continue

                # 执行收集（同步方法，在线程池跑以免阻塞事件循环）
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, self._collector.collect_card, character_id, card
                )

                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("知识收集循环异常")
