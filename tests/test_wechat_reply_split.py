"""「一句一句发」回复拆分回归（2026-09-19 用户反馈）。

用户原话：「一个正常人，怎么会一次性发那么回一大段？……不应该一句一句的吗？」
并给出真实反例（5 行一次发出）。
"""

from __future__ import annotations


def _split(reply, **kw):
    from wechat_direct.wechat_connector import split_reply_for_wechat

    return split_reply_for_wechat(reply, **kw)


# ── 基本行为 ──────────────────────────────────────────────

def test_short_reply_stays_single():
    assert _split("在呢") == ["在呢"]
    assert _split("还没呢，正想着你呢") == ["还没呢，正想着你呢"]


def test_empty_returns_empty_list():
    assert _split("") == []
    assert _split("   \n  ") == []


def test_user_reported_example_is_split():
    """用户贴的真实反例：5 行一次发出 → 必须拆成多条。"""
    reply = (
        "就是...你打字的时候，停顿比较长\n\n"
        "而且，声音听起来也不太精神\n\n"
        "可能是我多想了\n\n"
        "那你自己注意别中暑了\n\n"
        "我继续听雨了"
    )
    segs = _split(reply)
    assert len(segs) > 1, "整段多行不得作为一条发出"
    assert len(segs) <= 4, "段数须受上限约束，避免刷屏"
    assert all(s.strip() for s in segs)
    # 内容无损（拼接后应包含全部原文关键词）
    joined = "".join(segs)
    for kw in ("停顿比较长", "不太精神", "多想了", "中暑", "听雨"):
        assert kw in joined, kw


def test_long_single_line_split_at_sentence_enders():
    reply = "早上好呀。今天天气不错，你要不要出去走走？我刚刚看了下窗外，太阳很大。"
    segs = _split(reply, max_chars=20, max_segments=4)
    assert len(segs) >= 2
    assert all(len(s) <= 20 for s in segs[:-1])   # 末段可能因合并兜底而略长


def test_segments_capped():
    reply = "\n".join(f"第{i}句" for i in range(12))
    segs = _split(reply, max_segments=4)
    assert 1 < len(segs) <= 4, "超出上限的应并入最后一段，而不是继续刷屏"
    for i in range(12):
        assert f"第{i}句" in "".join(segs)


def test_no_content_loss_on_merge():
    reply = "一。" * 30
    segs = _split(reply, max_chars=10, max_segments=3)
    assert len(segs) == 3
    assert "".join(segs) == reply


def test_no_newlines_in_any_segment():
    """不得把换行留在消息里（微信里会显示成奇怪的空行）。"""
    reply = "第一句\n\n第二句\n\n第三句"
    for seg in _split(reply):
        assert "\n" not in seg


def test_partial_reply_records_only_api_accepted_segments(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(SimpleNamespace())
    c.token = "test-token"
    monkeypatch.setattr(c, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(c, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(c, "_schedule_followup", lambda *a, **kw: None)
    monkeypatch.setattr(wc, "_segment_delay", lambda text: 0)
    accepted = []
    transport_calls = []

    def send(**kwargs):
        transport_calls.append(kwargs["text"])
        return {"ret": 0 if len(transport_calls) == 1 else -2}

    def manager(mgr, uid, text, attachments=None, reply_sender=None):
        assert reply_sender is not None
        reply = reply_sender("第一段\n第二段\n第三段")
        accepted.append(reply)
        return {"reply": reply, "character_id": "charA"}

    monkeypatch.setattr(wc, "_send_text", send)
    monkeypatch.setattr(wc, "_call_user_manager", manager)
    c._handle_message({
        "message_type": 1, "message_id": "partial", "from_user_id": "peer",
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    })
    assert transport_calls == ["第一段", "第二段"]
    assert accepted == ["第一段"]


def test_same_peer_holds_turn_lock_through_generation_and_sending(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(SimpleNamespace())
    c.token = "test-token"
    monkeypatch.setattr(c, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(c, "_schedule_followup", lambda *a, **kw: None)
    monkeypatch.setattr(wc, "_segment_delay", lambda text: 0)
    calls = []

    def manager(mgr, uid, text, attachments=None, reply_sender=None):
        # 同一 RLock 对其他线程必须不可用；不能只验证发送函数的局部锁。
        def probe():
            acquired = c._peer_lock(uid).acquire(blocking=False)
            if acquired:
                c._peer_lock(uid).release()
            return acquired

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(probe).result(timeout=3) is False
        return {"reply": reply_sender(text + "\n尾句"), "character_id": "default"}

    monkeypatch.setattr(wc, "_call_user_manager", manager)
    monkeypatch.setattr(wc, "_send_text", lambda **kw: calls.append(kw["text"]) or {"ret": 0})
    barrier = threading.Barrier(2)

    def run(i):
        barrier.wait(timeout=3)
        c._handle_message({
            "message_type": 1, "message_id": str(i), "from_user_id": "peer",
            "item_list": [{"type": 1, "text_item": {"text": str(i)}}],
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, i) for i in (1, 2)]
        for future in futures:
            future.result(timeout=5)
    assert calls in (["1", "尾句", "2", "尾句"], ["2", "尾句", "1", "尾句"])


# ── 段间停顿 ──────────────────────────────────────────────

def test_segment_delay_is_bounded():
    from wechat_direct.wechat_connector import _segment_delay

    assert 0.4 <= _segment_delay("") <= 1.6
    assert 0.4 <= _segment_delay("短") <= 1.6
    assert 0.4 <= _segment_delay("啊" * 200) <= 1.6
