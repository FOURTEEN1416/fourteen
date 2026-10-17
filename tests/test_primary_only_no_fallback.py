"""主动决策禁 in-call 降级 + 按调用超时（2026-10-16 三连同义根治批 A+D）红测。

A 生产实证：tick 时 agnes 未熔断 → 事前闸放行 → 决策调用当场 15s 超时 →
MultiGateway 同调用内静默降级智谱 glm-4.5-flash → 模板腔消息直达用户
（当日 10:36/11:41/16:14 三条同义全部此形态）。用户裁决「宁可不发也不让
降级模型代写」未落到此路径。修法：gateway.chat(allow_fallback=False) 只打
链首、失败抛 ProviderError，scheduler 捕获记 model_degraded 跳过。

D：决策调用 timeout=25s（agnes 健康分布 3.8~19.8s，主链 15s 误杀慢尾）。
httpx 陷阱：per-request timeout=None 是「禁用超时」而非「用客户端默认」，
故仅 truthy 时透传——本文件钉死该语义。
"""

from __future__ import annotations

import pytest

from llm_provider.llm_gateway import ProviderError
from proactive.llm_proactive import (
    PROACTIVE_DECISION_TIMEOUT,
    _candidate_score,
    _llm_chat_sync,
    _pick_best_candidate,
    decide_proactive,
    parse_decision,
)

# ── A：网关 primary_only ──────────────────────────────────────


class _StubProvider:
    def __init__(self, result=None, error=None, record=None):
        self._result = result
        self._error = error
        self._record = record  # list: 记录每次 chat 收到的 kwargs
        self.calls = 0

    async def chat(self, query="", system_prompt="", history=None, messages=None,
                   temperature=0.85, max_tokens=2048, tools=None, model=None,
                   timeout=None):
        self.calls += 1
        if self._record is not None:
            self._record.append({"timeout": timeout, "model": model})
        if self._error is not None:
            raise self._error
        return self._result


def _make_gateway(head, tail):
    from llm_provider.multi_provider_gateway import MultiProviderGateway

    gw = MultiProviderGateway(
        fallback_chain=["agnes", "zhipu"],
        providers_config={"agnes": {"api_key": "k"}, "zhipu": {"api_key": "k"}},
        inherit_platform=False,
    )
    gw._providers["agnes"] = head
    gw._providers["zhipu"] = tail
    return gw


class TestGatewayPrimaryOnly:
    def test_head_failure_raises_and_tail_never_called(self):
        """A 核心：链首失败 → ProviderError；链尾（智谱）零调用。"""
        head = _StubProvider(error=RuntimeError("模型请求失败，未生成角色回复"))
        tail = _StubProvider(result="智谱模板句")
        gw = _make_gateway(head, tail)

        async def _run():
            return await gw.chat(
                query="q", system_prompt="s", allow_fallback=False
            )

        import asyncio

        with pytest.raises(ProviderError) as ei:
            asyncio.run(_run())
        assert "primary_only" in str(ei.value)
        assert head.calls == 1
        assert tail.calls == 0, "禁降级时链尾必须零调用"

    def test_head_success_returns_without_tail(self):
        """链首成功 → 返回值；链尾零调用。"""
        head = _StubProvider(result="agnes 有话题意识的句子")
        tail = _StubProvider(result="智谱模板句")
        gw = _make_gateway(head, tail)

        import asyncio

        out = asyncio.run(gw.chat(query="q", system_prompt="s", allow_fallback=False))
        assert out == "agnes 有话题意识的句子"
        assert tail.calls == 0

    def test_default_allow_fallback_unchanged(self):
        """默认 allow_fallback=True：链首失败自动落链尾（主链行为不回归）。"""
        head = _StubProvider(error=RuntimeError("boom"))
        tail = _StubProvider(result="fallback ok")
        gw = _make_gateway(head, tail)

        import asyncio

        out = asyncio.run(gw.chat(query="q", system_prompt="s"))
        assert out == "fallback ok"

    def test_breaker_still_recorded_under_primary_only(self):
        """primary_only 下熔断记账照常（后续 tick 的事前闸仍能看见）。"""
        head = _StubProvider(error=RuntimeError("boom"))
        tail = _StubProvider(result="x")
        gw = _make_gateway(head, tail)

        import asyncio

        with pytest.raises(ProviderError):
            asyncio.run(gw.chat(query="q", system_prompt="s", allow_fallback=False))
        key, ok = gw.primary_provider_status()
        assert key == "agnes" and ok is False, "失败后熔断必须开（供事前闸）"


class TestTimeoutPassthrough:
    def test_truthy_timeout_reaches_provider(self):
        """D：truthy timeout 透传到 provider.chat。"""
        record: list[dict] = []
        head = _StubProvider(result="ok", record=record)
        tail = _StubProvider(result="x")
        gw = _make_gateway(head, tail)

        import asyncio

        asyncio.run(
            gw.chat(query="q", system_prompt="s", timeout=25.0)
        )
        assert record and record[0]["timeout"] == 25.0

    def test_none_timeout_not_forwarded(self):
        """httpx 陷阱（M3 假阴性自纠）：timeout=None 时网关不得注入覆盖。

        用哨兵默认值区分「显式传 None」vs「省略」：正确实现（网关仅在
        timeout 非 None 时注入）→ provider 收到哨兵；退化为无条件透传 →
        provider 收到 None（红）。"""
        seen: list = []

        class _SentinelProvider:
            async def chat(self, query="", system_prompt="", history=None, messages=None,
                           temperature=0.85, max_tokens=2048, tools=None, model=None,
                           timeout="__UNSET__"):
                seen.append(timeout)
                return "ok"

        head = _SentinelProvider()
        tail = _StubProvider(result="x")
        gw = _make_gateway(head, tail)

        import asyncio

        asyncio.run(gw.chat(query="q", system_prompt="s"))  # 不传 timeout
        assert seen and seen[0] == "__UNSET__", (
            f"网关向 provider 显式注入了 None timeout（httpx 禁用超时陷阱）: {seen}"
        )

    def test_chat_sync_threads_params(self):
        """chat_sync 同步入口同参透传。"""
        record: list[dict] = []
        head = _StubProvider(result="ok", record=record)
        tail = _StubProvider(result="x")
        gw = _make_gateway(head, tail)
        gw.chat_sync(query="q", system_prompt="s", timeout=25.0)
        assert record and record[0]["timeout"] == 25.0

    def test_decision_timeout_constant_pinned(self):
        assert PROACTIVE_DECISION_TIMEOUT == 25.0


# ── A：_llm_chat_sync / decide_proactive 接线语义 ──────────────


class _KwargsLLM:
    """签名支持 allow_fallback/timeout 的假网关（记录透传值）。"""

    def __init__(self, result="{}", error=None):
        self.kwargs: dict = {}
        self._result = result
        self._error = error

    def chat_sync(self, query="", system_prompt="", temperature=0.4,
                  allow_fallback=True, timeout=None):
        self.kwargs = {"allow_fallback": allow_fallback, "timeout": timeout}
        if self._error:
            raise self._error
        return self._result


class _LegacyLLM:
    """旧式签名（不支持新参）——新参不得透传（向后兼容）。"""

    def __init__(self):
        self.called_with: dict = {}

    def chat_sync(self, query="", system_prompt="", temperature=0.4):
        self.called_with = {"query": query, "system_prompt": system_prompt}
        return "{}"


class TestDecisionCallWiring:
    def test_llm_chat_sync_passes_supported_kwargs(self):
        llm = _KwargsLLM(result="{}")
        _llm_chat_sync(
            llm, "s", "u", strict=True, allow_fallback=False, timeout=25.0
        )
        assert llm.kwargs == {"allow_fallback": False, "timeout": 25.0}

    def test_llm_chat_sync_legacy_signature_not_broken(self):
        llm = _LegacyLLM()
        out = _llm_chat_sync(
            llm, "s", "u", strict=True, allow_fallback=False, timeout=25.0
        )
        assert out == "{}"
        assert "allow_fallback" not in llm.called_with

    def test_strict_propagates_provider_error(self):
        """strict=True：ProviderError 向上抛（scheduler 捕获记 model_degraded）。"""
        llm = _KwargsLLM(error=ProviderError("primary_only: agnes failed"))
        with pytest.raises(ProviderError):
            _llm_chat_sync(llm, "s", "u", strict=True)

    def test_non_strict_swallows_as_before(self):
        """strict=False（默认）：失败吞掉返回空串——旧调用方行为不变。"""
        llm = _KwargsLLM(error=ProviderError("x"))
        assert _llm_chat_sync(llm, "s", "u") == ""

    def test_decide_proactive_backward_compatible(self):
        llm = _KwargsLLM(
            result='{"should_contact": false, "reason": "quiet", "wait_minutes": 60}'
        )
        d = decide_proactive(llm, "ctx")
        assert d["should_contact"] is False and d["wait_minutes"] == 60

    def test_decide_proactive_strict_raise(self):
        llm = _KwargsLLM(error=ProviderError("primary_only"))
        with pytest.raises(ProviderError):
            decide_proactive(llm, "ctx", strict=True, allow_fallback=False)


# ── B：多候选竞争择优 ──────────────────────────────────────────


class TestCandidateCompetition:
    def test_parse_decision_extracts_candidates(self):
        raw = (
            '{"should_contact": true, "message": "在干嘛", "reason": "r", '
            '"candidates": ["你之前问的辩论赛准备得怎么样了", "在吗", 123]}'
        )
        d = parse_decision(raw)
        assert d["candidates"] == ["你之前问的辩论赛准备得怎么样了", "在吗"]

    def test_generic_opener_penalized(self):
        assert _candidate_score("你今天感觉怎么样？", None) <= -2.0
        assert _candidate_score("在吗", None) <= -2.0

    def test_hook_keyword_boosts_score(self):
        hooked = _candidate_score("你之前问的辩论赛准备得怎么样了", ["辩论赛"])
        generic = _candidate_score("今天过得怎么样", ["辩论赛"])
        assert hooked > generic

    def test_pick_best_replaces_generic_message(self):
        decision = {
            "should_contact": True,
            "message": "你今天感觉怎么样？",  # 泛问候（生产病灶形态）
            "reason": "r",
            "candidates": ["你之前问的辩论赛准备得怎么样了"],
        }
        out = _pick_best_candidate(decision, ["辩论赛准备"])
        assert out["message"] == "你之前问的辩论赛准备得怎么样了"

    def test_pick_best_without_candidates_unchanged(self):
        decision = {"should_contact": True, "message": "具体话题句", "reason": "r", "candidates": []}
        out = _pick_best_candidate(decision, ["kw"])
        assert out["message"] == "具体话题句"

    def test_decide_proactive_picks_best_when_keywords_given(self):
        raw = (
            '{"should_contact": true, "message": "在干嘛", "reason": "r", '
            '"candidates": ["你之前问的辩论赛准备得怎么样了"]}'
        )
        llm = _KwargsLLM(result=raw)
        d = decide_proactive(llm, "ctx", hook_keywords=["辩论赛准备"])
        assert d["message"] == "你之前问的辩论赛准备得怎么样了"

    def test_decide_proactive_picks_even_without_keywords(self):
        """2026-10-17 批契约升级：候选竞争不再被 hook_keywords 门卡死——
        生产实证活材料空仓时（keywords 空）泛问候 message 直通连发 4 条。
        无 keywords 时泛问候罚分与长度分照常生效，具体候选照样压掉泛问候。"""
        raw = (
            '{"should_contact": true, "message": "在干嘛", "reason": "r", '
            '"candidates": ["你之前问的辩论赛准备得怎么样了"]}'
        )
        llm = _KwargsLLM(result=raw)
        d = decide_proactive(llm, "ctx")  # 无 keywords → 择优仍触发（新契约）
        assert d["message"] == "你之前问的辩论赛准备得怎么样了"
