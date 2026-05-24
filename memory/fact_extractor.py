"""
事实提取器 — 从对话中提取用户事实

使用 LLM 或规则从对话中提取关于用户的信息：
- 偏好（"我喜欢吃火锅"）
- 习惯（"我每天12点睡"）
- 事件（"下周要去出差"）
- 个人信息（"我住在上海"）
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("fact_extractor")

# 事实类别
FACT_CATEGORIES = [
    "preference",   # 偏好
    "habit",        # 习惯
    "event",        # 事件
    "personal",     # 个人信息
    "relationship", # 关系
    "work",         # 工作
    "health",       # 健康
    "general",      # 通用
]

# 规则模式：提取常见事实
PATTERNS = {
    "preference": [
        r"(?:喜欢|爱[好]?|最[爱喜]|超[级]?[喜爱]).{0,20}(?:吃|喝|玩|去|看|听|做|打)",
        r"(?:不[喜爱]|讨厌|受不了).{0,20}",
    ],
    "habit": [
        r"(?:每天|平时|经常|总是|习惯).{0,30}(?:睡觉|起床|吃饭|运动|工作|学习|打游戏)",
        r"(?:熬夜|早起|午睡|健身|跑步)",
    ],
    "event": [
        r"(?:下周|明天|后天|下个月|这周末|周末).{0,30}(?:去|要|打算|准备|计划)",
        r"(?:出差|旅行|搬家|考试|面试|约会|聚会)",
    ],
    "personal": [
        r"(?:我[叫是]|名字).{0,10}",
        r"(?:住在|来自|家在).{0,15}",
        r"(?:今年|岁|出生).{0,10}",
    ],
    "work": [
        r"(?:上班|工作|公司|同事|老板|项目|加班)",
        r"(?:学生|上学|考试|专业|毕业|学校)",
    ],
    "health": [
        r"(?:生病|感冒|发烧|头疼|肚子疼|不舒服|医院|吃药)",
        r"(?:失眠|睡不着|累|疲惫|乏力)",
    ],
}


class FactExtractor:
    """
    事实提取器

    支持两种模式：
    1. LLM 模式（推荐）：使用大模型提取事实
    2. 规则模式：基于正则表达式提取
    """

    def __init__(self, llm_func: Callable | None = None):
        """
        Args:
            llm_func: LLM 调用函数，接受 prompt 返回文本
                      若为 None 则使用规则模式
        """
        self.llm_func = llm_func

    def extract_facts(self, user_messages: list[str]) -> list[dict[str, Any]]:
        """
        从用户消息中提取事实

        Args:
            user_messages: 用户消息列表

        Returns:
            [{"fact": str, "category": str, "confidence": float, "source": str}, ...]
        """
        if self.llm_func:
            return self._extract_with_llm(user_messages)
        return self._extract_with_rules(user_messages)

    def extract_from_chat(self, chat_history: list[dict[str, str]]) -> list[dict[str, Any]]:
        """
        从聊天历史中提取事实

        Args:
            chat_history: [{"role": "user"/"assistant", "content": str}, ...]

        Returns:
            事实列表
        """
        user_msgs = [
            m["content"] for m in chat_history
            if m.get("role") == "user"
        ]
        return self.extract_facts(user_msgs)

    # ── LLM 模式 ─────────────────────────────────────────

    def _extract_with_llm(self, messages: list[str]) -> list[dict[str, Any]]:
        """使用 LLM 提取事实"""
        if not self.llm_func or not messages:
            return []

        # 合并消息
        text = "\n".join(messages[-20:])  # 最多处理最近20条

        prompt = f"""从以下对话中提取关于用户的事实信息。
只提取明确提到的、有具体内容的事实。
对每个事实给出类别和置信度(0~1)。

输出 JSON 数组格式：
[
  {{"fact": "用户喜欢吃火锅", "category": "preference", "confidence": 0.9}},
  {{"fact": "用户下周去北京出差", "category": "event", "confidence": 0.8}}
]

类别: {', '.join(FACT_CATEGORIES)}

对话内容:
{text}

JSON:"""

        try:
            result = self.llm_func(prompt)
            # 尝试解析 JSON
            facts = self._parse_json_result(result)
            if facts:
                for f in facts:
                    f["source"] = "llm"
                return facts
        except Exception as e:
            logger.warning("LLM fact extraction failed: %s", e)

        return []

    # ── 规则模式 ─────────────────────────────────────────

    def _extract_with_rules(self, messages: list[str]) -> list[dict[str, Any]]:
        """使用正则规则提取事实"""
        facts = []

        for msg in messages:
            for category, patterns in PATTERNS.items():
                for pattern in patterns:
                    matches = re.findall(pattern, msg)
                    for m in matches:
                        fact_text = m.strip()
                        if len(fact_text) < 2:
                            continue
                        # 去重
                        if not any(f["fact"] == fact_text for f in facts):
                            facts.append({
                                "fact": fact_text,
                                "category": category,
                                "confidence": 0.5,
                                "source": "rule",
                            })

        return facts

    # ── 工具方法 ─────────────────────────────────────────

    @staticmethod
    def deduplicate(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        去重并合并相似事实

        合并规则：
        - 相同类别且文本相似度高的合并
        - 保留置信度高的
        """
        if not facts:
            return []

        # 按类别分组
        by_category: dict[str, list] = {}
        for f in facts:
            cat = f.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(f)

        result = []
        for cat, items in by_category.items():
            # 按置信度排序
            items.sort(key=lambda x: x.get("confidence", 0), reverse=True)

            seen_texts = set()
            for item in items:
                # 简单去重：相同文本只保留置信度最高的
                text = item["fact"].strip()
                if text not in seen_texts:
                    seen_texts.add(text)
                    result.append(item)

        return result

    @staticmethod
    def _parse_json_result(text: str) -> list[dict[str, Any]] | None:
        """尝试从 LLM 输出中解析 JSON"""
        # 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 块
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试提取 [...] 数组
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "llm_available": self.llm_func is not None,
            "rule_patterns": sum(len(p) for p in PATTERNS.values()),
        }
