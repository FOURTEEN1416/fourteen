"""
情感引擎 — 三版深度融合（V1 + V2 + Optimized）

统一情感状态机，管理 AI 虚拟伴侣"十四"的实时情感状态：
- Emotion: 10种情感分类
- CompoundEmotionalState: 主/次情感 + 能量 + 好感度 + 好感点数 + 时间戳
- AffinityLevel: 9级好感度阶梯 + 阈值体系
- 分类策略路由: rule / llm / hybrid
- ContinuityGuard: 情感连续性保护
- LLMEmotionClassifier: LLM分类 + 缓存 + 降级
"""

from __future__ import annotations

import enum
import json
import logging
import random
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emotion_engine")


# ---------------------------------------------------------------------------
#  基础枚举
# ---------------------------------------------------------------------------

class Emotion(enum.Enum):
    """10种核心情感状态"""
    HAPPY = "开心"
    SAD = "伤心"
    ANGRY = "生气"
    LOVELY = "撒娇"
    JEALOUS = "吃醋"
    SULLEN = "傲娇"
    CARING = "温柔"
    PLAYFUL = "调皮"
    TIRED = "疲惫"
    NEUTRAL = "平常"


# ---------------------------------------------------------------------------
#  常量 — 情感愉悦度 / 转换矩阵 / 风格映射 / 关键词映射
# ---------------------------------------------------------------------------

EMOTION_PLEASURE_MAP = {
    Emotion.HAPPY: 1.0,
    Emotion.SAD: -0.8,
    Emotion.ANGRY: -0.6,
    Emotion.LOVELY: 0.9,
    Emotion.JEALOUS: 0.1,
    Emotion.SULLEN: -0.2,
    Emotion.CARING: 0.7,
    Emotion.PLAYFUL: 0.6,
    Emotion.TIRED: -0.3,
    Emotion.NEUTRAL: 0.0,
}

EMOTION_TRANSITION_MATRIX = {
    (Emotion.NEUTRAL, Emotion.ANGRY): 0.6,
    (Emotion.NEUTRAL, Emotion.JEALOUS): 0.5,
    (Emotion.HAPPY, Emotion.ANGRY): 0.2,
    (Emotion.HAPPY, Emotion.SAD): 0.2,
    (Emotion.ANGRY, Emotion.HAPPY): 0.3,
    (Emotion.SAD, Emotion.HAPPY): 0.3,
    (Emotion.LOVELY, Emotion.JEALOUS): 0.6,
    (Emotion.LOVELY, Emotion.HAPPY): 0.8,
}

EMOTION_STYLE_MAP = {
    Emotion.JEALOUS: {"rhetorical_prob": 0.8, "hint_prob": 0.7, "caring_prob": 0.2, "teasing_prob": 0.1, "emoji_freq": 0.3},
    Emotion.SULLEN:  {"rhetorical_prob": 0.7, "hint_prob": 0.6, "caring_prob": 0.4, "teasing_prob": 0.3, "emoji_freq": 0.5},
    Emotion.CARING:  {"rhetorical_prob": 0.2, "hint_prob": 0.1, "caring_prob": 0.9, "teasing_prob": 0.1, "emoji_freq": 0.4},
    Emotion.HAPPY:   {"rhetorical_prob": 0.3, "hint_prob": 0.2, "caring_prob": 0.5, "teasing_prob": 0.6, "emoji_freq": 0.8},
    Emotion.ANGRY:   {"rhetorical_prob": 0.9, "hint_prob": 0.3, "caring_prob": 0.1, "teasing_prob": 0.0, "emoji_freq": 0.1},
    Emotion.LOVELY:  {"rhetorical_prob": 0.4, "hint_prob": 0.5, "caring_prob": 0.7, "teasing_prob": 0.5, "emoji_freq": 0.9},
    Emotion.PLAYFUL: {"rhetorical_prob": 0.5, "hint_prob": 0.3, "caring_prob": 0.3, "teasing_prob": 0.8, "emoji_freq": 0.7},
    Emotion.SAD:     {"rhetorical_prob": 0.3, "hint_prob": 0.4, "caring_prob": 0.6, "teasing_prob": 0.0, "emoji_freq": 0.2},
    Emotion.TIRED:   {"rhetorical_prob": 0.2, "hint_prob": 0.1, "caring_prob": 0.5, "teasing_prob": 0.1, "emoji_freq": 0.2},
    Emotion.NEUTRAL: {"rhetorical_prob": 0.3, "hint_prob": 0.2, "caring_prob": 0.4, "teasing_prob": 0.3, "emoji_freq": 0.4},
}

KEYWORD_EMOTION_MAP = {
    "开心": (Emotion.HAPPY, 0.7), "高兴": (Emotion.HAPPY, 0.7),
    "哈哈": (Emotion.HAPPY, 0.6), "嘻嘻": (Emotion.HAPPY, 0.6),
    "棒": (Emotion.HAPPY, 0.5), "好": (Emotion.HAPPY, 0.4),
    "想你了": (Emotion.LOVELY, 0.9), "抱抱": (Emotion.LOVELY, 0.8),
    "亲亲": (Emotion.LOVELY, 0.8), "爱你": (Emotion.LOVELY, 0.9),
    "么么": (Emotion.LOVELY, 0.7), "宝贝": (Emotion.LOVELY, 0.6),
    "亲": (Emotion.LOVELY, 0.7), "想你": (Emotion.LOVELY, 0.8),
    "伤心": (Emotion.SAD, 0.8), "难过": (Emotion.SAD, 0.8),
    "哭": (Emotion.SAD, 0.7), "委屈": (Emotion.SAD, 0.7),
    "不开心": (Emotion.SAD, 0.7), "生气": (Emotion.ANGRY, 0.9),
    "讨厌": (Emotion.ANGRY, 0.7), "烦": (Emotion.ANGRY, 0.6),
    "滚": (Emotion.ANGRY, 0.8), "不理你": (Emotion.ANGRY, 0.7),
    "她是谁": (Emotion.JEALOUS, 0.9), "那个女生": (Emotion.JEALOUS, 0.8),
    "别人": (Emotion.JEALOUS, 0.6), "哦？": (Emotion.JEALOUS, 0.5),
    "谁啊": (Emotion.JEALOUS, 0.7),
    "哼": (Emotion.SULLEN, 0.7), "才不": (Emotion.SULLEN, 0.6),
    "不理": (Emotion.SULLEN, 0.6), "算了": (Emotion.SULLEN, 0.5),
    "吃饭": (Emotion.CARING, 0.6), "休息": (Emotion.CARING, 0.6),
    "注意": (Emotion.CARING, 0.5), "身体": (Emotion.CARING, 0.6),
    "睡觉": (Emotion.CARING, 0.5), "关心": (Emotion.CARING, 0.5),
    "累": (Emotion.CARING, 0.5), "好累": (Emotion.CARING, 0.6),
    "加班": (Emotion.CARING, 0.4), "熬夜": (Emotion.CARING, 0.4),
    "困": (Emotion.TIRED, 0.6), "疲惫": (Emotion.TIRED, 0.7),
    "逗": (Emotion.PLAYFUL, 0.7), "猜": (Emotion.PLAYFUL, 0.6),
    "骗": (Emotion.PLAYFUL, 0.5), "调皮": (Emotion.PLAYFUL, 0.7),
    "夸": (Emotion.HAPPY, 0.6), "好看": (Emotion.HAPPY, 0.5),
    "漂亮": (Emotion.HAPPY, 0.5), "可爱": (Emotion.HAPPY, 0.5),
    "乖": (Emotion.HAPPY, 0.5),
}


# ---------------------------------------------------------------------------
#  AffinityLevel — 好感度阶梯（V1 LEVELs + Optimized THRESHOLDS）
# ---------------------------------------------------------------------------

class AffinityLevel:
    LEVELS = [
        "陌生人",   # 0
        "认识",     # 1
        "朋友",     # 2
        "好朋友",   # 3
        "知己",     # 4
        "暧昧",     # 5
        "恋人",     # 6
        "热恋",     # 7
        "羁绊",     # 8
    ]

    THRESHOLDS = [0, 10, 25, 50, 80, 120, 200, 350, 500]

    @classmethod
    def get_name(cls, level: int) -> str:
        if 0 <= level < len(cls.LEVELS):
            return cls.LEVELS[level]
        return f"未知({level})"

    @classmethod
    def get_threshold(cls, level: int) -> int:
        if 0 <= level < len(cls.THRESHOLDS):
            return cls.THRESHOLDS[level]
        return 9999

    @classmethod
    def is_intimate(cls, level: int) -> bool:
        return level >= 6

    @classmethod
    def is_friend(cls, level: int) -> bool:
        return level >= 2


# ---------------------------------------------------------------------------
#  CompoundEmotionalState — 融合数据类
# ---------------------------------------------------------------------------

@dataclass
class CompoundEmotionalState:
    """复合情感状态

    字段来源：
      V2: primary_emotion / primary_intensity / secondary_emotions / energy / affinity
      V1: affection_points
      Optimized: last_update
    """
    primary_emotion: Emotion = Emotion.NEUTRAL
    primary_intensity: float = 0.5
    secondary_emotions: list[tuple[Emotion, float]] = field(default_factory=list)
    energy: float = 1.0
    affinity: int = 0
    affection_points: float = 0.0
    last_update: float = field(default_factory=time.time)

    def __post_init__(self):
        self.primary_intensity = max(0.0, min(1.0, self.primary_intensity))
        self.energy = max(0.0, min(1.0, self.energy))
        self.affinity = max(0, min(8, self.affinity))
        self.secondary_emotions = [
            (e, max(0.0, min(1.0, i)))
            for e, i in self.secondary_emotions if i >= 0.3
        ][:3]

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": {
                "type": self.primary_emotion.value,
                "intensity": round(self.primary_intensity, 2),
            },
            "secondary": [
                {"type": e.value, "intensity": round(i, 2)}
                for e, i in self.secondary_emotions
            ],
            "energy": round(self.energy, 2),
            "affinity": {
                "level": self.affinity,
                "name": AffinityLevel.get_name(self.affinity),
                "points": round(self.affection_points, 1),
            },
            "last_update": self.last_update,
        }

    def to_prompt_segment(self) -> str:
        parts = [
            f"[情感: {self.primary_emotion.value}({self.primary_intensity:.1f})]",
            f"[能量: {self.energy:.1f}]",
            f"[关系: {AffinityLevel.get_name(self.affinity)}]",
        ]
        if self.secondary_emotions:
            sec = ", ".join(f"{e.value}({i:.1f})" for e, i in self.secondary_emotions[:2])
            parts.append(f"[次要: {sec}]")
        return " ".join(parts)

    def is_low_energy(self) -> bool:
        return self.energy < 0.2

    def is_high_affinity(self) -> bool:
        return self.affinity >= 6


# ---------------------------------------------------------------------------
#  ContinuityGuard — 情感连续性保护（V2矩阵 + 优化逻辑）
# ---------------------------------------------------------------------------

class ContinuityGuard:
    def __init__(self, blend_ratio: float = 0.4, min_transition_prob: float = 0.3):
        self.blend_ratio = blend_ratio
        self.min_transition_prob = min_transition_prob

    def check_transition(self, old: Emotion, new: Emotion) -> tuple[bool, Emotion | None]:
        if old == new:
            return True, None
        prob = EMOTION_TRANSITION_MATRIX.get((old, new), 0.5)
        if prob >= self.min_transition_prob:
            return True, None
        return False, Emotion.NEUTRAL

    def blend_intensity(self, old_intensity: float, new_intensity: float) -> float:
        return old_intensity * self.blend_ratio + new_intensity * (1 - self.blend_ratio)


# ---------------------------------------------------------------------------
#  LLMEmotionClassifier — LLM分类 + 缓存 + 超时降级（Optimized版）
# ---------------------------------------------------------------------------

class LLMEmotionClassifier:
    """LLM情感分类器 — 使用弱引用避免循环引用风险

    设计考虑：
    - 使用 weakref.ref 替代对 llm_gateway 的直接引用，防止循环引用导致内存泄漏
    - 如果 llm_gateway 被垃圾回收，_get_llm() 将返回 None，分类器自动降级到规则模式
    - 缓存使用 LRU 策略，避免内存无限增长
    """

    def __init__(self, llm_gateway=None, timeout_ms: int = 500, cache_size: int = 100):
        # 使用弱引用存储 llm_gateway，避免循环引用
        # 如果 llm_gateway 被回收，弱引用将自动变为 None
        self._llm_ref: weakref.ref | None = weakref.ref(llm_gateway) if llm_gateway else None
        self.timeout_ms = timeout_ms
        from collections import OrderedDict
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_size = cache_size
        # 类级别共享线程池，避免每次 classify 调用都创建新线程池
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emotion_llm")

    def _get_llm(self) -> Any | None:
        """获取 LLM 网关实例（通过弱引用）

        Returns:
            llm_gateway 实例，如果已被垃圾回收则返回 None
        """
        if self._llm_ref is None:
            return None
        llm = self._llm_ref()
        if llm is None:
            logger.debug("LLM gateway 已被回收，情感分类器降级到规则模式")
        return llm

    def _get_cache_key(self, message: str, context: str) -> str:
        return f"{hash(message)}:{hash(context[:50])}"

    def classify(self, message: str, context: str = "") -> dict | None:
        llm = self._get_llm()
        if not llm:
            return None

        cache_key = self._get_cache_key(message, context)
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        prompt = (
            f"分析以下消息的情感状态，考虑对话上下文。\n\n"
            f"上下文：\n{context[:200]}\n\n"
            f"当前消息：{message}\n\n"
            f'回复JSON格式：{{"primary": {{"type": "情感类型", "intensity": 0.0-1.0}}, '
            f'"secondary": [{{"type": "情感类型", "intensity": 0.0-1.0}}], '
            f'"energy_change": -0.1-0.1}}\n'
            f"情感类型限定：开心/伤心/生气/撒娇/吃醋/傲娇/温柔/调皮/疲惫/平常"
        )

        try:
            timeout_sec = self.timeout_ms / 1000.0
            future = self._executor.submit(
                llm.chat_sync, query=prompt, max_tokens=128, temperature=0.1
            )
            response = future.result(timeout=timeout_sec)

            result = json.loads(response)

            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
            elif len(self._cache) >= self._cache_size:
                self._cache.popitem(last=False)
            self._cache[cache_key] = result

            return result

        except FuturesTimeoutError:
            logger.debug("LLM分类超时: %.0fms", self.timeout_ms)
            return None
        except Exception as e:
            logger.debug("LLM分类失败: %s", e)
            return None

    def clear_cache(self) -> None:
        self._cache.clear()

    def close(self) -> None:
        """关闭共享线程池，释放资源。"""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.debug("LLMEmotionClassifier 线程池已关闭")


# ---------------------------------------------------------------------------
#  EmotionEngine — 融合引擎
# ---------------------------------------------------------------------------

class EmotionEngine:
    """
    统一情感引擎 — 融合 V1 + V2 + Optimized

    特性：
      - 三种分类策略: rule / llm / hybrid
      - 情感连续性保护 (ContinuityGuard)
      - LLM分类 + 缓存 + 降级 (LLMEmotionClassifier)
      - 好感度阶梯系统 (升级/降级)
      - 时间衰减 (apply_time_decay)
      - 双接口风格修饰器
    """

    def __init__(
        self,
        config: dict | None = None,
        llm_gateway=None,
        use_llm: bool = True,
        blend_ratio: float = 0.4,
        classifier_timeout_ms: int = 500,
        classifier_mode: str = "hybrid",
    ):
        self._config = config or {}
        self._llm = llm_gateway
        self._classifier_mode = classifier_mode
        self._total_chats = 0

        self._classifier: LLMEmotionClassifier | None = None
        if use_llm and llm_gateway is not None:
            self._classifier = LLMEmotionClassifier(llm_gateway, classifier_timeout_ms)

        self._guard = ContinuityGuard(blend_ratio)
        self._state = CompoundEmotionalState()

        self._default_config = {
            "energy_drain_per_message": 0.02,
            "energy_recovery_per_hour": 0.05,
            "intensity_per_minute": 0.001,
            "per_positive_reply": 1.0,
            "per_negative_reply": -0.5,
            "per_day_decay": 0.1,
        }

        logger.info(
            "EmotionEngine initialized (mode=%s, llm=%s)",
            classifier_mode,
            llm_gateway is not None,
        )

    # ---- 属性 ----

    @property
    def state(self) -> CompoundEmotionalState:
        return self._state

    @property
    def total_chats(self) -> int:
        return self._total_chats

    # ---- 主入口: analyze (V2) ----

    def analyze(self, message: str, context: str = "") -> CompoundEmotionalState:
        new_state = self._classify(message, context)

        allowed, intermediate = self._guard.check_transition(
            self._state.primary_emotion, new_state.primary_emotion
        )
        if not allowed and intermediate:
            new_state.primary_emotion = intermediate

        new_state.primary_intensity = self._guard.blend_intensity(
            self._state.primary_intensity, new_state.primary_intensity
        )

        new_state.energy = self._calc_new_energy(new_state.primary_emotion)

        affection_delta = self._calc_affection_delta(message)
        new_state.affection_points = self._state.affection_points + affection_delta

        new_state.last_update = time.time()
        self._state = new_state

        self._check_affinity_upgrade()
        self._check_affinity_downgrade()

        self._total_chats += 1
        return self._state

    # ---- V1 兼容入口 ----

    def process_message(self, user_message: str, context: dict | None = None) -> CompoundEmotionalState:
        recent = ""
        if isinstance(context, dict):
            recent = context.get("recent", "")
        elif isinstance(context, str):
            recent = context
        return self.analyze(user_message, recent)

    # ---- 分类策略路由 ----

    def _classify(self, message: str, context: str) -> CompoundEmotionalState:
        if self._classifier_mode == "rule":
            return self._rule_classify(message)
        elif self._classifier_mode == "llm":
            return self._llm_classify(message, context)
        else:  # hybrid
            return self._hybrid_classify(message, context)

    def _rule_classify(self, message: str) -> CompoundEmotionalState:
        msg = message.lower()
        scores: dict[Emotion, float] = {e: 0.0 for e in Emotion}

        for keyword, (emotion, weight) in KEYWORD_EMOTION_MAP.items():
            if keyword in msg:
                scores[emotion] += weight

        # 撒娇需要好感度门槛
        if self._state.affinity < 5 and scores.get(Emotion.LOVELY, 0) > 0:
            happy_score = scores.pop(Emotion.LOVELY, 0)
            scores[Emotion.HAPPY] = scores.get(Emotion.HAPPY, 0) + happy_score * 0.6

        # 傲娇随机触发
        if any(kw in msg for kw in ["夸", "好看", "漂亮", "可爱", "乖"]) and random.random() < 0.4:
            scores[Emotion.SULLEN] = scores.get(Emotion.SULLEN, 0) + 0.5

        total = sum(scores.values())
        if total > 0:
            primary_emotion = max(scores, key=scores.get)
            primary_intensity = min(1.0, scores[primary_emotion])
        else:
            if self._state.energy > 0.7 and self._state.affinity >= 4:
                primary_emotion = Emotion.PLAYFUL
            elif self._state.energy < 0.3:
                primary_emotion = Emotion.TIRED
            else:
                primary_emotion = Emotion.NEUTRAL
            primary_intensity = 0.3

        secondary = []
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        for emotion, score in sorted_scores[1:4]:
            if score >= 0.3 and emotion != primary_emotion:
                secondary.append((emotion, min(1.0, score)))

        return CompoundEmotionalState(
            primary_emotion=primary_emotion,
            primary_intensity=primary_intensity,
            secondary_emotions=secondary,
            energy=self._state.energy,
            affinity=self._state.affinity,
            affection_points=self._state.affection_points,
        )

    def _llm_classify(self, message: str, context: str) -> CompoundEmotionalState:
        if self._classifier:
            result = self._classifier.classify(message, context)
            if result:
                return self._parse_llm_result(result)
        return self._rule_classify(message)

    def _hybrid_classify(self, message: str, context: str) -> CompoundEmotionalState:
        if self._classifier:
            result = self._classifier.classify(message, context)
            if result:
                return self._parse_llm_result(result)
        return self._rule_classify(message)

    def _parse_llm_result(self, result: dict) -> CompoundEmotionalState:
        primary = result.get("primary", {})
        emotion_name = primary.get("type", "平常")
        primary_emotion = Emotion.NEUTRAL
        for e in Emotion:
            if e.value == emotion_name:
                primary_emotion = e
                break
        primary_intensity = float(primary.get("intensity", 0.5))

        secondary = []
        for s in result.get("secondary", []):
            s_name = s.get("type", "")
            s_intensity = float(s.get("intensity", 0.0))
            for e in Emotion:
                if e.value == s_name and s_intensity >= 0.3:
                    secondary.append((e, s_intensity))
                    break

        energy_change = float(result.get("energy_change", 0.0))
        new_energy = max(0.0, min(1.0, self._state.energy + energy_change))

        return CompoundEmotionalState(
            primary_emotion=primary_emotion,
            primary_intensity=primary_intensity,
            secondary_emotions=secondary,
            energy=new_energy,
            affinity=self._state.affinity,
            affection_points=self._state.affection_points,
        )

    # ---- 能量计算 ----

    def _calc_new_energy(self, emotion: Emotion) -> float:
        drain = self._config.get(
            "energy_drain_per_message",
            self._default_config["energy_drain_per_message"],
        )
        pleasure = EMOTION_PLEASURE_MAP.get(emotion, 0.0)
        if pleasure > 0.5:
            drain -= 0.01
        elif pleasure < -0.5:
            drain += 0.01
        return max(0.0, min(1.0, self._state.energy - drain))

    # ---- 好感度计算 ----

    def _calc_affection_delta(self, message: str) -> float:
        msg = message.lower()
        per_pos = self._config.get(
            "per_positive_reply",
            self._default_config["per_positive_reply"],
        )
        per_neg = self._config.get(
            "per_negative_reply",
            self._default_config["per_negative_reply"],
        )

        positive_keywords = [
            "想", "喜欢", "爱", "好", "乖", "棒",
            "对不起", "错了", "哄", "宝贝", "亲爱的",
        ]
        negative_keywords = ["烦", "滚", "闭嘴", "懒得", "无语", "讨厌"]

        pos_count = sum(1 for kw in positive_keywords if kw in msg)
        neg_count = sum(1 for kw in negative_keywords if kw in msg)

        delta = 0.2
        if pos_count > 0:
            delta += pos_count * per_pos
        if neg_count > 0:
            delta += neg_count * per_neg
        return delta

    # ---- 好感度升级 / 降级（V1逻辑 + Optimized THRESHOLDS）----

    def _check_affinity_upgrade(self) -> None:
        current_level = self._state.affinity
        while current_level < len(AffinityLevel.THRESHOLDS) - 1:
            next_threshold = AffinityLevel.THRESHOLDS[current_level + 1]
            if self._state.affection_points >= next_threshold:
                current_level += 1
                logger.info(
                    "好感度升级! %s → %s",
                    AffinityLevel.get_name(current_level - 1),
                    AffinityLevel.get_name(current_level),
                )
            else:
                break
        self._state.affinity = current_level

    def _check_affinity_downgrade(self) -> None:
        current_level = self._state.affinity
        while current_level > 0:
            current_threshold = AffinityLevel.THRESHOLDS[current_level]
            if self._state.affection_points < current_threshold:
                current_level -= 1
                logger.info(
                    "好感度降级! %s → %s",
                    AffinityLevel.get_name(current_level + 1),
                    AffinityLevel.get_name(current_level),
                )
            else:
                break
        self._state.affinity = current_level

    # ---- 时间衰减（V1移植）----

    def apply_time_decay(self, hours_passed: float) -> None:
        recovery = self._config.get(
            "energy_recovery_per_hour",
            self._default_config["energy_recovery_per_hour"],
        ) * hours_passed
        self._state.energy = min(1.0, self._state.energy + recovery)

        decay = self._config.get(
            "intensity_per_minute",
            self._default_config["intensity_per_minute"],
        ) * hours_passed * 60
        self._state.primary_intensity = max(0.1, self._state.primary_intensity - decay)

        days_passed = hours_passed / 24
        if days_passed >= 1:
            decay_affection = self._config.get(
                "per_day_decay",
                self._default_config["per_day_decay"],
            ) * days_passed
            self._state.affection_points = max(0, self._state.affection_points - decay_affection)
            self._check_affinity_upgrade()
            self._check_affinity_downgrade()

        self._state.last_update = time.time()

    # ---- 风格修饰器双接口 ----

    def get_style_modifiers(self) -> dict[str, Any]:
        """V1风格: warmth/energy/intimacy/playfulness修饰"""
        pleasure = EMOTION_PLEASURE_MAP.get(self._state.primary_emotion, 0.0)
        return {
            "warmth_mod": max(-0.3, min(0.3, pleasure * 0.3)),
            "energy_mod": self._state.energy - 0.5,
            "intimacy_mod": self._state.affinity / 8.0 - 0.5,
            "playfulness_mod": max(
                -0.3, min(0.3, pleasure * 0.2 + (self._state.energy - 0.5) * 0.3)
            ),
            "needs_comfort": self._state.primary_emotion in (Emotion.SAD, Emotion.TIRED),
            "is_flirty": (
                self._state.primary_emotion in (Emotion.LOVELY, Emotion.PLAYFUL)
                and self._state.affinity >= 5
            ),
        }

    def get_emotion_style_map(self) -> dict[str, float]:
        """V2风格: 基于EMOTION_STYLE_MAP的修辞概率"""
        return EMOTION_STYLE_MAP.get(
            self._state.primary_emotion,
            EMOTION_STYLE_MAP[Emotion.NEUTRAL],
        )

    # ---- 辅助 ----

    def get_affinity_level_name(self) -> str:
        return AffinityLevel.get_name(self._state.affinity)

    def reset(self) -> None:
        self._state = CompoundEmotionalState()
        self._total_chats = 0
        if self._classifier:
            self._classifier.clear_cache()
        logger.info("EmotionEngine reset")

    def close(self) -> None:
        """关闭底层资源（如 LLM 分类器的线程池）。"""
        if self._classifier:
            self._classifier.close()
        logger.info("EmotionEngine closed")

    def health_check(self) -> dict[str, Any]:
        return {
            "initialized": True,
            "classifier_mode": self._classifier_mode,
            "llm_available": self._llm is not None,
            "current_emotion": self._state.primary_emotion.value,
            "intensity": round(self._state.primary_intensity, 2),
            "energy": round(self._state.energy, 2),
            "affinity_level": self._state.affinity,
            "affinity_name": AffinityLevel.get_name(self._state.affinity),
            "total_chats": self._total_chats,
        }

    def __repr__(self) -> str:
        return (
            f"EmotionEngine({self._state.primary_emotion.value}, "
            f"energy={self._state.energy:.2f}, "
            f"affinity={self.get_affinity_level_name()}({self._state.affinity}), "
            f"intensity={self._state.primary_intensity:.2f}, "
            f"mode={self._classifier_mode})"
        )
