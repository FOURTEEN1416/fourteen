"""可观测性 + 工具账本批（2026-11-21）回归。

钉住四件事：
1. APScheduler 的 ``Running job`` / ``executed successfully`` INFO 噪声被压到
   WARNING，短 job 只留 DEBUG；ERROR/WARNING 仍可见。
2. structlog 的位置参数在落盘前完成 ``event % args`` 渲染，不再生产
   ``positional_args`` 未渲染 dict；llm_gateway 冷却日志自身也先格式化。
3. 工具调用与工具结果在 ``event_ledger`` 中一一对应：主链 dispatch 与
   画像同步直连 dispatch 两条路径都写 ``call_id`` + 成功/失败。
4. 失败工具结果必须落账（``success=False`` + ``error``），供回放定位。

本文件禁止依赖真实 ``data/agent_plane.db``：所有账本断言都切到 tmp_path。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from shisi.agent_plane.event_ledger import (
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EventLedger,
    default_ledger,
    set_default_ledger,
)

SESSION = "1:peer-observability@im.wechat"
TURN = "turn-observability-1"


# ---------------------------------------------------------------------------
# 1. 日志级别 / 格式
# ---------------------------------------------------------------------------


def test_apscheduler_info_is_quiet_and_positional_args_are_rendered() -> None:
    """真实调用 setup_logging：apscheduler 生效级别必须是 WARNING。

    用子进程隔离 structlog 的全局 configure（cache_logger_on_first_use 会
    污染同进程其它用例）；同时验证一条 ``%s`` 位置参数日志落盘后是渲染文本。
    """
    project_root = Path(__file__).resolve().parents[1]
    code = "\n".join(
        [
            "import logging",
            "from observability import logging_setup as ls",
            "ls._add_rotating_file_handler = lambda *a, **k: None",
            "ls.setup_logging('INFO', 'json')",
            "print('APSCHEDULER_LEVEL=%d' % logging.getLogger('apscheduler').level)",
            "print('APSCHEDULER_CHILD_LEVEL=%d' % logging.getLogger('apscheduler.executors.default').getEffectiveLevel())",
            "ls.get_logger('llm_gateway').warning("
            "'Model %s entering cooldown for %.0fs', 'deepseek-chat', 10.0)",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert "APSCHEDULER_LEVEL=30" in lines, proc.stdout
    assert "APSCHEDULER_CHILD_LEVEL=30" in lines, proc.stdout

    payloads = []
    for line in lines:
        line = line.strip()
        if line.startswith("{"):
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    assert payloads, f"没有捕获到 structlog JSON 行：{proc.stdout!r}"
    rendered = payloads[-1]
    assert rendered["event"] == "Model deepseek-chat entering cooldown for 10s", rendered
    assert "positional_args" not in rendered, rendered


def test_llm_gateway_cooldown_event_is_preformatted(monkeypatch) -> None:
    """llm_gateway 自身先 ``event % args``，不把 %s 和参数交给 structlog。"""
    from llm_provider import llm_gateway as lg

    captured: list[tuple[str, tuple]] = []

    class _CaptureLogger:
        def warning(self, event: str, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            captured.append((event, args))

    monkeypatch.setattr(lg, "logger", _CaptureLogger())
    entry = lg.ModelEntry(
        name="deepseek-chat", priority=1, max_retries=1, cooldown_seconds=10.0
    )
    entry.mark_failed()

    assert captured == [("Model deepseek-chat entering cooldown for 10s", ())], captured


# ---------------------------------------------------------------------------
# 2. event_ledger：主链 dispatch 的 call/result 对称
# ---------------------------------------------------------------------------


class _Registry:
    def __init__(self) -> None:
        self._instances: dict[str, object] = {}

    def get(self, name: str):
        return self._instances.get(name)


def _tools_stub(tool_result):
    from tools.base_tool import ToolResult

    class _Tools:
        registry = _Registry()

        def dispatch(self, name: str, args: dict, **kwargs):
            return tool_result if tool_result is not None else ToolResult(True, {"ok": 1})

    return _Tools()


def _dispatch(tools, *, call_id: str, name: str = "weather"):
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    carrier = SimpleNamespace(components={})
    tc = {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps({"city": "杭州"})},
    }
    return asyncio.run(
        OptimizedOrchestrator._dispatch_tool_call(
            carrier,
            tc,
            tools=tools,
            session_key=SESSION,
            user_id=1,
            turn_id=TURN,
            character_id="c1",
        )
    )


def test_dispatch_tool_call_and_result_are_symmetric(tmp_path, monkeypatch) -> None:
    """一次成功 + 一次失败 → 2 条 tool_call / 2 条 tool_result，call_id 对齐。"""
    # agent_core 影子账本是另一本账，本测只钉 shisi.agent_plane.event_ledger。
    from agent_core import events as agent_events
    from tools.base_tool import ToolResult

    monkeypatch.setattr(agent_events, "record_event", lambda *a, **k: None)

    ledger = EventLedger(tmp_path / "ledger.db")
    old = default_ledger()
    set_default_ledger(ledger)
    try:
        ok = _dispatch(_tools_stub(ToolResult(True, {"temperature": 21})), call_id="c-ok")
        failed = _dispatch(
            _tools_stub(ToolResult(False, error="boom")), call_id="c-fail"
        )

        calls = ledger.query(session_key=SESSION, event_type=EVENT_TOOL_CALL)
        results = ledger.query(session_key=SESSION, event_type=EVENT_TOOL_RESULT)

        assert ok["success"] is True
        assert failed["success"] is False
        assert len(calls) == 2, f"tool_call 数量不对：{len(calls)}"
        assert len(results) == 2, f"tool_result 数量不对：{len(results)}（call/result 不对称）"
        assert [e.payload["call_id"] for e in calls] == ["c-ok", "c-fail"]
        assert [e.payload["call_id"] for e in results] == ["c-ok", "c-fail"]
        assert results[0].payload["success"] is True
        assert results[0].payload["tool"] == "weather"
        assert results[1].payload["success"] is False
        assert results[1].payload["error"] == "boom"

        bundle = ledger.replay(session_key=SESSION, turn_id=TURN)
        assert [op["type"] for op in bundle.tool_ops] == [
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
        ], bundle.tool_ops
    finally:
        set_default_ledger(old)


# ---------------------------------------------------------------------------
# 3. event_ledger：画像同步直连 dispatch 的 call/result 对称
# ---------------------------------------------------------------------------


def test_profile_sync_direct_dispatch_records_call_and_result(
    tmp_path, monkeypatch
) -> None:
    """``apply_profile_sync_calls`` 绕过 orchestrator，也必须双账对称。"""
    from tools.base_tool import ToolResult
    from tools.builtin import profile_agent_tools as pa

    class _FakeDispatcher:
        def dispatch(self, name: str, args: dict, **kwargs):
            if name == "remember_facts":
                return ToolResult(True, {"fact_id": 1})
            return ToolResult(False, error="profile_write_denied")

    monkeypatch.setattr(pa, "_profile_sync_dispatcher", lambda: _FakeDispatcher())

    ledger = EventLedger(tmp_path / "profile_ledger.db")
    old = default_ledger()
    set_default_ledger(ledger)
    try:
        out = pa.apply_profile_sync_calls(
            SESSION,
            [
                {
                    "id": "p-ok",
                    "function": {"name": "remember_facts", "arguments": "{}"},
                },
                {
                    "id": "p-fail",
                    "function": {"name": "update_user_profile", "arguments": "{}"},
                },
            ],
            None,
            character_id="c1",
            turn_id="turn-profile-1",
        )

        calls = ledger.query(
            session_key=SESSION, event_type=EVENT_TOOL_CALL, turn_id="turn-profile-1"
        )
        results = ledger.query(
            session_key=SESSION, event_type=EVENT_TOOL_RESULT, turn_id="turn-profile-1"
        )
        assert len(out) == 2
        assert len(calls) == 2, f"直连 dispatch 漏 tool_call：{len(calls)}"
        assert len(results) == 2, f"直连 dispatch 漏 tool_result：{len(results)}"
        assert [e.payload["call_id"] for e in calls] == ["p-ok", "p-fail"]
        assert [e.payload["call_id"] for e in results] == ["p-ok", "p-fail"]
        assert results[0].payload["success"] is True
        assert results[1].payload["success"] is False
        assert results[1].payload["error"] == "profile_write_denied"
    finally:
        set_default_ledger(old)


# ---------------------------------------------------------------------------
# 4. 账号清理：channel_state 的 `channel:<uid>:<slot>` 键形必须纳入 owner 级清理
# ---------------------------------------------------------------------------


def test_owner_purge_and_count_include_channel_state_keys(tmp_path) -> None:
    from shisi.agent_plane.event_ledger import EVENT_CHANNEL_STATE

    ledger = EventLedger(tmp_path / "owner_scope.db")
    ledger.append(session_key="9:web:abc", event_type="user_message")
    ledger.append(session_key="channel:9:0", event_type=EVENT_CHANNEL_STATE)
    ledger.append(session_key="channel:9:1", event_type=EVENT_CHANNEL_STATE)
    ledger.append(session_key="channel:10:0", event_type=EVENT_CHANNEL_STATE)
    ledger.append(session_key="10:web:def", event_type="user_message")

    assert ledger.count_owner_sessions(9) == 3
    assert ledger.purge_owner_sessions(9, ["9:web:abc"]) == 3
    assert ledger.count_owner_sessions(9) == 0
    assert ledger.count_owner_sessions(10) == 2
