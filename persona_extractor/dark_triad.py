"""
暗黑三人格检测 (Dark Triad)

参考文献:
  - Paulhus & Williams (2002). The Dark Triad of personality.
  - Jones & Paulhus (2014). Introducing the Short Dark Triad (SD3).
  - Muris et al. (2017). The malevolent side of human nature.

三维度:
  1. Narcissism (自恋) - 自我中心、优越感、需要崇拜
  2. Machiavellianism (马基雅维利主义) - 操纵性、愤世嫉俗、功利主义
  3. Psychopathy (精神病态) - 冷漠、冲动、缺乏共情

⚠️ 注意: 暗黑三人格是亚临床特质，不等于精神疾病诊断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 自恋标志性语言模式
_NARCISSISM_PATTERNS = [
    (re.compile(r"(我(最|很|非常|特别|超级).{0,5}(厉害|优秀|牛|强|棒|聪明|好看))"), 0.3),
    (re.compile(r"(别人都.{0,5}(不如|比不上|没有).{0,3}我)"), 0.35),
    (re.compile(r"(你.{0,3}(知道|应该知道).{0,3}我.{0,5}(是谁|什么身份))"), 0.4),
    (re.compile(r"(没人.{0,3}(懂|理解|配得上).{0,3}我)"), 0.3),
    (re.compile(r"(我.{0,3}从来.{0,3}(没错|都是对的))"), 0.35),
    (re.compile(r"(我就是.{0,3}(这样|这种|天生))"), 0.2),
    (re.compile(r"(他们都.{0,5}(嫉妒|羡慕|针对).{0,3}我)"), 0.3),
    (re.compile(r"(你不.{0,3}(知道|懂).{0,3}我.{0,5}(有多|付出了))"), 0.25),
]

# 马基雅维利主义标志性语言模式
_MACHIAVELLIAN_PATTERNS = [
    (re.compile(r"(利用|操控|摆布|左右).{0,5}(别人|对方|他们)"), 0.35),
    (re.compile(r"(目的.{0,3}(达到|达成).{0,3}(就行|就好|够了))"), 0.3),
    (re.compile(r"(人与人.{0,3}(就是|不过).{0,3}(利益|交易|利用))"), 0.4),
    (re.compile(r"(谁.{0,3}(有用|没用).{0,3}(就|才).{0,3}(怎样|怎么))"), 0.3),
    (re.compile(r"(别.{0,3}(太|那么).{0,3}(善良|天真|诚实|老实))"), 0.35),
    (re.compile(r"(这个社会.{0,3}(就是|本来).{0,3}(这样|弱肉强食))"), 0.3),
    (re.compile(r"(只要.{0,3}(结果|目的).{0,3}(达到|好).{0,3}(过程|手段).{0,3}(不重要|无所谓))"), 0.4),
    (re.compile(r"(我.{0,3}(说|做).{0,3}(话|事).{0,3}(都是有.{0,3}(目的|原因)))"), 0.25),
]

# 精神病态标志性语言模式
_PSYCHOPATHY_PATTERNS = [
    (re.compile(r"(无聊|没意思|刺激|好玩).{0,5}(找.{0,3}(刺激|乐子|新鲜))"), 0.25),
    (re.compile(r"(管他.{0,3}(呢|怎么样|死活))"), 0.35),
    (re.compile(r"(无所谓|不在乎|不在意).{0,5}(别人.{0,3}(感受|想法|怎么样))"), 0.3),
    (re.compile(r"(我.{0,3}(不在乎|无所谓|不关心).{0,3}(后果|结果))"), 0.35),
    (re.compile(r"(没.{0,3}(感觉|感情|触动))"), 0.2),
    (re.compile(r"(骗.{0,3}(他|她|你|人).{0,3}(又.{0,3}(怎样|如何|没事)))"), 0.4),
    (re.compile(r"(冲动.{0,3}(就.{0,3}(做了|干了|说了)))"), 0.3),
]


@dataclass
class DarkTriadTraits:
    """暗黑三人格特质 (0.0~1.0)

    参考 SD3 量表:
      - 0.0-0.3: 正常范围
      - 0.3-0.5: 偏高 (亚临床)
      - 0.5-0.7: 显著
      - 0.7+: 极高
    """
    narcissism: float = 0.0
    machiavellianism: float = 0.0
    psychopathy: float = 0.0
    overall_level: str = "normal"  # normal / elevated / significant
    matched_patterns: list[str] | None = None

    def __post_init__(self):
        if self.matched_patterns is None:
            self.matched_patterns = []

    def to_dict(self) -> dict:
        return {
            "narcissism": round(self.narcissism, 3),
            "machiavellianism": round(self.machiavellianism, 3),
            "psychopathy": round(self.psychopathy, 3),
            "overall_level": self.overall_level,
            "matched": self.matched_patterns[:10],  # type: ignore[index]
        }


class DarkTriadDetector:
    """暗黑三人格检测器

    基于语言模式匹配 + 可选的 LLM 深度分析。
    参考文献:
      - Holtzman (2011). 暗黑三人格的语言标记
      - Preotiuc-Pietro et al. (2016). 用社交媒体文本预测暗黑人格
    """

    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway

    def detect(self, text: str) -> DarkTriadTraits:
        """检测文本中的暗黑三人格信号"""
        text_lower = text.lower()
        traits = DarkTriadTraits()
        patterns_found = []

        # 自恋检测
        narc_score = 0.0
        for pattern, weight in _NARCISSISM_PATTERNS:
            if pattern.search(text_lower):
                narc_score += weight
                patterns_found.append(f"narcissism:{pattern.pattern[:30]}")
        traits.narcissism = min(1.0, narc_score)

        # 马基雅维利主义检测
        mach_score = 0.0
        for pattern, weight in _MACHIAVELLIAN_PATTERNS:
            if pattern.search(text_lower):
                mach_score += weight
                patterns_found.append(f"machiavellianism:{pattern.pattern[:30]}")
        traits.machiavellianism = min(1.0, mach_score)

        # 精神病态检测
        psych_score = 0.0
        for pattern, weight in _PSYCHOPATHY_PATTERNS:
            if pattern.search(text_lower):
                psych_score += weight
                patterns_found.append(f"psychopathy:{pattern.pattern[:30]}")
        traits.psychopathy = min(1.0, psych_score)

        traits.matched_patterns = patterns_found

        avg = (traits.narcissism + traits.machiavellianism + traits.psychopathy) / 3
        if avg > 0.5:
            traits.overall_level = "significant"
        elif avg > 0.25:
            traits.overall_level = "elevated"
        else:
            traits.overall_level = "normal"

        return traits

    def to_prompt_enhancement(self, traits: DarkTriadTraits) -> str:
        """生成给系统提示词的暗黑人格适配建议"""
        if traits.overall_level == "normal":
            return ""

        lines = ["[暗黑人格提示]"]
        if traits.narcissism > 0.3:
            lines.append("- 用户可能较自我中心，适当给予肯定但避免过度迎合")
        if traits.machiavellianism > 0.3:
            lines.append("- 用户可能有功利倾向，注意保持真诚但不被利用")
        if traits.psychopathy > 0.3:
            lines.append("- 用户可能缺乏共情，需要温和引导而非说教")
        return "\n".join(lines)
