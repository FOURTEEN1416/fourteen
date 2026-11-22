"""429 尊重 Retry-After 退避 + nex（nex-n2.6）供应商接入（2026-11-21 批）。

覆盖两件事：
1. **429 退避**：生产对账实锤 agnes 429 后旧退避不读 ``Retry-After`` 头。
   本批 provider 在 429 时解析该头（秒数 / HTTP 日期两种形态）暂存提示，
   网关熔断时长取 ``max(默认 120s, Retry-After + 缓冲)`` 并叠加随机抖动
   （错开多 worker 同步重试）。
2. **nex 接入**：``config/llm_providers.json`` 新增 openai 兼容供应商
   ``nex``（模型 ``nex-n2.6``），插在 agnes 之后、zhipu 之前；base_url/key
   经 ``NEX_API_BASE`` / ``NEX_API_KEY`` 注入，缺失时网关 fail-soft 跳过。

零网络调用——HTTP 错误用预构造的 ``httpx.HTTPStatusError`` 注入。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_provider.multi_provider_gateway import (
    BREAKER_COOLDOWN_MAX_SECONDS,
    DEFAULT_FALLBACK_CHAIN,
    MultiProviderGateway,
)
from llm_provider.openai_compatible_provider import (
    RETRY_AFTER_HINT_MAX_SECONDS,
    OpenAICompatibleProvider,
    parse_retry_after,
)

ROOT = Path(__file__).resolve().parents[1]


# ── 工具：预构造 HTTPStatusError / 假 client ────────────────────


def _status_error(status_code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    """构造一个携带状态码与响应头的 httpx.HTTPStatusError（零网络）。"""
    request = httpx.Request("POST", "https://unit.test/v1/chat/completions")
    response = httpx.Response(status_code, headers=headers or {}, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected raise_for_status to raise")


class _RaisingClient:
    """非流式假 client：post() 直接抛预构造异常。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        self.calls += 1
        raise self._exc


class _FakeStreamResponse:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def raise_for_status(self) -> None:
        raise self._exc

    async def aiter_lines(self):  # pragma: no cover —— 保持异步生成器形态
        yield ""


class _FakeStreamCM:
    def __init__(self, exc: Exception) -> None:
        self._resp = _FakeStreamResponse(exc)

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._resp

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _StreamingClient:
    """流式假 client：stream() 返回的 CM 在进入时拿到会 raise 的响应。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def stream(self, *args: Any, **kwargs: Any) -> _FakeStreamCM:
        return _FakeStreamCM(self._exc)


def _make_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_name="agnes",
        api_key="sk-test",
        api_base="https://unit.test/v1",
        model="agnes-3.0-flash",
    )


# ── parse_retry_after 单元测试 ─────────────────────────────────


class TestParseRetryAfter:
    def test_seconds_integer(self):
        assert parse_retry_after("120") == 120.0

    def test_seconds_with_whitespace(self):
        assert parse_retry_after(" 60 ") == 60.0

    def test_seconds_float(self):
        assert parse_retry_after("1.5") == 1.5

    def test_none_and_empty(self):
        assert parse_retry_after(None) is None
        assert parse_retry_after("") is None
        assert parse_retry_after("   ") is None

    def test_garbage_returns_none(self):
        assert parse_retry_after("soon") is None

    def test_non_finite_returns_none(self):
        # inf/nan 若被当成冷却时长会出大问题，必须判非法
        assert parse_retry_after("inf") is None
        assert parse_retry_after("nan") is None

    def test_http_date_future(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        value = format_datetime(future, usegmt=True)
        result = parse_retry_after(value)
        assert result is not None
        assert 0 < result <= 30.5

    def test_http_date_past_clamps_to_zero(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        value = format_datetime(past, usegmt=True)
        assert parse_retry_after(value) == 0.0


# ── parse_retry_after 上界钳制（2026-11-22 H1）──────────────────


class TestParseRetryAfterUpperBound:
    """超大秒数 / 远期 HTTP 日期必须钳到 RETRY_AFTER_HINT_MAX_SECONDS。

    无上界时 999999999s → 999999999s、2099 年日期 → 2275595314s，一次 429
    就能把熔断冷却抬到数年量级，半开重探永不到来。
    """

    def test_huge_seconds_clamped_to_bound(self):
        assert parse_retry_after("999999999") == RETRY_AFTER_HINT_MAX_SECONDS

    def test_far_future_http_date_clamped_to_bound(self):
        value = format_datetime(datetime(2099, 1, 1, tzinfo=timezone.utc), usegmt=True)
        assert parse_retry_after(value) == RETRY_AFTER_HINT_MAX_SECONDS

    def test_just_above_bound_clamped(self):
        assert parse_retry_after("300.5") == RETRY_AFTER_HINT_MAX_SECONDS
        assert parse_retry_after("301") == RETRY_AFTER_HINT_MAX_SECONDS

    def test_within_bound_unchanged(self):
        assert parse_retry_after("299.5") == 299.5
        assert parse_retry_after("120") == 120.0

    def test_bound_constant_is_module_level(self):
        """上界常数提为模块常量，供网关/测试对齐引用。"""
        assert RETRY_AFTER_HINT_MAX_SECONDS == 300.0


# ── provider 429 提示捕获 ──────────────────────────────────────


class TestProviderCapturesRetryAfterHint:
    def test_chat_429_with_retry_after_header(self, monkeypatch):
        provider = _make_provider()
        exc = _status_error(429, {"Retry-After": "90"})
        fake = _RaisingClient(exc)
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        assert provider._retry_after_hint_seconds == 90.0
        # 消费一次后清除（读取即清除，不反复生效）
        assert provider.consume_retry_after_hint() == 90.0
        assert provider.consume_retry_after_hint() is None

    def test_chat_429_without_retry_after_header(self, monkeypatch):
        """429 但没带头：提示置空，网关按默认冷却退避。"""
        provider = _make_provider()
        fake = _RaisingClient(_status_error(429))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        assert provider._retry_after_hint_seconds is None
        assert provider.consume_retry_after_hint() is None

    def test_chat_429_with_http_date_header(self, monkeypatch):
        provider = _make_provider()
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45), usegmt=True)
        fake = _RaisingClient(_status_error(429, {"Retry-After": future}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        hint = provider._retry_after_hint_seconds
        assert hint is not None and 0 < hint <= 45.5

    def test_non_429_failure_leaves_no_hint(self, monkeypatch):
        provider = _make_provider()
        fake = _RaisingClient(_status_error(500))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        assert provider.consume_retry_after_hint() is None

    def test_chat_with_tools_429_captures_hint(self, monkeypatch):
        from llm_provider.llm_gateway import ProviderError

        provider = _make_provider()
        fake = _RaisingClient(_status_error(429, {"Retry-After": "30"}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(ProviderError):
            asyncio.run(provider.chat_with_tools(query="hi"))

        assert provider._retry_after_hint_seconds == 30.0

    def test_stream_429_captures_hint(self, monkeypatch):
        provider = _make_provider()
        fake = _StreamingClient(_status_error(429, {"Retry-After": "15"}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        async def _consume() -> None:
            # _chat 是 async def：stream 分支返回 _stream_chat 的异步生成器，
            # 必须先 await 拿到生成器再迭代。
            gen = await provider._chat(query="hi", stream=True)
            async for _ in gen:  # pragma: no cover —— 首 token 前即抛
                break

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(_consume())

        assert provider._retry_after_hint_seconds == 15.0


# ── 网关熔断时长：max(默认, Retry-After+缓冲) + 抖动 ────────────


class _HintProvider:
    """带 consume_retry_after_hint 的假 provider（模拟 429 提示持有者）。"""

    def __init__(self, hint: float | None) -> None:
        self._hint = hint
        self.consume_calls = 0

    def consume_retry_after_hint(self) -> float | None:
        self.consume_calls += 1
        return self._hint


class _PlainProvider:
    """无 consume 方法的假 provider（旧式 mock，getattr 分支必须兼容）。"""


def _make_gw(providers: dict[str, Any]) -> MultiProviderGateway:
    gw = MultiProviderGateway(fallback_chain=list(providers), providers_config={})
    gw._providers = dict(providers)
    gw._chain = list(providers)
    return gw


class TestBreakerCooldownRespectsRetryAfter:
    def test_default_cooldown_without_hint(self):
        gw = _make_gw({"a": _HintProvider(None)})
        gw._BREAKER_JITTER_MAX = 0.0  # 实例属性遮蔽类属性 → 抖动置零便于精确断言
        gw._mark_provider_result("a", False)
        gw._mark_provider_result("a", False)
        delta = gw._breaker_open_until["a"] - time.time()
        assert abs(delta - gw._BREAKER_COOLDOWN) <= 1.0

    def test_plain_provider_without_consume_method(self):
        gw = _make_gw({"a": _PlainProvider()})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("a", False)
        gw._mark_provider_result("a", False)
        delta = gw._breaker_open_until["a"] - time.time()
        assert abs(delta - gw._BREAKER_COOLDOWN) <= 1.0

    def test_retry_after_below_default_keeps_default(self):
        """Retry-After=30（+缓冲 5）低于默认 120s → 取默认。"""
        gw = _make_gw({"a": _HintProvider(30.0)})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("a", False)
        gw._mark_provider_result("a", False)
        delta = gw._breaker_open_until["a"] - time.time()
        assert abs(delta - gw._BREAKER_COOLDOWN) <= 1.0

    def test_retry_after_above_default_extends_cooldown(self):
        """Retry-After=400（+缓冲 5）高于默认 120s → 冷却取 405s，不早于解限撞墙。

        2026-11-22 H1：400+5=405 低于总冷却上界 600s，语义不变；「提示超过
        上界时被钳到 600s」由 TestBreakerCooldownUpperBound 单独钉死。
        """
        provider = _HintProvider(400.0)
        gw = _make_gw({"a": provider})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("a", False)  # 第 1 次失败：未达阈值，不消费提示
        assert provider.consume_calls == 0
        gw._mark_provider_result("a", False)  # 第 2 次失败：开闸，消费提示
        assert provider.consume_calls == 1
        delta = gw._breaker_open_until["a"] - time.time()
        assert abs(delta - (400.0 + gw._RETRY_AFTER_BUFFER)) <= 1.0

    def test_jitter_spreads_cooldown(self):
        """默认抖动上限内：冷却落在 [120, 120+30] 区间（多 worker 错峰）。"""
        gw = _make_gw({"a": _HintProvider(None)})
        gw._mark_provider_result("a", False)
        gw._mark_provider_result("a", False)
        delta = gw._breaker_open_until["a"] - time.time()
        assert gw._BREAKER_COOLDOWN - 1.0 <= delta <= gw._BREAKER_COOLDOWN + gw._BREAKER_JITTER_MAX + 1.0


# ── 网关熔断冷却上界（2026-11-22 H1）───────────────────────────


class TestBreakerCooldownUpperBound:
    """无论 hint 多大、抖动多少，开闸冷却必须 ≤ BREAKER_COOLDOWN_MAX_SECONDS。

    背景：主动决策线（immediate=True）一次 429 即开闸；旧实现冷却无上界，
    超大 Retry-After 会把供应商打成「只能重启恢复」的准永久熔断。
    """

    def test_huge_hint_clamped_to_max_cooldown(self):
        gw = _make_gw({"a": _HintProvider(999999.0)})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("a", False)
        gw._mark_provider_result("a", False)
        delta = gw._breaker_open_until["a"] - time.time()
        assert abs(delta - BREAKER_COOLDOWN_MAX_SECONDS) <= 1.0

    def test_immediate_single_429_opens_with_bounded_cooldown(self):
        """immediate=True：一次失败即开闸，冷却仍被钳在上界内。"""
        gw = _make_gw({"a": _HintProvider(999999.0)})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("a", False, immediate=True)
        delta = gw._breaker_open_until["a"] - time.time()
        assert 0 < delta <= BREAKER_COOLDOWN_MAX_SECONDS + 1.0

    def test_jitter_included_in_clamp(self):
        """钳制作用于「base+抖动」整体：即使保留默认抖动也不超过上界。"""
        gw = _make_gw({"a": _HintProvider(999999.0)})
        # 不置零抖动，多次采样全部不得越过上界
        for _ in range(20):
            gw._breaker_failures["a"] = 0
            gw._mark_provider_result("a", False, immediate=True)
            delta = gw._breaker_open_until["a"] - time.time()
            assert delta <= BREAKER_COOLDOWN_MAX_SECONDS + 1.0

    def test_half_open_reprobe_reachable_after_max_cooldown(self):
        """冷却到期后 _breaker_open 自动半开（重探可达），不会永久熔断。"""
        gw = _make_gw({"a": _HintProvider(999999.0)})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("a", False, immediate=True)
        assert gw._breaker_open("a") is True
        # 把开闸时刻拨到上界之前 → 仍开闸；拨到上界之后 → 半开放行并清状态
        gw._breaker_open_until["a"] = time.time() - BREAKER_COOLDOWN_MAX_SECONDS - 1.0
        assert gw._breaker_open("a") is False
        assert "a" not in gw._breaker_open_until
        assert "a" not in gw._breaker_failures

    def test_max_cooldown_constant_is_module_level(self):
        assert BREAKER_COOLDOWN_MAX_SECONDS == 600.0

    def test_end_to_end_429_huge_header_keeps_cooldown_bounded(self, monkeypatch):
        """端到端：429 带超大 Retry-After 头 → parse 层钳 300 → 网关冷却 305s。

        provider 暂存的 hint 已被钳制，网关即使再被注入大 hint 也兜底钳 600。
        """
        provider = _make_provider()
        fake = _RaisingClient(_status_error(429, {"Retry-After": "999999999"}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        assert provider._retry_after_hint_seconds == RETRY_AFTER_HINT_MAX_SECONDS

        gw = _make_gw({"agnes": provider})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("agnes", False, immediate=True)
        delta = gw._breaker_open_until["agnes"] - time.time()
        assert abs(delta - (RETRY_AFTER_HINT_MAX_SECONDS + gw._RETRY_AFTER_BUFFER)) <= 1.0
        assert delta <= BREAKER_COOLDOWN_MAX_SECONDS


# ── 陈旧 hint 污染路径（2026-11-22 L1）─────────────────────────


class TestRetryAfterHintPollution:
    """docstring 承诺「非 429 失败清掉旧提示」——实现必须一致。

    旧缺陷：非 HTTPStatusError/非 429 时提前 return 不清，先 429 留下的 hint
    越过后续 500 存活，抬高 5 分钟内下一次熔断的冷却时长。
    """

    def test_500_after_429_clears_stale_hint(self, monkeypatch):
        provider = _make_provider()
        fake = _RaisingClient(_status_error(429, {"Retry-After": "90"}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))
        assert provider._retry_after_hint_seconds == 90.0

        # 同一 provider 随后一次 500：陈旧 hint 必须被清掉
        fake._exc = _status_error(500)
        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        assert provider._retry_after_hint_seconds is None
        assert provider._retry_after_hint_at == 0.0
        assert provider.consume_retry_after_hint() is None

    def test_non_http_error_after_429_clears_stale_hint(self, monkeypatch):
        """非 HTTP 状态错误（如连接失败）同样清旧提示。"""
        provider = _make_provider()
        fake = _RaisingClient(_status_error(429, {"Retry-After": "90"}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))
        assert provider._retry_after_hint_seconds == 90.0

        fake._exc = httpx.ConnectError("connection reset")
        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        assert provider._retry_after_hint_seconds is None
        assert provider.consume_retry_after_hint() is None

    def test_stale_hint_no_longer_inflates_next_cooldown(self, monkeypatch):
        """污染路径闭环：429 留 hint → 500 清 hint → 下次熔断冷却回落默认。"""
        provider = _make_provider()
        fake = _RaisingClient(_status_error(429, {"Retry-After": "90"}))
        monkeypatch.setattr(OpenAICompatibleProvider, "_async_client", property(lambda self: fake))

        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        fake._exc = _status_error(500)
        with pytest.raises(RuntimeError):
            asyncio.run(provider.chat(query="hi"))

        gw = _make_gw({"agnes": provider})
        gw._BREAKER_JITTER_MAX = 0.0
        gw._mark_provider_result("agnes", False)
        gw._mark_provider_result("agnes", False)
        delta = gw._breaker_open_until["agnes"] - time.time()
        # hint 已被 500 清掉 → 冷却回落默认 120s，而不是 max(120, 90+5)=120 的
        # 陈旧组合（若 hint 是 300+ 则差距更明显）；核心断言：consume 为空
        assert provider.consume_retry_after_hint() is None
        assert abs(delta - gw._BREAKER_COOLDOWN) <= 1.0


# ── nex（nex-n2.6）接入 ────────────────────────────────────────


def _load_json_config() -> dict:
    return json.loads((ROOT / "config" / "llm_providers.json").read_text(encoding="utf-8"))


class TestNexProviderEntry:
    def test_nex_entry_exists_with_empty_api_key(self):
        """tracked 配置里 nex 条目存在且 api_key 恒为空（§9.2 硬约束）。"""
        cfg = _load_json_config()
        nex = cfg["providers"]["nex"]
        assert nex["model"] == "nex-n2.6"
        assert nex["api_key"] == ""
        assert nex["auth_mode"] == "bearer"
        assert nex["enabled"] is True
        assert nex["stream_enabled"] is True
        # git-tracked 文件里不得出现真实 base_url/key（经环境变量注入）
        assert nex["api_base"] == ""

    def test_nex_chain_position_between_agnes_and_zhipu(self):
        cfg = _load_json_config()
        chain = cfg["fallback_chain"]
        assert chain.index("agnes") < chain.index("nex") < chain.index("zhipu")

    def test_default_chain_matches(self):
        assert DEFAULT_FALLBACK_CHAIN == ["agnes", "nex", "zhipu", "xunfei", "baidu"]

    def test_nex_env_override_documented_in_env_map(self, monkeypatch):
        """NEX_API_KEY / NEX_API_BASE / NEX_MODEL 三个环境变量被网关消费。"""
        monkeypatch.setenv("NEX_API_KEY", "sk-nex-test")
        monkeypatch.setenv("NEX_API_BASE", "https://nex.example.com/v1")
        monkeypatch.delenv("NEX_MODEL", raising=False)
        gw = MultiProviderGateway(
            fallback_chain=["nex"],
            providers_config={"nex": {"model": "nex-n2.6", "api_key": "", "api_base": ""}},
        )
        assert "nex" in gw._providers
        provider = gw._providers["nex"]
        assert provider.api_key == "sk-nex-test"
        assert provider.api_base == "https://nex.example.com/v1"
        assert provider.model == "nex-n2.6"


class TestNexFailSoftSkip:
    def test_nex_skipped_when_key_missing(self, monkeypatch):
        """key 缺失（未配 env / local overlay）→ 构造期跳过，不报错不入链。"""
        monkeypatch.delenv("NEX_API_KEY", raising=False)
        monkeypatch.delenv("NEX_API_BASE", raising=False)
        gw = MultiProviderGateway(
            fallback_chain=["nex"],
            providers_config={"nex": {"model": "nex-n2.6", "api_key": "", "api_base": ""}},
        )
        assert "nex" not in gw._providers

    def test_nex_skipped_when_base_missing_even_with_key(self, monkeypatch):
        """有 key 但 base 缺失（env 与显式 config 均未提供）→ fail-soft 跳过。"""
        monkeypatch.setenv("NEX_API_KEY", "sk-nex-test")
        monkeypatch.delenv("NEX_API_BASE", raising=False)
        gw = MultiProviderGateway(
            fallback_chain=["nex"],
            providers_config={"nex": {"model": "nex-n2.6", "api_key": "", "api_base": ""}},
        )
        assert "nex" not in gw._providers

    def test_enabled_false_provider_skipped(self):
        """enabled=false 的供应商不入链（管理端停用开关在网关侧生效）。"""
        gw = MultiProviderGateway(
            fallback_chain=["x"],
            providers_config={"x": {"api_key": "k", "api_base": "https://x.example.com/v1", "model": "m", "enabled": False}},
        )
        assert "x" not in gw._providers

    def test_provider_without_enabled_flag_still_loads(self):
        """未写 enabled 的配置视为启用（向后兼容，缺省不收窄）。"""
        gw = MultiProviderGateway(
            fallback_chain=["x"],
            providers_config={"x": {"api_key": "k", "api_base": "https://x.example.com/v1", "model": "m"}},
        )
        assert "x" in gw._providers
