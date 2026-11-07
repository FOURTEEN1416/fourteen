"""P10 残余缺口回归（2026-11-07 对抗验收批 v1.65.2）。

对抗验收实证的两处残余：
1. 幻觉 URL 无确定性防线——生产 10-30 假链接案（example.com 占位 URL 发给
   用户并自称「能打开」，被拆穿后重提 26.6 小时）。本文件钉出站终审守卫的
   占位域黑名单剥句（真实域名不误伤）。
2. parked 复提无次数记账——复提发送成功不 touch 行，同一话题每 ≥6h 周期性
   合法复提、机制上无上限。本文件钉 mark_topic_recalled 记账 + 冷却升级闸
   （0-1 次=基线 6h、2-3 次=翻倍、≥4 次=四倍）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from utils.prompt_sanitize import terminal_reply_guard
from utils.topic_state import (
    mark_topic_recalled,
    select_proactive_recall,
    update_after_turn,
)

# ── 1. 幻觉 URL 出站防线 ──────────────────────────────────


def test_guard_strips_placeholder_url_line_keeps_rest() -> None:
    reply = "给你找了个下雨天听的白噪音\n链接在这：https://example.com/rain.mp3 直接点就能听\n要不要来一段？"

    out = terminal_reply_guard(reply, reply_mode="")

    assert "example.com" not in out, "占位域 URL 所在行必须剥除"
    assert "白噪音" in out and "要不要来一段" in out, "其余行不受影响"


def test_guard_drops_reply_that_is_only_placeholder_url() -> None:
    reply = "链接在这，你直接点击就可以听了。/ https://example.com/rain_sound.mp3"

    assert terminal_reply_guard(reply, reply_mode="") == ""


def test_guard_hits_bare_placeholder_domain() -> None:
    reply = "去 example.com/xyz 看看就知道了"

    assert terminal_reply_guard(reply, reply_mode="") == ""


def test_guard_keeps_non_placeholder_urls() -> None:
    reply = "这首歌在 https://music.163.com/song?id=1 能搜到，叫《晚风》"

    out = terminal_reply_guard(reply, reply_mode="")

    assert "music.163.com" in out, "真实域名不在黑名单，不误伤"


# ── 2. parked 复提记账与冷却升级 ──────────────────────────


def _seed_parked_topic(session_key: str, hours_idle: float, now: datetime) -> str:
    """造一个已 parked 的旧话题：先经确定性状态机落 active，再回拨时间闲置转 parked。"""
    result = update_after_turn(
        session_key, "default", "我们聊一下那家新开的拉面店", now=now - timedelta(hours=hours_idle + 2)
    )
    topic_id = str(result.get("topic_id") or "")
    assert topic_id, "确定性状态机应产出 topic_id"
    # 闲置超过 park_idle_hours → select_proactive_recall 会先把它 timeout 成 parked
    return topic_id


def test_recall_cooldown_blocks_recently_recalled_topic() -> None:

    session = "test:recall-cooldown@im.wechat"
    base = datetime(2026, 11, 7, 12, 0, 0)
    topic_id = _seed_parked_topic(session, hours_idle=8.0, now=base)

    first = select_proactive_recall(session, "default", now=base)
    assert [t["topic_id"] for t in first] == [topic_id], "闲置 8h 的 parked 话题首次应可复提"

    # 投递成功 → 记账（count=1，last_recalled_at=base）；count=1 的冷却 = 基线 6h
    assert mark_topic_recalled(session, "default", topic_id, now=base) is True

    again_soon = select_proactive_recall(session, "default", now=base + timedelta(hours=5))
    assert again_soon == [], "count=1 刚记完账 5h 内不得再复提（基线 6h 起算自记账时刻）"

    later = select_proactive_recall(session, "default", now=base + timedelta(hours=7))
    assert [t["topic_id"] for t in later] == [topic_id], "距记账 7h（≥6h）后恢复可复提"


def test_recall_cooldown_escalates_with_count() -> None:

    session = "test:recall-escalate@im.wechat"
    base = datetime(2026, 11, 7, 12, 0, 0)
    topic_id = _seed_parked_topic(session, hours_idle=30.0, now=base)

    # 第 1 次复提成功（12:00），第 2 次在 +7h（19:00，≥基线 6h）成功 → count=2，阈值翻倍=12h
    mark_topic_recalled(session, "default", topic_id, now=base)
    mark_topic_recalled(session, "default", topic_id, now=base + timedelta(hours=7))

    at_17h = select_proactive_recall(session, "default", now=base + timedelta(hours=17))
    assert at_17h == [], "count=2 后 12h 冷却：距上次记账（19:00）仅 10h，不得复提"

    at_20h = select_proactive_recall(session, "default", now=base + timedelta(hours=20))
    assert [t["topic_id"] for t in at_20h] == [topic_id], "距上次记账 13h（≥12h）后恢复可复提"

    # 第 3、4 次记账（间隔均 ≥12h）→ count=4 → 阈值四倍=24h
    mark_topic_recalled(session, "default", topic_id, now=base + timedelta(hours=20))
    mark_topic_recalled(session, "default", topic_id, now=base + timedelta(hours=32))

    at_50h = select_proactive_recall(session, "default", now=base + timedelta(hours=50))
    assert at_50h == [], "count=4 后 24h 冷却：距上次记账（32:00 时刻）仅 18h，不得复提"

    at_57h = select_proactive_recall(session, "default", now=base + timedelta(hours=57))
    assert [t["topic_id"] for t in at_57h] == [topic_id], "距上次记账 25h（≥24h）后恢复可复提"


def test_cooldown_filtered_rows_yield_slot_to_fresh_candidates() -> None:

    session = "test:recall-slot@im.wechat"
    base = datetime(2026, 11, 7, 12, 0, 0)
    hot_id = _seed_parked_topic(session, hours_idle=8.0, now=base)
    fresh = update_after_turn(
        session, "default", "对了那部新电影上映了", now=base - timedelta(hours=8)
    )
    fresh_id = str(fresh.get("topic_id") or "")

    mark_topic_recalled(session, "default", hot_id, now=base)

    picks = select_proactive_recall(session, "default", now=base + timedelta(hours=1))
    picked_ids = [t["topic_id"] for t in picks]
    assert hot_id not in picked_ids, "刚记完账的话题在冷却中"
    assert fresh_id in picked_ids, "冷却中的行必须让位给低计数候选（过滤在截断之后）"
