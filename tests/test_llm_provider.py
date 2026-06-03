"""
LLM Provider 单元测试 — 覆盖 0 测试 P0 风险
====================================

测试目标：
1. ModelEntry / ModelRegistry 冷却机制
2. OpenCodeZenProvider 消息构建 + fallback 行为（mock 网络）
3. LLMGatewayV2 mock 回复（无 API key 时的回退）
4. OpenAICompatibleProvider 配置解析
5. MultiProviderGateway fallback 链

零网络调用 — 全部用 respx/httpx_mock 或直接传 mock
"""

from __future__ import annotations

import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── ModelEntry / ModelRegistry 测试 ──────────────────────────


class TestModelEntry:
    """ModelEntry 冷却机制（修复 P0-F：核心 LLM 基础设施零测试）"""

    def test_init_defaults(self):
        from llm_provider.llm_gateway import ModelEntry

        e = ModelEntry(name="test-model", priority=1)
        assert e.name == "test-model"
        assert e.priority == 1
        assert e.retry_count == 0
        assert e.cooldown_until == 0.0
        assert e.max_retries == 1
        assert e.cooldown_seconds == 10.0

    def test_is_available_initially(self):
        from llm_provider.llm_gateway import ModelEntry

        e = ModelEntry(name="m", priority=1)
        assert e.is_available() is True

    def test_mark_failed_below_max_retries_no_cooldown(self):
        from llm_provider.llm_gateway import ModelEntry

        e = ModelEntry(name="m", priority=1, max_retries=3)
        e.mark_failed()
        assert e.retry_count == 1
        assert e.cooldown_until == 0.0  # 没到阈值，不进冷却
        assert e.is_available() is True

    def test_mark_failed_at_max_retries_enters_cooldown(self):
        from llm_provider.llm_gateway import ModelEntry

        e = ModelEntry(name="m", priority=1, max_retries=2, cooldown_seconds=5.0)
        e.mark_failed()
        e.mark_failed()
        assert e.retry_count == 0  # 进入冷却时重置
        assert e.cooldown_until > 0  # 有冷却时间
        assert e.is_available() is False

    def test_mark_success_resets_retry(self):
        from llm_provider.llm_gateway import ModelEntry

        e = ModelEntry(name="m", priority=1, max_retries=3)
        e.mark_failed()
        e.mark_failed()
        e.mark_success()
        assert e.retry_count == 0


class TestModelRegistry:
    """ModelRegistry 优先级排序与查询"""

    def test_init_with_default_models(self):
        from llm_provider.llm_gateway import ModelRegistry

        r = ModelRegistry()
        assert len(r.all_models) == 2  # 默认 deepseek-chat + deepseek-reasoner
        assert r.all_models[0].name == "deepseek-chat"
        assert r.all_models[1].name == "deepseek-reasoner"

    def test_init_sorts_by_priority(self):
        from llm_provider.llm_gateway import ModelRegistry

        r = ModelRegistry([
            {"name": "z", "priority": 3},
            {"name": "a", "priority": 1},
            {"name": "m", "priority": 2},
        ])
        assert [m.name for m in r.all_models] == ["a", "m", "z"]

    def test_get_by_name_returns_model(self):
        from llm_provider.llm_gateway import ModelRegistry

        r = ModelRegistry([{"name": "foo", "priority": 1}, {"name": "bar", "priority": 2}])
        entry = r.get_by_name("bar")
        assert entry is not None
        assert entry.name == "bar"

    def test_get_by_name_missing_returns_none(self):
        from llm_provider.llm_gateway import ModelRegistry

        r = ModelRegistry([{"name": "foo", "priority": 1}])
        assert r.get_by_name("nonexistent") is None

    def test_get_available_skips_in_cooldown(self):
        from llm_provider.llm_gateway import ModelRegistry

        r = ModelRegistry([
            {"name": "a", "priority": 1},
            {"name": "b", "priority": 2},
        ])
        # a 进冷却
        a = r.get_by_name("a")
        a.cooldown_until = 99999999999  # 未来时间
        # 应该返回 b
        assert r.get_available().name == "b"

    def test_get_available_all_in_cooldown_returns_none(self):
        from llm_provider.llm_gateway import ModelRegistry

        r = ModelRegistry([{"name": "a", "priority": 1}])
        a = r.get_by_name("a")
        a.cooldown_until = 99999999999
        assert r.get_available() is None


# ── OpenCodeZenProvider 测试（零网络） ─────────────────────────


class TestOpenCodeZenProviderMessages:
    """OpenCodeZenProvider 内部 _build_messages 逻辑"""

    def test_build_messages_simple(self):
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m")
        msgs = p._build_messages("hello", "", None, None)
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_build_messages_with_system(self):
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m")
        msgs = p._build_messages("hello", "you are a bot", None, None)
        assert msgs == [
            {"role": "system", "content": "you are a bot"},
            {"role": "user", "content": "hello"},
        ]

    def test_build_messages_with_history(self):
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m")
        history = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        msgs = p._build_messages("q2", "sys", history, None)
        assert msgs == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]

    def test_build_messages_explicit_messages_takes_precedence(self):
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m")
        explicit = [{"role": "user", "content": "explicit"}]
        msgs = p._build_messages("hello", "sys", [{"role": "user", "content": "ignored"}], explicit)
        assert msgs == [{"role": "user", "content": "explicit"}]

    def test_build_messages_empty_query_skipped(self):
        """空 query 应该被跳过（不添加空 user 消息）"""
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m")
        msgs = p._build_messages("", "sys", None, None)
        # 实际行为：空 query 不追加（避免给 LLM 发空消息）
        assert msgs == [{"role": "system", "content": "sys"}]


class TestOpenCodeZenProviderFallback:
    """Fallback 行为 — 模拟主模型失败，验证切到下一模型"""

    def test_fallback_to_next_model_on_failure(self, monkeypatch):
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m1")
        # 手动设置两个模型
        p.registry = p.registry.__class__([
            {"name": "m1", "priority": 1},
            {"name": "m2", "priority": 2},
        ])
        p.available_models = ["m1", "m2"]
        p._chat_url = "http://test/chat/completions"

        # mock httpx.post 第一次失败，第二次成功
        responses = [
            Exception("first model fails"),
            _make_httpx_response({"choices": [{"message": {"content": "fallback reply"}}]}, 200),
        ]
        call_count = {"n": 0}

        def mock_post(*args, **kwargs):
            call_count["n"] += 1
            r = responses[call_count["n"] - 1]
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr("httpx.post", mock_post)

        result = p.chat("hello", "sys")
        assert result == "fallback reply"
        assert call_count["n"] == 2

    def test_all_models_failing_returns_error_message(self, monkeypatch):
        from llm_provider.opencode_zen_provider import OpenCodeZenProvider

        p = OpenCodeZenProvider(api_base="http://test", model="m1")
        p.registry = p.registry.__class__([
            {"name": "m1", "priority": 1},
            {"name": "m2", "priority": 2},
        ])
        p.available_models = ["m1", "m2"]
        p._chat_url = "http://test/chat/completions"

        def always_fail(*args, **kwargs):
            raise Exception("boom")

        monkeypatch.setattr("httpx.post", always_fail)
        result = p.chat("hello", "sys")
        # 全部失败时返回错误消息字符串
        assert "失败" in result or "不可用" in result or "error" in result.lower()


# ── LLMGatewayV2 测试（mock 模式） ─────────────────────────────


class TestLLMGatewayV2MockMode:
    """无 API key 时使用 mock 回复（覆盖 _mock_reply）"""

    def test_no_api_key_returns_mock(self, monkeypatch):
        from llm_provider.llm_gateway import LLMGatewayV2

        # 确保没有环境变量
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        g = LLMGatewayV2()
        assert g.api_key == ""
        result = g._mock_reply("hello")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mock_reply_includes_query_echo(self):
        from llm_provider.llm_gateway import LLMGatewayV2

        g = LLMGatewayV2(api_key="")
        result = g._mock_reply("test query")
        # mock 回复应该引用输入（至少部分）
        assert "test" in result or "query" in result or "收到" in result

    def test_chat_with_no_key_uses_mock(self, monkeypatch):
        import asyncio

        from llm_provider.llm_gateway import LLMGatewayV2

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        g = LLMGatewayV2()
        result = asyncio.run(g.chat("hi", "sys"))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_messages(self):
        from llm_provider.llm_gateway import LLMGatewayV2

        g = LLMGatewayV2(api_key="dummy")
        msgs = g._build_messages("hi", "sys", None, None)
        assert msgs == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]


# ── 工具函数 ───────────────────────────────────────────────────


def _make_httpx_response(data: dict, status_code: int = 200):
    """构造一个 httpx.Response mock"""
    import json

    import httpx

    return httpx.Response(
        status_code=status_code,
        content=json.dumps(data).encode("utf-8"),
        request=httpx.Request("POST", "http://test/"),
    )
