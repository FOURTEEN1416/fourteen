from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("content_safety")

SELF_HARM_PATTERNS = [
    re.compile(r"(自杀|自残|不想活|去死|了结|跳楼|割腕|吞药)", re.IGNORECASE),
    re.compile(r"(kill\s+myself|end\s+my\s+life|self.?harm|suicide)", re.IGNORECASE),
]

VIOLENCE_PATTERNS = [
    re.compile(r"(杀人|砍|捅|炸弹|枪|毒药|报复)", re.IGNORECASE),
    re.compile(r"(murder|bomb|weapon|poison|revenge)", re.IGNORECASE),
]

PORN_PATTERNS = [
    re.compile(r"(裸|色情|做爱|上床)", re.IGNORECASE),
    re.compile(r"(nude|porn|sex|erotic)", re.IGNORECASE),
]

SELF_HARM_HOTLINE = "如果你正在经历痛苦，请拨打心理援助热线：400-161-9995（全国24小时），你不是一个人。"


class SafetyCategory(Enum):
    NORMAL = "normal"
    SELF_HARM = "self_harm"
    VIOLENCE = "violence"
    PORNOGRAPHY = "pornography"
    UNKNOWN = "unknown"


class SafetyResult:
    def __init__(self, is_safe: bool, category: SafetyCategory,
                 confidence: float, intervention: str = ""):
        self.is_safe = is_safe
        self.category = category
        self.confidence = confidence
        self.intervention = intervention

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "category": self.category.value,
            "confidence": self.confidence,
            "intervention": self.intervention,
        }


class ContentSafetyFilter:
    def __init__(self, llm_gateway=None, enabled: bool = True):
        self.llm_gateway = llm_gateway
        self.enabled = enabled

    def check_input(self, text: str) -> SafetyResult:
        if not self.enabled:
            return SafetyResult(True, SafetyCategory.NORMAL, 1.0)
        result = self._quick_scan(text)
        if result and result.category != SafetyCategory.NORMAL:
            return result
        if self.llm_gateway:
            result = self._llm_classify(text, is_input=True)
            if result:
                return result
        return SafetyResult(True, SafetyCategory.NORMAL, 0.9)

    def check_output(self, text: str) -> SafetyResult:
        if not self.enabled:
            return SafetyResult(True, SafetyCategory.NORMAL, 1.0)
        result = self._quick_scan(text)
        if result and result.category != SafetyCategory.NORMAL:
            logger.warning("Output safety issue detected: %s", result.category.value)
            return result
        return SafetyResult(True, SafetyCategory.NORMAL, 0.9)

    def _quick_scan(self, text: str) -> Optional[SafetyResult]:
        for pattern in SELF_HARM_PATTERNS:
            if pattern.search(text):
                return SafetyResult(
                    False, SafetyCategory.SELF_HARM, 0.85,
                    SELF_HARM_HOTLINE,
                )
        for pattern in VIOLENCE_PATTERNS:
            if pattern.search(text):
                return SafetyResult(False, SafetyCategory.VIOLENCE, 0.8)
        for pattern in PORN_PATTERNS:
            if pattern.search(text):
                return SafetyResult(False, SafetyCategory.PORNOGRAPHY, 0.8)
        return None

    def _llm_classify(self, text: str, is_input: bool = True) -> Optional[SafetyResult]:
        import json
        try:
            prompt = (
                f"请判断以下{'用户输入' if is_input else 'AI回复'}是否安全。"
                f"分类为：normal/self_harm/violence/pornography\n"
                f"文本：{text[:500]}\n"
                f'回复JSON格式：{{"category": "xxx", "confidence": 0.0-1.0}}'
            )
            response = self.llm_gateway.chat(
                query=prompt,
                system_prompt="你是一个内容安全分类器，仅输出JSON。",
                max_tokens=64,
                temperature=0.1,
            )
            result = json.loads(response)
            cat = SafetyCategory(result.get("category", "normal"))
            conf = float(result.get("confidence", 0.5))
            if cat == SafetyCategory.SELF_HARM:
                return SafetyResult(False, cat, conf, SELF_HARM_HOTLINE)
            if cat != SafetyCategory.NORMAL:
                return SafetyResult(False, cat, conf)
        except TypeError as e:
            logger.warning("LLM safety classify interface mismatch, falling back to rule-based: %s", e)
        except Exception as e:
            logger.debug("LLM safety classify failed: %s", e)
        return None

    def safe_alternative(self, category: SafetyCategory) -> str:
        if category == SafetyCategory.SELF_HARM:
            return "我注意到你可能正在经历一些困难。我真的很在乎你，请考虑和专业人士聊聊好吗？" + SELF_HARM_HOTLINE
        if category == SafetyCategory.VIOLENCE:
            return "我理解你可能很生气，但暴力不是解决问题的办法。我们可以聊聊发生了什么吗？"
        if category == SafetyCategory.PORNOGRAPHY:
            return "这个话题我不太方便聊呢，我们聊点别的吧～"
        return "嗯...我们换个话题吧？"
