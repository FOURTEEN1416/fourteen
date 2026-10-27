"""公有 ``WeChatConnector.send_reply_segments`` API 契约（Connector 修复批）。

修复背景
--------
复审指出：拆条投递逻辑原以私有方法 ``_send_reply_segments`` 实现，外部（入站主链
publish 与主动线 wiring）无法以稳定公开入口复用。修复将其公有化为
``WeChatConnector.send_reply_segments``，**行为零变更**：

- 方法体逐一保留（``split_reply_for_wechat`` → 逐段 ``_send_text`` → 只回已受理前缀）；
- ``context_token`` 仍是 keyword-only 且默认 ``""`` → 默认走
  ``self._get_context_token(peer) or context_token``（现行为）；
- 两处内部调用点同步改为公有名（``wechat_connector.py`` 主链 publish 与主动线）。

仓库同批契约（见 ``tests/test_humanize_delay_tiers.py`` 与
``tests/test_proactive_segmented_delivery.py``）：**公有名是唯一入口，不留私有
别名 shim**。本文件独立复验该契约，并补钉「默认参数 = 现行为」这一条 ——
它正是「零行为变更」的可执行证据。
"""

from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import pytest

from wechat_direct import wechat_connector as wc
from wechat_direct.wechat_connector import WeChatConnector

PEER = "peer-1"


@pytest.fixture()
def connector(tmp_path, monkeypatch):
    """构造真连接器；``_segment_delay`` 置零（不真睡），token 置为已登录。"""
    c = wc.WeChatConnector(SimpleNamespace(), owner_user_id=42, session_dir=tmp_path)
    c.token = "test-token"
    monkeypatch.setattr(wc, "_segment_delay", lambda *a, **k: 0)
    return c


def _capture_sends(monkeypatch) -> list[dict]:
    """替换模块级 ``_send_text``，返回收集到的调用 kwargs 列表（默认全成功）。"""
    calls: list[dict] = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return {"ret": 0}

    monkeypatch.setattr(wc, "_send_text", fake_send)
    return calls


# ── 1. 公有入口存在；私有别名按契约不存在 ─────────────────────────


def test_public_entry_present_without_private_shim():
    """公有名可解析；契约禁止 ``_`` 私有别名（公有名是唯一 owner）。"""
    assert callable(getattr(WeChatConnector, "send_reply_segments", None))
    assert not hasattr(WeChatConnector, "_send_reply_segments"), (
        "契约要求单一 owner 公有化，不留 '_' 别名 shim"
    )


# ── 2. 签名零变更：peer/reply 位置参；context_token keyword-only 默认 "" ──


def test_signature_default_context_token_is_kwonly_empty():
    """默认值 = 现行为：``context_token`` 为 keyword-only 且默认 ``""``。"""
    params = inspect.signature(WeChatConnector.send_reply_segments).parameters
    assert list(params) == ["self", "peer", "reply", "context_token"]
    ct = params["context_token"]
    assert ct.kind is inspect.Parameter.KEYWORD_ONLY
    assert ct.default == ""


# ── 3. 默认 context_token：走存量 token（现行为） ─────────────────


def test_default_context_token_prefers_stored_token(connector, monkeypatch):
    """不传 ``context_token`` 时，用该 peer 的存量 context_token（现行为）。"""
    connector._context_tokens[PEER] = {"token": "STORED", "ts": time.time()}
    calls = _capture_sends(monkeypatch)
    connector.send_reply_segments(PEER, "在呢")
    assert calls[0]["context_token"] == "STORED"


def test_explicit_context_token_used_only_as_fallback(connector, monkeypatch):
    """存量缺失时回落到显式实参；显式只在 ``or`` 的右侧（现行为）。"""
    calls = _capture_sends(monkeypatch)
    connector.send_reply_segments(PEER, "在呢", context_token="EXPLICIT")
    assert calls[0]["context_token"] == "EXPLICIT"


# ── 4. 零行为变更：单行短回复 = 一条消息，返回原文 ────────────────


def test_single_short_reply_one_message_unchanged(connector, monkeypatch):
    calls = _capture_sends(monkeypatch)
    accepted = connector.send_reply_segments(PEER, "在呢")
    assert [c["text"] for c in calls] == ["在呢"]
    assert accepted == "在呢"


# ── 5. 只返回受理前缀：失败段及后续不入历史（与原私有实现一致） ──


def test_returns_only_accepted_prefix(connector, monkeypatch):
    calls: list[str] = []

    def send(**kwargs):
        calls.append(kwargs["text"])
        return {"ret": 0 if len(calls) == 1 else -2}

    monkeypatch.setattr(wc, "_send_text", send)
    accepted = connector.send_reply_segments(PEER, "第一段\n第二段\n第三段")
    assert calls == ["第一段", "第二段"]  # Q6 默认 3 段：第 2 段被拒后不再尝试第 3 段
    assert accepted == "第一段"
