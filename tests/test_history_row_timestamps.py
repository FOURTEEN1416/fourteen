"""历史逐行时间标注 + 锚点推理指引 红测（2026-10-12，生产实证驱动）。

生产事故（2026-10-12 08:13-08:24）：用户 08:13 叙述了一个发生在清晨六点的
事件，角色 08:14-08:24 仍按"事件正在发生"回应（问现场状态、约事后安排）。
根因：system prompt 已有 [时间锚点]（当前北京时间），但
``annotate_history_time_gaps`` 只在间隔>30min 时给行加时间前缀 —— 连续聊天
（正是本事故形态）模型看不到每条消息的发送时刻，无从推理"六点"是两小时前。

改造口径（本批，红测先行）：
- 历史**每一行**（带可解析 timestamp）都加 ``[MM-DD HH:MM]`` 北京时间前缀；
- 相邻间隔>gap_minutes 的行追加「距上一条 X」人类可读后缀（humanize_gap_cn
  风格，与时间锚点同源）；短间隔行不掺该后缀；
- 时间锚点追加 ≤3 行推理指引（叙述钟点是事件/不确定直接问/不虚构自身状态）。
"""

from __future__ import annotations

import re
from datetime import datetime

# 连续短间隔对话（生产事故形态）：间隔 60/15/10 秒 —— 旧行为整段不标注
_ROWS_CONTINUOUS = [
    # timestamp 均为 UTC 落库口径；00:13 UTC = 北京 08:13
    {"role": "user", "content": "今天六点就出操了", "timestamp": "2026-10-12 00:13:00"},
    {"role": "assistant", "content": "辛苦啦，吃早饭没", "timestamp": "2026-10-12 00:14:00"},
    {"role": "user", "content": "还没，好饿", "timestamp": "2026-10-12 00:14:15"},
    {"role": "assistant", "content": "那快去吃点热的", "timestamp": "2026-10-12 00:14:25"},
]

_TS_PREFIX = re.compile(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] ")


class TestEveryRowTimestamped:
    """annotate_history_time_gaps 语义升级：逐行前缀，短间隔不再豁免。"""

    def _import(self):
        """本文件只测逐行前缀语义（2026-10-19 起 annotate 兼管时段分段渲染，
        见 test_session_segment_render.py）；此处关闭分段保持索引断言稳定。"""
        from orchestrator.context_budget import annotate_history_time_gaps

        def _annotate(rows, **kw):
            kw.setdefault("segment_gap_hours", float("inf"))
            return annotate_history_time_gaps(rows, **kw)

        return _annotate

    def test_continuous_short_interval_rows_all_get_prefix(self):
        """连续短间隔对话的每一行都必须有 [MM-DD HH:MM] 前缀（事故直接解药）。"""
        annotate = self._import()
        out = annotate(_ROWS_CONTINUOUS)
        for i, row in enumerate(out):
            assert _TS_PREFIX.match(row["content"]), (
                f"第 {i} 行缺时间前缀: {row['content']!r}"
            )

    def test_prefix_is_beijing_wall_time(self):
        """UTC 落库时间戳必须换算成北京时间前缀（00:13 UTC → 08:13 北京）。"""
        annotate = self._import()
        out = annotate(_ROWS_CONTINUOUS)
        assert out[0]["content"].startswith("[10-12 08:13]")
        assert "今天六点就出操了" in out[0]["content"]

    def test_first_row_gets_prefix_too(self):
        """首条不再有 >24h 距 now 豁免：每行都标。"""
        annotate = self._import()
        rows = [{"role": "user", "content": "早", "timestamp": "2026-10-01 00:00:00"}]
        out = annotate(rows, now=datetime(2026, 10, 11, 22, 0, 0))
        assert out[0]["content"].startswith("[10-01 08:00]")

    def test_long_gap_row_keeps_human_gap_suffix(self):
        """间隔>30min 的行在前缀之外保留「距上一条 X」人类可读后缀。"""
        annotate = self._import()
        rows = [
            {"role": "user", "content": "早", "timestamp": "2026-10-01 00:00:00"},
            {"role": "user", "content": "我国庆回来了", "timestamp": "2026-10-07 01:30:00"},
        ]
        out = annotate(rows)
        second = out[1]["content"]
        assert second.startswith("[10-07 09:30]")
        assert "距上一条" in second, "人类可读间隔后缀必须保留"
        assert "我国庆回来了" in second

    def test_short_gap_row_has_no_gap_suffix(self):
        """短间隔行只有前缀，不掺「距上一条」噪声；首条无上一条。"""
        annotate = self._import()
        out = annotate(_ROWS_CONTINUOUS)
        for i in (0, 1):
            assert "距上一条" not in out[i]["content"]

    def test_rows_without_timestamp_passed_through(self):
        annotate = self._import()
        rows = [
            {"role": "user", "content": "旧形态行"},
            {"role": "assistant", "content": "没有时间字段"},
        ]
        assert annotate(rows) == rows

    def test_malformed_timestamp_passed_through(self):
        annotate = self._import()
        rows = [{"role": "user", "content": "畸形时间", "timestamp": "not-a-date"}]
        assert annotate(rows) == rows

    def test_input_not_mutated(self):
        annotate = self._import()
        rows = [dict(r) for r in _ROWS_CONTINUOUS]
        snapshot = [dict(r) for r in rows]
        annotate(rows)
        assert rows == snapshot

    def test_other_fields_survive(self):
        """timestamp/channel 等字段必须随行存活（sanitize 契约的下游依赖）。"""
        annotate = self._import()
        rows = [
            {
                "role": "assistant",
                "content": "记得吃早饭",
                "channel": "proactive",
                "timestamp": "2026-10-12 00:05:37",
            }
        ]
        out = annotate(rows)
        assert out[0]["channel"] == "proactive"
        assert out[0]["timestamp"] == "2026-10-12 00:05:37"


class TestAnchorReasoningGuidance:
    """锚点必须带 ≤3 行推理指引（生产实证：模型把叙述钟点当正在发生）。"""

    def _import(self):
        from utils.local_time import time_anchor_line

        return time_anchor_line

    def _anchor(self) -> str:
        return self._import()(
            last_message_at="2026-10-12 00:13:00",  # UTC → 北京 08:13
            now=datetime(2026, 10, 12, 8, 14, 0),
        )

    def test_anchor_contains_guidance_on_narrated_times(self):
        assert "不得当作正在发生" in self._anchor()

    def test_anchor_contains_guidance_on_asking_not_assuming(self):
        assert "不要替用户假设" in self._anchor()

    def test_anchor_contains_guidance_no_fabrication(self):
        assert "不要虚构" in self._anchor()

    def test_guidance_at_most_three_lines(self):
        lines = [ln for ln in self._anchor().splitlines() if ln.strip()]
        assert len(lines) <= 4, f"指引不得超过 3 行，实际追加 {len(lines) - 1} 行"

    def test_guidance_keeps_core_anchor_signals(self):
        anchor = self._anchor()
        assert "[时间锚点]" in anchor
        assert "2026-10-12" in anchor
        assert "08:14" in anchor
