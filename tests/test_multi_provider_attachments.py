"""多供应商网关 attachments 支持回归测试（2026-09-15 线上故障）。

故障现象：微信任何消息都回「（处理消息时出现异常, 请稍后重试）」，日志栈为
    orchestrator/optimized_orchestrator.py:810  request_llm.chat(attachments=...)
    TypeError: MultiProviderGateway.chat() got an unexpected keyword argument 'attachments'

成因：w3 的多模态图片通道提交只给 LLMGateway 加了 attachments，漏改 MultiProviderGateway
（线上因配了多家 key，实际走的是后者）。且 orchestrator 对纯文本消息也会传 attachments=[]，
故**每条消息**都失败。本文件锁死该接口契约。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from llm_provider.multi_provider_gateway import MultiProviderGateway, _merge_attachments

_IMG = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


class _FakeProvider:
    """记录收到的实参并返回固定文本，避免真实调用 LLM API"""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


def _gateway_with(provider) -> MultiProviderGateway:
    """绕过 __init__（避免读配置/建真实 provider），只装一个假 provider"""
    gw = MultiProviderGateway.__new__(MultiProviderGateway)
    gw._providers = {"fake": provider}
    gw._current_index = 0
    # P1-2 熔断状态：chat() 会读 _breaker_open_until，绕过 __init__ 时必须自带
    gw._breaker_failures = {}
    gw._breaker_open_until = {}
    return gw


# ═══════════════════════════════════════════════════════
# _merge_attachments 纯函数
# ═══════════════════════════════════════════════════════


def test_no_attachments_keeps_original():
    assert _merge_attachments("hi", "sys", None, None, None) == (None, "hi")


def test_empty_attachments_keeps_original():
    """空列表（纯文本消息走的就是这条）→ 原样透传，不构建 messages"""
    assert _merge_attachments("hi", "sys", None, None, []) == (None, "hi")


def test_builds_messages_keeping_system_and_history():
    history = [{"role": "user", "content": "早上好"}]
    msgs, query = _merge_attachments("看图", "人设", history, None, [_IMG])

    assert query == "", "query 已并入 messages，应清空避免重复"
    assert msgs[0] == {"role": "system", "content": "人设"}
    assert msgs[1] == history[0]
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"][0] == {"type": "text", "text": "看图"}
    assert msgs[-1]["content"][1] is _IMG


def test_image_only_without_query():
    msgs, query = _merge_attachments("", "", None, None, [_IMG])
    assert query == ""
    assert msgs[-1]["content"] == [_IMG], "无文本时不应塞入空 text part"


def test_explicit_messages_win():
    explicit = [{"role": "user", "content": "调用方已给定"}]
    msgs, query = _merge_attachments("q", "s", None, explicit, [_IMG])
    assert msgs is explicit and query == "q"


# ═══════════════════════════════════════════════════════
# 网关接口契约（本次故障的直接护栏）
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_chat_accepts_empty_attachments():
    """故障复现点：attachments=[] 此前直接 TypeError，现在必须正常返回"""
    provider = _FakeProvider("你好呀")
    gw = _gateway_with(provider)

    out = await gw.chat(query="你好", system_prompt="sys", attachments=[])

    assert out == "你好呀"
    assert provider.calls[0]["query"] == "你好"
    assert provider.calls[0]["messages"] is None


@pytest.mark.asyncio
async def test_chat_forwards_image_via_messages():
    provider = _FakeProvider("我看到图了")
    gw = _gateway_with(provider)

    out = await gw.chat(query="这是什么", system_prompt="人设", attachments=[_IMG])

    assert out == "我看到图了"
    sent = provider.calls[0]
    assert sent["query"] == ""
    assert sent["messages"][-1]["content"][1] is _IMG


@pytest.mark.asyncio
async def test_chat_still_passes_plain_params():
    """无 attachments 时不得改变原有传参行为"""
    provider = _FakeProvider("ok")
    gw = _gateway_with(provider)

    await gw.chat(query="q", system_prompt="s", history=[{"role": "user", "content": "h"}],
                  temperature=0.5, max_tokens=100, tools=None, model="m")

    sent = provider.calls[0]
    assert sent["query"] == "q"
    assert sent["system_prompt"] == "s"
    assert sent["temperature"] == 0.5
    assert sent["model"] == "m"


def test_signatures_accept_attachments():
    """三个公共接口都必须显式声明 attachments（防止再次漏改）"""
    import inspect

    for name in ("chat", "chat_stream", "chat_with_tools"):
        sig = inspect.signature(getattr(MultiProviderGateway, name))
        assert "attachments" in sig.parameters, f"{name} 缺少 attachments 形参"
