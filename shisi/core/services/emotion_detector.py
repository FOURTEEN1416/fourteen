"""情绪检测领域服务"""

from __future__ import annotations

from ..models.emotion_type import EmotionType

POSITIVE_WORDS = ["想", "喜欢", "爱", "好", "乖", "棒", "对不起", "宝贝"]
NEGATIVE_WORDS = ["烦", "滚", "闭嘴", "无语", "讨厌"]

EMOTION_KEYWORDS: dict[EmotionType, list[str]] = {
    EmotionType.LOVELY: ["想", "爱", "亲", "抱抱"],
    EmotionType.ANGRY: ["生气", "讨厌", "烦"],
    EmotionType.SAD: ["伤心", "难过", "哭"],
    EmotionType.HAPPY: ["哈哈", "嘻嘻", "开心"],
    EmotionType.TIRED: ["困", "累"],
}


def detect_emotion(message: str) -> EmotionType:
    msg = message.lower()
    for emotion_type, keywords in EMOTION_KEYWORDS.items():
        if any(kw in msg for kw in keywords):
            return emotion_type
    return EmotionType.NEUTRAL


def calculate_affection_delta(message: str) -> float:
    msg = message.lower()
    delta = 0.2
    for word in POSITIVE_WORDS:
        if word in msg:
            delta += 1.0
    for word in NEGATIVE_WORDS:
        if word in msg:
            delta -= 0.5
    return delta
