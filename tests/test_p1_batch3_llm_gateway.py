"""P1 批3 回归：LLM 网关熔断/流式语义 + 工具调度超时/按人限速 + 每轮成本闸门。

钉住 2026-09-21 全面审查修复批次的行为契约（P1-1~11）。
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future

import pytest

from llm_provider.multi_provider_gateway import MultiProviderGateway
from tools.base_tool import BaseTool, ToolDispatcher, ToolRegistry, ToolResult

# ── provider 替身 ─────────────────────────────────────────


class _FailProv:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        raise RuntimeError("boom")

    async def chat_stream(self, **kwargs):
        self.calls += 1
        raise RuntimeError("boom")
        yield ""  # pragma: no cover


class _OkProv:
    def __init__(self, reply: str = "好的", tokens: tuple[str, ...] = ("好", "的")) -> None:
        self.reply = reply
        self.tokens = tokens
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return self.reply

    async def chat_stream(self, **kwargs):
        self.calls += 1
        for t in self.tokens:
            yield t


class _HalfThenFail:
    """流式：先吐内容再炸（已下发 token 不得重放）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat_stream(self, **kwargs):
        self.calls += 1
        yield "半句"
        raise RuntimeError("mid-stream break")


def _make_gw(**providers) -> MultiProviderGateway:
    gw = MultiProviderGateway(fallback_chain=list(providers), providers_config={})
    gw._providers = dict(providers)
    gw._chain = list(providers)
    return gw


# ── P1-2 provider 熔断 ───────────────────────────────────


def test_breaker_opens_after_consecutive_failures_and_skips():
    bad, good = _FailProv(), _OkProv()
    gw = _make_gw(a=bad, b=good)

    async def _three():
        for _ in range(3):
            r = await gw.chat(query="hi")
            assert r == "好的"

    asyncio.run(_three())
    # 第 3 次调用时 a 已开闸（连续 2 次失败），不应再被打扰
    assert bad.calls == 2
    assert good.calls == 3


def test_breaker_half_open_after_cooldown_reprobes():
    bad, good = _FailProv(), _OkProv()
    gw = _make_gw(a=bad, b=good)

    async def _two():
        await gw.chat(query="hi")
        await gw.chat(query="hi")

    asyncio.run(_two())
    assert bad.calls == 2
    # 冷却期手动置为已过期 → 半开，允许重新探测
    gw._breaker_open_until["a"] = time.time() - 1
    asyncio.run(gw.chat(query="hi"))
    assert bad.calls == 3


def test_breaker_success_resets_failures():
    flaky = _FailProv()
    gw = _make_gw(a=flaky, b=_OkProv())
    asyncio.run(gw.chat(query="hi"))
    assert gw._breaker_failures["a"] == 1
    gw._mark_provider_result("a", True)
    assert "a" not in gw._breaker_failures


def test_chain_keys_never_empty_when_all_open():
    gw = _make_gw(a=_OkProv(), b=_OkProv())
    for k in ("a", "b"):
        gw._breaker_open_until[k] = time.time() + 300
    assert gw._chain_keys() == ["a", "b"]  # 全开闸→仍然全量探测，绝不空链


def test_chain_keys_start_key_first():
    gw = _make_gw(a=_OkProv(), b=_OkProv())
    assert gw._chain_keys("b") == ["b", "a"]


# ── P1-1 chat_stream fallback 语义 ───────────────────────


def test_stream_prefail_falls_back_to_next_provider():
    bad, good = _FailProv(), _OkProv()
    gw = _make_gw(a=bad, b=good)

    async def _collect():
        return [t async for t in gw.chat_stream(query="hi")]

    assert asyncio.run(_collect()) == ["好", "的"]
    assert bad.calls == 1 and good.calls == 1


def test_stream_post_fail_does_not_replay_next_provider():
    half, good = _HalfThenFail(), _OkProv()
    gw = _make_gw(a=half, b=good)

    async def _collect():
        return [t async for t in gw.chat_stream(query="hi")]

    with pytest.raises(RuntimeError):
        asyncio.run(_collect())
    # 已下发"半句"后再切下一个 provider 会造成半句+整句重复 → 必须上抛
    assert good.calls == 0


def test_stream_all_empty_raises_not_error_text():
    gw = _make_gw(a=_OkProv(tokens=()), b=_OkProv(tokens=()))

    async def _collect():
        return [t async for t in gw.chat_stream(query="hi")]

    assert asyncio.run(_collect()) == []  # 零 token 无异常：不上抛也不 yield 错误文案


# ── P1-11 ToolDispatcher：超时 + 按调用者限速 ────────────


class _SleepTool(BaseTool):
    name = "sleepy"
    description = "sleeps"
    permission_level = "public"

    def execute(self, **kwargs):
        time.sleep(1.0)
        return ToolResult(True, "done")


class _EchoTool(BaseTool):
    name = "echo"
    description = "echo"
    permission_level = "public"
    seen: list = []

    def execute(self, **kwargs):
        _EchoTool.seen.append(kwargs)
        return ToolResult(True, kwargs.get("v", "ok"))


class _FlakySearchTool(BaseTool):
    name = "search"
    description = "flaky"
    permission_level = "public"
    calls: list = []

    def execute(self, **kwargs):
        _FlakySearchTool.calls.append(kwargs)
        if len(_FlakySearchTool.calls) == 1:
            raise RuntimeError("transient")
        return ToolResult(True, "recovered")


def _mk_disp(tools, **kw):
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    kw.setdefault("rate_limit_per_minute", 100)
    kw.setdefault("timeout", 5.0)
    return ToolDispatcher(reg, **kw)


def test_dispatch_timeout_returns_error_not_hang():
    disp = _mk_disp([_SleepTool()], timeout=0.2)
    r = disp.dispatch("sleepy", {})
    assert not r.success
    assert r.error.startswith("tool_timeout")
    disp.close()


def test_rate_limit_is_per_caller():
    disp = _mk_disp([_EchoTool()], rate_limit_per_minute=1)
    assert disp.dispatch("echo", {"v": 1}, caller_id="u1").success
    blocked = disp.dispatch("echo", {"v": 2}, caller_id="u1")
    assert not blocked.success and "Rate limit" in blocked.error
    # 另一个用户不受 u1 消耗影响（旧实现按工具名全局共享 → 用户互耗）
    assert disp.dispatch("echo", {"v": 3}, caller_id="u2").success
    disp.close()


def test_rate_limit_no_caller_falls_back_global_key():
    disp = _mk_disp([_EchoTool()], rate_limit_per_minute=1)
    assert disp.dispatch("echo", {}).success
    assert not disp.dispatch("echo", {}).success
    disp.close()


def test_retry_preserves_meta_attribution():
    _FlakySearchTool.calls = []
    disp = _mk_disp([_FlakySearchTool()], retry_count=1, retry_tools={"search"})
    meta = {"session_key": "7:wx"}
    r = disp.dispatch("search", {"q": "x", "_meta": meta})
    assert r.success
    # 重试必须带完整参数（含服务端注入的 _meta 归属），否则第二跳丢归属
    assert len(_FlakySearchTool.calls) == 2
    assert _FlakySearchTool.calls[1]["_meta"] == meta
    disp.close()


# ── P1-7 情感分类忙则跳过 ────────────────────────────────


def test_emotion_classify_busy_skip_returns_none():
    from my_character.emotion_engine import LLMEmotionClassifier

    eng = LLMEmotionClassifier(llm_gateway=_OkProv(), timeout_ms=50)
    pending: Future = Future()  # 永不完成 → 模拟 LLM 还在飞
    eng._pending = pending
    assert eng.classify("今天不开心") is None
    assert eng.classify("换一条消息") is None  # 忙期间不堆新任务
    pending.set_exception(RuntimeError("cleanup"))


# ── 摘要缓存：实际来源相同才复用，新增消息不可被步长吞掉 ──


def _msgs(n: int):
    return [{"role": "user", "content": f"m{i}"} for i in range(n)]


def test_summarizer_reuses_identical_sources_and_updates_changed_sources():
    from shisi.memory.legacy.conversation_summarizer import ConversationSummarizer

    cs = ConversationSummarizer(llm_gateway=None)
    calls: list[int] = []

    def summarize(older):
        calls.append(len(older))
        return f"摘要{len(older)}"

    cs._summarize = summarize
    assert cs.get_chat_context(_msgs(55), session_id="s1", keep_recent=20)[1] == "摘要35"
    assert cs.get_chat_context(_msgs(55), session_id="s1", keep_recent=20)[1] == "摘要35"
    assert calls == [35]
    assert cs.get_chat_context(_msgs(56), session_id="s1", keep_recent=20)[1] == "摘要36"
    assert calls == [35, 36]
    assert len(cs._cache) == 1


# ── P1-9 反思检索路由到 episodic+where，不再扫全部集合 ──


def test_vector_search_reflection_routes_with_where():
    from shisi.memory.legacy.vector_memory import VectorMemory

    vm = VectorMemory.__new__(VectorMemory)
    vm._collections = {}
    seen: list = []

    async def fake_search(collection_name, query, top_k, where=None):
        seen.append((collection_name, where))
        return []

    vm._search = fake_search  # type: ignore[method-assign]
    asyncio.run(vm.search("洞察", filter_dict={"type": "reflection"}))
    assert seen == [("episodic_memory", {"type": "reflection"})]

    seen.clear()
    asyncio.run(vm.search("片段", filter_dict={"type": "episode"}))
    assert seen == [("episodic_memory", {"type": "episode"})]


# ── P1-6 画像同步 L0 信号 + 调度器单例 ──────────────────


def test_has_profile_signal_gates_sync_agent():
    from orchestrator.tool_gate import has_profile_signal

    assert has_profile_signal("我的生日是腊月初一")
    assert has_profile_signal("我是学生")
    assert not has_profile_signal("今天天气不错呀")
    assert not has_profile_signal("")


def test_profile_sync_dispatcher_is_singleton():
    from tools.builtin.profile_agent_tools import _profile_sync_dispatcher

    d1 = _profile_sync_dispatcher()
    d2 = _profile_sync_dispatcher()
    assert d1 is d2  # 旧实现每次调用新建 dispatcher → 限速状态即抛
