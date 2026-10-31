"""provider 单次调用耗时埋点（2026-10-21 FIX-5）。

缺陷：日志里唯一与耗时相关的字段是 wechat 侧的**整轮** `llm_time`
（`wechat_direct/wechat_connector.py:2108`），provider 自身不记耗时 ——
`observability/metrics.record_chat_duration` 只在 ``HAS_METRICS`` 时生效且
日志里看不到。于是「agnes 单次延迟分布」只能靠外挂探针或反推，**无法从生产
日志直接取证**（这正是质量悬崖批排查时最大的证据缺口）。

修法：``_chat`` 成功/失败两条路径各打一条结构化日志（provider/model/耗时/
是否流式/是否空内容/异常类型），流式另带首 token 耗时（ttft_ms）。耗时用已有
``time.perf_counter()``；字段不含原文（延续既有脱敏纪律：只记规则名与长度）。
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
import pytest

_LOGGER = "llm_provider.openai_compatible_provider"
_MARK = "provider_call"


def _make_provider():
    from llm_provider.openai_compatible_provider import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        provider_name="agnes",
        api_key="sk-test",
        api_base="https://example.invalid/v1",
        model="agnes-3.0-flash",
    )


def _install(monkeypatch, provider, handler):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(30.0),
    )
    monkeypatch.setattr(
        type(provider), "_async_client", property(lambda self: client), raising=True,
    )


def _call_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if _MARK in r.getMessage()]


def _fields(line: str) -> dict[str, str]:
    """从 `... key=value key=value` 尾巴解析字段。"""
    out: dict[str, str] = {}
    for k, v in re.findall(r"(\w+)=(\S+)", line):
        out[k] = v
    return out


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "好啊"}}]})


# ── 1. 成功路径 ─────────────────────────────────────────────


def test_success_logs_elapsed_ms(monkeypatch, caplog):
    """成功调用必须留一条带 provider/model/elapsed_ms 的结构化日志。"""
    p = _make_provider()

    async def _slow(request: httpx.Request) -> httpx.Response:
        # 0.3s 而非 0.05s：Windows 时钟粒度下 50ms sleep 实测可低至 39ms，
        # 旧断言 ``>= 40`` 边界过贴导致 flaky（2026-10-31 清扫批）。
        await asyncio.sleep(0.3)
        return _ok_response(request)

    _install(monkeypatch, p, _slow)
    with caplog.at_level(logging.INFO):
        out = asyncio.run(p.chat(query="q", system_prompt="s"))

    assert out == "好啊"
    lines = _call_lines(caplog)
    assert lines, f"成功路径没有任何 {_MARK} 结构化日志：{[r.message for r in caplog.records]}"
    f = _fields(lines[0])
    assert f.get("provider") == "agnes"
    assert f.get("model") == "agnes-3.0-flash"
    assert f.get("ok") == "True"
    assert f.get("stream") == "False"
    # 真实测量（不是常量 0）：300ms 的响应至少量到 150ms（50% 余量）
    assert int(f["elapsed_ms"]) >= 150, f"耗时未被真实测量：{f}"


def test_failure_logs_elapsed_ms_and_error_type(monkeypatch, caplog):
    """失败路径同样必须有耗时 + 异常类型（否则慢失败无法取证）。"""
    p = _make_provider()

    async def _boom(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        raise httpx.ConnectError("offline")

    _install(monkeypatch, p, _boom)
    monkeypatch.setattr(
        type(p), "_try_fallback", _async_none, raising=True,
    )

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        asyncio.run(p.chat(query="q", system_prompt="s"))

    lines = _call_lines(caplog)
    assert lines, "失败路径没有任何结构化日志"
    f = _fields(lines[0])
    assert f.get("ok") == "False"
    assert f.get("error") == "ConnectError"
    assert int(f["elapsed_ms"]) >= 150, f"失败耗时未被真实测量：{f}"


async def _async_none(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
    return None


def test_empty_content_marked_in_log(monkeypatch, caplog):
    """空内容响应（HTTP 200 但 content 空）必须在日志里显式可辨。"""
    p = _make_provider()
    _install(
        monkeypatch, p,
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
    )
    monkeypatch.setattr(type(p), "_try_fallback", _async_none, raising=True)

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        asyncio.run(p.chat(query="q", system_prompt="s"))

    f = _fields(_call_lines(caplog)[0])
    assert f.get("empty") == "True", f"空内容未在日志标记：{f}"


# ── 2. 流式路径：首 token 耗时 ───────────────────────────────


def test_stream_logs_ttft_and_elapsed(monkeypatch, caplog):
    """流式调用必须带 stream=True 与 ttft_ms（首 token 延迟是流式唯一可用信号）。"""

    async def _sse(request: httpx.Request) -> httpx.Response:
        # 0.2s 而非 0.03s：同上，避免 Windows 时钟粒度把 30ms 测成 <20ms。
        await asyncio.sleep(0.2)
        frames = [
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
            "data: [DONE]\n\n",
        ]
        return httpx.Response(
            200, content="".join(frames).encode(),
            headers={"content-type": "text/event-stream"},
        )

    p = _make_provider()
    _install(monkeypatch, p, _sse)

    async def _collect():
        return [t async for t in p.chat_stream(query="q", system_prompt="s")]

    with caplog.at_level(logging.INFO):
        toks = asyncio.run(_collect())

    assert toks == ["你"]
    lines = _call_lines(caplog)
    assert lines, "流式路径没有任何结构化日志"
    f = _fields(lines[0])
    assert f.get("stream") == "True"
    assert "ttft_ms" in f, f"流式日志缺 ttft_ms：{f}"
    assert int(f["ttft_ms"]) >= 100
    assert int(f["elapsed_ms"]) >= int(f["ttft_ms"])


# ── 3. 脱敏纪律：日志不得含原文 ─────────────────────────────


def test_log_contains_no_prompt_or_reply_text(monkeypatch, caplog):
    """结构化日志只记元数据，不得落 prompt / 回复原文。"""
    secret_prompt = "我是秘密提示词SECRET_PROMPT_XYZ"
    secret_reply = "你好呀这条是回复SECRET_REPLY_XYZ"
    p = _make_provider()
    _install(
        monkeypatch, p,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": secret_reply}}]},
        ),
    )

    with caplog.at_level(logging.INFO):
        asyncio.run(p.chat(query=secret_prompt, system_prompt=secret_prompt))

    joined = "\n".join(_call_lines(caplog))
    assert _MARK in joined
    assert "SECRET_PROMPT_XYZ" not in joined, "日志泄漏了 prompt 原文"
    assert "SECRET_REPLY_XYZ" not in joined, "日志泄漏了回复原文"
