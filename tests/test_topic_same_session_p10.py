"""P10 话题「同时段」与去重回归（2026-11-07 痛点批）。

用户报障两句原话：
1. 「同一话题……系统竟然会相隔两三个小时让他们连在一起」；
2. 「话题比较少的时候，LLM 会反复地说同一个话题，比如关于假链接这件事情」。

本文件锁两层确定性修复：
- 续聊锚点只取「同时段」（默认 120 分钟）内的用户原话，三小时前的旧话题不教唆；
- 同一话题/同一标题只出现一次，且保留**最新**说法；状态块把超窗 active 降级
  到「之前没聊完」，不再标成「正在聊」。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from utils.local_time import now_local
from utils.prompt_sanitize import _extract_user_anchor_sentences
from utils.topic_state import render_topic_state_block


def _utc_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _beijing_ago(hours: float) -> str:
    # naive 北京墙钟串（topic_state._parse_ts 按 Beijing naive 解析）——
    # datetime.now() 在 UTC 宿主是 UTC 墙钟，strftime 出来的「伪北京串」
    # 会与 now_local 基准差 8h（run 42401562467 test_fresh_active 红）。
    return (now_local() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


# ── 1. 续聊锚点的时间窗 ───────────────────────────────────


def test_old_topic_outside_same_session_window_is_dropped() -> None:
    history = [
        {"role": "user", "content": "我在看那个假链接到底是不是骗人的", "timestamp": _utc_ago(3)},
        {"role": "assistant", "content": "嗯嗯", "timestamp": _utc_ago(2.9)},
        {"role": "user", "content": "今天在食堂吃了番茄牛腩", "timestamp": _utc_ago(0.1)},
    ]

    anchors = _extract_user_anchor_sentences(
        history, current_user_message="", max_age_hours=2.0, now=now_local()
    )

    joined = "|".join(anchors)
    assert "番茄牛腩" in joined
    assert "假链接" not in joined, "三小时前的旧话题不得进入续聊钩子"


def test_topic_within_window_is_kept() -> None:
    history = [
        {"role": "user", "content": "我在看那个假链接到底是不是骗人的", "timestamp": _utc_ago(0.5)},
    ]

    anchors = _extract_user_anchor_sentences(
        history, current_user_message="", max_age_hours=2.0, now=now_local()
    )

    assert any("假链接" in a for a in anchors)


# ── 2. 去重与最新优先 ─────────────────────────────────────


def test_duplicate_topic_keeps_latest_wording() -> None:
    history = [
        {"role": "user", "content": "那个假链接是不是骗人的啊", "timestamp": _utc_ago(1.0)},
        {"role": "user", "content": "假链接是不是骗人的", "timestamp": _utc_ago(0.2)},
    ]

    anchors = _extract_user_anchor_sentences(
        history, current_user_message="", max_age_hours=2.0, now=now_local(), limit=2
    )

    # 归一化 + 包含判定去重后只留一条，且是**最新**的那句
    assert len(anchors) == 1
    assert anchors[0] == "假链接是不是骗人的"


def test_limit_caps_to_latest_items() -> None:
    history = [
        {"role": "user", "content": "我在准备明天的数学考试", "timestamp": _utc_ago(0.9)},
        {"role": "user", "content": "晚上想去操场跑步", "timestamp": _utc_ago(0.6)},
        {"role": "user", "content": "刚吃完一碗热干面", "timestamp": _utc_ago(0.1)},
    ]

    anchors = _extract_user_anchor_sentences(
        history, current_user_message="", max_age_hours=2.0, now=now_local(), limit=2
    )

    assert len(anchors) == 2
    assert any("热干面" in a for a in anchors)


# ── 3. 状态块：超窗降级 + 同标题去重 ─────────────────────


def _topic(title: str, hours_ago: float, turns: int = 3, anchors: list[str] | None = None) -> dict:
    return {
        "title": title,
        "turn_count": turns,
        "last_active_at": _beijing_ago(hours_ago),
        "anchors": anchors or [],
    }


def test_stale_active_topic_downgraded_to_parked() -> None:
    snapshot = {
        "active": [_topic("假链接", hours_ago=3)],
        "parked": [],
    }

    block = render_topic_state_block(snapshot, fresh_minutes=120)

    assert "正在聊" not in block
    assert "之前没聊完" in block and "假链接" in block


def test_fresh_active_topic_stays_active() -> None:
    snapshot = {"active": [_topic("假链接", hours_ago=0.2)], "parked": []}

    block = render_topic_state_block(snapshot, fresh_minutes=120)

    assert "正在聊" in block and "假链接" in block


def test_duplicate_active_titles_render_once() -> None:
    snapshot = {
        "active": [
            _topic("假链接", hours_ago=0.1, turns=3),
            _topic("假链接", hours_ago=0.2, turns=5),
            _topic("晚饭", hours_ago=0.3, turns=1),
        ],
        "parked": [],
    }

    block = render_topic_state_block(snapshot, fresh_minutes=120)

    assert block.count("假链接") == 1, "同一话题在状态块里被列多次 = 反复回放的放大器"
    assert "晚饭" in block
