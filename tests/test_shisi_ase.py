"""shisi ase 模块回归测试 — TriggerEngine dataclass 测试"""

from __future__ import annotations

from dataclasses import fields

import pytest

from shisi.ase.trigger_engine import (
    AffinityTrigger,
    EventTrigger,
    IdleTrigger,
    StageTrigger,
    TimeTrigger,
    TriggerResult,
)


class TestTimeTrigger:
    """TimeTrigger 构造与 check 测试"""

    def test_construction_defaults(self):
        """默认 priority / repeatable / triggered"""
        t = TimeTrigger(name="午休", story_time_minutes=30, message="该休息了")
        assert t.name == "午休"
        assert t.story_time_minutes == 30
        assert t.message == "该休息了"
        assert t.priority == 5
        assert t.repeatable is False
        assert t.triggered is False

    def test_check_trigger_once(self):
        """非 repeatable 触发一次后不再触发"""
        t = TimeTrigger(name="test", story_time_minutes=10, message="msg")
        assert t.check(10) is True
        assert t.triggered is True
        assert t.check(20) is False

    def test_check_not_yet(self):
        """未到时间不触发"""
        t = TimeTrigger(name="test", story_time_minutes=10, message="msg")
        assert t.check(5) is False
        assert t.triggered is False

    def test_repeatable_always_triggers(self):
        """repeatable 每次 check 都触发，但不修改 triggered 字段"""
        t = TimeTrigger(name="rep", story_time_minutes=5, message="rep", repeatable=True)
        assert t.check(5) is True
        assert t.check(6) is True
        assert t.triggered is False  # repeatable 不设置 triggered


class TestAffinityTrigger:
    """AffinityTrigger 构造与 check 测试"""

    def test_construction_defaults(self):
        """默认 direction / priority / triggered"""
        t = AffinityTrigger(name="好感1", threshold=50.0, message="好感达到50")
        assert t.name == "好感1"
        assert t.threshold == 50.0
        assert t.message == "好感达到50"
        assert t.direction == "above"
        assert t.priority == 6
        assert t.triggered is False

    def test_above_threshold_triggers(self):
        """direction=above 且亲和度 >= threshold 触发"""
        t = AffinityTrigger(name="t1", threshold=60.0, direction="above", message="达标")
        assert t.check(60.0) is True
        assert t.check(80.0) is False

    def test_below_threshold_triggers(self):
        """direction=below 且亲和度 < threshold 触发"""
        t = AffinityTrigger(name="t2", threshold=30.0, direction="below", message="过低")
        assert t.check(20.0) is True
        assert t.check(10.0) is False

    def test_not_triggered_when_below_with_above(self):
        """direction=above 但亲和度未达到不触发"""
        t = AffinityTrigger(name="t3", threshold=80.0, message="未达标")
        assert t.check(50.0) is False
        assert t.triggered is False


class TestTriggerResult:
    """TriggerResult 字段完整性测试"""

    def test_all_fields_present(self):
        """验证 TriggerResult 所有字段都存在"""
        field_names = {f.name for f in fields(TriggerResult)}
        expected = {"triggered", "trigger_type", "trigger_name", "message", "priority", "stage_name"}
        assert field_names == expected

    def test_default_values(self):
        """默认值正确"""
        r = TriggerResult()
        assert r.triggered is False
        assert r.trigger_type == ""
        assert r.trigger_name == ""
        assert r.message == ""
        assert r.priority == 0
        assert r.stage_name == ""

    def test_custom_values(self):
        """自定义构造"""
        r = TriggerResult(
            triggered=True,
            trigger_type="time",
            trigger_name="trigger_01",
            message="触发消息",
            priority=10,
            stage_name="stage_1",
        )
        assert r.triggered is True
        assert r.trigger_type == "time"
        assert r.trigger_name == "trigger_01"
        assert r.message == "触发消息"
        assert r.priority == 10
        assert r.stage_name == "stage_1"


class TestStageTrigger:
    """StageTrigger 基础测试"""

    def test_construction(self):
        t = StageTrigger(name="入场", stage_name="opening", message="开始")
        assert t.name == "入场"
        assert t.stage_name == "opening"
        assert t.on_enter is True
        assert t.priority == 8


class TestEventTrigger:
    """EventTrigger 基础测试"""

    def test_construction(self):
        t = EventTrigger(name="关键词", keywords=["帮助", "救"], message="触发救援")
        assert t.name == "关键词"
        assert len(t.keywords) == 2
        assert t.cooldown_turns == 5


class TestIdleTrigger:
    """IdleTrigger 基础测试"""

    def test_construction(self):
        t = IdleTrigger(name="离线触发", idle_minutes=15, message="回来吧")
        assert t.name == "离线触发"
        assert t.idle_minutes == 15
        assert t.priority == 3