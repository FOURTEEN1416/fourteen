"""W2 专项回归：模型接口契约 / 工具失败三态 / 对话生命周期（2026-09-27）。

四个已确认缺陷的钉子：
A【P1】独立兼容 provider（agnes/zhipu/xunfei/baidu/custom 直连）只有 async
  chat，无 chat_sync 也不可调用 → ``utils.llm_bridge.to_sync_callable`` 返回
  None → 事实抽取/日记/反思/摘要静默退化。修后：唯一桥经
  ``utils.async_utils`` 常驻共享循环补齐同步调用，且请求账号选择不被破坏。
B【P1】工具链只打 current_provider 且 provider 把异常吞成错误文案字典 →
  编排层把错误文案当「模型判无需工具」，把用户正在补齐提醒信息的 pending
  意图取消。修后：网关走链降级；provider 失败 raise ``ProviderError``；
  编排层区分 ProviderError / NoTool / ToolCalls 三态，故障不取消 pending。
C【P1/R】流式准备阶段（检索/摘要/L1 工具）在保护性 try/finally 之外 →
  取消或准备异常后用户行丢失。修后：锁内即建立 turn 上下文，统一 finally
  覆盖准备、生成、发布；准备阶段取消/异常也留用户行（真确认前缀语义不变）。
D【P2】记忆检索 to_thread+gather 无界，超时只是事后 slow 日志。修后：检索
  有界执行，超时本轮降级并记一条即时告警，不烧穿整轮预算。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.optimized_orchestrator import OptimizedOrchestrator
from shisi.memory.legacy.structured_memory import StructuredMemory
from tools.base_tool import ToolResult
from utils.local_time import now_local

# ══════════════════════════════════════════════════════════
#  公共替身
# ══════════════════════════════════════════════════════════


class _AsyncOnlyProvider:
    """独立兼容 provider 的接口形态：只有 async chat，无 chat_sync、无 __call__。

    与 ``llm_provider.OpenAICompatibleProvider`` 同构（llm_provider/__init__.py
    把独立 agnes/zhipu/xunfei/baidu/custom 直接构造成它）。
    """

    def __init__(self, reply: str = "用户喜欢猫，明天想去公园") -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def chat(self, query: str = "", **kwargs):
        self.prompts.append(query)
        return self.reply

    async def chat_stream(self, **kwargs):  # pragma: no cover - 形态占位
        yield self.reply


class _Registry:
    def __init__(self, tools: dict):
        self._tools = tools

    def get(self, name: str):
        return self._tools.get(name)

    def get_tools_by_permission(self, level: int):
        return [
            {"type": "function", "function": {"name": n}}
            for n in self._tools
        ]


class _Tools:
    def __init__(self, tools: dict | None = None):
        self.registry = _Registry(tools or {})
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, name: str, arguments: dict, affinity_level: int = 0,
                 caller_id: str = ""):
        self.calls.append((name, arguments))
        return ToolResult(True, data={"ok": name})


class _RecordingMemory:
    """带 write_chat_history_sync 记录的记忆替身（流式生命周期用）。"""

    def __init__(self):
        self.writes: list[dict] = []

    def write_chat_history_sync(self, **kwargs) -> bool:
        self.writes.append(kwargs)
        return True

    def get_recent_context(self, n, session_id="", character_id=""):
        return ""

    def retrieve_context(self, query, session_id, top_k, character_id=""):
        return {"facts": []}

    def get_chat_context(self, session_id="", character_id=""):
        return ([], "")


def _stream_orch(memory, llm):
    """流式生命周期测试用编排器（安全/PII/注入全过，prepare/_after 外部控制）。"""
    orch = OptimizedOrchestrator()
    orch._initialized = True
    orch.components = {
        "safety": SimpleNamespace(
            check_input=lambda text: SimpleNamespace(is_safe=True, category=""),
            check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
            safe_alternative=lambda category: "[拦截]",
        ),
        "pii": SimpleNamespace(anonymize=lambda text: (text, None)),
        "injection": SimpleNamespace(
            detect=lambda text: (False, None, None),
            sanitize=lambda text: text,
        ),
        "emotion": SimpleNamespace(analyze=lambda msg, ctx: None),
        "memory": memory,
        "rag": SimpleNamespace(retrieve=lambda query: {"results": []}),
        "persona": SimpleNamespace(build_system_prompt=lambda **kw: "sys"),
        "persona_extractor": None,
        "ase": SimpleNamespace(on_chat=lambda *a, **k: None),
        "world_info": None,
        "llm": llm,
    }
    return orch


async def _drain(orch, user_msg: str, session_id: str) -> list[dict]:
    events: list[dict] = []
    async for event in orch.process_message_stream(user_msg, session_id):
        events.append(event)
    return events


# ══════════════════════════════════════════════════════════
#  缺陷 A：async-only provider → 同步桥
# ══════════════════════════════════════════════════════════


def test_async_only_provider_bridge_captures_real_output(monkeypatch):
    """独立兼容 provider 必须能被唯一桥适配，且捕获真实出参（不接受「没报错」）。"""
    from llm_provider.openai_compatible_provider import OpenAICompatibleProvider
    from utils.llm_bridge import to_sync_callable

    provider = OpenAICompatibleProvider(
        provider_name="agnes", api_key="k", api_base="https://example.com/v1",
        model="agnes-3.0-flash",
    )
    seen: dict = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "真实摘要：用户喜欢猫"}}],
                "usage": {},
            }

    class _Client:
        async def post(self, url, json=None, **kwargs):
            seen["url"] = url
            seen["payload"] = json
            return _Resp()

    monkeypatch.setattr(
        OpenAICompatibleProvider, "_async_client",
        property(lambda self: _Client()),
    )

    fn = to_sync_callable(provider)
    assert fn is not None, (
        "async-only provider（无 chat_sync、不可调用）必须经唯一桥适配，"
        "旧判据直接返回 None → 记忆三件套静默退化"
    )
    # 记忆任务跑在无事件循环的后台线程 —— 必须经共享循环完成真实调用
    out: dict = {}

    def _run():
        out["v"] = fn("请总结：用户喜欢猫")

    th = threading.Thread(target=_run)
    th.start()
    th.join(timeout=10)
    assert not th.is_alive()
    assert out["v"] == "真实摘要：用户喜欢猫", "必须捕获真实出参"
    assert seen["url"].endswith("/chat/completions")
    assert seen["payload"]["messages"][-1]["content"] == "请总结：用户喜欢猫"


def test_memory_pipeline_adapts_async_only_provider(tmp_path):
    """记忆管道挂在 async-only provider 上时，抽取/日记/反思必须拿到可用 LLM。"""
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    sm = StructuredMemory(str(tmp_path / "w2a.db"))
    try:
        pipe = MemoryPipeline(
            structured_memory=sm,
            llm_gateway=_AsyncOnlyProvider(),
            fact_extract_interval=1,
        )
        assert pipe.fe.llm_func is not None, "FactExtractor 必须拿到 LLM"
        assert pipe.ds.llm_func is not None, "DiarySummarizer 必须拿到 LLM"
        assert pipe.reflection._llm is not None, "ReflectionEngine 必须拿到 LLM"
        # 不止「不为 None」：真实调用必须产出真文（旧缺陷正是静默空转）
        out: dict = {}

        def _run():
            out["v"] = pipe.fe.llm_func("提取事实")

        th = threading.Thread(target=_run)
        th.start()
        th.join(timeout=10)
        assert out["v"] == "用户喜欢猫，明天想去公园"
    finally:
        pipe._executor.shutdown(wait=True)
        sm.close()


def test_async_bridge_keeps_request_scoped_account(monkeypatch):
    """请求级账号选择不被破坏：ContextVar 里有用户网关时，桥必须打用户网关。"""
    from utils.llm_bridge import current_llm, request_llm, to_sync_callable

    platform = _AsyncOnlyProvider(reply="platform")
    user_gw = _AsyncOnlyProvider(reply="user-result")

    fn = to_sync_callable(platform)
    token = request_llm.set(user_gw)
    try:
        assert fn("总结") == "user-result", (
            "同步桥必须沿用本轮已授权的用户网关（current_llm），不得偷打平台网关"
        )
    finally:
        request_llm.reset(token)
    assert current_llm() is None


# ══════════════════════════════════════════════════════════
#  缺陷 B：工具链 fallback + 三态区分
# ══════════════════════════════════════════════════════════


def test_openai_compatible_chat_with_tools_raises_provider_failure(monkeypatch):
    """provider 工具调用失败必须 raise ProviderError，不得吞成错误文案字典。"""
    import httpx

    from llm_provider.llm_gateway import ProviderError
    from llm_provider.openai_compatible_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        provider_name="agnes", api_key="k", api_base="https://example.com/v1",
        model="agnes-3.0-flash",
    )

    class _Client:
        async def post(self, *a, **kw):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        OpenAICompatibleProvider, "_async_client",
        property(lambda self: _Client()),
    )
    with pytest.raises(ProviderError):
        asyncio.run(provider.chat_with_tools(query="六点叫我", tools=[{"type": "function"}]))


def test_llm_gateway_v2_chat_with_tools_raises_provider_failure(monkeypatch):
    """deepseek 网关工具调用同样必须 raise，mock/错误文案不得伪装成模型应答。"""
    from llm_provider.llm_gateway import LLMGatewayV2, ProviderError

    gw = LLMGatewayV2(api_key="k", api_base="https://example.com/v1", model="m")

    class _Client:
        async def post(self, *a, **kw):
            raise RuntimeError("network down")

    monkeypatch.setattr(LLMGatewayV2, "_async_client", property(lambda self: _Client()))
    with pytest.raises(ProviderError):
        asyncio.run(gw.chat_with_tools(query="q", tools=[{"type": "function"}]))


def _gateway_with_fakes(monkeypatch, first, second=None):
    from llm_provider.multi_provider_gateway import MultiProviderGateway

    g = MultiProviderGateway(fallback_chain=[], providers_config={})
    g._providers = {"agnes": first}
    if second is not None:
        g._providers["zhipu"] = second
    return g


def test_multi_gateway_chat_with_tools_falls_over_chain(monkeypatch):
    """工具调用必须与普通聊天同规走链：首家故障降级下一家（同一账号策略内）。"""
    from llm_provider.llm_gateway import ProviderError

    class _P1:
        async def chat_with_tools(self, **kw):
            raise ProviderError("（agnes 网络请求失败）")

    tool_calls = [{"function": {"name": "set_reminder", "arguments": "{}"}}]

    class _P2:
        async def chat_with_tools(self, **kw):
            return {"content": "", "tool_calls": tool_calls}

    g = _gateway_with_fakes(monkeypatch, _P1(), _P2())
    result = asyncio.run(g.chat_with_tools(query="q", tools=[{"type": "function"}]))
    assert result["tool_calls"] == tool_calls, "首家故障必须降级到链上下一家"
    assert g.current_provider_key == "zhipu", "成功后必须发布当前指针"


def test_multi_gateway_chat_with_tools_all_fail_raises(monkeypatch):
    from llm_provider.llm_gateway import ProviderError

    class _P:
        async def chat_with_tools(self, **kw):
            raise ProviderError("down")

    g = _gateway_with_fakes(monkeypatch, _P())
    with pytest.raises(ProviderError):
        asyncio.run(g.chat_with_tools(query="q", tools=[{"type": "function"}]))


def _orch_with_pending(sm, tools, llm):
    orch = OptimizedOrchestrator()
    orch.components = {
        "tools": tools,
        "memory": SimpleNamespace(structured_memory=sm),
    }
    return orch


def _pending_llm(payload_log: list, payloads: list):
    """依次返回 payloads 的终审替身；条目为 dict 直接返回，为 Exception 实例则抛出。"""
    calls = {"n": 0}

    async def chat_with_tools(**kwargs):
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        payload_log.append(kwargs)
        item = payloads[idx]
        if isinstance(item, Exception):
            raise item
        return item

    return SimpleNamespace(chat_with_tools=chat_with_tools)


_ERROR_DICT = {
    "content": "（agnes API 请求失败，错误代码 401）",
    "tool_calls": None,
}


def test_provider_failure_dict_keeps_pending_intent(tmp_path):
    """provider 故障（错误文案字典）≠「模型判无需工具」：pending 必须保留。

    场景：用户在补齐提醒信息（澄清中），此轮 provider 挂了——
    旧实现把错误文案当闲聊回复，把待澄清意图取消，用户前面说的话全丢。
    """
    from orchestrator import tool_gate

    sm = StructuredMemory(str(tmp_path / "w2b1.db"))
    try:
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "吃药"}, 1, "几点吃？")
        tools = _Tools({"set_reminder": object()})
        llm = _pending_llm([], [_ERROR_DICT])
        orch = _orch_with_pending(sm, tools, llm)
        result = asyncio.run(orch._run_tools_if_needed(
            llm, "下午三点", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert result == ("", "")
        pending = sm.get_active_pending_intent("s1")
        assert pending is not None, (
            "provider 故障不得取消 pending——错误文案被当「无需工具」是本缺陷本体"
        )
        assert tool_gate is not None
    finally:
        sm.close()


def test_provider_exception_keeps_pending_intent(tmp_path):
    """provider 抛异常（修复后网关的真实形态）同样保留 pending。"""
    from llm_provider.llm_gateway import ProviderError

    sm = StructuredMemory(str(tmp_path / "w2b2.db"))
    try:
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "吃药"}, 1, "几点吃？")
        tools = _Tools({"set_reminder": object()})
        llm = _pending_llm([], [ProviderError("所有 LLM 提供商工具调用均不可用")])
        orch = _orch_with_pending(sm, tools, llm)
        result = asyncio.run(orch._run_tools_if_needed(
            llm, "下午三点", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert result == ("", "")
        assert sm.get_active_pending_intent("s1") is not None
        assert tools.calls == [], "故障判定必须发生在执行副作用之前"
    finally:
        sm.close()


def test_explicit_no_tool_cancels_pending(tmp_path):
    """显式无工具（模型正常应答且不带工具）= 用户转移话题 → pending 照旧取消。"""
    sm = StructuredMemory(str(tmp_path / "w2b3.db"))
    try:
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "吃药"}, 1, "几点吃？")
        tools = _Tools({"set_reminder": object()})
        llm = _pending_llm([], [{"content": "好啦不聊这个了", "tool_calls": None}])
        orch = _orch_with_pending(sm, tools, llm)
        result = asyncio.run(orch._run_tools_if_needed(
            llm, "算了不说这个了", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert result == ("", "")
        assert sm.get_active_pending_intent("s1") is None, "显式无工具必须保留原取消语义"
    finally:
        sm.close()


def test_promise_recheck_provider_failure_keeps_pending(tmp_path):
    """防假承诺复核遇 provider 故障：不得把复核错误当「无需工具」取消 pending。"""
    from llm_provider.llm_gateway import ProviderError

    sm = StructuredMemory(str(tmp_path / "w2b4.db"))
    try:
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "吃药"}, 1, "几点吃？")
        tools = _Tools({"set_reminder": object()})
        llm = _pending_llm([], [
            {"content": "听到啦，我会提醒你的", "tool_calls": None},  # 空口承诺 → 强制复核
            ProviderError("复核时 provider 挂了"),
        ])
        orch = _orch_with_pending(sm, tools, llm)
        result = asyncio.run(orch._run_tools_if_needed(
            llm, "六点叫我起床", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert result == ("", "")
        assert sm.get_active_pending_intent("s1") is not None, (
            "复核阶段 provider 故障必须保留 pending"
        )
    finally:
        sm.close()


def test_tool_calls_fall_over_degraded_provider_and_fulfil(tmp_path, monkeypatch):
    """链上首家工具调用故障 → 降级下一家拿到真工具调用 → 正常执行结案。"""
    from datetime import timedelta

    import llm_provider.multi_provider_gateway as mg

    sm = StructuredMemory(str(tmp_path / "w2b5.db"))
    try:
        sm.upsert_pending_intent("s1", "set_reminder", {"content": "吃药"}, 1, "几点吃？")
        trigger = (now_local() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

        class _FlakyProvider:
            def __init__(self):
                self.n = 0

            async def chat_with_tools(self, **kw):
                self.n += 1
                from llm_provider.llm_gateway import ProviderError
                raise ProviderError("（agnes 网络请求失败）")

        class _OkProvider:
            async def chat_with_tools(self, **kw):
                return {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "set_reminder",
                            "arguments": json.dumps(
                                {"content": "吃药", "trigger_time": trigger},
                                ensure_ascii=False,
                            ),
                        }
                    }],
                }

        from tools.builtin.reminder_tool import ReminderTool

        flaky = _FlakyProvider()
        gw = mg.MultiProviderGateway(fallback_chain=[], providers_config={})
        gw._providers = {"agnes": flaky, "zhipu": _OkProvider()}

        tools = _Tools({"set_reminder": ReminderTool(sm)})
        orch = OptimizedOrchestrator()
        orch.components = {
            "tools": tools,
            "memory": SimpleNamespace(structured_memory=sm),
            "config": {},
        }
        result = asyncio.run(orch._run_tools_if_needed(
            gw, "下午三点吃药", "sys", [], affinity_level=2, session_key="s1",
        ))
        assert flaky.n == 1, "首家故障记一次，不得原地重试刷爆配额"
        assert gw.current_provider_key == "zhipu", "成功后必须发布当前指针"
        assert "set_reminder" in result[0], "工具调用必须真正执行并入 prompt"
        assert sm.get_active_pending_intent("s1") is None, "set_reminder 真成功才结案"
    finally:
        sm.close()


# ══════════════════════════════════════════════════════════
#  缺陷 C：流式取消时序矩阵
# ══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_during_prepare_persists_user_row(monkeypatch):
    """准备中取消：用户行必须落库，turn id 来自强锁后建立的上下文，无编造回复。"""
    memory = _RecordingMemory()
    orch = _stream_orch(memory, SimpleNamespace())
    entered = asyncio.Event()

    async def prepare(*args, **kwargs):
        entered.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    after_calls: list = []
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: after_calls.append(a))

    task = asyncio.create_task(_drain(orch, "明早六点叫我起床", "s-prepare"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(memory.writes) == 1, "准备阶段取消必须轻写一次用户行"
    w = memory.writes[0]
    assert w["user_msg"] == "明早六点叫我起床"
    assert w["reply"] == "", "无任何已确认前缀时不得编造回复"
    assert len(w["turn_id"]) == 12, "准备前必须已建立 turn 上下文"
    assert w["session_id"] == "s-prepare"
    assert after_calls == [], "未完成准备的轮次不得走全量 after_process"
    assert not orch._get_session_lock("s-prepare").locked(), "取消后不得泄漏会话锁"


@pytest.mark.asyncio
async def test_prepare_exception_persists_user_row_and_reports(monkeypatch):
    """准备阶段异常（非取消）：用户行落库 + 外层错误回执，不得静默吞掉用户轮。"""
    memory = _RecordingMemory()
    llm = SimpleNamespace(chat=AsyncMock(return_value="不该被调用"))

    async def _unexpected_stream(**kw):  # pragma: no cover
        yield ""

    llm.chat_stream = _unexpected_stream
    orch = _stream_orch(memory, llm)

    async def prepare(*args, **kwargs):
        raise RuntimeError("记忆检索崩溃")

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    events = await _drain(orch, "我今天很难过", "s-exc")

    assert len(memory.writes) == 1
    assert memory.writes[0]["user_msg"] == "我今天很难过"
    assert memory.writes[0]["reply"] == ""
    assert events[-1]["type"] == "done"
    assert events[-1]["reply"], "异常必须有可见回执，不得空完成"


@pytest.mark.asyncio
async def test_cancel_before_first_token_runs_full_after_process(monkeypatch):
    """首段前取消（准备已完成）：全量 after_process 兜底、确认前缀为空（既有语义）。"""
    memory = _RecordingMemory()
    orch = _stream_orch(memory, SimpleNamespace())
    generation_started = asyncio.Event()

    async def prepare(*args, **kwargs):
        return {
            "emotion_state": None, "system_prompt": "sys", "chat_history": [],
            "direct_reply": "", "ax_turn_id": "turn-pre-ok", "ax_reply_id": "reply-pre-ok",
        }

    async def slow_stream(**kw):
        generation_started.set()
        await asyncio.sleep(30)
        yield ""  # pragma: no cover

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    orch.components["llm"] = SimpleNamespace(chat_stream=slow_stream)
    after_calls: list = []
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: after_calls.append((a, kw)))

    task = asyncio.create_task(_drain(orch, "在吗", "s-first"))
    await asyncio.wait_for(generation_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(after_calls) == 1
    args, kw = after_calls[0]
    assert args[0] == "在吗"
    assert args[1] == "", "未确认任何 token 时前缀为空"
    assert kw["turn_id"] == "turn-pre-ok", "turn id 必须来自准备上下文"
    assert memory.writes == [], "全量 after_process 已覆盖，不得再轻写双份"


@pytest.mark.asyncio
async def test_cancel_mid_publish_keeps_only_acked_prefix(monkeypatch):
    """半段后取消：只保留传输已确认前缀（既有语义回归）。"""
    memory = _RecordingMemory()
    orch = _stream_orch(memory, SimpleNamespace())
    draft = "abcdefghABCDEFGHijklmn"

    async def prepare(*args, **kwargs):
        return {
            "emotion_state": None, "system_prompt": "sys", "chat_history": [],
            "direct_reply": "", "ax_turn_id": "turn-mid", "ax_reply_id": "reply-mid",
        }

    async def stream(**kw):
        yield draft

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    monkeypatch.setattr(orch, "_finalize_reply", lambda reply, cid: reply)
    after_calls: list = []
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: after_calls.append((a, kw)))

    gen = orch.process_message_stream("用户实际输入", "s-mid")
    token = await anext(gen)
    assert token["type"] == "token"
    token["_ack"]()  # 服务端已发送并确认前 8 字符
    await gen.aclose()

    assert len(after_calls) == 1
    args, kw = after_calls[0]
    assert args[1] == draft[:8], "半段后取消只保留已确认前缀"
    assert memory.writes == []


@pytest.mark.asyncio
async def test_normal_turn_writes_once_via_after_process_only(monkeypatch):
    """正常完成：只走全量 after_process，轻写路径不得触发（防双写）。"""
    memory = _RecordingMemory()
    orch = _stream_orch(memory, SimpleNamespace())

    async def prepare(*args, **kwargs):
        assert kwargs.get("turn_id"), "流式入口必须传入预生成的 turn id"
        return {
            "emotion_state": None, "system_prompt": "sys", "chat_history": [],
            "direct_reply": "好呀", "ax_turn_id": "turn-ok", "ax_reply_id": "reply-ok",
        }

    async def unexpected_stream(**kw):  # pragma: no cover
        raise AssertionError("直复不应再调主模型")
        yield ""

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    orch.components["llm"] = SimpleNamespace(chat_stream=unexpected_stream)
    after_calls: list = []
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: after_calls.append((a, kw)))

    events = await _drain(orch, "在吗", "s-ok")
    text = "".join(e["content"] for e in events if e["type"] == "token")
    assert text == "好呀"
    assert events[-1]["type"] == "done"
    assert len(after_calls) == 1
    assert after_calls[0][0][1] == "好呀"
    assert memory.writes == []


@pytest.mark.asyncio
async def test_cancel_during_prepare_writes_real_db_row(tmp_path, monkeypatch):
    """真实落库版：准备中取消后，StructuredMemory 里必须有这条用户行。"""
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    sm = StructuredMemory(str(tmp_path / "w2c.db"))
    pipe = MemoryPipeline(vector_memory=SimpleNamespace(), structured_memory=sm)
    orch = _stream_orch(pipe, SimpleNamespace())
    entered = asyncio.Event()

    async def prepare(*args, **kwargs):
        entered.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    try:
        task = asyncio.create_task(_drain(orch, "别忘了我生日", "s-db"))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        rows = sm.get_chats_by_session("s-db")
        user_rows = [r for r in rows if r["role"] == "user"]
        assert len(user_rows) == 1, "取消后用户行必须真实落库"
        assert user_rows[0]["content"] == "别忘了我生日"
        assert user_rows[0]["turn_id"], "落库行必须带 turn id"
    finally:
        pipe._executor.shutdown(wait=True)
        sm.close()


# ══════════════════════════════════════════════════════════
#  缺陷 D：检索有界执行
# ══════════════════════════════════════════════════════════


def _prepare_orch(memory, rag):
    """真 _prepare_context 可跑的最小组件集（emotion 用替身引擎）。"""
    orch = OptimizedOrchestrator()
    orch.components = {
        "safety": SimpleNamespace(
            check_input=lambda text: SimpleNamespace(is_safe=True, category=""),
            safe_alternative=lambda c: "[拦截]",
        ),
        "pii": SimpleNamespace(anonymize=lambda text: (text, None)),
        "injection": SimpleNamespace(
            detect=lambda text: (False, None, None),
            sanitize=lambda text: text,
        ),
        "emotion": SimpleNamespace(analyze=lambda msg, ctx: None),
        "memory": memory,
        "rag": rag,
        "persona": SimpleNamespace(build_system_prompt=lambda **kw: "sys"),
        "persona_extractor": None,
        "ase": SimpleNamespace(on_chat=lambda *a, **k: None),
        "world_info": None,
        "llm": SimpleNamespace(),
    }
    return orch


def test_memory_retrieval_bounded_and_degrades(monkeypatch):
    """记忆检索挂死：本轮必须在预算内返回（降级空记忆），不得烧穿整轮预算。"""
    from orchestrator import optimized_orchestrator as oo

    calls = {"retrieve": 0}

    def slow_retrieve(*args, **kwargs):
        calls["retrieve"] += 1
        time.sleep(1.5)
        return {"facts": ["late"]}

    memory = SimpleNamespace(
        get_recent_context=lambda n, session_id="", character_id="": "",
        retrieve_context=slow_retrieve,
        get_chat_context=lambda session_id="", character_id="": ([], ""),
    )
    rag = SimpleNamespace(retrieve=lambda query: {"results": []})
    orch = _prepare_orch(memory, rag)
    monkeypatch.setattr(oo, "MEMORY_RETRIEVE_TIMEOUT_SECONDS", 0.2)

    async def _main():
        t0 = time.perf_counter()
        ctx = await orch._prepare_context("你好", "s-d", "default")
        return time.perf_counter() - t0, ctx

    elapsed, ctx = asyncio.run(_main())
    assert calls["retrieve"] == 1
    # 注意在 await 内测时长：asyncio.run 收尾会 join 默认执行器线程（慢线程
    # 仍在后台跑完），那是资源回收语义，不属于本轮预算。
    assert elapsed < 1.0, f"检索挂死必须被有界截断（实测 {elapsed:.2f}s）"
    assert ctx["system_prompt"], "超时后本轮生成必须继续（降级而非失败）"


def test_memory_retrieval_timeout_logs_once(monkeypatch, caplog):
    """超时必须留一条即时告警（不是检索完成后的 slow 事后日志）。"""
    import logging

    from orchestrator import optimized_orchestrator as oo

    memory = SimpleNamespace(
        get_recent_context=lambda n, session_id="", character_id="": "",
        retrieve_context=lambda *a, **k: time.sleep(1.0),
        get_chat_context=lambda session_id="", character_id="": ([], ""),
    )
    orch = _prepare_orch(memory, SimpleNamespace(retrieve=lambda q: {"results": []}))
    monkeypatch.setattr(oo, "MEMORY_RETRIEVE_TIMEOUT_SECONDS", 0.1)

    with caplog.at_level(logging.WARNING, logger="orchestrator.optimized"):
        asyncio.run(orch._prepare_context("你好", "s-warn", "default"))
    assert any("检索超时" in r.message and "memory" in r.message for r in caplog.records), (
        "超时必须即时告警，便于区分「检索挂死」与「检索慢」"
    )


def test_legacy_rag_fallback_bounded(monkeypatch):
    """无 retrieve_async 的旧 RAG 同样必须有界（同类风险一并收口）。"""
    from orchestrator import optimized_orchestrator as oo

    def slow_rag(query):
        time.sleep(1.5)
        return {"results": []}

    memory = SimpleNamespace(
        get_recent_context=lambda n, session_id="", character_id="": "",
        retrieve_context=lambda *a, **k: {"facts": []},
        get_chat_context=lambda session_id="", character_id="": ([], ""),
    )
    orch = _prepare_orch(memory, SimpleNamespace(retrieve=slow_rag))
    monkeypatch.setattr(oo, "RAG_RETRIEVE_TIMEOUT_SECONDS", 0.2)

    async def _main():
        t0 = time.perf_counter()
        ctx = await orch._prepare_context("你好", "s-rag", "default")
        return time.perf_counter() - t0, ctx

    elapsed, ctx = asyncio.run(_main())
    assert elapsed < 1.0, f"旧 RAG 检索挂死必须被有界截断（实测 {elapsed:.2f}s）"
    assert ctx["system_prompt"]
