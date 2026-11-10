"""向量检索归属守卫批（2026-11-10）：_search 单点拦「无归属的全库检索」。

背景：``vector_memory._search`` 旧实现对 ``where=None`` / 仅含 type 键的查询
静默全库放行（跨用户风险面）。本批在 _search 单点、**try 之外**加
VectorScopeError 守卫：scoped（默认）要求 where 命中强归属键
user_key / session_id 至少其一（character_id 不是用户边界——D16 多用户可绑
同一角色卡，仅作附加条件）；``scope="global"`` 显式放行并记 WARNING。
env ``VECTOR_SCOPE_GUARD=0`` 一行回滚到旧行为。

红测分工（V1-V10）：
- V1/V2/V4/V5/V6/V7/V10 为红测（现行实现静默放行或无 scope 参数，必然失败）；
- V3/V6b/V8/V9 为防回归锚（钉合法路径下推格式逐字节不变 / 双用户隔离 /
  env 回滚开关），红跑时即绿属预期；
- 真 chroma 端到端双用户隔离由 test_user_isolation_chain.py 兜底，本文件
  全部用哨兵集合（不触 onnx 模型加载）。
"""

from __future__ import annotations

import asyncio

import pytest

from shisi.memory.legacy import semantic_memory as semantic_mod
from shisi.memory.legacy._legacy_episodic_memory import EpisodicMemory
from shisi.memory.legacy.vector_memory import VectorMemory, VectorScopeError


class _RecordingColl:
    """记录 query 下推参数的哨兵集合（不真连 chromadb，返回空结果）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


class _StubSM:
    """structured 替身：无 identity_keys（单键路径），structured 返回空。"""

    def search_facts(self, query, user_key=None):
        return []


def _bare_vm(monkeypatch, coll_name: str = "chat_history"):
    """绕过 __init__（不触发 chromadb 初始化），注入哨兵集合，测试后恢复。"""
    vm = VectorMemory.__new__(VectorMemory)
    coll = _RecordingColl()
    monkeypatch.setattr(vm, "_collections", {coll_name: coll}, raising=False)
    return vm, coll


# ── V1/V2：无归属检索必须 raise（守卫在 try 之外，响应亮上抛） ──


def test_v1_scoped_without_where_raises(monkeypatch):
    vm, _ = _bare_vm(monkeypatch)
    with pytest.raises(VectorScopeError) as ei:
        asyncio.run(vm._search("chat_history", "查询", 5))
    # 消息含集合名 + 缺失原因，可观测
    assert "chat_history" in str(ei.value)


def test_v2_type_only_where_raises(monkeypatch):
    vm, _ = _bare_vm(monkeypatch, "episodic_memory")
    with pytest.raises(VectorScopeError):
        asyncio.run(
            vm._search("episodic_memory", "洞察", 3, where={"type": "reflection"})
        )


# ── V3：三种合法归属 where 通过，且 $and 下推包装格式逐字节不变 ──


def test_v3_owner_where_passes_and_pushdown_format_unchanged(monkeypatch):
    vm, coll = _bare_vm(monkeypatch, "episodic_memory")
    # ① 单 user_key
    assert asyncio.run(vm._search("episodic_memory", "q", 3, where={"user_key": "u1"})) == []
    # ② session_id + type 双键 → $and 下推包装保持
    asyncio.run(
        vm._search(
            "episodic_memory", "q", 3,
            where={"session_id": "s1", "type": "episode"},
        )
    )
    assert coll.calls[-1]["where"] == {
        "$and": [{"session_id": "s1"}, {"type": "episode"}],
    }
    # ③ $or 组（identity 多键）原样下推（user_facts 集合单独注入哨兵）
    vm2, coll2 = _bare_vm(monkeypatch, "user_facts")
    or_where = {"$or": [{"user_key": "a"}, {"user_key": "b"}]}
    asyncio.run(vm2._search("user_facts", "q", 3, where=or_where))
    assert coll2.calls[-1]["where"] == or_where


# ── V4：scope="global" 显式放行 + WARNING 留痕 ──


def test_v4_explicit_global_passes_with_warning(monkeypatch, caplog):
    vm, _ = _bare_vm(monkeypatch)
    with caplog.at_level("WARNING", logger="vector_memory"):
        items = asyncio.run(
            vm._search("chat_history", "q", 5, where=None, scope="global")
        )
    assert items == []
    assert "vector global search (explicit)" in caplog.text


# ── V5：无 where 的公开便捷检索（零生产调用者的死路径）被守卫拦 ──


def test_v5_unscoped_public_helpers_raise(monkeypatch):
    vm, _ = _bare_vm(monkeypatch, "user_facts")
    with pytest.raises(VectorScopeError):
        asyncio.run(vm.search_facts("q"))
    vm2, _ = _bare_vm(monkeypatch, "chat_history")
    with pytest.raises(VectorScopeError):
        asyncio.run(vm2.search_chats("q"))


# ── V6：semantic 全局模式（user_key=None）从隐式变显式 + WARNING ──


def test_v6_semantic_global_mode_is_explicit_global(monkeypatch, caplog):
    vm, _ = _bare_vm(monkeypatch, "user_facts")
    sem = semantic_mod.SemanticMemory(vm, _StubSM())
    with caplog.at_level("WARNING", logger="vector_memory"):
        res = sem.search("q", top_k=3, user_key=None)
    assert res["vector"] == []
    # 显式全局检索可见：WARNING 落 vector_memory logger
    assert "vector global search (explicit)" in caplog.text


def test_v6b_semantic_with_user_key_stays_scoped(monkeypatch, caplog):
    vm, coll = _bare_vm(monkeypatch, "user_facts")
    sem = semantic_mod.SemanticMemory(vm, _StubSM())
    with caplog.at_level("WARNING", logger="vector_memory"):
        res = sem.search("q", top_k=3, user_key="u1")
    assert res["vector"] == []
    assert "vector global search" not in caplog.text
    # scoped 下推仍带 user_key 归属键（行为不变）
    assert coll.calls[-1]["where"] == {"user_key": "u1"}


# ── V7：episodic session_id=None 时守卫拒绝 → 响亮降级（WARNING + []） ──


def test_v7_episodic_none_session_degrades_loudly(monkeypatch, caplog):
    vm, _ = _bare_vm(monkeypatch, "episodic_memory")
    em = EpisodicMemory(vm, None)
    with caplog.at_level("WARNING", logger="episodic_memory"):
        out = em.search("q", top_k=3, session_id=None, character_id="")
    assert out == []
    # 不能悄悄发生：经其既有 except 落 "Episode search failed" WARNING
    assert "Episode search failed" in caplog.text


# ── V8：双用户同集合 scoped 检索，下推互不串（隔离集成·下推面） ──


def test_v8_two_users_scoped_pushdown_isolated(monkeypatch):
    vm, coll = _bare_vm(monkeypatch, "user_facts")
    sem = semantic_mod.SemanticMemory(vm, _StubSM())
    sem.search("q", top_k=3, user_key="user_a")
    sem.search("q", top_k=3, user_key="user_b")
    wheres = [c["where"] for c in coll.calls]
    assert wheres == [{"user_key": "user_a"}, {"user_key": "user_b"}]


# ── V9：env VECTOR_SCOPE_GUARD=0 一行回滚到旧的无守卫行为 ──


def test_v9_env_off_restores_legacy_behavior(monkeypatch):
    vm, _ = _bare_vm(monkeypatch)
    monkeypatch.setenv("VECTOR_SCOPE_GUARD", "0")
    items = asyncio.run(vm._search("chat_history", "q", 5))  # 无 where 也不 raise
    assert items == []


# ── V10（Q3）：character_id 不是用户边界，仅作附加条件 ──


def test_v10_character_id_alone_is_not_owner(monkeypatch):
    vm, _ = _bare_vm(monkeypatch, "episodic_memory")
    # 仅 character_id + type：D16 多用户可绑同一角色卡 → 拦
    with pytest.raises(VectorScopeError):
        asyncio.run(
            vm._search(
                "episodic_memory", "q", 3,
                where={"character_id": "c1", "type": "episode"},
            )
        )
    # character_id 与强归属键同现 → 通过（两集合分别注入哨兵）
    asyncio.run(
        vm._search(
            "episodic_memory", "q", 3,
            where={"session_id": "s1", "character_id": "c1", "type": "episode"},
        )
    )
    vm2, _ = _bare_vm(monkeypatch, "user_facts")
    asyncio.run(
        vm2._search(
            "user_facts", "q", 3,
            where={"character_id": "c1", "user_key": "u1"},
        )
    )
