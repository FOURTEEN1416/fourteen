"""`stream_enabled` 必须被消费（2026-10-21 FIX-2）。

缺陷：`config/llm_providers.json` 里 agnes/zhipu/讯飞/百度/DeepSeek 全是
``"stream_enabled": true``，前端设置页也能改这个开关，但**全仓没有任何读取点**：
``openai_compatible_provider.py`` 的 ``self.stream_enabled = stream_enabled``
之后再无消费者（grep 只命中 ``__init__.py`` 的透传与
``api/routers/llm_providers_routes.py`` 的回显）。实际是否流式由调用方决定 ——
主链 ``request_llm.chat()`` → ``_chat(..., stream=False)``。
排障时会误判「已开流式」。

修法（二选一，本批选「消费开关」）：``chat()`` 在 ``stream_enabled`` 为真时走
**流式聚合**（消费该 SSE 流并拼成完整文本），为假时保持原非流式路径。
选它而非「删字段」的原因：字段还出现在前端 ``llmProviders.ts`` /
``ProviderEditModal.tsx`` 与 ``observability/config_models.py``，那三处不在本批
允许清单内 —— 只删后端会让 UI 上的开关变成**更没有指向**的死控件。

⚠️ 顺带必须修的一致性缺口：``timeout`` 实参（如主动决策线放宽到 25s）此前只作用于
非流式分支；走流式后若不透传，25s 预算会静默变成 ``_stream_chat`` 内部的 60s。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest


def _make_provider(*, stream_enabled: bool, recorder: dict | None = None):
    from llm_provider.openai_compatible_provider import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        provider_name="t",
        api_key="sk-test",
        api_base="https://example.invalid/v1",
        model="m",
        stream_enabled=stream_enabled,
    )


def _install_mock(monkeypatch, provider, recorder: dict):
    """把 provider 的 httpx 客户端换成 MockTransport（零网络）。"""

    async def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        recorder.setdefault("bodies", []).append(body)
        if body.get("stream"):
            frames = [
                'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"好"}}]}\n\n',
                "data: [DONE]\n\n",
            ]
            return httpx.Response(
                200,
                content="".join(frames).encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "你好"}}]},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        timeout=httpx.Timeout(30.0),
    )
    monkeypatch.setattr(
        type(provider), "_async_client", property(lambda self: client), raising=True,
    )
    recorder["client"] = client


# ── 1. 开关为真 → 真流式并被聚合 ─────────────────────────────


def test_chat_consumes_stream_enabled(monkeypatch):
    """stream_enabled=True：请求必须带 stream=true，且返回聚合后的完整文本。"""
    rec: dict = {}
    provider = _make_provider(stream_enabled=True)
    _install_mock(monkeypatch, provider, rec)

    out = asyncio.run(provider.chat(query="q", system_prompt="s"))

    assert rec["bodies"], "未发出任何请求"
    assert rec["bodies"][0].get("stream") is True, (
        f"stream_enabled=true 时 chat() 仍走非流式请求：{rec['bodies'][0].get('stream')!r}"
    )
    assert out == "你好", f"流式聚合结果错误：{out!r}"


def test_chat_stays_non_streaming_when_disabled(monkeypatch):
    """stream_enabled=False：保持原非流式路径（开关必须真的可关）。"""
    rec: dict = {}
    provider = _make_provider(stream_enabled=False)
    _install_mock(monkeypatch, provider, rec)

    out = asyncio.run(provider.chat(query="q", system_prompt="s"))

    assert rec["bodies"][0].get("stream") is False, "开关为假时不得改走流式"
    assert out == "你好"


# ── 2. 与既有语义对等：空内容仍按失败处理 ────────────────────


def _install_sse(monkeypatch, provider, frames: list[str]):
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content="".join(frames).encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), timeout=httpx.Timeout(30.0),
    )
    monkeypatch.setattr(
        type(provider), "_async_client", property(lambda self: client), raising=True,
    )
    return client


def test_empty_stream_raises_and_marks_failed(monkeypatch):
    """流式聚合为空 → 必须 raise，且**在 mark_success 之前**判死（同非流式规则）。

    这条覆盖是非流式的 ``test_llm_empty_content_observability`` 覆盖不到的那一半：
    主链现在按配置走流式，空内容判死语义必须在流式路径上同样成立。
    """
    provider = _make_provider(stream_enabled=True)
    _install_sse(monkeypatch, provider, ["data: [DONE]\n\n"])
    monkeypatch.setattr(
        type(provider), "_try_fallback", _async_none, raising=True,
    )

    entry = provider.registry.get_by_name("m")
    with pytest.raises(RuntimeError, match="未生成角色回复"):
        asyncio.run(provider.chat(query="q", system_prompt="s"))

    assert entry.retry_count > 0 or not entry.is_available(), (
        "空内容必须判失败并落记账（不得 mark_success）"
    )


def test_valid_stream_marks_success(monkeypatch):
    """流式聚合成功 → registry 记成功（旧流式路径完全不记账）。"""
    provider = _make_provider(stream_enabled=True)
    _install_sse(
        monkeypatch, provider,
        ['data: {"choices":[{"delta":{"content":"好"}}]}\n\n', "data: [DONE]\n\n"],
    )

    entry = provider.registry.get_by_name("m")
    out = asyncio.run(provider.chat(query="q", system_prompt="s"))

    assert out == "好"
    assert entry.is_available(), "成功响应后模型必须可用（mark_success 已调用）"


async def _async_none(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    return None


def test_stream_failure_goes_through_fallback_chain(monkeypatch):
    """流式失败必须走同一条错误链：_try_fallback → 统一 RuntimeError。"""

    async def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    provider = _make_provider(stream_enabled=True)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_boom), timeout=httpx.Timeout(30.0),
    )
    monkeypatch.setattr(
        type(provider), "_async_client", property(lambda self: client), raising=True,
    )

    calls: list = []

    async def _fb(self, messages, temperature, max_tokens, tools):  # noqa: ANN001
        calls.append(True)
        return None

    monkeypatch.setattr(type(provider), "_try_fallback", _fb, raising=True)

    with pytest.raises(RuntimeError, match="未生成角色回复"):
        asyncio.run(provider.chat(query="q", system_prompt="s"))
    assert calls, "流式失败必须调用 _try_fallback（与非流式同一条错误链）"


# ── 3. 缺省缺省：未声明的配置必须保持旧非流式行为 ─────────────


def test_default_is_non_streaming_opt_in():
    """`stream_enabled` 缺省必须是 False（显式 opt-in 契约）。

    本批**新引入**「流式聚合」能力，未声明的配置不得改变既有行为；生产
    tracked 配置五个供应商都显式写 true。这条同时保证既有大量以
    「只实现 .post 的假 httpx 客户端」构造 provider 的用例仍然有效
    （它们构造时不声明该开关）。
    """
    import json
    from pathlib import Path

    from llm_provider.openai_compatible_provider import OpenAICompatibleProvider

    p = OpenAICompatibleProvider(
        provider_name="t", api_key="k", api_base="https://example.invalid/v1", model="m",
    )
    assert p.stream_enabled is False, "缺省必须为非流式（opt-in）"

    cfg = Path(__file__).resolve().parent.parent / "config" / "llm_providers.json"
    data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    enabled_flags = [
        bool(v.get("stream_enabled"))
        for v in (data.get("providers") or {}).values()
        if isinstance(v, dict) and v.get("enabled", True)
    ]
    assert enabled_flags and all(enabled_flags), (
        "生产配置里启用中的供应商必须显式声明 stream_enabled=true，否则主链会退回非流式"
    )


# ── 4. timeout 实参必须透传到流式请求 ────────────────────────


def test_chat_forwards_timeout_into_streaming(monkeypatch):
    """chat(timeout=N) 走流式后仍须带 N（否则 25s 预算静默变 60s）。"""
    rec: dict = {}
    provider = _make_provider(stream_enabled=True)
    _install_mock(monkeypatch, provider, rec)

    seen: dict = {}
    original = type(provider)._stream_chat

    async def _spy(self, payload, model_name, timeout=None):  # noqa: ANN001
        seen["timeout"] = timeout
        async for token in original(self, payload, model_name, timeout=timeout):
            yield token

    monkeypatch.setattr(type(provider), "_stream_chat", _spy, raising=True)

    out = asyncio.run(provider.chat(query="q", system_prompt="s", timeout=7.0))

    assert seen.get("timeout") == 7.0, (
        f"timeout 未透传到流式请求（实得 {seen.get('timeout')!r}）"
    )
    assert out == "你好"


# ── 4. 其它入口不受开关影响（回归护栏）───────────────────────


def test_chat_with_tools_unaffected(monkeypatch):
    """带工具链必须恒非流式（tool_calls 需要完整 message，不能流式聚合）。"""
    rec: dict = {}
    provider = _make_provider(stream_enabled=True)
    _install_mock(monkeypatch, provider, rec)

    out = asyncio.run(provider.chat_with_tools(query="q", system_prompt="s"))

    assert rec["bodies"][0].get("stream") is False, "带工具链不得被开关改成流式"
    assert out["content"] == "你好"


def test_chat_stream_entry_unaffected(monkeypatch):
    """显式流式入口 chat_stream() 必须仍是逐 token 产出（不因本次改动退化）。"""
    rec: dict = {}
    provider = _make_provider(stream_enabled=True)
    _install_mock(monkeypatch, provider, rec)

    async def _collect() -> list[str]:
        return [tok async for tok in provider.chat_stream(query="q", system_prompt="s")]

    tokens = asyncio.run(_collect())
    assert tokens == ["你", "好"], f"流式入口产出被改变：{tokens!r}"
    assert rec["bodies"][0].get("stream") is True
