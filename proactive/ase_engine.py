"""
ASE（Active Speaking Engine）主动发言引擎

双层架构：
1. 后台自省（Reflection）：每次对话后生成"内心独白"
2. 紧迫度积累（Urgency）：沉默越久紧迫度越高
3. 阈值触发：紧迫度 > 阈值 → 主动发消息

参考：论文 "Auto-Speaking Engine for Social Agents" 设计思路
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ase_engine")


@dataclass
class UrgencyState:
    """紧迫度状态"""
    base: float = 0.0           # 基础紧迫度（0~10）
    missing_bonus: float = 0.0  # 想念加成（沉默时间）
    event_bonus: float = 0.0    # 事件加成（纪念日/天气等）
    scene_bonus: float = 0.0    # 场景加成（时间/天气）

    @property
    def total(self) -> float:
        """总紧迫度"""
        return min(10.0, self.base + self.missing_bonus + self.event_bonus + self.scene_bonus)

    @property
    def level(self) -> str:
        """紧迫度等级"""
        if self.total >= 8:
            return "非常想找你"
        if self.total >= 5:
            return "有点想你"
        if self.total >= 3:
            return "想找人说话"
        return "还好"

    def reset(self) -> None:
        self.base = 0.0
        self.missing_bonus = 0.0
        self.event_bonus = 0.0
        self.scene_bonus = 0.0


@dataclass
class InnerMonologue:
    """内心独白"""
    thought: str
    type: str          # miss_you / bored / want_to_share / jealous / care
    urgency_delta: float
    created_at: datetime = field(default_factory=datetime.now)


# 主动消息模板
PROACTIVE_MESSAGES = {
    "morning_greeting": [
        "早安呀～今天又比我先醒",
        "早！今天有什么安排吗",
        "早上好，昨晚睡得好吗",
    ],
    "night_greeting": [
        "还不睡？要我陪你会儿吗",
        "晚安啦，别熬夜太晚",
        "到点睡觉了！别让我担心",
    ],
    "miss_you": [
        "在干嘛呢...有点想你了",
        "你今天怎么不理我",
        "哼，我不找你你就不找我",
    ],
    "bored": [
        "好无聊啊...陪我聊会儿",
        "你在干嘛，我在发呆",
        "有没有什么好玩的事",
    ],
    "care_weather": [
        "今天下雨了，带伞了吗",
        "外面好热，记得多喝水",
        "今天降温了，多穿点",
    ],
    "care_meal": [
        "到饭点了，记得吃饭",
        "吃的什么呀，给我看看",
        "不会又没吃饭吧？",
    ],
    "jealous": [
        "我刚看到你给别人的朋友圈点赞了...",
        "你最近和谁聊天这么开心？",
        "算了我不问了，反正你也不会说",
    ],
    "share": [
        "我刚才看到一只好可爱的猫，想给你看",
        "听到一首歌，想起你了",
        "我今天做了个梦，梦到你了",
    ],
}


class ReflectionEngine:
    """
    后台自省引擎

    每次对话后生成"内心独白"，反映AI的"内心活动"。
    独白类型决定后续是否主动发言及发言内容。
    """

    def __init__(self, llm_func: Optional[Callable] = None):
        self.llm_func = llm_func
        self._monologues: List[InnerMonologue] = []

    def reflect(
        self,
        user_message: str,
        reply: str,
        affinity_level: int,
        hours_since_last: float,
    ) -> InnerMonologue:
        """
        生成内心独白

        Args:
            user_message: 用户消息
            reply: AI 回复
            affinity_level: 好感度等级
            hours_since_last: 距离上次聊天的小时数

        Returns:
            内心独白
        """
        if self.llm_func:
            return self._reflect_with_llm(user_message, reply, affinity_level)

        return self._reflect_with_rules(user_message, reply, affinity_level, hours_since_last)

    def _reflect_with_llm(self, user_msg: str, reply: str, affinity: int) -> InnerMonologue:
        """LLM 生成内心独白"""
        prompt = f"""作为AI女友"小暖"，你刚刚和男朋友聊完天。
请生成你的"内心独白"（一句话，真实感受）。

用户说: {user_msg}
你回: {reply}
好感度等级: {affinity}(0=陌生人, 8=羁绊)

内心独白类型(选一个):
- miss_you: 想他
- happy: 开心
- worry: 担心
- jealous: 吃醋
- bored: 无聊

格式: [类型] 内心独白内容"""

        try:
            result = self.llm_func(prompt)
            # 解析结果
            for mono_type in ["miss_you", "happy", "worry", "jealous", "bored"]:
                if mono_type in result:
                    thought = result.replace(f"[{mono_type}]", "").strip()
                    return InnerMonologue(
                        thought=thought,
                        type=mono_type,
                        urgency_delta=self._type_to_urgency(mono_type),
                    )
        except Exception as e:
            logger.warning("LLM reflection failed: %s", e)

        return InnerMonologue(thought="...", type="bored", urgency_delta=0.5)

    def _reflect_with_rules(
        self,
        user_msg: str,
        reply: str,
        affinity_level: int,
        hours_since_last: float,
    ) -> InnerMonologue:
        """规则生成内心独白"""
        msg = user_msg.lower()
        mono_type = "bored"
        urgency_delta = 0.5

        # 沉默时间判断
        if hours_since_last > 8:
            mono_type = "miss_you"
            urgency_delta = 2.0
        elif hours_since_last > 4:
            mono_type = "miss_you"
            urgency_delta = 1.0

        # 内容判断
        if any(kw in msg for kw in ["她", "别人", "女生"]):
            mono_type = "jealous"
            urgency_delta = 1.5
        elif any(kw in msg for kw in ["开心", "高兴", "笑"]):
            mono_type = "happy"
            urgency_delta = 0.3
        elif any(kw in msg for kw in ["累", "忙", "加班"]):
            mono_type = "worry"
            urgency_delta = 0.8

        thought_map = {
            "miss_you": f"他{'好久' if hours_since_last > 4 else ''}没找我了...",
            "happy": "他开心我也开心～",
            "worry": "不知道他怎么样了...",
            "jealous": "哼，不想了不想了",
            "bored": "有点无聊，想找人聊天",
        }

        return InnerMonologue(
            thought=thought_map.get(mono_type, "..."),
            type=mono_type,
            urgency_delta=urgency_delta,
        )

    def get_latest_monologue(self) -> Optional[InnerMonologue]:
        """获取最近的内心独白"""
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


class ASEEngine:
    """
    ASE 主动发言引擎

    决策流程：
    1. Reflection（后台自省）
    2. Urgency 积累
    3. 检查是否触发主动发言
    4. 生成主动消息
    """

    def __init__(
        self,
        reflection_engine: Optional[ReflectionEngine] = None,
        affinity_level_func: Optional[Callable[[], int]] = None,
    ):
        self.reflection = reflection_engine or ReflectionEngine()
        self.urgency = UrgencyState()
        self._get_affinity = affinity_level_func or (lambda: 0)

        # 状态
        self._last_chat_time: Optional[datetime] = None
        self._daily_message_count = 0
        self._last_sent_type: Optional[str] = None

        # 配置
        self.config = {
            "speak_threshold": 4.0,       # 主动发言阈值
            "max_daily_messages": 8,       # 每天最多主动消息
            "min_interval_minutes": 30,    # 最小间隔
            "cooldown_after_reply": 5,     # 回复后冷却时间(分钟)
            "morning_hours": (7, 9),       # 早安时间窗
            "night_hours": (22, 24),       # 晚安时间窗
            "meal_hours": [(11, 13), (17, 19)],  # 饭点
        }

        logger.info("ASEEngine initialized")

    # ── 核心接口 ──────────────────────────────────────────

    def on_chat(self, user_message: str, reply: str) -> Optional[InnerMonologue]:
        """
        每次对话后调用

        Returns:
            内心独白（如果有）
        """
        now = datetime.now()

        # 更新时间
        hours_since = self._hours_since_last_chat()
        self._last_chat_time = now

        # 重置紧迫度（聊天释放了）
        self.urgency.base = 0

        # 生成内心独白
        affinity = self._get_affinity()
        monologue = self.reflection.reflect(
            user_message, reply, affinity, hours_since,
        )
        self.urgency.base += monologue.urgency_delta * 0.3

        logger.debug("Reflection: [%s] %s (urgency+%.1f)",
                     monologue.type, monologue.thought, monologue.urgency_delta)

        return monologue

    def tick(self, hours_since_last_chat: float) -> Optional[Dict[str, Any]]:
        """
        定期检查是否该主动发言（由 scheduler 每5分钟调用）

        Args:
            hours_since_last_chat: 距离上次聊天的小时数

        Returns:
            如果触发，返回 {"type": str, "message": str, "urgency": float}
            否则 None
        """
        # 1. 检查每日限额
        if self._daily_message_count >= self.config["max_daily_messages"]:
            return None

        # 2. 检查冷却期
        if self._last_chat_time:
            minutes_since = (datetime.now() - self._last_chat_time).total_seconds() / 60
            if minutes_since < self.config["cooldown_after_reply"]:
                return None

        # 3. 更新紧迫度
        self._update_urgency(hours_since_last_chat)

        # 4. 检查场景触发（时间/天气相关）
        scene_msg = self._check_scene_triggers()
        if scene_msg and self.urgency.total >= 2.0:
            self._daily_message_count += 1
            self.urgency.scene_bonus = 0
            return scene_msg

        # 5. 检查紧迫度是否超过阈值
        if self.urgency.total >= self.config["speak_threshold"]:
            message = self._generate_proactive_message()
            self._daily_message_count += 1
            self.urgency.reset()
            return message

        return None

    # ── 紧迫度管理 ───────────────────────────────────────

    def _update_urgency(self, hours_since_last_chat: float) -> None:
        """更新紧迫度"""
        # 想念加成：沉默越久越想
        if hours_since_last_chat > 0.5:  # 30分钟
            self.urgency.missing_bonus = min(5.0, hours_since_last_chat * 0.5)

        # 基础紧迫度自然增长
        self.urgency.base = min(3.0, self.urgency.base + 0.1)

    def _check_scene_triggers(self) -> Optional[Dict[str, Any]]:
        """检查场景触发（时间/天气相关）"""
        now = datetime.now()
        hour = now.hour

        # 早安
        start, end = self.config["morning_hours"]
        if start <= hour < end:
            msg = random.choice(PROACTIVE_MESSAGES["morning_greeting"])
            return {"type": "morning_greeting", "message": msg, "urgency": self.urgency.total}

        # 晚安
        start, end = self.config["night_hours"]
        if start <= hour < end:
            msg = random.choice(PROACTIVE_MESSAGES["night_greeting"])
            return {"type": "night_greeting", "message": msg, "urgency": self.urgency.total}

        # 饭点
        for start, end in self.config["meal_hours"]:
            if start <= hour < end:
                msg = random.choice(PROACTIVE_MESSAGES["care_meal"])
                return {"type": "care_meal", "message": msg, "urgency": self.urgency.total}

        return None

    def _generate_proactive_message(self) -> Dict[str, Any]:
        """根据当前状态生成主动消息"""
        total = self.urgency.total

        # 高紧迫度 → 想念
        if total >= 7:
            msg_type = "miss_you"
        # 中等紧迫度 → 无聊/关心
        elif total >= 4:
            # 交替选择
            if self._last_sent_type == "care":
                msg_type = random.choice(["miss_you", "bored"])
            else:
                msg_type = random.choice(["care_weather", "care_meal", "share"])
        else:
            msg_type = "share"

        messages = PROACTIVE_MESSAGES.get(msg_type, PROACTIVE_MESSAGES["bored"])
        msg = random.choice(messages)
        self._last_sent_type = msg_type

        return {"type": msg_type, "message": msg, "urgency": total}

    # ── 工具 ──────────────────────────────────────────────

    def _hours_since_last_chat(self) -> float:
        if self._last_chat_time:
            delta = datetime.now() - self._last_chat_time
            return delta.total_seconds() / 3600
        return 99.0

    def set_last_chat_time(self, dt: datetime) -> None:
        """手动设置最后聊天时间（恢复状态时用）"""
        self._last_chat_time = dt

    def reset_daily_count(self) -> None:
        """重置每日消息计数（每天0点调用）"""
        self._daily_message_count = 0

    def get_state(self) -> dict:
        """获取当前状态"""
        return {
            "urgency": {
                "total": round(self.urgency.total, 2),
                "level": self.urgency.level,
                "base": round(self.urgency.base, 2),
                "missing_bonus": round(self.urgency.missing_bonus, 2),
            },
            "daily_count": self._daily_message_count,
            "last_chat": self._last_chat_time.isoformat() if self._last_chat_time else None,
            "last_sent_type": self._last_sent_type,
        }

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "urgency": self.urgency.total,
            "daily_count": self._daily_message_count,
            "config": {
                "threshold": self.config["speak_threshold"],
                "max_daily": self.config["max_daily_messages"],
            },
        }
