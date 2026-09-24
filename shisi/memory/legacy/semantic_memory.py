"""语义记忆 — 提取的事实和知识（去重 + 冲突检测 + 置信度 + 多用户隔离）。

2026-09-20：`memory_pipeline` 统一使用本模块；`_legacy_semantic_memory` 改为转发，
避免双实现漂移。add_fact 返回 **bool**（兼容既有测试与调用方）。
"""

from __future__ import annotations

import hashlib
import inspect
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
            # 2026-09-24：失败必须 error 级——warning 曾使「FTS 虚表残缺 →
            # 事实层写入全程失败」在生产潜伏 3 天仅 46 条 warning 无人察觉
            # （谎言家族：失败可见性分级）。
            logger.error("Failed to add fact: %s", e)
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
            raw_vec: list = []
            # 2026-09-22 块E：读侧与写侧同源判据 —— `vector_memory._search` 是
            # **协程函数**（`async def`），旧实现两个分支都把它当同步函数直接调：
            #   · `self._vm._search(...)` 返回 coroutine，`or []` 不触发（coroutine
            #     恒为真）→ `raw_vec` 是协程对象；后续 `for r in raw_vec` 抛
            #     TypeError（coroutine 不可迭代）→ 被下方 except 吞掉 → **向量
            #     召回恒空**，warning 里只有一句 "Vector fact search failed"。
            #   · `elif hasattr(self._vm, "search_sync")` 分支同样 `or []` 不生效，
            #     且 `search_sync` 虽非协程，其内部 `_run_async` 在**已运行的事件
            #     循环**里会退化（见 utils.async_utils 注释）。
            # 现统一走 `utils.async_utils.run_async`（同步桥唯一 owner），并把
            # Chroma `where` 下推保留（写侧 store_fact 已按 P1-12 修复，向量通道
            # 需要读写两侧同口径才真正接通）。
            from utils.async_utils import run_async

            _where = {"user_key": str(user_key)} if user_key is not None else None
            if hasattr(self._vm, "_search"):
                # `where` 是较新参数：老实现/测试替身可能没有。不支持时退回
                # 不带过滤的调用，再由下方 Python 侧按 meta.user_key 过滤兜底
                # （语义安全，只是失去"下推"性能收益）——不能因替身签名不同
                # 就整条向量通道报错。
                try:
                    _ret = self._vm._search("user_facts", query, top_k, where=_where)
                except TypeError:
                    _ret = self._vm._search("user_facts", query, top_k)
                # `_search` 在真实现里是 async（需过桥）；测试替身可能是同步
                # 返回列表。两者都要正确处理 —— 旧实现直接当同步用，遇 async
                # 拿到 coroutine，迭代时抛 TypeError 被吞 ⇒ 向量召回恒空。
                raw_vec = (run_async(_ret) or []) if inspect.isawaitable(_ret) else (_ret or [])
            elif hasattr(self._vm, "search_sync"):
                raw_vec = self._vm.search_sync(
                    query, top_k=top_k, filter_dict={"type": "fact"}
                ) or []
            raw_vec = raw_vec if isinstance(raw_vec, list) else []
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
