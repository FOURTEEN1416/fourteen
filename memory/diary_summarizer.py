"""
日记摘要器 — 每日对话总结和情绪趋势分析

核心功能：
1. summarize_day(): 生成每日对话摘要
2. detect_mood_trend(): 分析情绪趋势
3. 长期记忆压缩（每日/每周）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("diary_summarizer")


class DiarySummarizer:
    """
    日记摘要器

    每次"午夜自省"时调用，分析当天对话并生成摘要。
    """

    def __init__(self, llm_func: Optional[Callable] = None):
        """
        Args:
            llm_func: LLM 调用函数（可选），为 None 时使用模板摘要
        """
        self.llm_func = llm_func
        self._daily_summaries: Dict[str, str] = {}
        logger.info("DiarySummarizer initialized")

    def summarize_day(self, chats: List[Dict[str, Any]]) -> str:
        """
        生成每日对话摘要

        Args:
            chats: 当天的聊天记录列表
                   [{"role": str, "content": str, "created_at": str, "emotion_tag": str}, ...]

        Returns:
            摘要文本
        """
        if not chats:
            return "今天没有聊天记录。"

        if self.llm_func:
            return self._summarize_with_llm(chats)

        return self._summarize_with_template(chats)

    def summarize_week(self, daily_summaries: List[str]) -> str:
        """生成每周综合摘要"""
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
                return self.llm_func(prompt)
            except Exception as e:
                logger.warning("Weekly LLM summary failed: %s", e)

        return "\n".join(daily_summaries)

    def detect_mood_trend(self, daily_summaries: Dict[str, str]) -> Dict[str, Any]:
        """
        分析情绪趋势

        Args:
            daily_summaries: {date_str: summary_text}

        Returns:
            {"trend": str, "avg_mood": str, "notable_days": [str]}
        """
        if not daily_summaries:
            return {
                "trend": "无数据",
                "avg_mood": "未知",
                "notable_days": [],
            }

        # 简单规则分析
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

        # 计算趋势
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

        # 平均情绪
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

        # 显著日期
        notable = [d for d, s in mood_scores.items() if abs(s) >= 2]

        return {
            "trend": trend,
            "avg_mood": avg_mood,
            "notable_days": notable,
        }

    def save_summary(self, date_str: str, summary: str) -> None:
        """保存每日摘要"""
        self._daily_summaries[date_str] = summary
        logger.info("Diary summary saved for %s", date_str)

    def get_summary(self, date_str: str) -> Optional[str]:
        """获取指定日期的摘要"""
        return self._daily_summaries.get(date_str)

    def get_all_summaries(self) -> Dict[str, str]:
        """获取所有摘要"""
        return dict(self._daily_summaries)

    # ── 内部方法 ─────────────────────────────────────────

    def _summarize_with_llm(self, chats: List[Dict[str, Any]]) -> str:
        """LLM 摘要"""
        chat_text = "\n".join(
            f"{'用户' if c['role'] == 'user' else '小暖'}: {c['content']}"
            for c in chats[-30:]  # 最多30条
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
            return self.llm_func(prompt)
        except Exception as e:
            logger.warning("LLM summary failed: %s", e)
            return self._summarize_with_template(chats)

    def _summarize_with_template(self, chats: List[Dict[str, Any]]) -> str:
        """模板摘要"""
        user_msgs = [c for c in chats if c.get("role") == "user"]
        assistant_msgs = [c for c in chats if c.get("role") == "assistant"]
        user_count = len(user_msgs)
        assistant_count = len(assistant_msgs)
        total = user_count + assistant_count

        # 提取主要话题（简单关键词）
        all_content = " ".join(c["content"] for c in chats)
        topic_keywords = [
            "工作", "学习", "吃饭", "睡觉", "游戏", "电影",
            "音乐", "运动", "旅行", "家人", "朋友", "心情",
            "天气", "购物", "计划",
        ]
        mentioned = [kw for kw in topic_keywords if kw in all_content]

        # 情绪标注
        emotions = [c.get("emotion_tag", "") for c in assistant_msgs if c.get("emotion_tag")]
        emotion_summary = ", ".join(set(emotions)) if emotions else "未记录"

        summary = (
            f"今日共 {total} 条消息（用户 {user_count} 条，小暖 {assistant_count} 条）。"
        )
        if mentioned:
            summary += f" 提到话题：{'、'.join(mentioned)}。"
        if emotion_summary:
            summary += f" 情绪状态：{emotion_summary}。"

        return summary

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "llm_available": self.llm_func is not None,
            "summaries_count": len(self._daily_summaries),
        }
