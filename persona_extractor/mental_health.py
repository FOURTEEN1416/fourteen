"""
心理健康筛查模块 — 基于 DSM-5 标准 + 心理语言学标记

参考:
  - DSM-5-TR (2022) 诊断标准
  - GAD-7 广泛性焦虑筛查量表
  - PHQ-9 抑郁症筛查量表
  - CLPsych 计算语言学与临床心理学共享任务
  - MentalBERT / PsychBERT 预训练模型思路

⚠️ 免责声明: 此模块仅为辅助分析工具，不能替代专业心理诊断。
   高风险信号会自动提示用户寻求专业帮助。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════
# DSM-5 抑郁发作 (Major Depressive Episode) 标志性词汇
# ═══════════════════════════════════════════════════════════

# SIGECAPS 助记词对应词汇库
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

# GAD-7 焦虑对应词汇
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

# PTSD/创伤相关词汇
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


@dataclass
class DepressionIndicators:
    """抑郁指标 (SIGECAPS)"""
    sleep: float = 0.0
    interest: float = 0.0
    guilt: float = 0.0
    energy: float = 0.0
    concentration: float = 0.0
    appetite: float = 0.0
    psychomotor: float = 0.0
    suicidal: float = 0.0
    total_score: float = 0.0
    level: str = "none"  # none / mild / moderate / severe / critical
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sleep": round(self.sleep, 3),
            "interest": round(self.interest, 3),
            "guilt": round(self.guilt, 3),
            "energy": round(self.energy, 3),
            "concentration": round(self.concentration, 3),
            "appetite": round(self.appetite, 3),
            "psychomotor": round(self.psychomotor, 3),
            "suicidal": round(self.suicidal, 3),
            "total_score": round(self.total_score, 3),
            "level": self.level,
            "matched": self.matched_keywords[:20],
        }


@dataclass
class AnxietyIndicators:
    """焦虑指标 (GAD-7 对应)"""
    nervousness: float = 0.0
    uncontrollable_worry: float = 0.0
    worry_too_much: float = 0.0
    trouble_relaxing: float = 0.0
    restlessness: float = 0.0
    irritability: float = 0.0
    fear_awful: float = 0.0
    total_score: float = 0.0
    level: str = "none"
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nervousness": round(self.nervousness, 3),
            "uncontrollable_worry": round(self.uncontrollable_worry, 3),
            "worry_too_much": round(self.worry_too_much, 3),
            "trouble_relaxing": round(self.trouble_relaxing, 3),
            "restlessness": round(self.restlessness, 3),
            "irritability": round(self.irritability, 3),
            "fear_awful": round(self.fear_awful, 3),
            "total_score": round(self.total_score, 3),
            "level": self.level,
            "matched": self.matched_keywords[:20],
        }


@dataclass
class MentalHealthSnapshot:
    """单次心理健康检测快照"""
    timestamp: str = ""
    depression: DepressionIndicators = field(default_factory=DepressionIndicators)
    anxiety: AnxietyIndicators = field(default_factory=AnxietyIndicators)
    trauma_signals: float = 0.0
    self_harm_risk: float = 0.0
    overall_risk: str = "low"  # low / moderate / high / critical

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "depression": self.depression.to_dict(),
            "anxiety": self.anxiety.to_dict(),
            "trauma_signals": round(self.trauma_signals, 3),
            "self_harm_risk": round(self.self_harm_risk, 3),
            "overall_risk": self.overall_risk,
        }


# ═══════════════════════════════════════════════════════════
# 筛查引擎
# ═══════════════════════════════════════════════════════════


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

        基于 DSM-5 标志性词汇和 GAD-7 对应词汇进行模式匹配。
        """
        from datetime import datetime, timezone

        text_lower = text.lower()
        snapshot = MentalHealthSnapshot(timestamp=datetime.now(tz=timezone.utc).isoformat())
        all_matched = []

        # ── 抑郁筛查 ──
        dep = DepressionIndicators()
        for dimension, keywords in _DEPRESSION_LEXICON.items():
            hits = [kw for kw in keywords if kw in text_lower]
            if hits:
                score = min(1.0, len(hits) * 0.25)
                setattr(dep, dimension, score)
                all_matched.extend(hits)
        dep.matched_keywords = all_matched

        # 计算总分 (SIGECAPS: 5/8 为中度)
        sigecaps = ["sleep", "interest", "guilt", "energy", "concentration",
                     "appetite", "psychomotor", "suicidal"]
        dep.total_score = sum(getattr(dep, d) for d in sigecaps) / len(sigecaps)

        if dep.suicidal >= 0.5:
            dep.level = "critical"
        elif dep.total_score >= 0.6:
            dep.level = "severe"
        elif dep.total_score >= 0.35:
            dep.level = "moderate"
        elif dep.total_score >= 0.15:
            dep.level = "mild"
        else:
            dep.level = "none"

        snapshot.depression = dep

        # ── 焦虑筛查 ──
        anx = AnxietyIndicators()
        anx_matched = []
        for dimension, keywords in _ANXIETY_LEXICON.items():
            hits = [kw for kw in keywords if kw in text_lower]
            if hits:
                score = min(1.0, len(hits) * 0.3)
                setattr(anx, dimension, score)
                anx_matched.extend(hits)
        anx.matched_keywords = anx_matched

        gad7_dims = ["nervousness", "uncontrollable_worry", "worry_too_much",
                      "trouble_relaxing", "restlessness", "irritability", "fear_awful"]
        anx.total_score = sum(getattr(anx, d) for d in gad7_dims) / len(gad7_dims)

        if anx.total_score >= 0.5:
            anx.level = "severe"
        elif anx.total_score >= 0.3:
            anx.level = "moderate"
        elif anx.total_score >= 0.12:
            anx.level = "mild"
        else:
            anx.level = "none"

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
        使用结构化 prompt 引导 LLM 按照 DSM-5 框架进行分析。
        """
        if not self._llm:
            return self.quick_screen(text)

        prompt = f"""你是一位接受过DSM-5培训的临床心理学助手。请分析以下对话内容，仅基于文本证据评估心理健康信号。

【分析框架】
1. 抑郁信号(SIGECAPS): 睡眠/兴趣/自责/精力/注意力/食欲/精神运动/自伤
2. 焦虑信号(GAD-7): 紧张/失控担忧/过度担心/难以放松/坐立不安/易怒/恐惧预感
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
            reply = await self._llm.chat(prompt, system="你是心理健康筛查助手，基于DSM-5标准。")
            import json as _json
            result = _json.loads(reply) if isinstance(reply, str) else reply

            snapshot = MentalHealthSnapshot()
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


# ═══════════════════════════════════════════════════════════
# 危机干预文本 (符合中国心理援助规范)
# ═══════════════════════════════════════════════════════════

SELF_HARM_INTERVENTION = (
    "我注意到你最近似乎状态不太好。如果你正在经历困难，请记住：\n"
    "— 全国统一心理援助热线: 12356（各地服务时段以当地公告为准）\n"
    "— 北京心理危机研究与干预中心: 010-82951332\n"
    "— 希望24热线: 400-161-9995\n"
    "你不是一个人，总有人愿意倾听你。"
)
