"""① 数据闭环回归（2026-09-21 自问自答根治）。

生产实证：她**主动发出去**的话（ASE 主动消息、对话内追问、到期提醒）从不入库
—— 6 条已送达消息在 chat_history / working / episodic / semantic 四张表全部 0 命中。
后果是她下一轮读不到自己说过什么，于是重复问同一件事、并把用户对追问的回应
当成用户新起的话题（用户侧体感＝自问自答）。

钉住三件事：
1. 记忆层有唯一的「只写 assistant 一行」入口；
2. 三条投递链（调度器 / 提醒任务 / 微信追问）都在**送达之后**才回写；
3. 未送达、无会话键、系统错误占位句一律不回写。
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from shisi.memory.legacy.memory_pipeline import MemoryPipeline
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION = "4:o9x@im.wechat"


class _FakeVM:
    def search_sync(self, query, top_k=5, filter_dict=None):
        return []

    def store_chat_sync(self, *a, **k):
        return None

    def health_check(self):
        return {"ok": True}


def _pipeline(tmp: Path) -> MemoryPipeline:
    sm = StructuredMemory(str(tmp / "closure.sqlite"))
    return MemoryPipeline(vector_memory=_FakeVM(), structured_memory=sm, llm_gateway=None)


def _chat_rows(sm: StructuredMemory, session_id: str) -> list[tuple[str, str]]:
    with sm._conn() as conn:  # noqa: SLF001 - 读原始行，避开任何读侧过滤
        return [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT role, content FROM chat_history WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        ]


# ═══════════════════════════════════════════════════════════════
#  记忆层入口
# ═══════════════════════════════════════════════════════════════

def test_record_outbound_writes_assistant_row_only(tmp_path):
    mp = _pipeline(tmp_path)
    try:
        ok = mp.record_outbound_message("花浇了没有？", session_id=SESSION)
        assert ok is True
        rows = _chat_rows(mp.sm, SESSION)
        assert rows == [("assistant", "花浇了没有？")]   # 只有她这一行，不伪造用户发言
    finally:
        mp.sm.close()


def test_record_outbound_is_visible_to_next_round_context(tmp_path):
    """回写的唯一意义：下一轮上下文读得到 —— 直接钉读侧，不钉写侧自说自话。"""
    mp = _pipeline(tmp_path)
    try:
        mp.record_outbound_message("花浇了没有？", session_id=SESSION)
        messages, _summary = mp.get_chat_context(session_id=SESSION, keep_recent=10)
        assert any(m["role"] == "assistant" and "花浇了没有" in m["content"] for m in messages)
    finally:
        mp.sm.close()


def test_record_outbound_does_not_leak_across_sessions(tmp_path):
    mp = _pipeline(tmp_path)
    try:
        mp.record_outbound_message("只发给 A 的一句", session_id="1:peer@im.wechat")
        assert _chat_rows(mp.sm, "4:peer@im.wechat") == []
    finally:
        mp.sm.close()


def test_record_outbound_rejects_blank_and_system_placeholder(tmp_path):
    mp = _pipeline(tmp_path)
    try:
        assert mp.record_outbound_message("   ", session_id=SESSION) is False
        # 兜底句（utils.fallback_lines 的 timeout 变体）不是她真的说过的话
        assert mp.record_outbound_message("等我一下，刚才有点卡", session_id=SESSION) is False
        assert _chat_rows(mp.sm, SESSION) == []
    finally:
        mp.sm.close()


def test_memory_service_exposes_single_owner():
    """回写 owner 只有记忆层一处：服务壳必须转发，不得另起一份实现。"""
    from shisi.application.memory_service import ShisiMemoryService

    src = inspect.getsource(ShisiMemoryService.record_outbound_message)
    assert "self._pipeline.record_outbound_message" in src


# ═══════════════════════════════════════════════════════════════
#  调度器投递链
# ═══════════════════════════════════════════════════════════════

class _Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def record_outbound_message(self, message: str, session_id: str = "", **_kw):
        self.calls.append((message, session_id))
        return True


def _scheduler():
    from proactive.scheduler import ProactiveScheduler

    return ProactiveScheduler(ase_engine=None, send_message_func=None)


def test_scheduler_deliver_records_after_successful_send(tmp_path, monkeypatch):
    s = _scheduler()
    rec = _Recorder()
    s.set_memory(rec)
    monkeypatch.setattr(s, "_send_targeted", lambda msg, session_key=None: _ok(msg))

    assert s._deliver("早安呀", session_key=SESSION) is True
    assert rec.calls == [("早安呀", SESSION)]


def test_scheduler_deliver_no_record_when_not_delivered(tmp_path, monkeypatch):
    """未送达不得回写 —— 否则历史里会出现她从没说出口的话（与谎报 reply_sent 同类）。"""
    s = _scheduler()
    rec = _Recorder()
    s.set_memory(rec)

    async def _fail(msg, session_key=None):
        return False

    monkeypatch.setattr(s, "_send_targeted", _fail)
    assert s._deliver("早安呀", session_key=SESSION) is False
    assert rec.calls == []


def test_scheduler_broadcast_without_session_key_is_not_recorded(monkeypatch):
    """广播无法归属到某个会话 → 宁可不回写，也不把消息写进别人的历史。"""
    s = _scheduler()
    rec = _Recorder()
    s.set_memory(rec)
    monkeypatch.setattr(s, "_send_targeted", lambda msg, session_key=None: _ok(msg))

    assert s._deliver("统一条") is True
    assert rec.calls == []


def test_scheduler_missing_memory_does_not_break_delivery():
    """记忆服务缺失（装配顺序/降级）只影响回写，不得让投递链抛异常。"""
    s = _scheduler()
    s._memory = object()          # 无 record_outbound_message 的壳
    assert s._record_outbound("一句", SESSION) is None


def _ok(msg: str):
    async def _coro():
        return True

    return _coro()


# ═══════════════════════════════════════════════════════════════
#  到期提醒投递链
# ═══════════════════════════════════════════════════════════════

class _FakeSM:
    def mark_reminder_result(self, *_a, **_k):
        return None

    def expire_stale_intents(self):
        return 0


def _reminder_task(wechat_ok: bool, memory):
    from proactive.reminder_delivery import ReminderDeliveryTask

    return ReminderDeliveryTask(
        _FakeSM(),
        llm=None,
        wechat_sender=lambda owner_id, peer, text: wechat_ok,
        ws_sender=None,
        memory=memory,
    )


def test_reminder_delivery_records_after_wechat_send():
    rec = _Recorder()
    task = _reminder_task(True, rec)
    asyncio.run(task._deliver({"id": 1, "content": "六点了，起来啦", "session_key": SESSION}))
    assert rec.calls == [("六点了，起来啦", SESSION)]


def test_reminder_delivery_no_record_on_failure():
    rec = _Recorder()
    task = _reminder_task(False, rec)
    asyncio.run(task._deliver({"id": 2, "content": "六点了", "session_key": SESSION}))
    assert rec.calls == []


def test_reminder_delivery_tolerates_missing_memory():
    task = _reminder_task(True, None)
    asyncio.run(task._deliver({"id": 3, "content": "六点了", "session_key": SESSION}))  # 不得抛
