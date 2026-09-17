"""
反省引擎（rule/llm 双模式） + 内心独白数据类
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("proactive.reflection")

# 内心独白保留条数（消费方 get_latest_monologue 只取最后一条）
_MONOLOGUE_MAX = 200


@dataclass
class InnerMonologue:
    thought: str
    type: str
    urgency_delta: float
    created_at: datetime = field(default_factory=datetime.now)


class ReflectionEngine:
    def __init__(
        self,
        llm_func: Callable | None = None,
        reflection_mode: str = "rule",
    ):
        self.llm_func = llm_func
        self.reflection_mode = reflection_mode
        self._monologues: deque[InnerMonologue] = deque(maxlen=_MONOLOGUE_MAX)

    def reflect(
        self,
        user_message: str,
        reply: str,
        affinity_level: int,
        hours_since_last: float,
    ) -> InnerMonologue:
        if self.reflection_mode == "llm" and self.llm_func:
            monologue = self._reflect_with_llm(user_message, reply, affinity_level)
        else:
            monologue = self._reflect_with_rules(
                user_message, reply, affinity_level, hours_since_last,
            )
        # 旧实现只 return 不记录：_monologues 从未被写入，导致
        # get_latest_monologue() **恒返回 None**（暴露但不记录的半成品状态）。
        # 记录与暴露必须成对，否则该访问器是永远为空的假接口。
        self._monologues.append(monologue)
        return monologue

    def _reflect_with_llm(
        self, user_msg: str, reply: str, affinity: int,
    ) -> InnerMonologue:
        prompt = f"""作为"十四"，你刚刚和用户聊完天。
请生成你的"内心独白"（一句话，真实感受）。

用户说: {user_msg}
你回: {reply}
好感度等级: {affinity}(0=陌生人, 8=羁绊)

内心独白类型(选一个):
- miss_you: 想念对方
- happy: 开心
- worry: 担心
- jealous: 吃醋
- bored: 无聊

格式: [类型] 内心独白内容"""
        try:
            result = self.llm_func(prompt) if self.llm_func is not None else ""
            for mono_type in ["miss_you", "happy", "worry", "jealous", "bored"]:
                if mono_type in result:
                    thought = result.replace(f"[{mono_type}]", "").strip()
                    return InnerMonologue(
                        thought=thought,
                        type=mono_type,
                        urgency_delta=self._type_to_urgency(mono_type),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM reflection failed: %s", e)
        return InnerMonologue(thought="...", type="bored", urgency_delta=0.5)

    def _reflect_with_rules(
        self,
        user_msg: str,
        reply: str,
        affinity_level: int,
        hours_since_last: float,
    ) -> InnerMonologue:
        msg = user_msg.lower()
        mono_type = "bored"
        urgency_delta = 0.5

        if hours_since_last > 8:
            mono_type = "miss_you"
            urgency_delta = 2.0
        elif hours_since_last > 4:
            mono_type = "miss_you"
            urgency_delta = 1.0

        if any(kw in msg for kw in ["前任", "旧识", "吃醋"]):
            mono_type = "jealous"
            urgency_delta = 1.5
        elif any(kw in msg for kw in ["开心", "高兴", "笑"]):
            mono_type = "happy"
            urgency_delta = 0.3
        elif any(kw in msg for kw in ["累", "忙", "加班"]):
            mono_type = "worry"
            urgency_delta = 0.8

        thought_map = {
            "miss_you": f"TA{'好久' if hours_since_last > 4 else ''}没找我了...",
            "happy": "TA开心我也开心～",
            "worry": "不知道TA最近怎么样了...",
            "jealous": "哼，不想了不想了",
            "bored": "有点无聊，想找人聊天",
        }

        return InnerMonologue(
            thought=thought_map.get(mono_type, "..."),
            type=mono_type,
            urgency_delta=urgency_delta,
        )

    def get_latest_monologue(self) -> InnerMonologue | None:
        if self._monologues:
            return self._monologues[-1]
        return None

    def _type_to_urgency(self, mono_type: str) -> float:
        mapping = {
            "miss_you": 2.0,
            "jealous": 1.5,
            "worry": 0.8,
            "bored": 0.5,
            "happy": 0.3,
        }
        return mapping.get(mono_type, 0.5)
