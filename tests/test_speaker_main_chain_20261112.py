"""A1 主链拆条与受理单点（2026-11-12）。

覆盖契约：
- ``send_reply_segments_result`` 返回同一次发送的 ``DeliveryResult``；
- 部分段失败 → ``accepted=False`` 且只保留已受理前缀；
- 全部成功 → ``accepted=True``，``accepted_text`` 为各段换行拼接；
- 主链 ``publish`` 只拆一次条（由 ``Speaker.express`` 触发），不再二次拆条；
- ``send_reply_segments_result`` 不自持 ``_peer_lock``（调用方持锁契约不变）；
- 空正文 → ``accepted=False`` 且不发送任何段。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wechat_direct import wechat_connector as wc

PEER = "peer-a1"
SESSION_KEY = "42:peer-a1"


@pytest.fixture()
def connector(tmp_path, monkeypatch):
    """真连接器 + 网络/凭证/节奏全桩；拆条上限固定 3（默认值）。"""
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(
        SimpleNamespace(), owner_user_id=42, session_dir=tmp_path
    )
    c.token = "test-token"
    monkeypatch.setattr(c, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(c, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(c, "_humanize_for", lambda peer: None)
    monkeypatch.setattr(c, "_split_cap_for", lambda peer, humanize=None: 3)
    monkeypatch.setattr(wc, "_segment_delay", lambda *a, **k: 0)
    return c


# ---------------------------------------------------------------------------
# 1. DeliveryResult：部分失败 / 全部成功 / 空正文
# ---------------------------------------------------------------------------


def test_partial_failure_keeps_only_accepted_prefix(connector, monkeypatch):
    calls: list[str] = []

    def send(**kwargs):
        calls.append(kwargs["text"])
        return (
            {"ret": 0}
            if len(calls) == 1
            else {"ret": -2, "errmsg": "prepare failed"}
        )

    monkeypatch.setattr(wc, "_send_text", send)

    result = connector.send_reply_segments_result(PEER, "第一段\n第二段")

    assert calls == ["第一段", "第二段"], "第 2 段失败后不得继续发送"
    assert result.accepted is False
    assert result.accepted_text == "第一段"
    assert result.segments == ("第一段",)
    assert result.channel == "reply"
    assert "ret=-2" in result.reason


def test_all_segments_accepted_returns_joined_text(connector, monkeypatch):
    calls: list[str] = []

    def send(**kwargs):
        calls.append(kwargs["text"])
        return {"ret": 0}

    monkeypatch.setattr(wc, "_send_text", send)

    result = connector.send_reply_segments_result(PEER, "第一段\n第二段")

    assert calls == ["第一段", "第二段"]
    assert result.accepted is True
    assert result.accepted_text == "第一段\n第二段"
    assert result.segments == ("第一段", "第二段")
    assert result.channel == "reply"
    assert result.reason == ""


def test_empty_reply_is_not_accepted_and_sends_nothing(connector, monkeypatch):
    sends: list[dict] = []
    monkeypatch.setattr(
        wc, "_send_text", lambda **kwargs: sends.append(kwargs) or {"ret": 0}
    )

    result = connector.send_reply_segments_result(PEER, "   ")

    assert sends == []
    assert result.accepted is False
    assert result.accepted_text == ""
    assert result.segments == ()
    # 薄包装保持旧空文本行为：返回空串而不是异常。
    assert connector.send_reply_segments(PEER, "   ") == ""


# ---------------------------------------------------------------------------
# 2. 主链 publish：只拆一次条，完整性与账本取自同一结果
# ---------------------------------------------------------------------------


def test_publish_splits_reply_only_once(connector, monkeypatch):
    original = wc.split_reply_for_wechat
    split_calls: list[tuple[str, int | None]] = []

    def counting_split(reply, **kwargs):
        split_calls.append((reply, kwargs.get("max_segments")))
        return original(reply, **kwargs)

    monkeypatch.setattr(wc, "split_reply_for_wechat", counting_split)
    sent: list[str] = []
    monkeypatch.setattr(
        wc, "_send_text", lambda **kwargs: sent.append(kwargs["text"]) or {"ret": 0}
    )

    def manager(
        mgr, uid, text, attachments=None, reply_sender=None, message_id="",
        typing_sender=None,
    ):
        reply_sender("第一段\n第二段")
        return {"reply": "第一段\n第二段", "character_id": "charA"}

    monkeypatch.setattr(wc, "_call_user_manager", manager)

    connector._handle_message_serial(
        {"item_list": [{"type": 1, "text_item": {"text": "你好"}}]},
        "m-a1", PEER, "ctx",
    )

    assert len(split_calls) == 1, "publish 不得二次拆条（旧实现会拆 2 次）"
    assert split_calls[0] == ("第一段\n第二段", 3)
    assert sent == ["第一段", "第二段"], "拆分结果必须真正逐段发送"


def test_intent_carries_humanize_scope(connector, monkeypatch):
    """Speaker 收到的意图必须带 humanize_scope 解析出的会话键与角色。"""
    from speaker import Speaker

    seen: dict[str, object] = {}

    def fake_express(self, intent, text, *, max_segments=None):
        seen["source"] = intent.source
        seen["session_key"] = intent.session_key
        seen["character_id"] = intent.character_id
        seen["max_segments"] = max_segments
        return [text]

    monkeypatch.setattr(Speaker, "express", fake_express)
    monkeypatch.setattr(wc, "_send_text", lambda **kwargs: {"ret": 0})

    result = connector.send_reply_segments_result(PEER, "在呢")

    assert seen == {
        "source": "reply",
        "session_key": SESSION_KEY,
        "character_id": "default",
        "max_segments": 3,
    }
    assert result.accepted is True


# ---------------------------------------------------------------------------
# 3. 锁契约：方法内不得获取 _peer_lock（调用方持锁）
# ---------------------------------------------------------------------------


def test_send_reply_segments_result_does_not_enter_peer_lock(
    connector, monkeypatch
):
    entered: list[str] = []

    class _RecordingLock:
        def __enter__(self):
            entered.append("enter")
            return self

        def __exit__(self, *exc_info):
            entered.append("exit")
            return False

    monkeypatch.setattr(connector, "_peer_lock", lambda key: _RecordingLock())
    monkeypatch.setattr(wc, "_send_text", lambda **kwargs: {"ret": 0})

    result = connector.send_reply_segments_result(PEER, "在呢")

    assert result.accepted is True
    assert entered == [], "send_reply_segments_result 不自持 _peer_lock"
