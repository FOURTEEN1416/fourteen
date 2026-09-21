"""语义记忆 — 提取的事实和知识（去重 + 冲突检测 + 置信度 + 多用户隔离）。

2026-09-20：`memory_pipeline` 统一使用本模块；`_legacy_semantic_memory` 改为转发，
避免双实现漂移。add_fact 返回 **bool**（兼容既有测试与调用方）。
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger("semantic_memory")


class SemanticMemory:
    """语义记忆 — 事实与知识（去重 + 冲突检测 + 置信度 + user_key 隔离）"""

    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory
        self._fact_cache: set[str] = set()

    @staticmethod
    def _hash(fact: str, user_key: str = "") -> str:
        return hashlib.md5(f"{user_key}:{fact}".encode()).hexdigest()

    @staticmethod
    def _accepts_user_key(fn) -> bool:
        import inspect
        try:
            return "user_key" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "",
                 importance: float = 0.5, user_key: str = "",
                 topics: str | list[str] | None = None,
                 **kwargs) -> bool:
        fact_hash = self._hash(fact, user_key)
        try:
            # 包 Q · B-b：near-dup 交由 StructuredMemory.add_fact 做 UPDATE 强化，
            # 这里不再「查到相似就 return False」（旧行为导致重复事实永不 reinforce）。
            if self._accepts_user_key(self._sm.add_fact):
                try:
                    self._sm.add_fact(
                        fact, category, confidence, source,
                        user_key=user_key, topics=topics,
                    )
                except TypeError:
                    # 旧签名无 topics
                    self._sm.add_fact(fact, category, confidence, source, user_key=user_key)
            else:
                self._sm.add_fact(fact, category, confidence, source)
            self._fact_cache.add(fact_hash)
            try:
                # P1-12（2026-09-21 审查修复）：旧实现把 **async** 的
                # vector_memory.store_fact 当同步函数调——返回协程被直接丢弃、
                # 不抛 TypeError，兜底分支永不命中 → user_facts 向量通道整体
                # 空转，向量召回恒空只剩 SQLite。现显式识别协程并经公共桥执行，
                # 且写入必须带 user_key（读侧按 meta.user_key 隔离）。
                store = getattr(self._vm, "store_fact", None)
                stored = False
                if callable(store):
                    import inspect

                    if inspect.iscoroutinefunction(store):
                        from utils.async_utils import run_async
                        if self._accepts_user_key(store):
                            run_async(store(fact, category, confidence,
                                            user_key=user_key))
                        else:
                            run_async(store(fact, category, confidence))
                        stored = True
                    else:
                        try:
                            store(fact, category, confidence, user_key=user_key)
                        except TypeError:
                            store(fact, category, confidence)
                        stored = True
                if not stored and callable(getattr(self._vm, "store_text_sync", None)):
                    self._vm.store_text_sync(fact, {
                        "type": "fact", "category": category,
                        "confidence": confidence, "user_key": user_key or "",
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to store fact vector: %s", e)
            try:
                coll = getattr(self._vm, "_collections", None)
                coll = coll() if callable(coll) else coll
                c = coll.get("semantic_knowledge") if isinstance(coll, dict) else None
                if c is not None and hasattr(c, "add"):
                    doc_id = f"sk_{hashlib.md5(fact.encode()).hexdigest()[:12]}"
                    c.add(
                        documents=[fact],
                        metadatas=[{
                            "category": category,
                            "confidence": confidence,
                            "importance": importance,
                            "user_key": user_key or "",
                        }],
                        ids=[doc_id],
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("semantic_knowledge store failed: %s", e)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to add fact: %s", e)
            return False

    @staticmethod
    def _meta_user_key(row: dict) -> str:
        meta = row.get("metadata") or {}
        return str(meta.get("user_key") or row.get("user_key") or "")

    def search(self, query: str, top_k: int = 5,
               user_key: str | None = None) -> dict[str, list]:
        """语义检索。user_key 非 None 时**强制隔离**，禁止回退全库。

        2026-09-21 串台修复：向量结果按 meta.user_key 精确匹配；
        空 user_key 的历史向量/事实不注入任何具体会话。
        """
        results: dict[str, list] = {"vector": [], "structured": [], "exact": []}
        try:
            if hasattr(self._vm, "search_sync"):
                raw_vec = self._vm.search_sync(
                    query, top_k=top_k, filter_dict={"type": "fact"}
                ) or []
            elif hasattr(self._vm, "_search"):
                vr = self._vm._search("semantic_knowledge", query, top_k)
                raw_vec = vr if isinstance(vr, list) else []
            else:
                raw_vec = []
            if user_key is not None:
                raw_vec = [
                    r for r in raw_vec
                    if self._meta_user_key(r) == str(user_key)
                ]
            results["vector"] = raw_vec[:top_k]
        except Exception as e:  # noqa: BLE001
            logger.warning("Vector fact search failed: %s", e)
        try:
            if user_key is not None:
                if self._accepts_user_key(self._sm.search_facts):
                    structured = self._sm.search_facts(query, user_key=user_key) or []
                else:
                    structured = []
            else:
                structured = self._sm.search_facts(query) or []
            results["structured"] = structured
            results["exact"] = structured
        except Exception as e:  # noqa: BLE001
            logger.warning("Fact search failed: %s", e)
        return results

    def extract_facts_from_message(self, message: str) -> list[dict]:
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

    def get_facts(self, category: str | None = None,
                  min_confidence: float = 0.0, limit: int = 50,
                  user_key: str | None = None, **kwargs) -> list[dict]:
        try:
            if user_key is not None and self._accepts_user_key(self._sm.get_facts):
                raw = self._sm.get_facts(
                    category, min_confidence, limit, user_key=user_key
                ) or []
            elif category is not None:
                raw = self._sm.get_facts(category, min_confidence=min_confidence, limit=limit) or []
            else:
                raw = self._sm.get_facts(category, limit=limit) or []
            out = []
            for r in raw:
                out.append({
                    "id": r.get("id", 0),
                    "fact": r.get("fact", r.get("content", "")),
                    "category": r.get("category", category or "general"),
                    "confidence": r.get("confidence", 0.5),
                    "source": r.get("source", ""),
                    "user_key": r.get("user_key", ""),
                    "access_count": r.get("access_count", 0),
                    "created_at": str(r.get("created_at", "")),
                })
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("get_facts failed: %s", e)
            return []

    def update_confidence(self, fact_id: int, confidence: float):
        self._sm.update_fact_confidence(fact_id, confidence)

    def delete_fact(self, fact_id: int, user_key: str = "", **kwargs):
        if self._accepts_user_key(self._sm.delete_fact):
            self._sm.delete_fact(fact_id, user_key=user_key)
        else:
            self._sm.delete_fact(fact_id)
