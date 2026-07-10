"""语音触发检测模块 — 从用户消息中检测是否要求语音回复"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("voice_detector")

# 直接命令词（高置信度）
_VOICE_COMMANDS = [
    "发语音", "语音回复", "发段语音", "发个语音",
    "语音消息", "语音说", "用语音", "用说的",
    "声音回复", "语音告诉我",
]

# 欲望/请求模式
_VOICE_DESIRE_PATTERNS = [
    r"想听你说[句话]?",
    r"用(你的?)?声音",
    r"听(到|见)你的声音",
    r"能不能?发(个|段)?语音",
    r"可以发语音",
    r"说话.*听听?",
    r"(给|帮)我(说|讲|读)",
]

# 情感修饰 + 说
_VOICE_EMOTION_SPEAK = [
    r"(温柔|轻声|小声|大声|悄悄|慢慢|好好)地说",
    r"(温柔|轻声|小声|大声|悄悄|慢慢|好好)说",
]

# 能力询问
_VOICE_CAPABILITY = [
    r"(能|会|可以)说话[吗么]?",
    r"(能|会)发声[吗么]?",
    r"有语音功能",
]

# 上下文触发
_VOICE_CONTEXT = [
    r"说句话",
    r"出[个声]?声",
    r"发声",
    r"说话",
    r"语音",
]

# ── 预编译正则（性能优化） ──
_RE_NEGATION = re.compile(r"(不要|别|不想|不用|懒得|算了).{0,5}(语音|说话|声音|发声)")
_RE_TEXT_PREFER = re.compile(r"文字(就|才|更)好|打字")
_RE_DESIRE = [re.compile(p) for p in _VOICE_DESIRE_PATTERNS]
_RE_EMOTION_SPEAK = [re.compile(p) for p in _VOICE_EMOTION_SPEAK]
_RE_CAPABILITY = [re.compile(p) for p in _VOICE_CAPABILITY]
_RE_CONTEXT = [re.compile(p) for p in _VOICE_CONTEXT]


def detect_voice_request(text: str) -> bool:
    """检测用户消息是否要求语音回复"""
    if not text:
        return False
    text = text.strip()

    # 排除否定
    if _RE_NEGATION.search(text):
        return False
    if _RE_TEXT_PREFER.search(text):
        return False

    # Tier 1: 直接命令词
    for cmd in _VOICE_COMMANDS:
        if cmd in text:
            return True

    # Tier 2: 欲望/请求模式
    for pat in _RE_DESIRE:
        if pat.search(text):
            return True

    # Tier 3: 情感修饰 + 说
    for pat in _RE_EMOTION_SPEAK:
        if pat.search(text):
            return True

    # Tier 4: 能力询问
    for pat in _RE_CAPABILITY:
        if pat.search(text):
            return True

    # Tier 5: 上下文触发（排除误触发）
    for pat in _RE_CONTEXT:
        m = pat.search(text)
        if m:
            surrounding = text[max(0, m.start() - 2):m.end() + 2]
            if any(x in surrounding for x in ["识别", "输入", "转文字", "普通", "导航", "搜索"]):
                continue
            if "说句话" in text and ("听听" in text or "吗" in text or "吧" in text):
                return True
            if pat == "说话" and len(text) < 8:
                return True
            if pat == "语音" and len(text) < 10 and "吗" in text:
                return True
            return True

    return False
