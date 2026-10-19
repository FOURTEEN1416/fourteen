"""会话分段渲染（2026-10-19 时间感知 3.0 批 · P1 核心重构）。

生产病灶（用户报障截图 10-19）：周六 14:45「咳咳」→ 澄清闭环 → **51 小时 4 分后**
周一 17:50 用户「哈喽」重新开场 → 模型回「你**刚说的**那个咳，不是真的咳吧」。
生产取证（venv 复现）：历史第 18 行明写「哈喽（距上一条 2 天）」、行首逐行
[MM-DD HH:MM] 前缀、时间锚点「上一条消息：2 天前」——**三重信号全在**，agnes
主链仍说「刚」。根因=时间信号是行尾注记+system prompt 软指引，在 15 条历史/
1855 字符记忆的注意力竞争下失败（hve-core 指令稀释机理）；微信 UI 用时间分隔条
把 51 小时视觉分组，prompt 里无等效结构（开源 companion 领域空白，无成熟方案可抄）。

修法：annotate_history_time_gaps 升级——相邻行间隔 > SESSION_SEGMENT_GAP_HOURS
时插入 [时段分隔] 伪行（role=system，与微信时间分隔条同语义），把长间隔
**结构性**渲染为历史里的显式分段边界；历史最后一行距今超阈值时补「翻篇」尾注
（旧话题已结束、以新开场为准）。出站守卫词表同步加入分隔标记防回显。
"""

from __future__ import annotations

from datetime import datetime


def _dt(s: str) -> str:
    """北京墙钟 str → UTC 落库串（与 chat_history.created_at 同口径）。"""
    from utils.local_time import _BEIJING

    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=_BEIJING).astimezone(
        __import__("datetime").timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


class TestSegmentDividerInsertion:
    """分段行插入：阈值、位置、多段、整段旧形态。"""

    def _rows(self, spec: list[tuple[str, str]]) -> list[dict]:
        return [
            {"role": r, "content": c, "timestamp": _dt(ts)}
            for r, c, ts in spec
        ]

    def test_production_case_segmented(self):
        """生产病例回归：10-17 旧历史（隔 51h 后用户重新开场）→ 头注+尾注。

        生产形态：17:50:42 构建 prompt 时「哈喽」尚未写库，历史最后一行是
        10-17 14:46 的 assistant —— 整段历史距今 51h，头注+翻篇尾注成双出现。
        """
        from orchestrator.context_budget import (
            SEGMENT_DIVIDER_MARKER,
            annotate_history_time_gaps,
        )

        rows = self._rows(
            [
                ("user", "咳咳", "2026-10-17 14:45"),
                ("assistant", "又咳嗽了……多喝温水", "2026-10-17 14:45"),
                ("user", "是打招呼，不是咳嗽", "2026-10-17 14:46"),
                ("assistant", "哦，原来是打招呼啊", "2026-10-17 14:46"),
            ]
        )
        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        segs = [r for r in out if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER)]
        assert len(segs) == 2, f"应插 2 条分隔行，实际 {len(segs)}"
        assert out.index(segs[0]) == 0, "头注必须插在最前"
        assert out.index(segs[-1]) == len(out) - 1, "尾注必须插在末尾"
        assert "已结束" in segs[0]["content"]
        assert "翻篇" in segs[-1]["content"]
        assert segs[0]["role"] == "system"
        # 时间窗信息：旧段起止
        assert "10-17" in segs[0]["content"]

    def test_gap_below_threshold_no_segment(self):
        """5.9 小时间隔 → 不插分隔行（既有行为不变）。"""
        from orchestrator.context_budget import (
            SEGMENT_DIVIDER_MARKER,
            annotate_history_time_gaps,
        )

        rows = self._rows(
            [
                ("user", "早", "2026-10-19 08:00"),
                ("assistant", "早呀", "2026-10-19 08:01"),
                ("user", "在干嘛", "2026-10-19 13:58"),
            ]
        )
        now = datetime.strptime("2026-10-19 13:59", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        segs = [r for r in out if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER)]
        assert segs == []

    def test_gap_at_threshold_segments(self):
        """恰 6 小时间隔（>= 语义）→ 段间插分隔行。"""
        from orchestrator.context_budget import (
            SEGMENT_DIVIDER_MARKER,
            annotate_history_time_gaps,
        )

        rows = self._rows(
            [
                ("user", "早", "2026-10-19 06:00"),
                ("user", "在干嘛", "2026-10-19 12:00"),
                ("user", "刚睡醒", "2026-10-19 12:30"),
            ]
        )
        now = datetime.strptime("2026-10-19 18:00", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        segs = [r for r in out if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER)]
        # 首行距今 12h → 头注；行2 与行1 恰 6h → 边界头注；末行距今 5.5h → 无尾注
        assert len(segs) == 2

    def test_multiple_segments(self):
        """三段历史 → 每个旧段头前都有注记。"""
        from orchestrator.context_budget import (
            SEGMENT_DIVIDER_MARKER,
            annotate_history_time_gaps,
        )

        rows = self._rows(
            [
                ("user", "a", "2026-10-15 10:00"),
                ("user", "b", "2026-10-17 10:00"),
                ("user", "c", "2026-10-19 10:00"),
            ]
        )
        now = datetime.strptime("2026-10-19 10:01", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        segs = [r for r in out if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER)]
        # 行1/行2 间隔 48h → 行2 头注；行1 距今 96h → 首行头注；行3 距今 1min → 无尾注
        assert len(segs) == 3

    def test_no_timestamp_rows_passthrough(self):
        """无 timestamp 行 fail-open：不阻断、不爆炸。"""
        from orchestrator.context_budget import annotate_history_time_gaps

        rows = [
            {"role": "user", "content": "没有时间的行"},
            {"role": "user", "content": "hi", "timestamp": _dt("2026-10-19 17:00")},
        ]
        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        assert out[0]["content"] == "没有时间的行"

    def test_segment_rows_do_not_break_existing_annotations(self):
        """分段行与 [MM-DD HH:MM] 行前缀/（距上一条 X）后缀共存不互毁。"""
        from orchestrator.context_budget import (
            HISTORY_GAP_NOTE_PREFIX,
            annotate_history_time_gaps,
        )

        rows = self._rows(
            [
                ("user", "早", "2026-10-19 08:00"),
                ("user", "在干嘛", "2026-10-19 17:00"),
            ]
        )
        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        stamped = [r for r in out if str(r.get("content", "")).startswith("[10-19")]
        assert stamped, "行前缀标注不得被分段逻辑破坏"
        assert any(HISTORY_GAP_NOTE_PREFIX in str(r.get("content")) for r in stamped)


class TestSegmentDividerReentrySafety:
    """分隔行自身安全性：重入不炸、可被出站守卫剥净。"""

    def test_reentry_idempotent(self):
        """分隔行无 timestamp → annotate 重入原样透传（不叠分段）。"""
        from orchestrator.context_budget import (
            SEGMENT_DIVIDER_MARKER,
            annotate_history_time_gaps,
        )

        rows = [
            {"role": "user", "content": "x", "timestamp": _dt("2026-10-17 10:00")},
            {"role": "user", "content": "y", "timestamp": _dt("2026-10-19 10:00")},
        ]
        now = datetime.strptime("2026-10-19 10:01", "%Y-%m-%d %H:%M")
        once = annotate_history_time_gaps(rows, now=now)
        twice = annotate_history_time_gaps(once, now=now)
        n1 = sum(1 for r in once if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER))
        n2 = sum(1 for r in twice if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER))
        assert n1 == n2, "重入不得叠加分隔行"

    def test_guard_strips_divider_echo(self):
        """模型回显分隔行文本 → 出站守卫剥净。"""
        from orchestrator.context_budget import SEGMENT_DIVIDER_MARKER
        from utils.prompt_sanitize import terminal_reply_guard

        raw = f"{SEGMENT_DIVIDER_MARKER} 哈喽"
        assert terminal_reply_guard(raw, user_msg="哈喽") == "哈喽"

    def test_clip_history_tail_keeps_divider(self):
        """尾部裁剪语义：超长历史取尾部，末尾翻篇注不被裁掉。"""
        from orchestrator.context_budget import (
            SEGMENT_DIVIDER_MARKER,
            annotate_history_time_gaps,
            clip_history,
        )

        rows = [
            {"role": "user", "content": f"m{i}", "timestamp": _dt(f"2026-10-19 08:0{i % 10}")}
            for i in range(8)
        ]
        rows.append({"role": "user", "content": "旧话题", "timestamp": _dt("2026-10-17 08:00")})
        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        out = annotate_history_time_gaps(rows, now=now)
        clipped = clip_history(out, None)  # None → DEFAULT_BUDGET
        segs = [r for r in clipped if str(r.get("content", "")).startswith(SEGMENT_DIVIDER_MARKER)]
        # 末尾翻篇注必须在（尾注是核心信号，clip 取尾部天然保护）
        assert any("翻篇" in str(s.get("content")) for s in segs)
