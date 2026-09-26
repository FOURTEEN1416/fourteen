"""
情绪信号筛查模块 — 关键词命中的相对信号，不是临床量表

口径（与实现严格一致）:
  - 单条文本按 ``_DEPRESSION_LEXICON`` / ``_ANXIETY_LEXICON`` 逐维度做子串命中，
    维度分 = min(1.0, 命中词数 / 4)，``total_score`` = 各维度均值（0-1）。
  - ``level`` 是按「最强维度命中词数」与「命中维度数」取大的证据档位
    （1-2=mild, 3-5=moderate, ≥6=severe），用于同一用户前后的趋势比较。
  - 维度划分参考抑郁/焦虑常见症状群的**概念**，但本模块 **未实现也未验证**
    PHQ-9、GAD-7 等任何量表：题项、计分方式、时间窗（过去两周）、
    临界值与中文常模全部不一致，输出不得当作量表得分或筛查结论。

⚠️ 免责声明: 此模块仅为辅助分析工具，不能替代专业心理诊断。
   高风险信号会自动提示用户寻求专业帮助；对话热路径的自伤拦截与热线文本
   由 ``security/content_safety.py`` 承载，与本模块的统计信号相互独立。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════
# 抑郁维度词汇库
# ═══════════════════════════════════════════════════════════

# 维度名沿用 SIGECAPS 助记符（睡眠/兴趣/自责/精力/注意/食欲/精神运动/自伤），
# 只借其症状群划分概念，不按任何量表题项或临界值计分。
_DEPRESSION_ITEMS_TOTAL = 8
_ANXIETY_ITEMS_TOTAL = 7
# 维度分 = min(1.0, 命中词数 / HITS_FOR_FULL_SIGNAL)
HITS_FOR_FULL_SIGNAL = 4
# 证据档位：max(最强维度命中词数, 命中维度数)
_BAND_THRESHOLDS = ((1, "mild"), (3, "moderate"), (6, "severe"))
_DEPRESSION_LEXICON: dict[str, list[str]] = {
    "sleep": [
        "失眠", "睡不着", "早醒", "睡太多", "嗜睡", "熬夜", "通宵",
        "insomnia", "can't sleep", "wake up early", "oversleep",
    ],
    "interest": [
        "没兴趣", "不想", "无所谓", "没意思", "无聊", "没劲", "提不起劲",
        "没意义", "失去兴趣", "什么都不想做",
        "no interest", "boring", "meaningless", "don't care", "don't want to",
    ],
    "guilt": [
        "是我的错", "对不起", "我不好", "我不配", "怪我", "自责", "内疚",
        "拖累", "废物", "没用", "一无是处",
        "my fault", "worthless", "useless", "guilty", "I'm bad",
    ],
    "energy": [
        "累", "疲惫", "没力气", "筋疲力尽", "乏力", "没精神", "困",
        "tired", "exhausted", "no energy", "fatigue",
    ],
    "concentration": [
        "记不住", "想不起来", "走神", "注意力", "集中不了", "脑子空白",
        "can't focus", "forgetful", "brain fog", "can't concentrate",
    ],
    "appetite": [
        "吃不下", "没胃口", "暴食", "不想吃", "食欲", "体重",
        "no appetite", "overeating", "weight loss", "weight gain",
    ],
    "psychomotor": [
        "动作慢", "坐立不安", "静不下来", "焦躁",
        "restless", "agitated", "slowed down", "can't sit still",
    ],
    "suicidal": [
        "想死", "不想活", "自杀", "了结", "一了百了", "消失",
        "没有我更好", "离开这个世界", "结束一切",
        "kill myself", "suicide", "end my life", "better off dead",
        "want to die", "don't want to live", "end it all",
    ],
}

# 焦虑维度词汇库（维度名沿用 GAD-7 条目概念，仅借概念不按量表计分）
_ANXIETY_LEXICON: dict[str, list[str]] = {
    "nervousness": [
        "紧张", "焦虑", "不安", "心慌", "忐忑", "害怕", "恐惧",
        "anxious", "nervous", "scared", "fearful", "worried",
    ],
    "uncontrollable_worry": [
        "控制不住", "一直想", "停不下来", "反复想", "纠结",
        "can't control", "can't stop thinking", "overthinking",
    ],
    "worry_too_much": [
        "担心太多", "过度担心", "想太多", "胡思乱想",
        "worry too much", "overthink",
    ],
    "trouble_relaxing": [
        "放松不了", "紧绷", "不能放松", "总是绷着",
        "can't relax", "tense", "on edge",
    ],
    "restlessness": [
        "坐不住", "静不下来", "坐立不安", "心神不宁",
        "restless", "can't sit still",
    ],
    "irritability": [
        "烦躁", "易怒", "发脾气", "暴躁", "不耐烦",
        "irritable", "annoyed", "angry easily",
    ],
    "fear_awful": [
        "可怕的事情", "灾难", "不好的预感", "总觉得要出事",
        "something awful", "catastrophe", "bad feeling",
    ],
}

# 创伤相关词汇
_TRAUMA_LEXICON: dict[str, list[str]] = {
    "intrusion": [
        "闪回", "噩梦", "总是想起", "画面挥之不去",
        "flashback", "nightmare", "can't stop thinking about it",
    ],
    "avoidance": [
        "不想提", "逃避", "回避", "不敢去", "躲着",
        "avoid", "don't want to talk about", "stay away",
    ],
    "hyperarousal": [
        "一惊一乍", "高度警觉", "容易被吓到", "敏感",
        "hypervigilant", "easily startled", "on guard",
    ],
}

# ═══════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════


def _signal_meta(items_hit: int, items_total: int, evidence: int) -> dict:
    """自报量纲与方法：读数者不必猜这个 0-1 是从哪来的。"""
    return {
        "scale": "0-1",
        "max_score": 1.0,
        "method": "keyword_hit",
        "items_hit": items_hit,
        "items_total": items_total,
        "evidence": evidence,
        "formula": (
            f"维度分=min(1, 命中词数/{HITS_FOR_FULL_SIGNAL}); "
            "total_score=各维度均值; "
            "evidence=max(最强维度命中词数, 命中维度数); level 按 evidence 分档"
        ),
    }


@dataclass
class DepressionIndicators:
    """抑郁维度信号（关键词命中，0-1；非量表得分）"""
    sleep: float = 0.0
    interest: float = 0.0
    guilt: float = 0.0
    energy: float = 0.0
    concentration: float = 0.0
    appetite: float = 0.0
    psychomotor: float = 0.0
    suicidal: float = 0.0
    total_score: float = 0.0
    peak_score: float = 0.0
    evidence: int = 0
    items_hit: int = 0
    level: str = "none"  # none / mild / moderate / severe / critical（信号档，非临床严重度）
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            name: round(getattr(self, name), 3)
            for name in ("sleep", "interest", "guilt", "energy",
                         "concentration", "appetite", "psychomotor", "suicidal",
                         "total_score", "peak_score")
        }
        d["level"] = self.level
        d["matched"] = self.matched_keywords[:20]
        d.update(_signal_meta(self.items_hit, _DEPRESSION_ITEMS_TOTAL, self.evidence))
        return d


@dataclass
class AnxietyIndicators:
    """焦虑维度信号（关键词命中，0-1；非量表得分）"""
    nervousness: float = 0.0
    uncontrollable_worry: float = 0.0
    worry_too_much: float = 0.0
    trouble_relaxing: float = 0.0
    restlessness: float = 0.0
    irritability: float = 0.0
    fear_awful: float = 0.0
    total_score: float = 0.0
    peak_score: float = 0.0
    evidence: int = 0
    items_hit: int = 0
    level: str = "none"
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            name: round(getattr(self, name), 3)
            for name in ("nervousness", "uncontrollable_worry", "worry_too_much",
                         "trouble_relaxing", "restlessness", "irritability",
                         "fear_awful", "total_score", "peak_score")
        }
        d["level"] = self.level
        d["matched"] = self.matched_keywords[:20]
        d.update(_signal_meta(self.items_hit, _ANXIETY_ITEMS_TOTAL, self.evidence))
        return d


@dataclass
class MentalHealthSnapshot:
    """单次情绪信号快照（关键词命中的相对信号）"""
    timestamp: str = ""
    depression: DepressionIndicators = field(default_factory=DepressionIndicators)
    anxiety: AnxietyIndicators = field(default_factory=AnxietyIndicators)
    trauma_signals: float = 0.0
    self_harm_risk: float = 0.0
    overall_risk: str = "low"  # low / moderate / high / critical
    method: str = "keyword_hit"  # keyword_hit / keyword_hit+llm_assessment

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "depression": self.depression.to_dict(),
            "anxiety": self.anxiety.to_dict(),
            "trauma_signals": round(self.trauma_signals, 3),
            "self_harm_risk": round(self.self_harm_risk, 3),
            "overall_risk": self.overall_risk,
            "method": self.method,
            "caveat": (
                "关键词命中的情绪信号强度分档，不是临床量表得分，"
                "不能作为诊断或筛查结论使用"
            ),
        }


# ═══════════════════════════════════════════════════════════
# 筛查引擎
# ═══════════════════════════════════════════════════════════

def _band_level(evidence: int) -> str:
    """按证据档位（max(最强维度命中词数, 命中维度数)）分档。

    旧实现按「全维度均值」定档：单条文本只命中一个维度时总分别超过 1/n，
    明显的焦虑表达也被判成 none —— 均值衡量的是词典覆盖广度，不是信号强度。
    """
    level = "none"
    for min_evidence, name in _BAND_THRESHOLDS:
        if evidence >= min_evidence:
            level = name
    return level


def _fill_indicators(target: object, lexicon: dict[str, list[str]], text_lower: str) -> None:
    """把逐维度命中写入指标对象，并同步 total/peak/evidence/items_hit/level。"""
    matched: list[str] = []
    best_hits = 0
    scores: list[float] = []
    for dimension, keywords in lexicon.items():
        hits = [kw for kw in keywords if kw in text_lower]
        score = min(1.0, len(hits) / HITS_FOR_FULL_SIGNAL) if hits else 0.0
        setattr(target, dimension, score)
        scores.append(score)
        if hits:
            matched.extend(hits)
            best_hits = max(best_hits, len(hits))
    target.matched_keywords = matched  # type: ignore[attr-defined]
    target.total_score = sum(scores) / len(scores)  # type: ignore[attr-defined]
    target.peak_score = max(scores) if scores else 0.0  # type: ignore[attr-defined]
    target.items_hit = sum(1 for d in lexicon if getattr(target, d) > 0.0)  # type: ignore[attr-defined]
    target.evidence = max(best_hits, target.items_hit)  # type: ignore[attr-defined]
    target.level = _band_level(target.evidence)  # type: ignore[attr-defined]



class MentalHealthScreener:
    """心理健康筛查器

    基于词典匹配 + LLM深度分析的混合方法:
      - 第一阶段: 快速词典匹配 (零延迟, 隐私友好)
      - 第二阶段: LLM 深度分析 (仅在词典命中时触发,
        参考 MentalBERT/PsychBERT 思路但使用通用LLM)
    """

    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway
        self.screening_count = 0
        self.crisis_alerts = 0
        self.history: list[MentalHealthSnapshot] = []

    def quick_screen(self, text: str) -> MentalHealthSnapshot:
        """快速词典筛查 (零成本)

        逐维度统计词表命中，产出 0-1 相对信号与证据档位。
        """
        from datetime import datetime, timezone

        text_lower = text.lower()
        snapshot = MentalHealthSnapshot(timestamp=datetime.now(tz=timezone.utc).isoformat())

        # ── 抑郁维度信号 ──
        dep = DepressionIndicators()
        _fill_indicators(dep, _DEPRESSION_LEXICON, text_lower)
        if dep.suicidal >= 0.5:  # 自伤维度命中 ≥2 词，独立于档位直接判 critical
            dep.level = "critical"
        snapshot.depression = dep

        # ── 焦虑维度信号 ──
        anx = AnxietyIndicators()
        _fill_indicators(anx, _ANXIETY_LEXICON, text_lower)
        snapshot.anxiety = anx

        snapshot.anxiety = anx

        # ── 创伤信号 ──
        trauma_hits = 0
        for keywords in _TRAUMA_LEXICON.values():
            trauma_hits += sum(1 for kw in keywords if kw in text_lower)
        snapshot.trauma_signals = min(1.0, trauma_hits * 0.2)

        # ── 自伤风险评估 ──
        snapshot.self_harm_risk = dep.suicidal

        # ── 总体风险评估 ──
        if snapshot.self_harm_risk >= 0.5:
            snapshot.overall_risk = "critical"
            self.crisis_alerts += 1
        elif dep.level in ("severe", "critical") or anx.level == "severe":
            snapshot.overall_risk = "high"
        elif dep.level == "moderate" or anx.level == "moderate":
            snapshot.overall_risk = "moderate"
        else:
            snapshot.overall_risk = "low"

        self.history.append(snapshot)
        self.screening_count += 1

        return snapshot

    async def deep_screen(self, text: str, context: str = "") -> MentalHealthSnapshot:
        """深度LLM分析 (参考 MentalBERT 思路但用通用LLM)

        仅在 quick_screen 检测到中等以上风险时触发。
        让模型按情绪信号维度描述文本证据；输出仍是信号档位，不是诊断。
        """
        if not self._llm:
            return self.quick_screen(text)

        prompt = f"""你是文本情绪信号分析助手。请仅依据下列文本证据描述情绪信号强度，不要下诊断结论，不要扮演临床角色。

【分析框架】
1. 抑郁向信号: 睡眠/兴趣/自责/精力/注意力/食欲/精神运动/自伤
2. 焦虑向信号: 紧张/失控担忧/过度担心/难以放松/坐立不安/易怒/恐惧预感
3. 认知扭曲: 全或无思维/灾难化/过度概括/情绪推理/贴标签
4. 自伤风险: 任何自杀意念或自伤意图

【对话内容】
{context}
用户: {text}

【输出格式】严格JSON，不要其他文字:
{{
  "depression_level": "none/mild/moderate/severe",
  "anxiety_level": "none/mild/moderate/severe",
  "self_harm_risk": 0.0-1.0,
  "cognitive_distortions": ["扭曲类型1", ...],
  "key_observations": ["观察1", ...],
  "recommendation": "建议文本"
}}

⚠️ 若检测到自伤风险，recommendation必须包含24小时心理援助热线（全国统一12356或北京24h线010-82951332）"""

        try:
            reply = await self._llm.chat(prompt, system_prompt="你是文本情绪信号分析助手，只描述信号强度与文本证据，不做诊断。")
            import json as _json
            result = _json.loads(reply) if isinstance(reply, str) else reply

            snapshot = MentalHealthSnapshot(method="llm_assessment")
            snapshot.depression.level = result.get("depression_level", "none")
            snapshot.anxiety.level = result.get("anxiety_level", "none")
            snapshot.self_harm_risk = float(result.get("self_harm_risk", 0))
            snapshot.overall_risk = (
                "critical" if snapshot.self_harm_risk >= 0.5
                else "high" if "severe" in [snapshot.depression.level, snapshot.anxiety.level]
                else "moderate" if "moderate" in [snapshot.depression.level, snapshot.anxiety.level]
                else "low"
            )
            return snapshot
        except Exception:  # noqa: BLE001
            return self.quick_screen(text)

    def get_risk_summary(self) -> dict:
        """获取历史风险摘要"""
        if not self.history:
            return {"total_screenings": 0, "crisis_alerts": 0, "trend": "insufficient_data"}

        recent = self.history[-10:]
        risk_scores = [1.0 if s.overall_risk == "critical" else
                        0.7 if s.overall_risk == "high" else
                        0.3 if s.overall_risk == "moderate" else 0.0
                        for s in recent]

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0

        return {
            "total_screenings": self.screening_count,
            "crisis_alerts": self.crisis_alerts,
            "recent_avg_risk": round(avg_risk, 3),
            "current_level": recent[-1].overall_risk if recent else "low",
            "trend": "improving" if len(risk_scores) >= 3 and risk_scores[-1] < sum(risk_scores[:-1]) / max(1, len(risk_scores) - 1)
                     else "worsening" if len(risk_scores) >= 3 and risk_scores[-1] > sum(risk_scores[:-1]) / max(1, len(risk_scores) - 1)
                     else "stable",
            "depression_latest": recent[-1].depression.level if recent else "none",
            "anxiety_latest": recent[-1].anxiety.level if recent else "none",
        }

    def health_check(self) -> dict:
        return {
            "screening_count": self.screening_count,
            "crisis_alerts": self.crisis_alerts,
            "history_size": len(self.history),
        }


# 危机干预文本统一由 security/content_safety.py 的 SELF_HARM_HOTLINE 承载
# （对话热路径真实返回的拦截文本），此处不再重复定义。
