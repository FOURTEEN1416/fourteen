"""
LLM Provider 单元测试 — 覆盖 0 测试 P0 风险
====================================

测试目标：
1. ModelEntry / ModelRegistry 冷却机制
2. LLMGatewayV2 mock 回复（无 API key 时的回退）
3. OpenAICompatibleProvider 配置解析
4. MultiProviderGateway fallback 链

零网络调用 — 全部用 respx/httpx_mock 或直接传 mock
"""

from __future__ import annotations

import asyncio
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
