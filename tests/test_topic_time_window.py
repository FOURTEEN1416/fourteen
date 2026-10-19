"""当前话题钩子时间窗（2026-10-19 时间感知 3.0 批 · P2）。

生产病灶（取证 10-19 17:50 轮）：extract_current_topics 无时间窗，
三天前（10-16）的「吃饭」「那叫早点」被注入 system prompt 的
「## 当前话题（请沿着聊下去）」——旧时段话题被教唆续聊。
修法：只从距今 max_age_hours（默认 24h）内的消息提取话题；无 timestamp
行保持旧行为（fail-open，兼容旧形态/合成行）。
"""

from __future__ import annotations

from datetime import datetime


def _utc(s: str) -> str:
    from utils.local_time import _BEIJING

    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=_BEIJING).astimezone(
        __import__("datetime").timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


class TestTopicTimeWindow:
    def test_old_topic_excluded(self):
        """30 小时前的消息不进话题（生产病例：10-16 的「吃饭」在 10-19 不教唆）。"""
        from utils.prompt_sanitize import extract_current_topics

        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        rows = [
            {"role": "user", "content": "中午吃饭了吗", "timestamp": _utc("2026-10-16 12:00")},
            {"role": "user", "content": "明天考试", "timestamp": _utc("2026-10-19 10:00")},
        ]
        topics = extract_current_topics(rows, current_user_message="哈喽", now=now)
        assert "吃饭" not in topics
        assert "考试" in topics

    def test_recent_topic_kept(self):
        """1 小时前的消息正常提取。"""
        from utils.prompt_sanitize import extract_current_topics

        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        rows = [{"role": "user", "content": "刚考试完", "timestamp": _utc("2026-10-19 17:00")}]
        topics = extract_current_topics(rows, current_user_message="嗯", now=now)
        assert "考试" in topics

    def test_no_timestamp_rows_keep_legacy_behavior(self):
        """无 timestamp 行不被时间窗排除（兼容旧形态）。"""
        from utils.prompt_sanitize import extract_current_topics

        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        rows = [{"role": "user", "content": "聊到期末考试了"}]
        topics = extract_current_topics(rows, current_user_message="", now=now)
        assert "考试" in topics

    def test_zero_window_excludes_all_dated(self):
        """max_age_hours=0 → 全部带时间戳行排除；当前消息仍参与。"""
        from utils.prompt_sanitize import extract_current_topics

        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        rows = [{"role": "user", "content": "期末", "timestamp": _utc("2026-10-19 17:49")}]
        topics = extract_current_topics(
            rows, current_user_message="周末去图书馆", now=now, max_age_hours=0.0
        )
        assert "期末" not in topics
        # 当前用户消息不受时间窗限制（它就是「当前」）
        assert "周末去图书馆" in topics

    def test_production_case_regression(self):
        """生产病例全量回归：10-16/10-17 旧话题全部出局，只有当前消息相关。"""
        from utils.prompt_sanitize import extract_current_topics

        now = datetime.strptime("2026-10-19 17:50", "%Y-%m-%d %H:%M")
        rows = [
            {"role": "user", "content": "七点吃什么饭啊", "timestamp": _utc("2026-10-16 07:12")},
            {"role": "user", "content": "十二点嘛", "timestamp": _utc("2026-10-16 09:13")},
            {"role": "user", "content": "咳咳", "timestamp": _utc("2026-10-17 14:45")},
            {"role": "user", "content": "我这个是打招呼的用语", "timestamp": _utc("2026-10-17 14:46")},
        ]
        topics = extract_current_topics(rows, current_user_message="哈喽", now=now)
        for stale in ("吃饭", "早点", "咳咳", "咳嗽", "招呼"):
            assert stale not in topics, f"旧话题「{stale}」不得教唆"
