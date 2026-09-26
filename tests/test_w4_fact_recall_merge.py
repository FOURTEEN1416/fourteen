"""W4 · 事实向量召回合并回归（缺陷 A：vector 结果被主管线丢弃）。

钉住：
1. `semantic.search` 已按 user_key 过滤并做过主库存在性复核的 **vector** 结果，
   必须在 `retrieve_context` 的唯一合并点进入 `facts`；
2. 「仅语义近邻能找回、关键词查不到的旧事实」不得由「最近事实回退」冒充通过
   —— 对照组把该事实钉在回退窗口之外，只有向量通道接上才会出现；
3. 合并按来源 id 与规范文本去重；
4. 归属边界：他人 user_key 的向量命中不得注入；
5. 主库存在性：向量命中但主库已无该 active 事实 → 不得注入（删除不复现）；
6. 出处随事实下传（recall=keyword/vector/both + fact_id），供预算与回放记账。

隔离：真实 StructuredMemory 落在临时库；向量后端用替身；不触碰生产 data/。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from shisi.memory.legacy.memory_pipeline import MemoryPipeline
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION_A = "1:peer-a@im.wechat"
SESSION_B = "2:peer-b@im.wechat"

# 只有语义近邻能召回的旧事实：查询「早上喝什么」既非 FTS 命中也非 LIKE 子串
SEMANTIC_ONLY_FACT = "默默说过他喜欢喝手冲咖啡"
QUERY = "早上喝点什么好"


class FakeVectorMemory:
    """向量后端替身：`_search` 与真实现同为协程，返回可控命中列表。"""

    def __init__(self, hits: list[dict] | None = None):
        self.hits = hits or []
        self.calls: list[dict] = []
        self._collections: dict = {}

    def hits_for(self, hits: list[dict]) -> None:
        self.hits = hits

    async def _search(
        self, collection: str, query: str, top_k: int, where: dict | None = None
    ) -> list[dict]:
        self.calls.append({"collection": collection, "query": query, "where": where})
        return [dict(h) for h in self.hits[:top_k]]

    def vector_item(self, content: str, user_key: str) -> dict:
        return {
            "content": content,
            "metadata": {"user_key": user_key, "category": "preference", "confidence": 0.6},
            "distance": 0.21,
        }

    def store_chat_sync(self, *a, **k) -> None:
        return None

    def search_sync(self, query, top_k=5, filter_dict=None) -> list[dict]:
        return []

    def health_check(self) -> dict:
        return {"available": True}


def _make(tmp: Path):
    sm = StructuredMemory(str(tmp / "recall.sqlite"))
    vm = FakeVectorMemory()
    mp = MemoryPipeline(vector_memory=vm, structured_memory=sm, llm_gateway=None)
    return sm, vm, mp


def _push_out_of_fallback_window(sm: StructuredMemory, fact_ids: list[int]) -> None:
    """把干扰事实的 updated_at 钉到未来，使「最近 N 条」回退窗口必然不含目标事实。"""
    with sm._conn(write=True) as conn:
        for fid in fact_ids:
            conn.execute(
                "UPDATE user_facts SET updated_at = '2099-01-01 00:00:00' WHERE id = ?",
                (fid,),
            )
        conn.commit()


def _seed(tmp: Path):
    """目标事实 + 5 条更新的干扰事实（同会话），使回退窗口只含干扰事实。"""
    sm, vm, mp = _make(tmp)
    target_id = sm.add_fact(SEMANTIC_ONLY_FACT, category="preference", user_key=SESSION_A)
    distractors = [
        sm.add_fact(f"干扰事实{i}", category="general", user_key=SESSION_A)
        for i in range(5)
    ]
    _push_out_of_fallback_window(sm, distractors)
    return sm, vm, mp, int(target_id)


def test_semantic_only_fact_reaches_prompt_facts():
    """向量命中的旧事实必须进入 facts（修复前：vector 被丢弃 → 断言失败）。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, _ = _seed(tmp)
    try:
        # 关键词/FTS 侧确实查不到，排除「本来就能通过」的假绿
        assert sm.search_facts(QUERY, user_key=SESSION_A) == []
        vm.hits_for([vm.vector_item(SEMANTIC_ONLY_FACT, SESSION_A)])
        ctx = mp.retrieve_context(QUERY, session_id=SESSION_A, top_k=3)
        assert SEMANTIC_ONLY_FACT in ctx["facts"], (
            f"向量召回被丢弃：facts={ctx['facts']}"
        )
    finally:
        sm.close()


def test_recent_fallback_alone_cannot_impersonate_semantic_recall():
    """对照组：向量通道无命中时，该旧事实不得出现在 facts（否则上一条测试是假绿）。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, _ = _seed(tmp)
    try:
        vm.hits_for([])
        ctx = mp.retrieve_context(QUERY, session_id=SESSION_A, top_k=3)
        assert SEMANTIC_ONLY_FACT not in ctx["facts"], (
            "回退窗口把目标事实带进来了，对照组失去判别力"
        )
        assert ctx["facts"], "回退路径本身应仍有产出（干扰事实）"
    finally:
        sm.close()


def test_vector_recall_isolated_by_user_key():
    """他人 user_key 的向量命中不得注入本会话（多用户隔离硬约束）。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, _ = _seed(tmp)
    try:
        sm.add_fact(SEMANTIC_ONLY_FACT, category="preference", user_key=SESSION_B)
        vm.hits_for([vm.vector_item(SEMANTIC_ONLY_FACT, SESSION_B)])
        ctx = mp.retrieve_context(QUERY, session_id=SESSION_A, top_k=3)
        assert SEMANTIC_ONLY_FACT not in ctx["facts"]
    finally:
        sm.close()


def test_vector_hit_absent_from_primary_db_is_dropped():
    """主库存在性复核：向量残留但 active 事实已删 → 不得复活进 prompt。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, target_id = _seed(tmp)
    try:
        sm.delete_fact(target_id, recycle=False, user_key=SESSION_A)
        vm.hits_for([vm.vector_item(SEMANTIC_ONLY_FACT, SESSION_A)])
        ctx = mp.retrieve_context(QUERY, session_id=SESSION_A, top_k=3)
        assert SEMANTIC_ONLY_FACT not in ctx["facts"]
    finally:
        sm.close()


def test_merge_dedups_by_id_and_norm_text():
    """同一事实同时被关键词与向量命中时只出现一次，出处记为 both。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, target_id = _seed(tmp)
    try:
        fact = "默默在云南昆明工作"
        fid = int(sm.add_fact(fact, category="occupation", user_key=SESSION_A))
        vm.hits_for([vm.vector_item(fact, SESSION_A)])
        # 关键词路与向量路同时命中同一条事实
        assert sm.search_facts("云南昆明", user_key=SESSION_A)
        ctx = mp.retrieve_context("云南昆明", session_id=SESSION_A, top_k=5)
        assert ctx["facts"].count(fact) == 1, f"未去重：{ctx['facts']}"
        prov = {p["fact"]: p for p in ctx.get("fact_provenance") or []}
        assert fact in prov, f"缺少出处下传：{ctx.get('fact_provenance')}"
        assert prov[fact]["recall"] == "both"
        assert prov[fact]["fact_id"] == fid
        assert target_id
    finally:
        sm.close()


def test_provenance_marks_vector_only_recall():
    """仅向量召回的事实出处标为 vector，并带主库 fact_id。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, target_id = _seed(tmp)
    try:
        vm.hits_for([vm.vector_item(SEMANTIC_ONLY_FACT, SESSION_A)])
        ctx = mp.retrieve_context(QUERY, session_id=SESSION_A, top_k=3)
        prov = {p["fact"]: p for p in ctx.get("fact_provenance") or []}
        assert prov[SEMANTIC_ONLY_FACT]["recall"] == "vector"
        assert prov[SEMANTIC_ONLY_FACT]["fact_id"] == target_id
    finally:
        sm.close()


def test_async_search_bridge_still_awaited():
    """合并改造不得回退成「协程当同步用」——向量通道必须真的被 await。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp, _ = _seed(tmp)
    try:
        vm.hits_for([vm.vector_item(SEMANTIC_ONLY_FACT, SESSION_A)])
        mp.retrieve_context(QUERY, session_id=SESSION_A, top_k=3)
        assert vm.calls, "向量通道未被调用"
        assert vm.calls[0]["where"] == {"user_key": SESSION_A}
    finally:
        sm.close()


def test_event_loop_contract_of_fake_matches_production():
    """替身与真实现同为协程函数，否则本文件全部结论失去意义。"""
    from shisi.memory.legacy.vector_memory import VectorMemory

    assert asyncio.iscoroutinefunction(VectorMemory._search)
    assert asyncio.iscoroutinefunction(FakeVectorMemory._search)
