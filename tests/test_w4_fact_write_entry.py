"""W4 · 事实写路径统一入口回归（缺陷 B / 包任务 1·3）。

钉住：
1. 事实写入唯一入口 = ShisiMemoryService.record_fact —— 结构化行 + 向量派生
   + 来源水位（source_last_id）+ EventLedger 记账一次完成，返回真实回执
   （fact_id/action/source_last_id/turn_id/user_key）；
2. `remember_facts` 工具不再直写 StructuredMemory（旧路径既不做向量派生、
   也不带 source_last_id → 迟到的旧来源写入能复活已删事实）；
3. 迟到旧来源不复活：删除后同来源重放 → action=skipped_deleted_source，
   主库不重新 active、向量不再派生；
4. 用户新一轮重述可重新记住：新对话轮（id 超过删除水位）→ 正常插入；
5. 低层 int/bool 契约不回退（add_fact→int、semantic.add_fact→bool 是
   receipt 的投影，既有测试与调用方语义不变）。

隔离：真实 StructuredMemory + 假向量后端落临时目录；不触碰生产 data/。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from shisi.application.memory_service import ShisiMemoryService
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION_A = "1:peer-a@im.wechat"
SESSION_B = "2:peer-b@im.wechat"
FACT = "用户喜欢喝手冲咖啡"


class FakeVectorMemory:
    """向量后端替身：store_fact 与真实现同为协程，记录派生请求。"""

    def __init__(self) -> None:
        self.stored: list[dict] = []
        self._collections: dict = {}

    async def store_fact(
        self, fact: str, category: str = "general", confidence: float = 0.5,
        user_key: str = "",
    ) -> None:
        self.stored.append({"fact": fact, "user_key": user_key, "category": category})

    async def _search(self, collection, query, top_k, where=None) -> list:
        return []

    def store_chat_sync(self, *a, **k) -> None:
        return None

    def health_check(self) -> dict:
        return {"available": True}


def _make(tmp: Path):
    sm = StructuredMemory(str(tmp / "write.sqlite"))
    vm = FakeVectorMemory()
    svc = ShisiMemoryService(structured_memory=sm, vector_memory=vm, db_path=tmp / "fav.db")
    return sm, vm, svc


def _seed_turn(sm: StructuredMemory, session: str, text: str, turn_id: str) -> int:
    sm.add_chat_turn(text, "好嘞", session_id=session, character_id="c1", turn_id=turn_id)
    return sm.chat_turn_last_id(session, turn_id)


# ── 1. 统一写入口：回执真实、向量派生、水位接入 ─────────────


def test_record_fact_returns_real_receipt_and_derives_vector():
    tmp = Path(tempfile.mkdtemp())
    sm, vm, svc = _make(tmp)
    try:
        src = _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        receipt = svc.record_fact(FACT, session_key=SESSION_A, category="preference", turn_id="t1")
        assert receipt["ok"] is True
        assert receipt["action"] == "inserted"
        assert int(receipt["fact_id"]) > 0
        assert receipt["source_last_id"] == src, "回执必须带真实来源水位（删除水位据此判迟到）"
        assert receipt["turn_id"] == "t1"
        assert receipt["user_key"] == SESSION_A
        assert receipt["vector_stored"] is True, "统一写入口必须做向量派生（缺陷 B）"
        assert vm.stored and vm.stored[0]["fact"] == FACT
        rows = sm.get_facts(user_key=SESSION_A)
        assert any(r["fact"] == FACT for r in rows)
    finally:
        sm.close()


def test_record_fact_appends_ledger_once():
    """EventLedger 是记忆写入审计真源——统一入口必须记账，且只记一次。"""
    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    events: list[dict] = []
    try:
        _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        from shisi.agent_plane import runtime as apruntime

        orig = apruntime.append_memory_write_event
        apruntime.append_memory_write_event = lambda **kw: events.append(kw)
        try:
            svc.record_fact(FACT, session_key=SESSION_A, category="preference", turn_id="t1")
        finally:
            apruntime.append_memory_write_event = orig
        assert len(events) == 1, f"ledger 记账应恰好一次：{events}"
        payload_facts = events[0]["facts"]
        assert any(int(f.get("fact_id") or -1) > 0 for f in payload_facts)
    finally:
        sm.close()


# ── 2/3. 迟到旧来源不复活（水位），新一轮重述可重新记住 ──────


def test_deleted_fact_not_revived_by_late_same_source_write():
    tmp = Path(tempfile.mkdtemp())
    sm, vm, svc = _make(tmp)
    try:
        _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        receipt = svc.record_fact(FACT, session_key=SESSION_A, category="preference", turn_id="t1")
        fid = int(receipt["fact_id"])
        del_receipt = svc.forget_fact(fid, session_key=SESSION_A)
        assert del_receipt["ok"] is True
        assert not any(r["fact"] == FACT for r in sm.get_facts(user_key=SESSION_A))
        stored_before = len(vm.stored)
        # 同一来源（t1，无新消息）迟到的重放 → 必须被删除水位挡住
        replay = svc.record_fact(FACT, session_key=SESSION_A, category="preference", turn_id="t1")
        assert replay["action"] == "skipped_deleted_source", f"迟到旧来源复活了：{replay}"
        assert not any(r["fact"] == FACT for r in sm.get_facts(user_key=SESSION_A))
        assert len(vm.stored) == stored_before, "被跳过的一律不得再派生向量"
    finally:
        sm.close()


def test_new_user_restatement_after_new_turn_is_remembered():
    """水位不是永久黑名单：用户新一轮重述（来源 id 超过删除水位）必须能重新记住。"""
    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        fid = int(svc.record_fact(FACT, session_key=SESSION_A, turn_id="t1")["fact_id"])
        svc.forget_fact(fid, session_key=SESSION_A)
        _seed_turn(sm, SESSION_A, "我还是喜欢手冲咖啡", "t2")
        again = svc.record_fact(FACT, session_key=SESSION_A, category="preference", turn_id="t2")
        assert again["action"] == "inserted", f"新一轮重述被永久拉黑：{again}"
        assert any(r["fact"] == FACT for r in sm.get_facts(user_key=SESSION_A))
    finally:
        sm.close()


def test_record_fact_isolated_between_sessions():
    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        _seed_turn(sm, SESSION_A, "我喜欢手冲", "t1")
        svc.record_fact(FACT, session_key=SESSION_A, turn_id="t1")
        assert sm.get_facts(user_key=SESSION_B) == []
    finally:
        sm.close()


# ── 4. remember_facts 工具经统一入口（向量派生 + 水位 + 回执）──


def test_remember_facts_tool_writes_through_service():
    from tools.builtin.profile_agent_tools import RememberFactsTool

    tmp = Path(tempfile.mkdtemp())
    sm, vm, svc = _make(tmp)
    try:
        src = _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        tool = RememberFactsTool()
        res = tool.execute(
            _meta={"session_key": SESSION_A, "turn_id": "t1"},
            memory_service=svc,
            facts=[
                {"fact": FACT, "category": "preference"},
                {"fact": "叫我", "category": "commitment"},  # 清洗拒绝
            ],
        )
        assert res.success
        written = res.data["written"]
        assert len(written) == 1, written
        entry = written[0]
        assert entry["fact"] == FACT
        assert int(entry["id"]) > 0
        assert entry["action"] == "inserted", "回执必须透传统一写入口的 action"
        assert entry["source_last_id"] == src
        assert vm.stored and vm.stored[0]["fact"] == FACT, "工具路径必须做向量派生（修复前直写 sm 无派生）"
    finally:
        sm.close()


def test_remember_facts_tool_late_replay_is_skipped_not_written():
    """删除后同一来源的工具重放 → 记为 skipped，不得混进 written 假成功。"""
    from tools.builtin.profile_agent_tools import RememberFactsTool

    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        fid = int(svc.record_fact(FACT, session_key=SESSION_A, turn_id="t1")["fact_id"])
        svc.forget_fact(fid, session_key=SESSION_A)
        res = RememberFactsTool().execute(
            _meta={"session_key": SESSION_A, "turn_id": "t1"},
            memory_service=svc,
            facts=[{"fact": FACT, "category": "preference"}],
        )
        data = res.data if res.success else {}
        assert not any(w["fact"] == FACT for w in data.get("written") or [])
        assert any(s["fact"] == FACT for s in data.get("skipped") or []), \
            f"迟到跳过必须可见：success={res.success} data={data}"
        assert not any(r["fact"] == FACT for r in sm.get_facts(user_key=SESSION_A))
    finally:
        sm.close()


def test_remember_facts_fallback_still_applies_watermark():
    """无服务注入（旧测试/维护路径）时，工具直写也必须带来源水位，迟到重放不复活。"""
    from tools.builtin.profile_agent_tools import RememberFactsTool

    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        fid = int(svc.record_fact(FACT, session_key=SESSION_A, turn_id="t1")["fact_id"])
        svc.forget_fact(fid, session_key=SESSION_A)
        res = RememberFactsTool().execute(
            _meta={"session_key": SESSION_A, "turn_id": "t1"},
            structured_memory=sm,
            facts=[{"fact": FACT, "category": "preference"}],
        )
        data = res.data if res.success else {}
        assert not any(w["fact"] == FACT for w in data.get("written") or []), \
            f"无服务回退路径复活了已删事实：{data}"
        assert not any(r["fact"] == FACT for r in sm.get_facts(user_key=SESSION_A))
    finally:
        sm.close()


def test_forget_facts_tool_goes_through_service_receipt():
    from tools.builtin.profile_agent_tools import ForgetFactsTool

    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        _seed_turn(sm, SESSION_A, "我平时喜欢喝手冲咖啡", "t1")
        fid = int(svc.record_fact(FACT, session_key=SESSION_A, turn_id="t1")["fact_id"])
        res = ForgetFactsTool().execute(
            _meta={"session_key": SESSION_A, "turn_id": "t1"},
            memory_service=svc,
            structured_memory=sm,
            fact_ids=[fid],
        )
        assert res.success
        assert res.data["removed"] == [fid]
        assert all(r["fact"] != FACT for r in sm.get_facts(user_key=SESSION_A))
    finally:
        sm.close()


# ── 5. 低层 int/bool 投影契约不回退 ────────────────────────


def test_add_fact_int_projection_unchanged():
    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        assert sm.add_fact("", user_key=SESSION_A) == -1
        fid = int(sm.add_fact(FACT, user_key=SESSION_A))
        assert fid > 0
        # 等价文本 → 强化返回既有 id（不双插）
        assert sm.add_fact(FACT + "。", user_key=SESSION_A) == fid
        # 已删来源 → -2（跳过），既有契约保持
        _seed_turn(sm, SESSION_A, "别记这个", "t1")
        svc.forget_fact(fid, session_key=SESSION_A)
        src = sm.chat_turn_last_id(SESSION_A, "t1")
        assert sm.add_fact(FACT, user_key=SESSION_A, source_last_id=src) == -2
    finally:
        sm.close()


def test_semantic_write_fact_receipt_and_bool_projection():
    tmp = Path(tempfile.mkdtemp())
    sm, _vm, svc = _make(tmp)
    try:
        sem = svc.semantic
        _seed_turn(sm, SESSION_A, "我喜欢手冲", "t1")
        receipt = sem.write_fact(
            FACT, category="preference", confidence=0.85, source="agent",
            user_key=SESSION_A, source_last_id=sm.chat_last_id(SESSION_A),
        )
        assert receipt["action"] == "inserted"
        assert int(receipt["fact_id"]) > 0
        assert receipt["vector_stored"] is True
        assert sem.add_fact(FACT, user_key=SESSION_A) is True  # bool 投影
    finally:
        sm.close()
