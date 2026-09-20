"""生产事故回归：set_reminder 权限 + pending 不得假完成。"""

from __future__ import annotations

from pathlib import Path

from tools.base_tool import ToolDispatcher, ToolRegistry
from tools.builtin.reminder_tool import CalendarQueryTool, ReminderTool


def test_reminder_tools_are_public():
    assert ReminderTool.permission_level == "public"
    assert CalendarQueryTool.permission_level == "public"


def test_set_reminder_works_at_zero_affinity(tmp_path):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "r.db"))
    try:
        reg = ToolRegistry()
        reg.register(ReminderTool(sm))
        disp = ToolDispatcher(reg, rate_limit_per_minute=10)
        result = disp.dispatch(
            "set_reminder",
            {
                "content": "叫我起床",
                "trigger_time": "2026-09-22 06:00",
                "_meta": {"session_key": "2:p@im.wechat", "user_id": 2},
            },
            affinity_level=0,
        )
        assert result.success is True
        due = sm.get_pending_reminders(session_key="2:p@im.wechat")
        assert any("叫我起床" in (r.get("content") or "") for r in due)
    finally:
        sm.close()


def test_set_reminder_denied_at_zero_affinity_would_fail_if_friend():
    """钉住：若误改回 friend，affinity=0 必失败（突变防护）。"""
    import tempfile

    from shisi.memory.legacy.structured_memory import StructuredMemory
    from tools.base_tool import ToolDispatcher
    from tools.base_tool import ToolRegistry as R

    class FriendReminder(ReminderTool):
        permission_level = "friend"

    tmp = Path(tempfile.mkdtemp()) / "r2.db"
    sm = StructuredMemory(str(tmp))
    try:
        reg = R()
        reg.register(FriendReminder(sm))
        disp = ToolDispatcher(reg, rate_limit_per_minute=10)
        result = disp.dispatch(
            "set_reminder",
            {"content": "x", "trigger_time": "2026-09-22 06:00",
             "_meta": {"session_key": "1:a@im.wechat"}},
            affinity_level=0,
        )
        assert result.success is False
        assert "permission" in str(result.error).lower()
    finally:
        sm.close()


def test_tool_gate_prompt_requires_set_reminder():
    import inspect

    from orchestrator import tool_gate

    raw = inspect.getsource(tool_gate)
    assert "set_reminder" in raw
    assert "禁止只调 calendar" in raw or "必须调用 set_reminder" in raw


def test_orchestrator_does_not_fulfill_pending_before_tool_success():
    import inspect

    from orchestrator import optimized_orchestrator as orch

    src = inspect.getsource(orch.OptimizedOrchestrator)
    # 不得再出现「real_calls 分支开头无条件 resolve fulfilled」
    assert "if pending and sm is not None:\n                sm.resolve_pending_intent(session_key, \"fulfilled\")" not in src
    assert "set_reminder 执行失败" in src or "pending 不结案" in src
