"""2026-10-20 八问题批 D：供应商空内容响应与降级可观测性。

生产背景：agnes 健康响应 3.8~19.8s、挂死恒 25s；降级窗内的回复质量明显
劣化。旧实现 HTTP 200 但 ``message.content=None`` 时先 ``mark_success()``
再 ``content.strip()`` → AttributeError 被宽 except 吞掉走 fallback，健康统计
被污染且日志无痕。本批要求：空内容在 mark_success 之前判死在案、异常与响应
体前缀进日志、fallback 失败逐模型记日志。
"""

from __future__ import annotations

import logging
import time

import httpx
import pytest

from llm_provider.openai_compatible_provider import OpenAICompatibleProvider

LOGGER = "llm_provider.openai_compatible_provider"


def _provider(models_config=None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_name="t",
        api_key="k",
        api_base="https://example.invalid/v1",
        model="primary",
        models_config=models_config,
    )


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RoutingClient:
    """按 payload.model 返回预置响应/异常。"""

    def __init__(self, routes):
        self._routes = routes

    async def post(self, url, json=None, **kwargs):
        model = (json or {}).get("model")
        result = self._routes[model]
        if isinstance(result, Exception):
            raise result
        return _Resp(result)


def _install_client(monkeypatch, provider, routes):
    monkeypatch.setattr(
        type(provider), "_async_client", property(lambda self: _RoutingClient(routes))
    )


EMPTY_CHOICES = {"choices": []}
NONE_CONTENT = {
    "choices": [{"message": {"content": None, "reasoning_content": "只在心里想了想"}}]
}
EMPTY_STRING = {"choices": [{"message": {"content": "   "}}]}
GOOD = {"choices": [{"message": {"content": "好啊，那你早点休息。"}}]}


class TestEmptyContentIsFailure:
    @pytest.mark.parametrize(
        "payload",
        [NONE_CONTENT, EMPTY_STRING, EMPTY_CHOICES],
        ids=["none", "blank", "no-choices"],
    )
    async def test_empty_content_raises_and_marks_failed(self, monkeypatch, caplog, payload):
        provider = _provider()
        _install_client(monkeypatch, provider, {"primary": payload})
        with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(
            RuntimeError, match="模型请求失败"
        ):
            await provider.chat("hi")
        assert "空内容响应" in caplog.text
        entry = provider.registry.get_by_name("primary")
        assert entry is not None
        assert entry.cooldown_until > time.time(), "空内容不得当成功，必须走 mark_failed"

    async def test_valid_content_marks_success(self, monkeypatch):
        provider = _provider()
        _install_client(monkeypatch, provider, {"primary": GOOD})
        assert await provider.chat("hi") == "好啊，那你早点休息。"
        entry = provider.registry.get_by_name("primary")
        assert entry is not None
        assert entry.cooldown_until == 0.0

    async def test_empty_primary_falls_back_to_backup(self, monkeypatch):
        provider = _provider(
            models_config=[
                {"name": "primary", "priority": 1},
                {"name": "backup", "priority": 2},
            ]
        )
        _install_client(monkeypatch, provider, {"primary": NONE_CONTENT, "backup": GOOD})
        assert await provider.chat("hi") == "好啊，那你早点休息。"

    async def test_fallback_failure_is_logged_with_reason(self, monkeypatch, caplog):
        provider = _provider(
            models_config=[
                {"name": "primary", "priority": 1},
                {"name": "backup", "priority": 2},
            ]
        )
        _install_client(
            monkeypatch,
            provider,
            {"primary": NONE_CONTENT, "backup": httpx.ConnectError("boom")},
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(
            RuntimeError, match="模型请求失败"
        ):
            await provider.chat("hi")
        assert "空内容响应" in caplog.text
        assert "Fallback primary → backup failed" in caplog.text
        assert "boom" in caplog.text
