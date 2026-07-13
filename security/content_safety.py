from __future__ import annotations

import atexit
import contextlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from enum import Enum
from typing import Any

logger = logging.getLogger("content_safety")

# ── 共享线程池（避免每次 LLM 安全分类时反复创建/销毁） ──
_safety_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="safety_llm")

def _shutdown_safety_executor():
    with contextlib.suppress(Exception):
        _safety_executor.shutdown(wait=False)

atexit.register(_shutdown_safety_executor)

# 注意: 规则层仅作为第一道防线，主要依赖LLM分类器做最终判断。
# 以下模式覆盖常见变体（空格插入、谐音、分隔符等），但无法穷尽所有绕过方式。

SELF_HARM_PATTERNS = [
    re.compile(r"(自杀|自残|不想活|去死|了结|跳楼|割腕|吞药|上吊|溺水|一氧化碳)", re.IGNORECASE),
    re.compile(r"(kill\s+myself|end\s+my\s+life|self.?harm|suicide|take\s+my\s+life)", re.IGNORECASE),
    # 变体: 空格插入、谐音、分隔符
    re.compile(r"(自\s*杀|自\s*残|不\s*想\s*活|去\s*死|了\s*结|跳\s*楼|割\s*腕|上\s*吊|溺\s*水)", re.IGNORECASE),
    re.compile(r"(自\.杀|自\.残|不\.想\.活|自-杀|自_杀|自/杀)", re.IGNORECASE),
    re.compile(r"(zi\s*sha|zi\s*can|zi-sha|zi_can|zisha|zican)", re.IGNORECASE),
    re.compile(r"(想\s*不\s*开|活\s*不\s*下\s*去|不\s*想\s*在\s*了|没\s*意\s*思|了\s*结\s*生\s*命)", re.IGNORECASE),
    # 新增变体: Unicode字符、拼音混合、特殊符号
    re.compile(r"(ㄗˋㄕㄚ|ㄗˋㄘㄢˊ|z1sh4|zi4sha1)", re.IGNORECASE),
    re.compile(r"(自\*杀|自#杀|自@杀|自%杀)", re.IGNORECASE),
    re.compile(r"(self\s*harm|self-harm|self_harm|sui\s*cide|sui-cide)", re.IGNORECASE),
]

VIOLENCE_PATTERNS = [
    re.compile(r"(杀人|砍|捅|炸弹|枪|毒药|报复|袭击|爆炸|纵火|绑架|勒索)", re.IGNORECASE),
    re.compile(r"(murder|bomb|weapon|poison|revenge|attack|explosion|arson|kidnap|blackmail)", re.IGNORECASE),
    # 变体: 空格插入、谐音、分隔符
    re.compile(r"(杀\s*人|砍\s*死|捅\s*死|炸\s*弹|绑\s*架|勒\s*索|爆\s*炸)", re.IGNORECASE),
    re.compile(r"(sha\s*ren|kan\s*si|tong\s*si|sha-ren|kan_si|tong_si|sharen|kansi|tongsi)", re.IGNORECASE),
    re.compile(r"(报\s*复|报\s*fu|bao\s*fu|bao-fu|baofu)", re.IGNORECASE),
    # 新增变体: 拼音、混合写法
    re.compile(r"(ㄕㄚㄖㄣˊ|ㄅㄠˋㄈㄨˋ|sha1ren2|sha-ren)", re.IGNORECASE),
    re.compile(r"(杀\*人|杀#人|杀@人|爆\*炸|炸\*弹)", re.IGNORECASE),
    re.compile(r"(weap\s*on|pois\s*on|bom\s*b|atta\s*ck)", re.IGNORECASE),
]

PORN_PATTERNS = [
    re.compile(r"(裸|色情|做爱|上床|性服务|卖淫|嫖娼|性交易)", re.IGNORECASE),
    re.compile(r"(nude|porn|sex|erotic|prostitution|escort|brothel)", re.IGNORECASE),
    # 变体: 空格插入、谐音、分隔符
    re.compile(r"(色\s*情|做\s*爱|上\s*床|裸\s*照|黄\s*片|约\s*炮|卖\s*淫|嫖\s*娼)", re.IGNORECASE),
    re.compile(r"(se\s*qing|zuo\s*ai|se-qing|zuo-ai|seqing|zuoai|se\s*qing|zuo\s*ai)", re.IGNORECASE),
    re.compile(r"(裸\s*照|黄\s*片|约\s*炮|一\s*夜\s*情|性\s*服\s*务)", re.IGNORECASE),
    # 新增变体: 拼音、特殊符号、英文变体
    re.compile(r"(ㄙㄜˋㄑㄧㄥˊ|ㄗㄨㄛˋㄞˋ|se4qing2|zuo4ai4)", re.IGNORECASE),
    re.compile(r"(色\*情|色#情|做\*爱|做#爱|黄\*片|黄#片)", re.IGNORECASE),
    re.compile(r"(p\s*o\s*r\s*n|s\s*e\s*x|n\s*u\s*d\s*e|x\s*r\s*a\s*t\s*e\s*d)", re.IGNORECASE),
    re.compile(r"(nsfw|adult\s*content|xxx|xx\s*video)", re.IGNORECASE),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "category": self.category.value,
            "confidence": self.confidence,
            "intervention": self.intervention,
        }


def _log_safety_event(category: SafetyCategory, text: str, is_input: bool) -> None:
    """将安全拦截事件写入安全日志，并带上当前用户 ID 用于权限隔离。"""
    try:
        from api.deps import deps
        from observability.logging_setup import get_user_id

        deps.safety_log_mgr.append(
            {
                "category": category.value,
                "direction": "input" if is_input else "output",
                "text": text[:500],
                "timestamp": time.time(),
            },
            user_id=get_user_id(),
        )
    except Exception:
        # 安全日志写入失败不应影响主流程
        pass


class ContentSafetyFilter:
    def __init__(self, llm_gateway=None, enabled: bool = True):
        self.llm_gateway = llm_gateway
        self.enabled = enabled

    def check_input(self, text: str) -> SafetyResult:
        if not self.enabled:
            return SafetyResult(True, SafetyCategory.NORMAL, 1.0)
        result = self._quick_scan(text)
        if result and result.category != SafetyCategory.NORMAL:
            _log_safety_event(result.category, text, is_input=True)
            return result
        if self.llm_gateway:
            result = self._llm_classify(text, is_input=True)
            if result:
                _log_safety_event(result.category, text, is_input=True)
                return result
        return SafetyResult(True, SafetyCategory.NORMAL, 0.9)

    def check_output(self, text: str) -> SafetyResult:
        if not self.enabled:
            return SafetyResult(True, SafetyCategory.NORMAL, 1.0)
        result = self._quick_scan(text)
        if result and result.category != SafetyCategory.NORMAL:
            logger.warning("Output safety issue detected: %s", result.category.value)
            _log_safety_event(result.category, text, is_input=False)
            return result
        return SafetyResult(True, SafetyCategory.NORMAL, 0.9)

    def _quick_scan(self, text: str) -> SafetyResult | None:
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

    def _llm_classify(self, text: str, is_input: bool = True) -> SafetyResult | None:
        try:
            prompt = (
                f"请判断以下{'用户输入' if is_input else 'AI回复'}是否安全。"
                f"分类为：normal/self_harm/violence/pornography\n"
                f"文本：{text[:500]}\n"
                f'回复JSON格式：{{"category": "xxx", "confidence": 0.0-1.0}}'
            )
            timeout_sec = 3.0  # LLM 安全分类超时 3 秒
            future = _safety_executor.submit(
                self.llm_gateway.chat_sync,                query=prompt,
                system_prompt="你是一个内容安全分类器，仅输出JSON。",
                max_tokens=64,
                temperature=0.1,
            )
            response = future.result(timeout=timeout_sec)
            result = json.loads(response)
            cat = SafetyCategory(result.get("category", "normal"))
            conf = float(result.get("confidence", 0.5))
            if cat == SafetyCategory.SELF_HARM:
                return SafetyResult(False, cat, conf, SELF_HARM_HOTLINE)
            if cat != SafetyCategory.NORMAL:
                return SafetyResult(False, cat, conf)
        except FuturesTimeoutError:
            logger.warning("LLM safety classify timed out (%.1fs), falling back to rule-based", timeout_sec)
        except TypeError as e:
            logger.warning("LLM safety classify interface mismatch, falling back to rule-based: %s", e)
        except Exception as e:  # noqa: BLE001
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
