"""W3 缺陷 I 回归 —— 入站 message_id 贯通到**工具副作用**（set_reminder）。

消息级幂等（`inbound_claim`）只挡住"整回合重放"：处理**前**认领、异常
release。可 release 之后重放、终审守卫**补跑** set_reminder、以及同一条消息在
两个 worker 上被重复投递时，回合**内部**的副作用仍会各写一行——用户被叫两次。

本文件钉四段链，缺一即断：

1. `utils/inbound_context`：请求级入站身份（ContextVar，与
   `utils.llm_bridge.request_llm` 同一模式）；
2. `wechat_direct`：把入站 `msg_id` 交给用户管理器（跨线程/共享循环这一跳
   **必须显式传参**——ContextVar 不随 `run_coroutine_threadsafe` 传播）；
3. `user_scheduler.process_message`：在回合起点绑定，使下游（编排器/工具）可见；
4. 编排器 `_dispatch` 把 id 注入 `_meta`，`set_reminder` 以
   `tool:session:message_id:args_hash` 走 `runtime_plane.effect_once`。

副作用只在**真实创建成功**后记账（producer 抛异常 → 撤销占位、允许重试）；
无 message_id（web/console/主动线）不去重——宁可不 dedup，也不拿别的消息的键
误吞本轮的提醒。数据全部合成（tmp 库 + 沙箱控制面，conftest 已重定向）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from orchestrator.optimized_orchestrator import OptimizedOrchestrator
from proactive import runtime_plane as rp
from shisi.memory.legacy.structured_memory import StructuredMemory
from tools.base_tool import ToolResult
from tools.builtin.reminder_tool import ReminderTool
from utils.inbound_context import bind_message_id, current_message_id
from utils.local_time import now_local

SESSION = "7:peer_a@im.wechat"


def _future_trigger(days: int = 1) -> str:
    return (now_local() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(db_path=str(tmp_path / "t.db"))
    yield memory
    memory.close()


@pytest.fixture(autouse=True)
def plane_tmp(tmp_path):
    """副作用幂等账落在 per-test 临时库（conftest 已兜底，这里再钉一次并复原）。"""
    prev = rp.db_path()
    rp.set_db_path(tmp_path / "runtime_plane.db")
    yield
    rp.set_db_path(prev)


# ── 1. 请求级入站身份 ──────────────────────────────────────


def test_context_binds_only_inside_scope():
    assert current_message_id() == ""
    with bind_message_id("m-1") as mid:
        assert mid == "m-1" and current_message_id() == "m-1"
    assert current_message_id() == "", "退出作用域必须复位（否则跨请求串身份）"


def test_context_empty_id_is_not_bound():
    with bind_message_id(""):
        assert current_message_id() == ""


# ── 2. 微信入口把 msg_id 交给用户管理器 ────────────────────


def test_call_user_manager_forwards_message_id():
    from wechat_direct.wechat_connector import _call_user_manager

    seen: dict = {}

    class _Mgr:
        async def process_message(self, user_id, text, **kwargs):
            seen["args"] = (user_id, text)
            seen["kwargs"] = kwargs
            return {"reply": "好"}

    out = _call_user_manager(
        _Mgr(), SESSION, "明早六点叫我", None, None, message_id="1001",
    )
    assert out == {"reply": "好"}
    assert seen["args"] == (SESSION, "明早六点叫我")
    assert seen["kwargs"].get("message_id") == "1001", (
        "入站 id 必须在**跨线程/共享循环这一跳**显式传下去（ContextVar 不随之传播）"
    )


def test_call_user_manager_without_id_does_not_pass_empty_kwarg():
    """无 id 时不塞空键——旧管理器签名不含该形参，多余关键字会直接 TypeError。"""
    from wechat_direct.wechat_connector import _call_user_manager

    seen: dict = {}

    class _Mgr:
        async def process_message(self, user_id, text, **kwargs):
            seen["kwargs"] = kwargs
            return {"reply": "嗯"}

    _call_user_manager(_Mgr(), SESSION, "在吗", None, None, message_id="")
    assert "message_id" not in seen["kwargs"]


# ── 3. 调度器在回合起点绑定 ────────────────────────────────


async def _scheduler_sees(message_id: str) -> str:
    from user_scheduler import UserManager

    seen: dict = {}

    async def _inner(self, user_id, text, message_type, attachments, reply_sender):
        seen["id"] = current_message_id()
        return {"reply": "ok"}

    mgr = UserManager(SimpleNamespace())
    mgr._process_message_inner = _inner.__get__(mgr, UserManager)
    await mgr.process_message(SESSION, "六点叫我", message_id=message_id)
    return str(seen.get("id", "__missing__"))


def test_process_message_binds_inbound_id_for_whole_round():
    assert asyncio.run(_scheduler_sees("m-9")) == "m-9"


def test_process_message_without_id_leaves_context_empty():
    assert asyncio.run(_scheduler_sees("")) == ""


# ── 4. 编排器注入 _meta + set_reminder 幂等 ────────────────


class _Tools:
    class _Registry:
        def __init__(self, tools):
            self._t = tools

        def get(self, name):
            return self._t.get(name)

        def get_tools_by_permission(self, affinity_level: int = 0):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema,
                    },
                }
                for t in self._t.values()
            ]

    def __init__(self, tools):
        self.registry = _Tools._Registry(tools)
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, name, arguments, affinity_level=0, caller_id=""):
        self.calls.append((name, arguments))
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(False, error="not_found")
        return tool.execute(**arguments)


def _reminder_llm(log: list):
    payload = {
        "content": "",
        "tool_calls": [{
            "function": {
                "name": "set_reminder",
                "arguments": json.dumps({
                    "content": "叫我起床",
                    "trigger_time": _future_trigger(),
                }),
            }
        }],
    }

    async def chat_with_tools(**kwargs):
        log.append(kwargs)
        return payload

    return SimpleNamespace(chat_with_tools=chat_with_tools)


def _orch(sm_obj, tools, llm):
    orch = OptimizedOrchestrator()
    orch.components = {
        "tools": tools,
        "memory": SimpleNamespace(structured_memory=sm_obj),
    }
    return orch


def test_dispatch_injects_inbound_id_into_meta(sm):
    """工具归属由服务端注入：LLM 不可决定 message_id，缺它则副作用无从去重。"""
    tools = _Tools({"set_reminder": ReminderTool(sm)})
    llm = _reminder_llm([])
    orch = _orch(sm, tools, llm)
    with bind_message_id("m-77"):
        asyncio.run(orch._run_tools_if_needed(
            llm, "明早六点叫我起床", "sys", [], affinity_level=2,
            session_key=SESSION, user_id=7,
        ))
    assert tools.calls and tools.calls[0][0] == "set_reminder"
    assert tools.calls[0][1]["_meta"]["message_id"] == "m-77"


def _run_round(sm: StructuredMemory, *, message_id: str) -> int:
    """以给定入站 id 走一遍真实终审→派发→落库，返回该会话提醒行数。"""
    tools = _Tools({"set_reminder": ReminderTool(sm)})
    llm = _reminder_llm([])
    orch = _orch(sm, tools, llm)
    with bind_message_id(message_id):
        asyncio.run(orch._run_tools_if_needed(
            llm, "明早六点叫我起床", "sys", [], affinity_level=2,
            session_key=SESSION, user_id=7,
        ))
    return len(sm.get_pending_reminders(session_key=SESSION))


def test_same_message_id_creates_exactly_one_reminder(sm):
    """🔴 验收项：相同 message_id 只产生 1 个提醒（重放/守卫补跑同键）。"""
    assert _run_round(sm, message_id="m-dup") == 1
    assert _run_round(sm, message_id="m-dup") == 1, (
        "同一入站消息重放又建了一条提醒（用户被叫两次）"
    )


def test_different_message_ids_create_two(sm):
    """两条不同消息各建一条——去重不得跨消息吞掉后来的托付。"""
    assert _run_round(sm, message_id="m-a") == 1
    assert _run_round(sm, message_id="m-b") == 2


def test_no_message_id_never_dedups(sm):
    """无入站 id（web/console）时不去重：宁可不 dedup，也不误吞本轮提醒。"""
    assert _run_round(sm, message_id="") == 1
    assert _run_round(sm, message_id="") == 2


def test_replay_reports_the_original_reminder_not_a_new_one(sm):
    """重放仍回成功与**原** reminder_id（模型据此说"已设好"不是撒谎）。"""
    first = ReminderTool(sm).execute(
        content="喝水", trigger_time=_future_trigger(),
        _meta={"session_key": SESSION, "user_id": 7, "message_id": "m-r"},
    )
    assert first.success is True
    replay = ReminderTool(sm).execute(
        content="喝水", trigger_time=_future_trigger(),
        _meta={"session_key": SESSION, "user_id": 7, "message_id": "m-r"},
    )
    assert replay.success is True
    assert replay.data["reminder_id"] == first.data["reminder_id"]
    assert replay.data["deduplicated"] is True
    assert first.data.get("deduplicated") is not True
    assert len(sm.get_pending_reminders(session_key=SESSION)) == 1


def test_different_slots_in_one_message_both_created(sm):
    """一条消息托付两件事（六点和七点）：args 不同 → 两条都建，不得互相吞。"""
    tool = ReminderTool(sm)
    meta = {"session_key": SESSION, "user_id": 7, "message_id": "m-two"}
    for hh in ("06", "07"):
        day = (now_local() + timedelta(days=1)).strftime("%Y-%m-%d")
        res = tool.execute(
            content=f"叫我（{hh}点）", trigger_time=f"{day} {hh}:00", _meta=meta,
        )
        assert res.success is True
    assert len(sm.get_pending_reminders(session_key=SESSION)) == 2


def test_failed_creation_does_not_reserve_the_slot(sm):
    """🔴 处理前不标记成功：真实创建抛错 → 撤销占位，下一次同 id 仍能建。"""
    calls: list[int] = []
    real_add = sm.add_reminder

    def _flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("库写入失败")
        return real_add(*args, **kwargs)

    monkey_sm = SimpleNamespace(
        add_reminder=_flaky,
        get_pending_reminders=sm.get_pending_reminders,
    )
    tool = ReminderTool(monkey_sm)
    meta = {"session_key": SESSION, "user_id": 7, "message_id": "m-retry"}
    first = tool.execute(content="叫我", trigger_time=_future_trigger(), _meta=meta)
    assert first.success is False
    second = ReminderTool(sm).execute(
        content="叫我", trigger_time=_future_trigger(), _meta=meta,
    )
    assert second.success is True
    assert len(sm.get_pending_reminders(session_key=SESSION)) == 1
