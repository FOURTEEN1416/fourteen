"""W3 块5 —— 提醒生命周期（缺陷 F）。

钉的行为：提醒落库后必须能被**本人**取消、改期、查到 failed、显式恢复。

旧现实（2026-09-27 排查）：`reminders` 表只有 `add_reminder` /
`get_pending_reminders` / `get_due_reminders` / `mark_reminder_result` 四个入口
—— 写入与投递之间有路，**生命周期没有任何一条路**：

- 用户说「那个提醒取消吧」→ 无工具、无 SQL 入口，只能等它到点发一条废话；
- 说时间说错了「改到八点」→ 只能再设一条，旧的仍在，**同一天被叫两次**；
- 3 次投递失败判死（`status='failed'`、`active=0`，DECISION_LEDGER:115 是刻意
  的，不得改成无限重试）→ 但 `get_pending_reminders` 的 WHERE 带 `active=1`，
  判死后**用户与管理员都查不到**，它只是静默消失；
- 判死后用户明确说「还是叫我一声」→ 无任何入口能恢复。

隔离边界同时钉死：所有生命周期操作都以 `session_key` 为归属谓词（本人），
跨会话按 id 操作必须改不到（生产多用户共用一张 reminders 表）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

import orchestrator._init_mixin as _init_mixin
from shisi.memory.legacy.structured_memory import StructuredMemory
from tools.builtin.reminder_tool import (
    CalendarQueryTool,
    ReminderManageTool,
    ReminderTool,
)
from utils.local_time import now_local
from utils.project_paths import project_path


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(db_path=str(tmp_path / "reminders.db"))
    yield memory
    memory.close()


def _future(days: int = 1, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return (now_local() + timedelta(days=days)).strftime(fmt)


def _meta(key: str, user_id: int | None = 7):
    return {"_meta": {"session_key": key, "user_id": user_id}}


A = "7:wx_a@im.wechat"
B = "8:wx_b@im.wechat"


# ── 数据层 ────────────────────────────────────────────────


def test_cancel_reminder_only_touches_own_session(sm):
    rid = sm.add_reminder("喝水", _future(), session_key=A, user_id=7)
    other = sm.add_reminder("开会", _future(), session_key=B, user_id=8)

    assert sm.cancel_reminder(rid, session_key=A) is True
    row = sm.get_reminder(rid)
    assert row["status"] == "cancelled" and row["active"] == 0
    # 越权：B 取消 A 的提醒必须失败，且 A 的行不动
    assert sm.cancel_reminder(rid, session_key=B) is False
    assert sm.get_reminder(rid)["status"] == "cancelled"
    assert sm.cancel_reminder(other, session_key=A) is False
    assert sm.get_reminder(other)["status"] == "pending"


def test_cancelled_reminder_is_never_delivered(sm):
    rid = sm.add_reminder("喝水", "2020-01-01 00:00:00", session_key=A, user_id=7)
    assert sm.get_due_reminders()  # 到期且未取消 → 可投
    sm.cancel_reminder(rid, session_key=A)
    assert sm.get_due_reminders() == []


def test_reschedule_moves_trigger_and_keeps_pending(sm):
    rid = sm.add_reminder("喝水", _future(), session_key=A, user_id=7)
    new_time = _future(days=3)

    row = sm.reschedule_reminder(rid, session_key=A, trigger_time=new_time)
    assert row is not None
    assert row["trigger_time"] == new_time
    assert row["status"] == "pending" and row["active"] == 1
    # 越权改期不得生效
    assert sm.reschedule_reminder(rid, session_key=B, trigger_time=_future(days=9)) is None
    assert sm.get_reminder(rid)["trigger_time"] == new_time


def test_reschedule_requeues_cancelled_or_failed(sm):
    """改期隐含重新排队：用户对一条已取消/判死的提醒说「改到八点再叫我」= 再次显式托付。

    这不是投递层的自动重试（DECISION_LEDGER:115 的 3 次判死仍然成立）——
    每一步都由用户开口。
    """
    rid = sm.add_reminder("喝水", "2020-01-01 00:00:00", session_key=A, user_id=7)
    assert sm.get_due_reminders()
    sm.cancel_reminder(rid, session_key=A)
    assert sm.get_due_reminders() == []

    new_time = _future(days=2)
    row = sm.reschedule_reminder(rid, session_key=A, trigger_time=new_time)
    assert row is not None
    assert row["status"] == "pending" and row["active"] == 1
    assert sm.get_reminder(rid)["trigger_time"] == new_time
    # 重新排队后按**新**时刻待投，不会把已过期那一拍立刻补发
    assert sm.get_due_reminders() == []


def test_failed_reminder_hidden_until_explicit_restore(sm):
    """判死是刻意的（3 次失败不再重试），但**必须可查**；恢复必须显式。"""
    rid = sm.add_reminder("喝水", "2020-01-01 00:00:00", session_key=A, user_id=7)
    assert sm.get_due_reminders()  # 未判死时到期即投（对照）
    for _ in range(3):
        sm.mark_reminder_result(rid, delivered=False)
    row = sm.get_reminder(rid)
    assert row["status"] == "failed" and row["active"] == 0

    # 可查：按状态查得到（旧实现只有 active=1 的 pending 视图，判死即静默消失）
    listed = sm.list_reminders(session_key=A, status="failed")
    assert [r["id"] for r in listed] == [rid]
    # 到期轮询绝不投（判死未被绕过 = 不引入无限重试）
    assert sm.get_due_reminders() == []

    # 显式恢复：回到 pending、清失败计数，且重新可被轮询取到
    assert sm.restore_reminder(rid, session_key=A) is True
    restored = sm.get_reminder(rid)
    assert restored["status"] == "pending" and restored["active"] == 1
    assert restored["fail_count"] == 0
    assert [r["id"] for r in sm.get_due_reminders()] == [rid]
    assert sm.restore_reminder(rid, session_key=B) is False


def test_restore_does_not_resurrect_delivered(sm):
    rid = sm.add_reminder("喝水", _future(), session_key=A, user_id=7)
    sm.mark_reminder_result(rid, delivered=True)
    assert sm.restore_reminder(rid, session_key=A) is False
    assert sm.get_reminder(rid)["status"] == "delivered"
    # 已送达的提醒重新排队 = 同一天再叫一次；恢复只针对 failed/cancelled
    assert sm.get_due_reminders() == []


def test_list_reminders_scoped_to_session(sm):
    sm.add_reminder("A的", _future(), session_key=A, user_id=7)
    sm.add_reminder("B的", _future(), session_key=B, user_id=8)
    ids = [r["content"] for r in sm.list_reminders(session_key=A)]
    assert ids == ["A的"]
    # 无归属谓词一律拒绝（与 query_reminders 的 missing_session_key 同纪律）
    with pytest.raises(ValueError):
        sm.list_reminders(session_key="")


# ── 工具层（LLM 可用的生命周期入口）──────────────────────


def test_manage_tool_requires_call_context(sm):
    sm.add_reminder("喝水", _future(), session_key=A, user_id=7)
    result = ReminderManageTool(sm).execute(action="cancel", reminder_id=1)
    assert result.success is False
    assert result.error == "missing_session_key"


def test_manage_tool_cancel_and_reschedule_and_list(sm):
    created = ReminderTool(sm).execute(
        content="喝水", trigger_time=_future(days=1, fmt="%Y-%m-%d %H:%M"), **_meta(A)
    )
    rid = created.data["reminder_id"]

    tool = ReminderManageTool(sm)
    assert tool.execute(action="reschedule", reminder_id=rid,
                        trigger_time=_future(days=2, fmt="%Y-%m-%d %H:%M"),
                        **_meta(A)).success is True
    assert tool.execute(action="cancel", reminder_id=rid, **_meta(B)).success is False
    ok = tool.execute(action="cancel", reminder_id=rid, **_meta(A))
    assert ok.success is True and ok.data["status"] == "cancelled"

    # 默认视图看不到已取消；include_finished 才回全生命周期
    pending = CalendarQueryTool(sm).execute(**_meta(A))
    assert pending.data == []
    full = CalendarQueryTool(sm).execute(include_finished=True, **_meta(A))
    assert [r["status"] for r in full.data] == ["cancelled"]


def test_manage_tool_rejects_bad_trigger_time(sm):
    original = _future()
    rid = sm.add_reminder("喝水", original, session_key=A, user_id=7)
    result = ReminderManageTool(sm).execute(
        action="reschedule", reminder_id=rid, trigger_time="明晚八点", **_meta(A)
    )
    assert result.success is False
    assert "trigger_time" in str(result.error)
    # 校验失败必须在落库前拒绝：改期不能把原时刻写坏
    assert sm.get_reminder(rid)["trigger_time"] == original


def test_manage_tool_restore_reports_requeued(sm):
    rid = sm.add_reminder("喝水", _future(), session_key=A, user_id=7)
    for _ in range(3):
        sm.mark_reminder_result(rid, delivered=False)
    result = ReminderManageTool(sm).execute(action="restore", reminder_id=rid, **_meta(A))
    assert result.success is True
    assert sm.get_reminder(rid)["status"] == "pending"


def test_manage_tool_unknown_action_and_missing_id(sm):
    tool = ReminderManageTool(sm)
    assert tool.execute(action="delete", reminder_id=1, **_meta(A)).success is False
    assert tool.execute(action="cancel", **_meta(A)).success is False


def test_manage_tool_registered_as_tool_with_public_permission():
    """工具必须能被终审调用：public 权限 + 注入调用归属 + schema 动作枚举齐全。"""
    assert ReminderManageTool.name == "manage_reminder"
    assert ReminderManageTool.permission_level == "public"
    assert ReminderManageTool.wants_call_context is True
    props = ReminderManageTool.parameters_schema["properties"]
    assert set(props["action"]["enum"]) == {"cancel", "reschedule", "restore"}
    required = ReminderManageTool.parameters_schema["required"]
    assert "action" in required and "reminder_id" in required


def test_orchestrator_registers_manage_reminder():
    """注册面缺一条 = 工具写了却永远调不到（v1.30 权限门槛事故同族）。"""
    src = Path(_init_mixin.__file__).read_text(encoding="utf-8")
    assert "manage_reminder" in src, "orchestrator 未注册 manage_reminder"
    cfg = yaml.safe_load(project_path("config", "system.yaml").read_text(encoding="utf-8"))
    assert "manage_reminder" in cfg["tools"]["builtin_tools"]
