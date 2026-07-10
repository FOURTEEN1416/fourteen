"""
日记摘要器 — 每日对话总结和情绪趋势分析（memory_pipeline 内联版本）

支持 LLM 和模板两种摘要模式，持久化到 SQLite。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("diary_summarizer")


class DiarySummarizer:
    """日记摘要器 — 每日对话总结 + 情绪趋势分析"""

    def __init__(self, llm_func: Callable | None = None,
                 structured_memory=None):
        self.llm_func = llm_func
        self._daily_summaries: dict[str, str] = {}
        self._structured_memory = structured_memory  # 用于持久化摘要到 SQLite

    def summarize_day(self, chats: list[dict[str, Any]]) -> str:
        if not chats:
            return "今天没有聊天记录。"
        if self.llm_func:
            return self._summarize_with_llm(chats)
        return self._summarize_with_template(chats)

    def summarize_week(self, daily_summaries: list[str]) -> str:
        if not daily_summaries:
            return "本周没有记录。"
        if self.llm_func:
            text = "\n".join(daily_summaries)
            prompt = f"""以下是本周每日摘要，请生成周报：

{text}

周报格式：
## 本周概览
- 聊天频率:
- 主要话题:
- 用户状态:

## 新发现
- ...

## 情绪趋势
- ..."""
            try:
                return self.llm_func(prompt)  # type: ignore[no-any-return]
            except Exception as e:  # noqa: BLE001
                logger.warning("Weekly LLM summary failed: %s", e)
        return "\n".join(daily_summaries)

    def detect_mood_trend(self, daily_summaries: dict[str, str]) -> dict[str, Any]:
        if not daily_summaries:
            return {"trend": "无数据", "avg_mood": "未知", "notable_days": []}
        mood_keywords = {
            "positive": ["开心", "高兴", "不错", "愉快", "好", "甜", "暖", "感动"],
            "negative": ["难过", "生气", "累", "不好", "烦", "伤心", "郁闷", "焦虑"],
        }
        mood_scores = {}
        for date_str, summary in daily_summaries.items():
            score = 0
            for kw in mood_keywords["positive"]:
                if kw in summary:
                    score += 1
            for kw in mood_keywords["negative"]:
                if kw in summary:
                    score -= 1
            mood_scores[date_str] = score
        scores = list(mood_scores.values())
        if len(scores) >= 2:
            if scores[-1] > scores[0]:
                trend = "上升趋势，关系在变好"
            elif scores[-1] < scores[0]:
                trend = "下降趋势，需要注意"
            else:
                trend = "稳定"
        else:
            trend = "数据不足"
        if scores:
            avg = sum(scores) / len(scores)
            if avg > 1:
                avg_mood = "积极"
            elif avg < -1:
                avg_mood = "消极"
            else:
                avg_mood = "中性"
        else:
            avg_mood = "未知"
        notable = [d for d, s in mood_scores.items() if abs(s) >= 2]
        return {"trend": trend, "avg_mood": avg_mood, "notable_days": notable}

    def save_summary(self, date_str: str, summary: str) -> None:
        self._daily_summaries[date_str] = summary
        if self._structured_memory:
            try:
                with self._structured_memory.get_connection() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_summaries (date, summary) VALUES (?, ?)",
                        (date_str, summary),
                    )
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to persist diary summary to DB: %s", e)
        logger.info("Diary summary saved for %s", date_str)

    def load_summaries_from_db(self) -> None:
        if not self._structured_memory:
            return
        try:
            with self._structured_memory.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS daily_summaries (
                        date TEXT PRIMARY KEY,
                        summary TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                rows = conn.execute(
                    "SELECT date, summary FROM daily_summaries ORDER BY date"
                ).fetchall()
                for row in rows:
                    self._daily_summaries[row[0]] = row[1]
                if rows:
                    logger.info("Loaded %d diary summaries from DB", len(rows))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load diary summaries from DB: %s", e)

    def get_summary(self, date_str: str) -> str | None:
        return self._daily_summaries.get(date_str)

    def get_all_summaries(self) -> dict[str, str]:
        return dict(self._daily_summaries)

    def _summarize_with_llm(self, chats: list[dict[str, Any]]) -> str:
        chat_text = "\n".join(
            f"{'用户' if c['role'] == 'user' else '十四'}: {c['content']}"
            for c in chats[-30:]
        )
        prompt = f"""以下是今天的对话记录，请生成简洁的每日摘要。

要求：
1. 今天聊了什么话题
2. 用户的情绪状态
3. 有什么新发现或值得记住的事
4. 关系进展

对话记录：
{chat_text}

每日摘要（100字以内）："""
        try:
            return self.llm_func(prompt)  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM summary failed: %s", e)
            return self._summarize_with_template(chats)

    def _summarize_with_template(self, chats: list[dict[str, Any]]) -> str:
        user_msgs = [c for c in chats if c.get("role") == "user"]
        assistant_msgs = [c for c in chats if c.get("role") == "assistant"]
        user_count = len(user_msgs)
        assistant_count = len(assistant_msgs)
        total = user_count + assistant_count
        all_content = " ".join(c["content"] for c in chats)
        topic_keywords = [
            "工作", "学习", "吃饭", "睡觉", "游戏", "电影",
            "音乐", "运动", "旅行", "家人", "朋友", "心情",
            "天气", "购物", "计划",
        ]
        mentioned = [kw for kw in topic_keywords if kw in all_content]
        emotions = [c.get("emotion_tag", "") for c in assistant_msgs
                    if c.get("emotion_tag")]
        emotion_summary = ", ".join(set(emotions)) if emotions else "未记录"
        summary = f"今日共 {total} 条消息（用户 {user_count} 条，十四 {assistant_count} 条）."
        if mentioned:
            summary += f" 提到话题：{'、'.join(mentioned)}。"
        if emotion_summary != "未记录":
            summary += f" 情绪状态：{emotion_summary}。"
        return summary

    def health_check(self) -> dict:
        return {
            "llm_available": self.llm_func is not None,
            "summaries_count": len(self._daily_summaries),
        }
