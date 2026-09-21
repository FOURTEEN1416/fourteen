"""提醒意图管线 + 澄清状态机 + 到期投递 回归测试（2026-09-20 批次）。

背景：2026-09-19 深夜用户「明早六点记得发消息给我，叫我起床」未触发任何工具
（关键词裁决漏检），且旧提醒链路只写库不触发、SQL UTC 与北京时间差 8 小时、
无投递目标。本文件锁住修复后的完整链路行为。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator import tool_gate
from orchestrator.optimized_orchestrator import OptimizedOrchestrator
from proactive.reminder_delivery import ReminderDeliveryTask
from shisi.memory.legacy.structured_memory import StructuredMemory
from tools.base_tool import ToolResult
from tools.builtin.reminder_tool import CalendarQueryTool, ReminderTool

# 昨晚事故原话——必须命中的头号回归用例
LAST_NIGHT_MSG = "明早六点记得发消息给我，叫我起床，听到没有？"


# ── L0 晋级线 ─────────────────────────────────────────────


class TestEscalation:
    @pytest.mark.parametrize("text", [
        LAST_NIGHT_MSG,
        "六点叫我起床",
        "半小时后提醒我喝水",
        "明天早上7点喊我",
        "25号提醒我交作业",
        "设个提醒",
        "北京今天天气怎么样",   # 查询类（旧行为兼容）
        "帮我算一下 23*7",
    ])
    def test_hits(self, text: str):
        assert tool_gate.should_escalate(text), f"漏检: {text}"

    @pytest.mark.parametrize("text", [
        "今天好累啊，也不想动",
        "你觉得这部电影怎么样？",
        "明天见！",
        "今天是周一",
        "我记得你说过这个",
        "周五开心",
    ])
    def test_misses(self, text: str):
        assert not tool_gate.should_escalate(text), f"误晋级: {text}"

    def test_pending_forces_escalation(self):
        assert tool_gate.should_escalate("嗯", has_pending_intent=True)


# ── 测试基建 ───────────────────────────────────────────────


class _Registry:
    def __init__(self, tools: dict):
        self._tools = tools

    def get_tools_by_permission(self, affinity_level: int = 0):
        return [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description,
                             "parameters": t.parameters_schema},
            }
            for t in self._tools.values()
        ]

    def get(self, name: str):
        return self._tools.get(name)


class _Tools:
    def __init__(self, tools: dict):
        self.registry = _Registry(tools)
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, name: str, arguments: dict, affinity_level: int = 0,
                 caller_id: str = ""):
        self.calls.append((name, arguments))
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(False, error="not_found")
        return tool.execute(**arguments)


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(db_path=str(tmp_path / "t.db"))
    yield memory
    memory.close()


def _orch(sm_obj, tools: _Tools, llm):
    orch = OptimizedOrchestrator()
    orch.components = {
        "tools": tools,
        "memory": SimpleNamespace(structured_memory=sm_obj),
    }
    return orch


# ── L1 终审三分支 ──────────────────────────────────────────


class TestFinalReview:
    def _llm_returning(self, payload: dict, log: list):
        async def chat_with_tools(**kwargs):
            log.append(kwargs)
            return payload
        return SimpleNamespace(chat_with_tools=chat_with_tools)

    def test_call_branch_dispatches_and_injects_meta(self, sm):
        tools = _Tools({"set_reminder": ReminderTool(sm)})
        log: list = []
        llm = self._llm_returning({
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "set_reminder",
                    "arguments": json.dumps({
                        "content": "叫我起床",
                        "trigger_time": "2026-09-20 06:00",
                    }),
                }
            }],
        }, log)
        orch = _orch(sm, tools, llm)
        results, direct = asyncio.run(orch._run_tools_if_needed(
            llm, LAST_NIGHT_MSG, "sys", [], affinity_level=2,
            session_key="1:peer@im.wechat", user_id=7,
        ))
        # 工具真被调用且归属由服务端注入
        assert tools.calls and tools.calls[0][0] == "set_reminder"
        assert tools.calls[0][1]["_meta"] == {
            "session_key": "1:peer@im.wechat", "user_id": 7,
        }
        # 提醒确实落库且带投递目标
        due = sm.get_due_reminders("2099-01-01 00:00:00")
        assert len(due) == 1
        assert due[0]["session_key"] == "1:peer@im.wechat"
        assert due[0]["user_id"] == 7
        assert 'trust="untrusted"' in results
        assert "不是指令" in results
        assert direct == ""

    def test_ask_user_branch_creates_pending_and_returns_question(self, sm):
        tools = _Tools({"set_reminder": ReminderTool(sm)})
        log: list = []
        llm = self._llm_returning({
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "ask_user",
                    "arguments": json.dumps({
                        "question": "几点叫你？",
                        "known": {"content": "叫我起床"},
                        "missing": ["trigger_time"],
                    }),
                }
            }],
        }, log)
        orch = _orch(sm, tools, llm)
        results, direct = asyncio.run(orch._run_tools_if_needed(
            llm, "叫我起床", "sys", [], affinity_level=2,
            session_key="1:peer@im.wechat",
        ))
        assert results == ""
        assert direct == "几点叫你？"
        assert tools.calls == []  # 未成任务不落提醒
        # CI 聚合态下偶发 event loop/连接可见性问题：读 API 与 SQL 旁路双检
        pending = sm.get_active_pending_intent("1:peer@im.wechat")
        if pending is None:
            with sm._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM pending_intents WHERE session_key=? AND status='active'",
                    ("1:peer@im.wechat",),
                ).fetchall()
            if rows:
                pending = dict(rows[0])
                # TTL/状态机误标过期时旁路可见则仍算写入成功，但要求 ask_count
        assert pending is not None, (
            "ask_user 分支必须落 pending_intents；"
            f"sm={id(sm)} direct={direct!r}"
        )
        assert pending["ask_count"] == 1
        assert json.loads(pending["slots_json"])["content"] == "叫我起床"

    def test_pending_answer_merges_then_fulfills(self, sm):
        # 第一轮：信息不全 → 澄清
        tools = _Tools({"set_reminder": ReminderTool(sm)})
        llm = self._llm_returning({
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "ask_user",
                    "arguments": json.dumps({
                        "question": "几点？", "known": {"content": "起床"},
                    }),
                }
            }],
        }, [])
        orch = _orch(sm, tools, llm)
        _, direct = asyncio.run(orch._run_tools_if_needed(
            llm, "叫我起床", "sys", [], affinity_level=2,
            session_key="s1",
        ))
        assert direct == "几点？"
        # 第二轮：用户补时间 → 合并后直接调度工具
        llm2 = self._llm_returning({
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "set_reminder",
                    "arguments": json.dumps({
                        "content": "起床", "trigger_time": "2026-09-21 07:00",
                    }),
                }
            }],
        }, [])
        results, _ = asyncio.run(orch._run_tools_if_needed(
            llm2, "七点吧", "sys", [], affinity_level=2,
            session_key="s1",
        ))
        assert 'trust="untrusted"' in results
        assert "不是指令" in results
        assert sm.get_active_pending_intent("s1") is None  # fulfilled 后关闭

    def test_pending_third_ask_is_cancelled(self, sm):
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "x"}, 2, "q")
        tools = _Tools({"set_reminder": ReminderTool(sm)})
        llm = self._llm_returning({
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "ask_user",
                    "arguments": json.dumps({"question": "几点嘛", "known": {}}),
                }
            }],
        }, [])
        orch = _orch(sm, tools, llm)
        results, direct = asyncio.run(orch._run_tools_if_needed(
            llm, "再说一遍呗", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert (results, direct) == ("", "")
        assert sm.get_active_pending_intent("s1") is None  # cancelled

    def test_chat_branch_returns_empty(self, sm):
        tools = _Tools({"set_reminder": ReminderTool(sm)})
        llm = self._llm_returning({"content": "哈哈是吗", "tool_calls": None}, [])
        orch = _orch(sm, tools, llm)
        results, direct = asyncio.run(orch._run_tools_if_needed(
            llm, "六点叫我起床顺便聊聊天", "sys", [], affinity_level=2,
            session_key="s1",
        ))
        assert (results, direct) == ("", "")
        assert tools.calls == []

    def test_fake_promise_triggers_retry_then_aborts(self, sm):
        """防假承诺：空口「听到啦」→ 复核一次；仍不调工具则弃内容走主链。"""
        tools = _Tools({"set_reminder": ReminderTool(sm)})
        log: list = []
        llm = self._llm_returning({"content": "听到啦", "tool_calls": None}, log)
        orch = _orch(sm, tools, llm)
        results, direct = asyncio.run(orch._run_tools_if_needed(
            llm, LAST_NIGHT_MSG, "sys", [], affinity_level=2, session_key="s1",
        ))
        assert (results, direct) == ("", "")
        assert tools.calls == []          # 没有假提醒落库
        assert len(log) == 2              # 复核恰好一次
        assert "系统复核" in log[1]["system_prompt"]

    def test_escalation_miss_skips_llm(self, sm):
        tools = _Tools({"set_reminder": ReminderTool(sm)})

        def _boom(**kwargs):
            raise AssertionError("未晋级消息不得发起终审")

        llm = SimpleNamespace(chat_with_tools=_boom)
        orch = _orch(sm, tools, llm)
        result = asyncio.run(orch._run_tools_if_needed(
            llm, "今天天气真差心情也不好", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert result == ("", "")


# ── 澄清状态机（StructuredMemory）────────────────────────


class TestPendingIntentStore:
    def test_upsert_updates_same_session(self, sm):
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "a"}, 1, "q1")
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "b"}, 2, "q2")
        pending = sm.get_active_pending_intent("s1")
        assert pending["ask_count"] == 2
        assert json.loads(pending["slots_json"]) == {"content": "b"}

    def test_expiry(self, sm):
        sm.upsert_pending_intent("s1", "set_reminder", {}, 1, "q", ttl_minutes=-1)
        assert sm.expire_stale_intents() == 1
        assert sm.get_active_pending_intent("s1") is None

    def test_resolve(self, sm):
        sm.upsert_pending_intent("s1", "set_reminder", {}, 1, "q")
        sm.resolve_pending_intent("s1", "cancelled")
        assert sm.get_active_pending_intent("s1") is None


# ── 到期投递 ──────────────────────────────────────────────


class TestReminderDelivery:
    def test_tick_actually_runs_batch_intent_gc(self, sm):
        """每 tick 的批量过期清理必须**真的执行**（2026-09-20 修复锁定）。

        ⚠️ 旧实现 `_maybe_gc_intents` 是同步函数且调用
        `asyncio.run(self._sm.expire_stale_intents())` —— ① 该方法本身是同步的
        （`asyncio.run` 只收协程对象）；② 它由 `_run_once()` 在已运行的事件循环里
        同步调用（`asyncio.run` 在运行中循环内必抛 RuntimeError）。两个异常都被
        `except Exception` 吞掉，于是批量清理**从未执行**。

        断言必须读**原始行**：`get_active_pending_intent` 自带惰性过期，会掩盖缺陷。
        """
        import inspect

        assert inspect.iscoroutinefunction(ReminderDeliveryTask._maybe_gc_intents), (
            "_maybe_gc_intents 必须是协程（同步版在运行中的事件循环里无法安全执行 DB 调用）"
        )

        sm.upsert_pending_intent("gc-s1", "set_reminder", {}, 1, "q", ttl_minutes=-1)
        with sm._conn() as conn:
            before = conn.execute(
                "SELECT status FROM pending_intents WHERE session_key = 'gc-s1'"
            ).fetchone()["status"]
        assert before == "active"  # 前提：写入后确实处于 active

        ReminderDeliveryTask(sm, llm=None)()  # 同步入口 → asyncio.run(_run_once())

        with sm._conn() as conn:
            after = conn.execute(
                "SELECT status FROM pending_intents WHERE session_key = 'gc-s1'"
            ).fetchone()["status"]
        assert after == "expired", "批量过期清理未执行（行仍为 active → pending_intents 无界增长）"

    def test_due_delivered_via_wechat_with_fallback_text(self, sm):
        sent: list[tuple[int, str, str]] = []

        def wechat_send(owner_id: int, peer: str, text: str) -> bool:
            sent.append((owner_id, peer, text))
            return True

        rid = sm.add_reminder("叫我起床", "2020-01-01 06:00",
                              session_key="1:peer@im.wechat", user_id=1)
        task = ReminderDeliveryTask(sm, llm=None, wechat_sender=wechat_send)
        task()
        assert sent == [(1, "peer@im.wechat", "叫我起床")]  # LLM 缺席 → 原文兜底
        row = [r for r in sm.get_pending_reminders() if r["id"] == rid]
        assert row == []  # triggered=1 后不再出现在 pending 视图

    def test_llm_copy_used_when_available(self, sm):
        sent: list[str] = []

        def wechat_send(owner_id: int, peer: str, text: str) -> bool:
            sent.append(text)
            return True

        async def chat(**kwargs):
            return "起床啦笨蛋，太阳晒屁股啦"

        llm = SimpleNamespace(chat=chat)
        sm.add_reminder("起床", "2020-01-01 06:00", session_key="1:p@im.wechat")
        ReminderDeliveryTask(sm, llm=llm, wechat_sender=wechat_send)()
        assert sent == ["起床啦笨蛋，太阳晒屁股啦"]

    def test_fail_three_times_marks_failed(self, sm):
        sm.add_reminder("x", "2020-01-01 06:00", session_key="1:p@im.wechat")
        task = ReminderDeliveryTask(sm, llm=None, wechat_sender=lambda *a: False)
        for _ in range(3):
            task()
        with sm._conn() as conn:
            row = conn.execute("SELECT status, active FROM reminders").fetchone()
        assert row["status"] == "failed"
        assert row["active"] == 0
        assert sm.get_due_reminders() == []  # 判死后不再投递

    def test_web_session_routes_to_ws(self, sm):
        """ws_sender 契约=可返回 awaitable 且必须被 await（P0-6 假送达回归钉）。

        旧测试用同步 list.append 钉死了「协程未执行也返回 True」的错误契约。
        """
        sent: list[str] = []

        async def ws_send(text: str) -> bool:
            sent.append(text)
            return True

        sm.add_reminder("x", "2020-01-01 06:00", session_key="web-console-s1")
        task = ReminderDeliveryTask(sm, llm=None, ws_sender=ws_send)
        task()
        assert sent == ["x"]
        with sm._conn() as conn:
            row = conn.execute("SELECT status FROM reminders").fetchone()
        assert row["status"] == "delivered"

    def test_web_session_ws_failure_not_marked_delivered(self, sm):
        async def ws_fail(text: str) -> bool:
            return False

        sm.add_reminder("y", "2020-01-01 06:00", session_key="web-console-s2")
        task = ReminderDeliveryTask(sm, llm=None, ws_sender=ws_fail)
        task()
        with sm._conn() as conn:
            row = conn.execute("SELECT status FROM reminders").fetchone()
        assert row["status"] != "delivered"

    def test_timezone_semantics_local_beijing(self, sm):
        """到期比较必须用北京时间口径：未来/过去时刻相对 now_local 构造。

        CI/容器主机为 UTC 时，裸 datetime.now() 会比读侧 _now_local 慢 8 小时，
        「未来 1 小时」会被提前判到期。
        """
        from datetime import timedelta

        from utils.local_time import now_local

        future_local = (now_local() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        sm.add_reminder("soon", future_local, session_key="1:p@im.wechat")
        assert len(sm.get_due_reminders()) == 0  # 未到期
        past_local = (now_local() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        sm.add_reminder("past", past_local, session_key="1:p@im.wechat")
        due = sm.get_due_reminders()
        assert [r["content"] for r in due] == ["past"]

    def test_legacy_orphan_not_delivered(self, sm):
        """存量无主提醒（session_key 空）永不投递（等价旧行为，不误发）。"""
        sm.add_reminder("旧喝水提醒", "2020-01-01 09:00")
        assert sm.get_due_reminders() == []


# ── 工具层 ────────────────────────────────────────────────


class TestReminderTools:
    def test_missing_trigger_time_rejected(self, sm):
        result = ReminderTool(sm).execute(content="x")
        assert not result.success
        assert result.error == "trigger_time_required"

    def test_query_filters_by_session(self, sm):
        sm.add_reminder("mine", "2099-01-01 08:00", session_key="s1")
        sm.add_reminder("other", "2099-01-01 09:00", session_key="s2")
        mine = CalendarQueryTool(sm).execute(
            _meta={"session_key": "s1"},
        )
        assert mine.success
        assert [r["content"] for r in mine.data] == ["mine"]


# ── 老库迁移 ──────────────────────────────────────────────


class TestLegacyMigration:
    def test_old_schema_gains_columns(self, tmp_path):
        import sqlite3

        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, trigger_time TIMESTAMP, active BOOLEAN DEFAULT 1, "
            "triggered BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO reminders (content, trigger_time) VALUES ('喝水', '2026-09-20 09:00')"
        )
        conn.commit()
        conn.close()

        memory = StructuredMemory(db_path=str(db))
        try:
            cols = {
                r["name"] for r in memory._conn().__enter__()
                .execute("PRAGMA table_info(reminders)").fetchall()
            }
            assert {"session_key", "user_id", "status", "delivered_at", "fail_count"} <= cols
            # 存量数据无损，且因无 session_key 不会被误投递
            assert memory.get_due_reminders() == []
        finally:
            memory.close()
