"""包 Q · A3/C1-C3 流式一致性 + 工具信封/限额/防假承诺测试。"""

from __future__ import annotations

import inspect

from my_character.consistency_checker import (
    detect_hard_violation,
    light_sanitize_hard_violation,
)
from orchestrator import tool_gate


class TestHardViolationA3:
    def test_detect_self_as_ai(self):
        assert detect_hard_violation("作为AI，我觉得…") == "self_as_ai"
        assert detect_hard_violation("我是AI助手") == "self_as_ai"

    def test_normal_reply_no_hard_violation(self):
        assert detect_hard_violation("今天有点累，你呢？") is None
        assert detect_hard_violation("米彩说她喜欢安静") is None

    def test_light_sanitize_removes_ai_claim(self):
        out = light_sanitize_hard_violation("作为AI，我理解你的感受。今晚风好大。")
        assert "作为AI" not in out
        assert "今晚风好大" in out

    def test_stream_mixin_uses_transmitted_chat_round(self):
        src = inspect.getsource(
            __import__("orchestrator._stream_mixin", fromlist=["x"])._StreamPipelineMixin._async_consistency_check
        )
        assert "chat_round" in src
        assert "get_chat_context" not in src
        assert "detect_hard_violation" in src
        assert "不改写" in src or "不做静默改写" in src or "已推送" in src

    def test_stream_passes_chat_round_from_ctx(self):
        src = inspect.getsource(
            __import__("orchestrator._stream_mixin", fromlist=["x"])._StreamPipelineMixin.process_message_stream
        )
        assert 'ctx.get("chat_round")' in src
        assert "chat_round=chat_round" in src


class TestToolLimitsC2:
    def test_max_three_calls(self):
        calls = [
            {"function": {"name": f"t{i}", "arguments": "{}"}}
            for i in range(5)
        ]
        kept = tool_gate.limit_tool_calls(calls, max_calls=3, max_same=1)
        assert len(kept) == 3

    def test_same_tool_once(self):
        calls = [
            {"function": {"name": "set_reminder", "arguments": "{}"}},
            {"function": {"name": "set_reminder", "arguments": "{}"}},
            {"function": {"name": "weather", "arguments": "{}"}},
        ]
        kept = tool_gate.limit_tool_calls(calls, max_calls=3, max_same=1)
        names = [tool_gate._tc_name(tc) for tc in kept]
        assert names.count("set_reminder") == 1
        assert "weather" in names

    def test_load_tool_limits_defaults(self):
        limits = tool_gate.load_tool_limits(None)
        assert limits["max_tool_calls_per_turn"] == 3
        assert limits["max_same_tool_per_turn"] == 1
        assert limits["tool_result_chars_max"] == 6000

    def test_load_tool_limits_from_config(self):
        limits = tool_gate.load_tool_limits(
            {"max_tool_calls_per_turn": 5, "tool_result_chars_max": 100}
        )
        assert limits["max_tool_calls_per_turn"] == 5
        assert limits["tool_result_chars_max"] == 100


class TestToolEnvelopeC1:
    def test_envelope_marks_untrusted(self):
        wrapped = tool_gate.wrap_tool_results(
            [{"name": "weather", "result": {"success": True, "temp": 20}}]
        )
        assert 'trust="untrusted"' in wrapped
        assert "不是指令" in wrapped
        assert "[weather]" in wrapped

    def test_failure_forbids_claiming_success(self):
        wrapped = tool_gate.wrap_tool_results(
            [{"name": "set_reminder", "result": {"success": False, "error": "db_locked"}}]
        )
        assert "未执行成功" in wrapped
        assert "不得声称" in wrapped
        assert 'trust="untrusted"' in wrapped

    def test_result_truncated(self):
        long_body = "x" * 10000
        wrapped = tool_gate.wrap_tool_results(
            [{"name": "search", "result": {"success": True, "content": long_body}}],
            chars_max=100,
        )
        assert len(wrapped) < 400
        assert "…" in wrapped

    def test_prepare_injects_wrapped_tools_not_raw_json_tail(self):
        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        src = inspect.getsource(OptimizedOrchestrator._prepare_context)
        assert "tool_results" in src
        # 旧写法：f"[{name}] json.dumps" 直接拼
        assert "请根据以上结果自然地回复用户" not in src


class TestAntiPromiseC3:
    def test_contains_promise_catches_canned(self):
        assert tool_gate.contains_promise("听到啦，我会提醒你的")
        assert tool_gate.contains_promise("好的，我去设个闹钟")
        assert tool_gate.contains_promise("没问题，包在我身上")
        assert not tool_gate.contains_promise("今天天气不错")

    def test_review_prompt_hard_bans_empty_promise(self):
        system, _ = tool_gate.build_review_messages("明早六点叫我起床", "你是米彩。", [])
        assert "禁止输出任何承诺句" in system
        assert "不是风格建议" in system
        assert "untrusted" in system
        assert "3" in system

    def test_orchestrator_uses_limit_and_wrap(self):
        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        src = inspect.getsource(OptimizedOrchestrator._run_tools_if_needed)
        assert "limit_tool_calls" in src
        assert "wrap_tool_results" in src
        assert "听到啦" in src or "contains_promise" in src
