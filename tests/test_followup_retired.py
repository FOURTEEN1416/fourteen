"""追问链退役 + 拆条收敛守卫（2026-10-12 用户裁决）。

用户裁决原话：「下一轮对话何时进行由 LLM 决策，不是数秒之内的多次回复，
要像真人一样隔一段时间再回，类似主动关怀的主动消息。」

两个违背该裁决的机制就此退役/收敛：
- 机制 A（机械追问链）：`FOLLOW_UP_DEFAULTS`（45s/600s 定时追问）与
  `_schedule_followup`/`_cancel_followup` 整体删除 —— 用户回复后隔多久、
  要不要再接话，唯一 Owner = proactive 决策链（`decide_proactive` +
  `should_skip_proactive_decision` 节奏双闸，回复后冷却 15 分钟）；
- 机制 B（拆条轰炸）：`split_reply_for_wechat` 上限 4 条 → **最多 2 条**，
  超出部分并入第 2 条（行间换行保留，内容逐字无损）。
"""

from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

# ── a. 静态守卫：追问链源码零残留 ─────────────────────────


def test_wechat_connector_source_has_no_followup_mechanism():
    from wechat_direct import wechat_connector as wc

    src = inspect.getsource(wc)
    # 注册路径与取消路径
    assert "_schedule_followup" not in src
    assert "_cancel_followup" not in src
    assert "_pending_followups" not in src
    assert "_followup_thread" not in src
    # 默认值与配置读取（配置读取唯一消费方 training_routes 已同步移除）
    assert "FOLLOW_UP_DEFAULTS" not in src
    assert "read_follow_up_config" not in src


def test_proactive_decision_chain_is_the_surviving_owner():
    """接话时机的唯一合法 Owner（proactive 决策链 + 节奏双闸）必须仍在位。"""
    from proactive.llm_proactive import (  # noqa: F401
        decide_proactive,
        should_skip_proactive_decision,
    )


# ── b. 行为守卫：拆条上限 2 条 + 内容无损 ─────────────────


def _split(reply, **kw):
    from wechat_direct.wechat_connector import split_reply_for_wechat

    return split_reply_for_wechat(reply, **kw)


def test_six_line_reply_caps_at_two_segments_and_rejoins_exactly():
    """6 行回复 → ≤2 条，且按原始分隔拼回与原文完全一致（换行保留）。"""
    reply = "\n".join(f"第{i}句" for i in range(1, 7))
    segs = _split(reply)
    assert 1 <= len(segs) <= 2, f"拆条上限 2，实际 {len(segs)} 条"
    assert "\n".join(segs) == reply, "并条必须保留换行，拼回与原文逐字一致"


def test_two_line_reply_stays_two_segments():
    """未超上限的行数维持旧行为：一行一条（≤2 条）。"""
    assert _split("第一句\n第二句") == ["第一句", "第二句"]


def test_single_line_reply_untouched():
    assert _split("在呢") == ["在呢"]
    assert _split("") == []
    assert _split("   \n  ") == []


def test_overlong_single_line_merges_losslessly_without_added_newlines():
    """超长单行：句切后并入第 2 条，不得凭空插入换行（内容逐字无损）。"""
    reply = "一。" * 30
    segs = _split(reply, max_chars=10)
    assert 1 <= len(segs) <= 2
    assert "".join(segs) == reply


def test_blank_lines_dropped_but_content_lossless():
    segs = _split("第一句\n\n第二句\n\n第三句\n\n第四句")
    assert 1 <= len(segs) <= 2
    assert "\n".join(segs) == "第一句\n第二句\n第三句\n第四句"


# ── c. 行为守卫：消息主路径不再注册定时追问 ───────────────


def test_message_main_path_registers_no_timers(tmp_path, monkeypatch):
    """沿用 connector 测试夹具模式：完整走一遍消息主路径，回复后不得登记任何
    45s/600s 定时任务，也不得存在追问守护线程。"""
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(SimpleNamespace())
    c.token = "test-token"
    monkeypatch.setattr(c, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(wc, "_segment_delay", lambda text: 0)
    calls: list[str] = []

    def send(**kwargs):
        calls.append(kwargs["text"])
        return {"ret": 0}

    def manager(mgr, uid, text, attachments=None, reply_sender=None, message_id=""):
        six_lines = "\n".join(f"第{i}句" for i in range(1, 7))
        return {"reply": reply_sender(six_lines), "character_id": "default"}

    monkeypatch.setattr(wc, "_send_text", send)
    monkeypatch.setattr(wc, "_call_user_manager", manager)

    c._handle_message({
        "message_type": 1, "message_id": "t1", "from_user_id": "peer",
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    })

    # 拆条 ≤2 条，且全部发出后主路径就此结束 —— 没有第三条、没有定时登记
    assert 1 <= len(calls) <= 2
    assert not hasattr(wc.WeChatConnector, "_schedule_followup")
    assert not hasattr(wc.WeChatConnector, "_followup_thread")
    assert not hasattr(c, "_pending_followups")
    assert not any(t.name.startswith("wx-followup") for t in threading.enumerate())
