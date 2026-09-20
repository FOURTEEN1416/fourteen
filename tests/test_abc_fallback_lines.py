"""包 Q · A2 系统旁路句角色化测试。"""

from __future__ import annotations

from my_character.counter_rebuttal import CounterRebuttal
from utils.fallback_lines import (
    get_fallback_line,
    inject_rebuttal_constraint,
    reset_daily_dedup,
)
from utils.reply_mode import REPLY_MODE_IMMERSIVE


def setup_function():
    reset_daily_dedup()


class TestFallbackLines:
    def test_empty_reply_no_brackets_immersive(self):
        line = get_fallback_line(None, "empty_reply", REPLY_MODE_IMMERSIVE)
        assert line
        assert "（" not in line and "(" not in line

    def test_timeout_not_machine_apology(self):
        line = get_fallback_line("default", "timeout", REPLY_MODE_IMMERSIVE)
        assert "处理超时" not in line
        assert "（" not in line

    def test_exception_no_bracket_wrapper(self):
        line = get_fallback_line(None, "exception", REPLY_MODE_IMMERSIVE)
        assert not line.startswith("（")
        assert "消息处理异常" not in line

    def test_rebuttal_uses_count(self):
        line = get_fallback_line(None, "rebuttal", REPLY_MODE_IMMERSIVE, count=5)
        assert "5" in line or "五" in line
        assert "没事" in line

    def test_daily_dedup_same_kind(self):
        first = get_fallback_line("cid_a", "empty_reply", REPLY_MODE_IMMERSIVE)
        second = get_fallback_line("cid_a", "empty_reply", REPLY_MODE_IMMERSIVE)
        assert first != second

    def test_unknown_kind_falls_back(self):
        line = get_fallback_line(None, "unknown_kind_x", REPLY_MODE_IMMERSIVE)
        assert line
        assert "（" not in line

    def test_inject_rebuttal_constraint(self):
        base = "# 角色设定\n你是米彩。"
        out = inject_rebuttal_constraint(base, 5)
        assert "连续 5 次" in out
        assert "情绪关切约束" in out
        assert base in out


class TestCounterRebuttalReturnsCount:
    def test_returns_count_not_hardcoded_sentence(self):
        cr = CounterRebuttal()
        result = None
        for _ in range(CounterRebuttal.THRESHOLD):
            result = cr.check_and_increment("没事", "s1")
        assert result == CounterRebuttal.THRESHOLD
        assert not isinstance(result, str) or result.isdigit()

    def test_non_perfunctory_resets(self):
        cr = CounterRebuttal()
        cr.check_and_increment("没事", "s2")
        cr.check_and_increment("没事", "s2")
        assert cr.get_count("s2") == 2
        assert cr.check_and_increment("今天上班好累啊老板还骂人", "s2") is None
        assert cr.get_count("s2") == 0

    def test_orchestrator_rebuttal_no_longer_appends_machine_line(self):
        """静态防护：process_message 不得再把反诘写死句 append 到 reply。"""
        import inspect

        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        src = inspect.getsource(OptimizedOrchestrator.process_message)
        assert "这已经是第" not in src
        assert "你不用跟我装没事" not in src
        assert "inject_rebuttal_constraint" in src


class TestSystemFallbackRecognition:
    def test_pool_lines_are_system_placeholders(self):
        from utils.fallback_lines import is_system_fallback_line

        assert is_system_fallback_line("抱歉，处理超时，请稍后重试")
        assert is_system_fallback_line("刚才没接上，你再说一句？")
        assert is_system_fallback_line("等我一下，刚才有点卡")
        assert is_system_fallback_line("（消息处理异常，请稍后重试）")
        assert is_system_fallback_line("")
        assert not is_system_fallback_line("好的，我记下了")
        assert not is_system_fallback_line("今天上班好累啊")

    def test_memory_pipeline_uses_fallback_recognition(self):
        from shisi.memory.legacy.memory_pipeline import _is_system_error_reply

        assert _is_system_error_reply("等我一下，刚才有点卡")
        assert _is_system_error_reply("刚才没接上，你再说一句？")
        assert not _is_system_error_reply("记得带伞哦")
