"""单元测试: 语音触发检测模块"""

from __future__ import annotations

import pytest

from orchestrator.voice_detector import detect_voice_request


class TestDirectCommandWords:
    """场景1: 直接命令词 → 应返回 True"""

    @pytest.mark.parametrize("text", [
        "发语音",
        "语音回复",
        "发段语音",
        "发个语音",
        "语音消息",
        "语音说",
        "用语音",
        "用说的",
        "声音回复",
        "语音告诉我",
    ])
    def test_direct_commands(self, text: str) -> None:
        assert detect_voice_request(text) is True


class TestDesirePatterns:
    """场景2: 欲望/请求模式 → 应返回 True"""

    @pytest.mark.parametrize("text", [
        "想听你说话",
        "想听你说句话",
        "能不能发语音",
        "能不能发段语音",
        "可以发语音",
        "说话给我听听",
        "帮我读一下",
        "给我讲个故事",
        "用你的声音说话",
        "用声音说话",
        "听到你的声音",
        "听见你的声音",
    ])
    def test_desire_patterns(self, text: str) -> None:
        assert detect_voice_request(text) is True


class TestEmotionSpeak:
    """场景3: 情感修饰 + 说 → 应返回 True"""

    @pytest.mark.parametrize("text", [
        "温柔地说",
        "轻声地说",
        "好好说话",
        "大声地说",
        "悄悄地说",
        "慢慢地说",
        "大声说",
        "轻声说",
    ])
    def test_emotion_speak(self, text: str) -> None:
        assert detect_voice_request(text) is True


class TestCapabilityQuestions:
    """场景4: 能力询问 → 应返回 True"""

    @pytest.mark.parametrize("text", [
        "能说话吗",
        "会发声吗",
        "有语音功能",
        "可以说话么",
        "能发声么",
    ])
    def test_capability_questions(self, text: str) -> None:
        assert detect_voice_request(text) is True


class TestContextTriggers:
    """场景5: 上下文触发 → 应返回 True"""

    @pytest.mark.parametrize("text", [
        "说句话听听",
        "说句话吧",
        "说话",
        "语音吗",
        "说句话",
    ])
    def test_context_triggers(self, text: str) -> None:
        assert detect_voice_request(text) is True


class TestNegation:
    """场景6: 否定表达 → 应返回 False"""

    @pytest.mark.parametrize("text", [
        "不要发语音",
        "别说话",
        "不用语音",
        "文字就好",
        "打字吧",
        "文字才好",
        "文字更好",
    ])
    def test_negation(self, text: str) -> None:
        assert detect_voice_request(text) is False


class TestFalsePositives:
    """场景7: 应排除的误触发 → 应返回 False"""

    @pytest.mark.parametrize("text", [
        "语音识别",
        "语音输入",
        "语音导航",
        "语音搜索",
    ])
    def test_false_positives(self, text: str) -> None:
        assert detect_voice_request(text) is False


class TestEdgeCases:
    """场景8: 边界情况"""

    def test_empty_string(self) -> None:
        assert detect_voice_request("") is False

    def test_none(self) -> None:
        assert detect_voice_request(None) is False  # type: ignore[arg-type]

    def test_whitespace_only(self) -> None:
        assert detect_voice_request("   ") is False

    def test_punctuation_only(self) -> None:
        assert detect_voice_request("！！！？？？") is False

    def test_very_long_text(self) -> None:
        long_text = "今天天气真好" * 500
        assert detect_voice_request(long_text + "发语音") is True

    def test_voice_command_within_long_text(self) -> None:
        long_text = "我想问一下关于那个事情能不能" + "发段语音" + "给我听一下"
        assert detect_voice_request(long_text) is True
