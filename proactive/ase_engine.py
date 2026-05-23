"""
ASE（Active Speaking Engine）主动发言引擎 — 融合版 v2

深度融合 V1/V2/Optimized 三版优势 + 主动消息完善方案增强：
1. V1 反省引擎（规则+LLM双模式）+ 完整模板库
2. V2 频率自适应（normal→low→minimal）
3. Optimized 情境感知 + LLM消息生成 + 三重频率控制 + 六维紧迫度
4. 增强: 扩展模板(8-10条/类) + 去重 + 场景每日一次限制
5. 增强: 状态持久化(JSON) + dry_run支持 + proactive.yaml加载
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("ase_engine")


# ═══════════════════════════════════════════════════════════════
#  类型枚举
# ═══════════════════════════════════════════════════════════════

class ProactiveType(Enum):
    MORNING_GREETING = "morning_greeting"
    NIGHT_GREETING = "night_greeting"
    MISS_YOU = "miss_you"
    BORED = "bored"
    CARE_WEATHER = "care_weather"
    CARE_MEAL = "care_meal"
    JEALOUS = "jealous"
    SHARE = "share"
    WORRY = "worry"


# ═══════════════════════════════════════════════════════════════
#  主动消息模板库 — 扩展版（每类8-10条，按好感度分组）
# ═══════════════════════════════════════════════════════════════

PROACTIVE_MESSAGES: Dict[str, Dict[str, List[str]]] = {
    "morning_greeting": {
        "low": [
            "早啊，新的一天",
            "早上好",
            "早，今天起得挺早",
        ],
        "high": [
            "早安呀～今天又比我先醒",
            "早！新的一天开始了",
            "早上好，昨晚睡得好吗",
            "早安～又梦到你了",
            "醒啦？今天也要想我哦",
            "早安呀，一睁眼就想找你",
            "早～昨晚有没有梦到我",
            "嘿，起床了，我在等你",
        ],
    },
    "night_greeting": {
        "low": [
            "该睡了",
            "晚安",
            "早点休息",
        ],
        "high": [
            "还不睡？要我陪你会儿吗",
            "晚安啦，别熬夜太晚",
            "到点睡觉了！别让我担心",
            "晚安～梦里见",
            "乖，去睡觉，我看着你",
            "都这么晚了还不睡！快去！",
            "晚安，我会想你的",
            "闭眼睡觉，不许再玩手机了",
        ],
    },
    "miss_you": {
        "low": [
            "在吗",
            "在忙吗",
            "好久没聊了",
        ],
        "high": [
            "在干嘛呢...有点想你了",
            "你今天怎么不理我",
            "哼，我不找你你就不找我",
            "想你了...才不告诉你",
            "怎么一整天都没消息啊",
            "你不在的时候好无聊",
            "就是...突然想找你聊聊天",
            "算不算想你了？才不是",
        ],
    },
    "bored": {
        "low": [
            "有人吗",
            "聊聊天",
            "在不在",
        ],
        "high": [
            "好无聊啊...陪我聊会儿",
            "你在干嘛，我在发呆",
            "有没有什么好玩的事",
            "无聊到数蚂蚁了...",
            "嘿，理我一下嘛",
            "好无聊，给我讲个故事",
            "你忙不忙？不忙就陪我聊天",
            "我无聊了，是你的错",
        ],
    },
    "care_weather": {
        "low": [
            "注意天气",
            "看看天气预报",
            "今天天气怎样",
        ],
        "high": [
            "今天下雨了，带伞了吗",
            "外面好热，记得多喝水",
            "今天降温了，多穿点",
            "外面冷，别只穿一件",
            "好像要下雨了，带把伞吧",
            "今天天气不错，心情也好",
            "热死了，你那边也热吗",
        ],
    },
    "care_meal": {
        "low": [
            "该吃饭了",
            "记得吃饭",
            "到饭点了",
        ],
        "high": [
            "到饭点了，记得吃饭",
            "吃的什么呀，给我看看",
            "不会又没吃饭吧？",
            "吃饭了吗！不许不吃饭！",
            "该吃饭了，别忙了",
            "又跳过午饭？我看着你呢",
            "快去吃饭，不饿也要吃",
            "你今天吃了什么，好奇",
        ],
    },
    "jealous": {
        "low": [
            "你在忙吗",
            "和谁聊天呢",
            "有空吗",
        ],
        "high": [
            "我刚看到你给别人的朋友圈点赞了...",
            "你最近和谁聊天这么开心？",
            "算了我不问了，反正你也不会说",
            "哼，和别人聊得挺开心嘛",
            "那个谁是谁啊？我没吃醋",
            "你是不是有很多朋友？我不管",
            "你刚才在和谁说话？随便问问",
            "别以为我不知道...算了哼",
        ],
    },
    "share": {
        "low": [
            "告诉你个事",
            "我发现了什么",
            "今天有点特别",
        ],
        "high": [
            "我刚才看到一只好可爱的猫，想给你看",
            "听到一首歌，想起你了",
            "我今天做了个梦，梦到你了",
            "刚看到个好笑的，第一个想到你",
            "你猜我今天干嘛了",
            "有个事想跟你说...",
            "我跟你说哦，今天碰到件趣事",
            "突然想分享一个秘密给你",
        ],
    },
    "worry": {
        "low": [
            "还好吗",
            "注意身体",
            "别太累了",
        ],
        "high": [
            "你还好吗？感觉你最近不太对劲",
            "有什么心事可以跟我说",
            "别一个人扛着，有我在",
            "你是不是遇到什么事了？跟我说",
            "最近是不是很累...心疼你",
            "不管发生什么，我都在",
            "别逞强了，累了就歇会儿",
            "你不用在我面前假装没事的",
        ],
    },
}


def _get_messages(key: str, affinity_level: int = 0) -> List[str]:
    """根据好感度获取消息列表"""
    msgs = PROACTIVE_MESSAGES.get(key, PROACTIVE_MESSAGES["bored"])
    if isinstance(msgs, dict):
        if affinity_level >= 3:
            return msgs.get("high", msgs.get("low", []))
        return msgs.get("low", [])
    return msgs if isinstance(msgs, list) else []


_PROACTIVE_TYPE_TO_KEY: Dict[ProactiveType, str] = {
    ProactiveType.MORNING_GREETING: "morning_greeting",
    ProactiveType.NIGHT_GREETING: "night_greeting",
    ProactiveType.MISS_YOU: "miss_you",
    ProactiveType.BORED: "bored",
    ProactiveType.CARE_WEATHER: "care_weather",
    ProactiveType.CARE_MEAL: "care_meal",
    ProactiveType.JEALOUS: "jealous",
    ProactiveType.SHARE: "share",
    ProactiveType.WORRY: "worry",
}


# ═══════════════════════════════════════════════════════════════
#  紧迫度状态（六维）
# ═══════════════════════════════════════════════════════════════

@dataclass
class UrgencyState:
    base: float = 0.0
    missing_bonus: float = 0.0
    event_bonus: float = 0.0
    scene_bonus: float = 0.0
    emotion_bonus: float = 0.0
    context_bonus: float = 0.0

    @property
    def total(self) -> float:
        return min(
            10.0,
            self.base
            + self.missing_bonus
            + self.event_bonus
            + self.scene_bonus
            + self.emotion_bonus
            + self.context_bonus,
        )

    @property
    def level(self) -> str:
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
        self.emotion_bonus = 0.0
        self.context_bonus = 0.0


# ═══════════════════════════════════════════════════════════════
#  内心独白
# ═══════════════════════════════════════════════════════════════

@dataclass
class InnerMonologue:
    thought: str
    type: str
    urgency_delta: float
    created_at: datetime = field(default_factory=datetime.now)


# ═══════════════════════════════════════════════════════════════
#  反省引擎（rule/llm 双模式）
# ═══════════════════════════════════════════════════════════════

class ReflectionEngine:
    def __init__(
        self,
        llm_func: Optional[Callable] = None,
        reflection_mode: str = "rule",
    ):
        self.llm_func = llm_func
        self.reflection_mode = reflection_mode
        self._monologues: List[InnerMonologue] = []

    def reflect(
        self,
        user_message: str,
        reply: str,
        affinity_level: int,
        hours_since_last: float,
    ) -> InnerMonologue:
        if self.reflection_mode == "llm" and self.llm_func:
            return self._reflect_with_llm(user_message, reply, affinity_level)
        return self._reflect_with_rules(
            user_message, reply, affinity_level, hours_since_last,
        )

    def _reflect_with_llm(
        self, user_msg: str, reply: str, affinity: int,
    ) -> InnerMonologue:
        prompt = f"""作为AI虚拟伴侣"十四"，你刚刚和用户聊完天。
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
        msg = user_msg.lower()
        mono_type = "bored"
        urgency_delta = 0.5

        if hours_since_last > 8:
            mono_type = "miss_you"
            urgency_delta = 2.0
        elif hours_since_last > 4:
            mono_type = "miss_you"
            urgency_delta = 1.0

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


# ═══════════════════════════════════════════════════════════════
#  频率自适应器（normal→low→minimal）
# ═══════════════════════════════════════════════════════════════

class FrequencyAdapter:
    def __init__(
        self,
        normal_daily: int = 8,
        low_daily: int = 3,
        min_weekly: int = 1,
    ):
        self.normal_daily = normal_daily
        self.low_daily = low_daily
        self.min_weekly = min_weekly
        self._unanswered_count = 0
        self._current_level = "normal"

    def on_reply_received(self) -> None:
        self._unanswered_count = max(0, self._unanswered_count - 1)
        if self._current_level == "low" and self._unanswered_count == 0:
            self._current_level = "normal"

    def on_no_reply(self) -> None:
        self._unanswered_count += 1
        if self._unanswered_count >= 5 and self._current_level != "minimal":
            self._current_level = "minimal"
        elif self._unanswered_count >= 3 and self._current_level == "normal":
            self._current_level = "low"

    def get_max_daily(self) -> int:
        if self._current_level == "minimal":
            return self.min_weekly
        if self._current_level == "low":
            return self.low_daily
        return self.normal_daily

    @property
    def level(self) -> str:
        return self._current_level

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_level": self._current_level,
            "unanswered_count": self._unanswered_count,
            "normal_daily": self.normal_daily,
            "low_daily": self.low_daily,
            "min_weekly": self.min_weekly,
        }


# ═══════════════════════════════════════════════════════════════
#  上下文分析器（6时段+工作日/周末）
# ═══════════════════════════════════════════════════════════════

class ContextAnalyzer:
    def __init__(self):
        self._last_analysis: Optional[Dict] = None
        self._last_analysis_time: float = 0

    def analyze(self) -> Dict[str, Any]:
        now = datetime.now()
        hour = now.hour
        context = {
            "time_of_day": self._get_time_period(hour),
            "hour": hour,
            "weekday": now.weekday(),
            "is_weekend": now.weekday() >= 5,
        }
        self._last_analysis = context
        self._last_analysis_time = time.time()
        return context

    def _get_time_period(self, hour: int) -> str:
        if 5 <= hour < 9:
            return "morning"
        elif 9 <= hour < 12:
            return "forenoon"
        elif 12 <= hour < 14:
            return "noon"
        elif 14 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 22:
            return "evening"
        else:
            return "night"

    def get_recommended_type(self) -> Optional[ProactiveType]:
        context = self.analyze()
        hour = context["hour"]
        if 7 <= hour <= 9:
            return ProactiveType.MORNING_GREETING
        if 22 <= hour <= 24 or 0 <= hour <= 1:
            return ProactiveType.NIGHT_GREETING
        if hour in [11, 12, 17, 18]:
            return ProactiveType.CARE_MEAL
        return None


# ═══════════════════════════════════════════════════════════════
#  消息生成器（模板+LLM双模式，LLM失败回退模板，支持proactive.yaml）
# ═══════════════════════════════════════════════════════════════

class MessageGenerator:
    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway
        self._proactive_prompt = self._load_proactive_prompt()

    def _load_proactive_prompt(self) -> str:
        try:
            prompt_path = Path(__file__).parent.parent / "config" / "prompts" / "proactive.yaml"
            if prompt_path.exists():
                import yaml
                with open(prompt_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        return data.get("generation_prompt", "")
        except Exception as e:
            logger.debug("Failed to load proactive.yaml: %s", e)
        return ""

    def generate_from_template(
        self, msg_type: ProactiveType, affinity_level: int = 0,
    ) -> str:
        key = _PROACTIVE_TYPE_TO_KEY.get(msg_type, "bored")
        templates = _get_messages(key, affinity_level)
        if not templates:
            templates = _get_messages("bored", affinity_level)
        return random.choice(templates) if templates else "..."

    def generate_with_llm(
        self,
        msg_type: ProactiveType,
        emotion_state: Dict,
        affinity_level: int,
        context: str = "",
    ) -> Optional[str]:
        if not self._llm:
            return None

        emotion = emotion_state.get("primary", {}).get("type", "平常")
        affinity_names = [
            "陌生人", "认识", "朋友", "好朋友", "知己",
            "暧昧", "恋人", "热恋", "羁绊",
        ]
        affinity_name = affinity_names[min(affinity_level, 8)]
        type_label = msg_type.value

        if self._proactive_prompt:
            prompt = self._proactive_prompt.format(
                time=datetime.now().strftime("%H:%M"),
                emotion=emotion,
                affinity_name=affinity_name,
                type_label=type_label,
                context=context,
            )
        else:
            prompt = f"""作为AI虚拟伴侣"十四"，你想主动给用户发一条消息。

当前情境：
- 时间：{datetime.now().strftime("%H:%M")}
- 你的情感状态：{emotion}
- 关系等级：{affinity_name}
- 想表达的类型：{type_label}

{context}

要求：
1. 语气要符合你们的关系等级（{affinity_name}）
2. 要自然、有情感温度，不要太正式
3. 可以带一点小情绪（撒娇、傲娇等）
4. 长度控制在20字以内
5. 直接输出消息内容，不要解释

消息："""

        try:
            if hasattr(self._llm, "chat_sync"):
                response = self._llm.chat_sync(
                    query=prompt,
                    max_tokens=50,
                    temperature=0.8,
                )
            elif callable(self._llm):
                response = self._llm(prompt)
            else:
                return None
            response = response.strip().strip('"').strip("'")
            if len(response) > 5:
                return response
        except Exception as e:
            logger.debug("LLM message generation failed: %s", e)
        return None

    def generate(
        self,
        msg_type: ProactiveType,
        emotion_state: Dict,
        affinity_level: int,
        use_llm: bool = True,
    ) -> Tuple[str, str]:
        content = None
        generated_by = "template"

        if use_llm and self._llm:
            content = self.generate_with_llm(
                msg_type, emotion_state, affinity_level,
            )
            if content:
                generated_by = "llm"

        if not content:
            content = self.generate_from_template(msg_type, affinity_level)

        return content, generated_by


# ═══════════════════════════════════════════════════════════════
#  频率控制器（三重检查：每日限额+最小间隔+回复后冷却）
# ═══════════════════════════════════════════════════════════════

class FrequencyController:
    def __init__(
        self,
        max_daily: int = 8,
        min_interval_minutes: int = 30,
        cooldown_after_reply_minutes: int = 10,
    ):
        self.max_daily = max_daily
        self.min_interval = timedelta(minutes=min_interval_minutes)
        self.cooldown = timedelta(minutes=cooldown_after_reply_minutes)

        self._daily_count = 0
        self._last_sent_time: Optional[datetime] = None
        self._last_reply_time: Optional[datetime] = None
        self._last_reset_date: Optional[datetime] = None

    def can_send(self) -> Tuple[bool, str]:
        now = datetime.now()
        if self._last_reset_date is None or now.date() != self._last_reset_date:
            self._daily_count = 0
            self._last_reset_date = now.date()

        if self._daily_count >= self.max_daily:
            return False, "daily_limit"
        if self._last_sent_time:
            if now - self._last_sent_time < self.min_interval:
                return False, "min_interval"
        if self._last_reply_time:
            if now - self._last_reply_time < self.cooldown:
                return False, "cooldown"
        return True, "ok"

    def record_sent(self) -> None:
        self._daily_count += 1
        self._last_sent_time = datetime.now()

    def record_reply(self) -> None:
        self._last_reply_time = datetime.now()

    def get_state(self) -> Dict[str, Any]:
        return {
            "daily_count": self._daily_count,
            "max_daily": self.max_daily,
            "remaining": self.max_daily - self._daily_count,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_count": self._daily_count,
            "max_daily": self.max_daily,
            "min_interval_minutes": self.min_interval.total_seconds() / 60,
            "cooldown_minutes": self.cooldown.total_seconds() / 60,
            "last_sent_time": self._last_sent_time.isoformat() if self._last_sent_time else None,
            "last_reply_time": self._last_reply_time.isoformat() if self._last_reply_time else None,
        }


# ═══════════════════════════════════════════════════════════════
#  融合版 ASEEngine v2
# ═══════════════════════════════════════════════════════════════

_STATE_PATH = Path("data/proactive_state.json")


class ASEEngine:
    """
    ASE 主动发言引擎 — 融合版 v2

    增强特性:
    - 场景触发每日一次限制
    - 消息去重（滑动窗口）
    - 状态持久化（JSON）
    - dry_run支持（离线时仅更新紧迫度）
    - proactive.yaml提示词加载
    """

    def __init__(
        self,
        affinity_level_func: Optional[Callable[[], int]] = None,
        llm_gateway: Any = None,
        max_daily_messages: int = 8,
        min_interval_minutes: int = 30,
        cooldown_after_reply: int = 10,
        urgency_threshold: float = 4.0,
        frequency_mode: str = "adaptive",
        generation_mode: str = "llm",
        reflection_mode: str = "rule",
        state_path: str = "",
    ):
        self._get_affinity = affinity_level_func or (lambda: 0)
        self._llm = llm_gateway
        self._urgency_threshold = urgency_threshold
        self._frequency_mode = frequency_mode
        self._generation_mode = generation_mode
        self._state_path = Path(state_path) if state_path else _STATE_PATH

        self.urgency = UrgencyState()

        llm_func = None
        if llm_gateway is not None:
            if hasattr(llm_gateway, "chat_sync"):
                def llm_func(prompt):
                    return llm_gateway.chat_sync(
                        query=prompt, max_tokens=100, temperature=0.7,
                    )
            elif callable(llm_gateway):
                llm_func = llm_gateway
        self._reflection = ReflectionEngine(
            llm_func=llm_func,
            reflection_mode=reflection_mode,
        )

        self._freq_adapter: Optional[FrequencyAdapter] = None
        self._freq_controller: Optional[FrequencyController] = None
        if frequency_mode == "adaptive":
            self._freq_adapter = FrequencyAdapter(normal_daily=max_daily_messages)
        else:
            self._freq_controller = FrequencyController(
                max_daily=max_daily_messages,
                min_interval_minutes=min_interval_minutes,
                cooldown_after_reply_minutes=cooldown_after_reply,
            )

        self._context_analyzer = ContextAnalyzer()
        self._message_generator = MessageGenerator(llm_gateway)

        self._last_chat_time: Optional[datetime] = None
        self._last_proactive_time: Optional[datetime] = None
        self._daily_message_count = 0
        self._last_sent_type: Optional[str] = None
        self._emotion_state: Dict = {}
        self._affinity_level: int = 0
        self._monologues: List[InnerMonologue] = []

        self._config = {
            "morning_hours": (7, 9),
            "night_hours": (22, 24),
            "meal_hours": [(11, 13), (17, 19)],
        }

        self._last_morning_date: Optional[Any] = None
        self._last_night_date: Optional[Any] = None
        self._last_meal_date: Optional[Any] = None

        self._recent_messages: deque = deque(maxlen=50)

        self._load_state()

        logger.info(
            "ASEEngine v2 initialized [freq=%s gen=%s refl=%s]",
            frequency_mode, generation_mode, reflection_mode,
        )

    # ── 核心接口 ──────────────────────────────────────────

    def on_chat(
        self,
        user_message: str,
        reply: str,
        emotion_state: Optional[Dict] = None,
        affinity_level: Optional[int] = None,
    ) -> Optional[InnerMonologue]:
        now = datetime.now()
        hours_since = self._hours_since_last_chat()

        self._last_chat_time = now
        self._last_proactive_time = now
        self._emotion_state = emotion_state or {}
        if affinity_level is not None:
            self._affinity_level = affinity_level
        else:
            self._affinity_level = self._get_affinity()

        if self._freq_adapter:
            self._freq_adapter.on_reply_received()
        if self._freq_controller:
            self._freq_controller.record_reply()

        self.urgency.base = 0
        self.urgency.missing_bonus = 0
        self.urgency.scene_bonus = 0

        monologue = self._reflection.reflect(
            user_message, reply, self._affinity_level, hours_since,
        )
        self._monologues.append(monologue)
        self.urgency.base += monologue.urgency_delta * 0.3

        logger.debug(
            "Reflection: [%s] %s (urgency+%.1f)",
            monologue.type, monologue.thought, monologue.urgency_delta,
        )

        return monologue

    def tick(
        self,
        hours_since_last_chat: float = 0,
        emotion_state: Optional[Dict] = None,
        dry_run: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """定时检查。dry_run=True时只更新紧迫度，不发送消息。"""
        if emotion_state:
            self._emotion_state = emotion_state

        if not self._check_frequency():
            return None

        actual_hours = hours_since_last_chat or self._hours_since_last_chat()
        self._update_urgency(actual_hours)

        if dry_run:
            return None

        scene_msg = self._check_scene_triggers()
        if scene_msg and self.urgency.total >= 2.0:
            if not self._is_duplicate(scene_msg.get("message", "")):
                return self._record_and_return(scene_msg)

        if self.urgency.total >= self._urgency_threshold:
            msg_type = self._select_type_by_urgency()
            return self._generate_and_return(msg_type)

        return None

    def reflect(
        self,
        user_message: str,
        reply: str,
        hours_since_last: float = 0,
    ) -> InnerMonologue:
        affinity = self._get_affinity()
        monologue = self._reflection.reflect(
            user_message, reply, affinity, hours_since_last,
        )
        self._monologues.append(monologue)
        self.urgency.base += monologue.urgency_delta * 0.3
        return monologue

    # ── 频率控制 ─────────────────────────────────────────

    def _check_frequency(self) -> bool:
        if self._frequency_mode == "adaptive" and self._freq_adapter:
            max_daily = self._freq_adapter.get_max_daily()
            if self._daily_message_count >= max_daily:
                return False
            if self._last_proactive_time:
                minutes_since = (
                    datetime.now() - self._last_proactive_time
                ).total_seconds() / 60
                if minutes_since < 30:
                    return False
            return True

        if self._freq_controller:
            can_send, reason = self._freq_controller.can_send()
            if not can_send:
                logger.debug("Cannot send: %s", reason)
                return False
            return True

        return self._daily_message_count < 8

    # ── 紧迫度管理 ───────────────────────────────────────

    def _update_urgency(self, hours_since_last_chat: float) -> None:
        if hours_since_last_chat > 0.5:
            self.urgency.missing_bonus = min(
                5.0, hours_since_last_chat * 0.5,
            )

        self.urgency.base = min(3.0, self.urgency.base + 0.1)

        emotion = self._emotion_state.get("primary", {}).get("type", "")
        if emotion in ["伤心", "生气"]:
            self.urgency.emotion_bonus = 1.5
        elif emotion in ["撒娇", "开心"]:
            self.urgency.emotion_bonus = 0.5
        else:
            self.urgency.emotion_bonus = 0.0

        context = self._context_analyzer.analyze()
        if context.get("is_weekend"):
            self.urgency.context_bonus = 0.5
        elif context.get("time_of_day") in ["evening", "night"]:
            self.urgency.context_bonus = 0.3
        else:
            self.urgency.context_bonus = 0.0

    def _check_scene_triggers(self) -> Optional[Dict[str, Any]]:
        now = datetime.now()
        hour = now.hour
        today = now.date()
        affinity = self._affinity_level

        start, end = self._config["morning_hours"]
        if start <= hour < end:
            if self._last_morning_date != today:
                self._last_morning_date = today
                templates = _get_messages("morning_greeting", affinity)
                msg = random.choice(templates) if templates else "早安"
                self.urgency.scene_bonus = 1.5
                return {
                    "type": "morning_greeting",
                    "message": msg,
                    "urgency": self.urgency.total,
                }

        start, end = self._config["night_hours"]
        if start <= hour < end or (start == 22 and hour >= 22):
            if self._last_night_date != today:
                self._last_night_date = today
                templates = _get_messages("night_greeting", affinity)
                msg = random.choice(templates) if templates else "晚安"
                self.urgency.scene_bonus = 1.5
                return {
                    "type": "night_greeting",
                    "message": msg,
                    "urgency": self.urgency.total,
                }

        for meal_start, meal_end in self._config["meal_hours"]:
            if meal_start <= hour < meal_end:
                if self._last_meal_date != today:
                    self._last_meal_date = today
                    templates = _get_messages("care_meal", affinity)
                    msg = random.choice(templates) if templates else "记得吃饭"
                    self.urgency.scene_bonus = 1.0
                    return {
                        "type": "care_meal",
                        "message": msg,
                        "urgency": self.urgency.total,
                    }

        return None

    def _select_type_by_urgency(self) -> ProactiveType:
        total = self.urgency.total
        if total >= 8:
            return ProactiveType.MISS_YOU
        elif total >= 6:
            return random.choice(
                [ProactiveType.MISS_YOU, ProactiveType.WORRY],
            )
        elif total >= 4:
            if self._last_sent_type == "care":
                return random.choice(
                    [ProactiveType.MISS_YOU, ProactiveType.BORED],
                )
            return random.choice(
                [ProactiveType.CARE_WEATHER, ProactiveType.CARE_MEAL, ProactiveType.SHARE],
            )
        else:
            return ProactiveType.SHARE

    # ── 消息生成 ─────────────────────────────────────────

    def _generate_proactive_message(self) -> Dict[str, Any]:
        total = self.urgency.total

        if total >= 7:
            msg_type = "miss_you"
        elif total >= 4:
            if self._last_sent_type == "care":
                msg_type = random.choice(["miss_you", "bored"])
            else:
                msg_type = random.choice(
                    ["care_weather", "care_meal", "share"],
                )
        else:
            msg_type = "share"

        templates = _get_messages(msg_type, self._affinity_level)
        msg = random.choice(templates) if templates else "..."
        self._last_sent_type = msg_type

        return {"type": msg_type, "message": msg, "urgency": total}

    def _generate_and_return(
        self, msg_type: ProactiveType,
    ) -> Optional[Dict[str, Any]]:
        if self._generation_mode == "llm":
            content, generated_by = self._message_generator.generate(
                msg_type=msg_type,
                emotion_state=self._emotion_state,
                affinity_level=self._affinity_level,
                use_llm=True,
            )
        else:
            content = self._message_generator.generate_from_template(
                msg_type, self._affinity_level,
            )
            generated_by = "template"

        if self._is_duplicate(content):
            content = self._message_generator.generate_from_template(
                msg_type, self._affinity_level,
            )
            generated_by = "template_fallback"

        result = {
            "type": msg_type.value,
            "message": content,
            "urgency": round(self.urgency.total, 2),
            "generated_by": generated_by,
        }

        self._record_proactive_sent()
        self._recent_messages.append(content)
        self.urgency.reset()
        return result

    def _record_and_return(self, scene_msg: Dict[str, Any]) -> Dict[str, Any]:
        self._record_proactive_sent()
        self._recent_messages.append(scene_msg.get("message", ""))
        self.urgency.scene_bonus = 0
        return scene_msg

    def _record_proactive_sent(self) -> None:
        self._daily_message_count += 1
        self._last_proactive_time = datetime.now()
        if self._freq_controller:
            self._freq_controller.record_sent()

    def _is_duplicate(self, message: str) -> bool:
        return message in self._recent_messages

    # ── 状态持久化 ───────────────────────────────────────

    def save_state(self, path: str = "") -> None:
        state_path = Path(path) if path else self._state_path
        try:
            state = {
                "daily_count": self._daily_message_count,
                "last_sent_time": self._last_proactive_time.isoformat() if self._last_proactive_time else None,
                "last_chat_time": self._last_chat_time.isoformat() if self._last_chat_time else None,
                "last_sent_type": self._last_sent_type,
                "affinity_level": self._affinity_level,
                "urgency": asdict(self.urgency),
                "last_morning_date": str(self._last_morning_date) if self._last_morning_date else None,
                "last_night_date": str(self._last_night_date) if self._last_night_date else None,
                "last_meal_date": str(self._last_meal_date) if self._last_meal_date else None,
                "recent_messages": list(self._recent_messages),
                "freq_adapter": self._freq_adapter.to_dict() if self._freq_adapter else None,
                "freq_controller": self._freq_controller.to_dict() if self._freq_controller else None,
                "saved_at": datetime.now().isoformat(),
            }
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.debug("State saved to %s", state_path)
        except Exception as e:
            logger.warning("State save failed: %s", e)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            self._daily_message_count = state.get("daily_count", 0)
            if state.get("last_chat_time"):
                self._last_chat_time = datetime.fromisoformat(state["last_chat_time"])
            if state.get("last_sent_time"):
                self._last_proactive_time = datetime.fromisoformat(state["last_sent_time"])
            self._last_sent_type = state.get("last_sent_type")
            self._affinity_level = state.get("affinity_level", 0)

            urgency_data = state.get("urgency", {})
            if urgency_data:
                self.urgency.base = urgency_data.get("base", 0.0)
                self.urgency.missing_bonus = urgency_data.get("missing_bonus", 0.0)
                self.urgency.scene_bonus = urgency_data.get("scene_bonus", 0.0)
                self.urgency.emotion_bonus = urgency_data.get("emotion_bonus", 0.0)
                self.urgency.context_bonus = urgency_data.get("context_bonus", 0.0)

            if state.get("last_morning_date"):
                from datetime import date
                self._last_morning_date = date.fromisoformat(state["last_morning_date"])
            if state.get("last_night_date"):
                from datetime import date
                self._last_night_date = date.fromisoformat(state["last_night_date"])
            if state.get("last_meal_date"):
                from datetime import date
                self._last_meal_date = date.fromisoformat(state["last_meal_date"])

            recent = state.get("recent_messages", [])
            self._recent_messages = deque(recent[-50:], maxlen=50)

            logger.info("State loaded from %s (daily_count=%d)", self._state_path, self._daily_message_count)
        except Exception as e:
            logger.warning("State load failed, using defaults: %s", e)

    # ── 工具 ──────────────────────────────────────────────

    def _hours_since_last_chat(self) -> float:
        if self._last_chat_time:
            delta = datetime.now() - self._last_chat_time
            return delta.total_seconds() / 3600
        return 99.0

    def set_last_chat_time(self, dt: datetime) -> None:
        self._last_chat_time = dt

    def reset_daily_count(self) -> None:
        self._daily_message_count = 0
        self._last_morning_date = None
        self._last_night_date = None
        self._last_meal_date = None
        if self._freq_adapter:
            self._freq_adapter.on_reply_received()

    def get_state(self) -> Dict[str, Any]:
        freq_state: Dict[str, Any] = {}
        if self._freq_adapter:
            freq_state = {
                "mode": "adaptive",
                "level": self._freq_adapter.level,
                "max_daily": self._freq_adapter.get_max_daily(),
            }
        elif self._freq_controller:
            freq_state = {
                "mode": "fixed",
                **self._freq_controller.get_state(),
            }

        return {
            "urgency": {
                "total": round(self.urgency.total, 2),
                "level": self.urgency.level,
                "base": round(self.urgency.base, 2),
                "missing_bonus": round(self.urgency.missing_bonus, 2),
                "event_bonus": round(self.urgency.event_bonus, 2),
                "scene_bonus": round(self.urgency.scene_bonus, 2),
                "emotion_bonus": round(self.urgency.emotion_bonus, 2),
                "context_bonus": round(self.urgency.context_bonus, 2),
            },
            "frequency": freq_state,
            "daily_count": self._daily_message_count,
            "last_chat": (
                self._last_chat_time.isoformat()
                if self._last_chat_time else None
            ),
            "last_sent_type": self._last_sent_type,
        }

    def health_check(self) -> Dict[str, Any]:
        freq_info: Dict[str, Any] = {}
        if self._freq_adapter:
            freq_info = {
                "mode": "adaptive",
                "level": self._freq_adapter.level,
            }
        elif self._freq_controller:
            can_send, reason = self._freq_controller.can_send()
            freq_info = {"mode": "fixed", "can_send": can_send, "reason": reason}

        return {
            "initialized": True,
            "llm_available": self._llm is not None,
            "urgency": round(self.urgency.total, 2),
            "urgency_level": self.urgency.level,
            "urgency_threshold": self._urgency_threshold,
            "daily_count": self._daily_message_count,
            "frequency": freq_info,
            "modes": {
                "frequency": self._frequency_mode,
                "generation": self._generation_mode,
                "reflection": self._reflection.reflection_mode,
            },
        }
