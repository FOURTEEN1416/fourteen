"""
心理语言学词表分析器（自建中文小词表）

口径说明（与实现严格一致，不得对外越级宣称）:
  - 本模块是一套 **自建** 的中文小词表（类别见 ``_LIWC_ZH_DICT``），
    分类思想参考 Pennebaker 一系的 LIWC 功能词/情感词框架，
    但 **未经中文效度验证，其数值不代表 LIWC2015 / LIWC-22 / CLIWC 的输出**，
    也不与任何已发表量表对标。
  - 指标口径 = 类别命中词元数 / 词元总数。词元由 ``segment_tokens`` 用 jieba
    精确模式切分并去掉标点，即「一个词」计一次（不是字符数、不是连续串数）。
  - ``analytical_thinking`` / ``clout`` / ``authentic`` 是照抄 LIWC 复合指标
    **公式形状** 的简化比值，分母只加 1 防零除，未经任何常模校准，
    只能作为同一用户前后的相对趋势读数。

参考（思想来源，非实现依据）:
  - Pennebaker, J.W. et al. (2015). The development and psychometric
    properties of LIWC2015.
  - Tausczik & Pennebaker (2010). The psychological meaning of words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════
# 中文小词表（自建，精选核心词）
# ═══════════════════════════════════════════════════════════

_LIWC_ZH_DICT: dict[str, set[str]] = {
    # ── 代词 (Pronouns) ──
    "i_words": {"我", "俺", "本人", "自己", "咱"},
    "we_words": {"我们", "咱们", "大家", "咱俩", "我俩"},
    "you_words": {"你", "您", "你们", "你俩"},
    "he_she_words": {"他", "她", "他们", "她们", "它", "它们"},
    "impersonal_pronouns": {"有人", "某人", "任何人", "没有人", "所有人"},

    # ── 情感词 (Affect) ──
    "positive_emotion": {
        "开心", "快乐", "幸福", "高兴", "喜欢", "爱", "美好", "温暖",
        "感恩", "满足", "兴奋", "期待", "希望", "棒", "好", "赞",
        "感动", "甜蜜", "安心", "舒服", "愉快", "欣慰",
    },
    "negative_emotion": {
        "难过", "伤心", "痛苦", "悲伤", "生气", "愤怒", "讨厌", "恨",
        "焦虑", "害怕", "担心", "恐惧", "绝望", "失落", "孤独",
        "委屈", "烦躁", "郁闷", "后悔", "失望", "迷茫",
    },
    "anxiety_words": {
        "紧张", "焦虑", "不安", "担心", "担忧", "害怕", "恐惧",
        "恐慌", "惊慌", "忐忑", "心慌",
    },
    "anger_words": {
        "生气", "愤怒", "恼怒", "愤慨", "火大", "暴躁", "怒",
        "烦", "讨厌", "恶心", "过分", "可恶",
    },
    "sadness_words": {
        "伤心", "难过", "悲伤", "悲哀", "忧伤", "哭泣", "哭",
        "泪", "心碎", "失落", "沮丧", "郁闷", "低落",
    },

    # ── 社会词 (Social) ──
    "social_words": {
        "朋友", "同事", "同学", "家人", "父母", "爸爸", "妈妈",
        "孩子", "老公", "老婆", "对象", "恋人", "兄弟", "姐妹",
        "亲戚", "邻居", "老板", "领导", "老师", "师傅",
    },
    "family_words": {
        "家", "爸爸", "妈妈", "父亲", "母亲", "儿子", "女儿",
        "哥哥", "姐姐", "弟弟", "妹妹", "爷爷", "奶奶",
    },
    "friend_words": {
        "朋友", "闺蜜", "哥们", "伙伴", "死党", "知己", "老铁",
    },

    # ── 认知过程 (Cognitive Processes) ──
    "insight_words": {
        "明白", "理解", "意识到", "发现", "原来", "知道", "感觉",
        "觉得", "认为", "想", "思考", "反思", "领悟",
    },
    "causation_words": {
        "因为", "所以", "因此", "导致", "引起", "原因", "结果",
        "由于", "从而", "于是", "之所以",
    },
    "discrepancy_words": {
        "但是", "可是", "不过", "然而", "虽然", "尽管", "却",
        "应该", "本来", "按理", "如果", "要是", "希望",
    },
    "tentative_words": {
        "可能", "也许", "大概", "或许", "好像", "似乎", "说不定",
        "不一定", "不确定", "没准", "万一",
    },
    "certainty_words": {
        "一定", "肯定", "绝对", "确实", "真的", "就是", "必然",
        "毫无疑问", "显然", "当然", "的确",
    },

    # ── 感知过程 (Perceptual) ──
    "see_words": {"看", "看见", "看到", "望", "观察", "注视"},
    "hear_words": {"听", "听见", "听到", "声音", "噪音"},
    "feel_words": {"感觉", "感受到", "触摸", "温暖", "冷", "热", "柔软"},

    # ── 生物过程 (Biological) ──
    "body_words": {
        "身体", "头", "手", "脚", "心", "肚子", "胃", "眼睛",
        "皮肤", "背", "肩", "腰", "腿", "脸",
    },
    "health_words": {
        "病", "痛", "疼", "累", "疲惫", "困", "不舒服", "生病",
        "吃药", "医院", "医生", "健康", "锻炼",
    },
    "sleep_words": {
        "睡", "睡觉", "困", "熬夜", "失眠", "梦", "醒", "起床",
    },

    # ── 驱力 (Drives) ──
    "affiliation_words": {
        "一起", "陪伴", "见面", "聚会", "约", "聊天", "陪",
        "关系", "联系", "交往", "相处", "沟通",
    },
    "achievement_words": {
        "成功", "目标", "努力", "加油", "完成", "达成", "赢",
        "进步", "提升", "做好", "优秀", "厉害",
    },
    "power_words": {
        "控制", "决定", "领导", "指挥", "命令", "服从", "必须",
        "权力", "优势", "主导", "说了算",
    },

    # ── 时间焦点 (Time) ──
    "past_words": {
        "以前", "曾经", "过去", "之前", "那天", "上次", "记得",
        "小时候", "原来", "当时",
    },
    "present_words": {
        "现在", "此刻", "目前", "当下", "正在", "今天", "这次",
    },
    "future_words": {
        "以后", "将来", "未来", "明天", "下次", "打算", "计划",
        "会", "要", "将要", "想要", "希望",
    },

    # ── 语言风格 ──
    "swear_words": {
        "卧槽", "我靠", "妈的", "tmd", "操", "靠", "我去",
    },
    "filler_words": {
        "嗯", "啊", "哦", "呃", "那个", "这个", "就是", "反正",
        "是吧", "对吧", "嘛", "呢", "吧", "呀",
    },
    "negation_words": {
        "不", "没", "没有", "别", "非", "无", "否",
    },
    "comparison_words": {
        "更", "比", "比较", "更加", "最", "越来越", "还算",
    },
}

# ═══════════════════════════════════════════════════════════
# 类别 → 画像字段（单一真源）
# ═══════════════════════════════════════════════════════════
# 旧实现用 ``setattr(profile, f"{category}_ratio", ...)`` 隐式派生字段名，
# 而 ``to_dict()`` 只遍历 ``__dataclass_fields__`` → 34 类里 32 类被静默丢弃。
# 现在类别与字段的对应关系必须显式声明，构造时双向校验，加类别漏字段即报错。

CATEGORY_FIELDS: dict[str, str] = {
    # 代词
    "i_words": "i_ratio",
    "we_words": "we_ratio",
    "you_words": "you_ratio",
    "he_she_words": "he_she_ratio",
    "impersonal_pronouns": "impersonal_ratio",
    # 情感
    "positive_emotion": "positive_emotion_ratio",
    "negative_emotion": "negative_emotion_ratio",
    "anxiety_words": "anxiety_ratio",
    "anger_words": "anger_ratio",
    "sadness_words": "sadness_ratio",
    # 社会
    "social_words": "social_ratio",
    "family_words": "family_ratio",
    "friend_words": "friend_ratio",
    # 认知过程
    "insight_words": "insight_ratio",
    "causation_words": "causation_ratio",
    "discrepancy_words": "discrepancy_ratio",
    "tentative_words": "tentative_ratio",
    "certainty_words": "certainty_ratio",
    # 感知过程
    "see_words": "see_ratio",
    "hear_words": "hear_ratio",
    "feel_words": "feel_ratio",
    # 生物过程
    "body_words": "body_ratio",
    "health_words": "health_ratio",
    "sleep_words": "sleep_ratio",
    # 驱力
    "affiliation_words": "affiliation_ratio",
    "achievement_words": "achievement_ratio",
    "power_words": "power_ratio",
    # 时间焦点
    "past_words": "past_ratio",
    "present_words": "present_ratio",
    "future_words": "future_ratio",
    # 语言风格
    "swear_words": "swear_ratio",
    "filler_words": "filler_ratio",
    "negation_words": "negation_ratio",
    "comparison_words": "comparison_ratio",
}

# 聚合字段 → 组成类别（按 LIWC 分类树的包含关系求和，可在 to_dict() 里复算）
DERIVED_FIELDS: dict[str, tuple[str, ...]] = {
    "pronoun_ratio": (
        "i_words", "we_words", "you_words", "he_she_words", "impersonal_pronouns",
    ),
    "cognitive_ratio": (
        "insight_words", "causation_words", "discrepancy_words",
        "tentative_words", "certainty_words",
    ),
    "biological_ratio": ("body_words", "health_words", "sleep_words"),
}

_PUNCT_RE = re.compile(r"^\W+$")

_SEGMENTER = None


def _get_segmenter():
    """词典感知的分词器（首次使用时构建，进程内复用）。

    把词表条目注册进分词器，避免「意识到 / 不舒服 / 不确定 / 感受到」被切成
    「意 + 识 + 到」后既命中不了词表、又抬高词元分母。
    """
    global _SEGMENTER
    if _SEGMENTER is None:
        import jieba

        seg = jieba.Tokenizer()  # 精确模式（cut_all=False），独立实例不动全局词典
        for lexicon in _LIWC_ZH_DICT.values():
            for word in lexicon:
                seg.add_word(word)
        _SEGMENTER = seg
    return _SEGMENTER


def segment_tokens(text: str) -> list[str]:
    """切分为「词元」：词典感知分词 + 去掉标点/空白。

    词元数即所有比例指标的分母（口径见模块文档，不再与字符数混用）。
    """
    return [
        t for t in (tok.strip() for tok in _get_segmenter().lcut(text.lower()))
        if t and not _PUNCT_RE.match(t)
    ]


def _validate_field_map() -> None:
    """类别与字段双向闭合；漏配即抛错，杜绝再次退化成「写了不落地」。"""
    declared = set(LiwcProfile.__dataclass_fields__)
    sources = {CATEGORY_FIELDS[c] for c in _LIWC_ZH_DICT if c in CATEGORY_FIELDS}
    targets = sources | set(DERIVED_FIELDS)
    problems: list[str] = []
    for category in _LIWC_ZH_DICT:
        if category != category.strip():
            problems.append(f"类别名含首尾空白: {category!r}")
        elif category not in CATEGORY_FIELDS:
            problems.append(f"类别无落地字段: {category}")
        elif CATEGORY_FIELDS[category] not in declared:
            problems.append(f"类别 {category} 指向未声明字段 {CATEGORY_FIELDS[category]}")
    for field in declared:
        if field.endswith("_ratio") and field not in targets:
            problems.append(f"字段无来源类别: {field}")
    for field, categories in DERIVED_FIELDS.items():
        for category in categories:
            if category not in _LIWC_ZH_DICT:
                problems.append(f"聚合字段 {field} 引用不存在类别 {category}")
    orphan = set(_LIWC_ZH_DICT) - set(CATEGORY_FIELDS)
    if orphan:
        problems.append(f"映射表多余类别: {sorted(orphan)}")
    if problems:
        raise ValueError("LIWC 词表与画像字段不闭合: " + "; ".join(problems))


@dataclass
class LiwcProfile:
    """自建中文词表的语言学画像（字段与 ``CATEGORY_FIELDS`` 一一对应）"""
    # 功能词比例（pronoun_ratio 为代词聚合，见 DERIVED_FIELDS）
    pronoun_ratio: float = 0.0
    i_ratio: float = 0.0
    we_ratio: float = 0.0
    you_ratio: float = 0.0
    he_she_ratio: float = 0.0
    impersonal_ratio: float = 0.0

    # 情感调性
    positive_emotion_ratio: float = 0.0
    negative_emotion_ratio: float = 0.0
    anxiety_ratio: float = 0.0
    anger_ratio: float = 0.0
    sadness_ratio: float = 0.0
    emotional_tone: float = 0.0  # -1(负面) ~ +1(正面)

    # 社会性
    social_ratio: float = 0.0
    family_ratio: float = 0.0
    friend_ratio: float = 0.0

    # 认知风格
    cognitive_ratio: float = 0.0
    insight_ratio: float = 0.0
    causation_ratio: float = 0.0
    discrepancy_ratio: float = 0.0
    tentative_ratio: float = 0.0
    certainty_ratio: float = 0.0

    # 感知过程
    see_ratio: float = 0.0
    hear_ratio: float = 0.0
    feel_ratio: float = 0.0

    # 其他
    biological_ratio: float = 0.0  # = body + health + sleep（见 DERIVED_FIELDS）
    body_ratio: float = 0.0
    health_ratio: float = 0.0
    sleep_ratio: float = 0.0
    affiliation_ratio: float = 0.0
    achievement_ratio: float = 0.0
    power_ratio: float = 0.0

    # 时间焦点
    past_ratio: float = 0.0
    present_ratio: float = 0.0
    future_ratio: float = 0.0

    # 语言风格
    swear_ratio: float = 0.0
    filler_ratio: float = 0.0
    negation_ratio: float = 0.0
    comparison_ratio: float = 0.0

    # 分析元数据
    total_words: int = 0  # 词元数（segment_tokens 口径），所有 *_ratio 的分母
    analytical_thinking: float = 0.0  # 简化比值，非常模校准，只作同用户前后趋势
    clout: float = 0.0  # 简化比值，非常模校准
    authentic: float = 0.0  # 简化比值，非常模校准

    def to_dict(self) -> dict:
        """转字典（只保留非零字段；字段集与 CATEGORY_FIELDS 闭合）"""
        d = {}
        for field_name in self.__dataclass_fields__:
            val = getattr(self, field_name)
            if isinstance(val, float) and val != 0.0:
                d[field_name] = round(val, 3)
            elif field_name == "total_words" and val > 0:
                d[field_name] = val
        return d

    def to_prompt_segment(self) -> str:
        """生成 LLM 提示词增强"""
        lines = ["[心理语言学画像-自建词表，未经中文效度验证]"]
        if self.emotional_tone > 0.3:
            lines.append("- 情绪基调: 偏正面")
        elif self.emotional_tone < -0.3:
            lines.append("- 情绪基调: 偏负面 (需关注)")
        else:
            lines.append("- 情绪基调: 中性")

        if self.i_ratio > 0.08:
            lines.append("- 自我关注度较高")
        if self.we_ratio > 0.03:
            lines.append("- 有群体归属感表达")
        if self.tentative_ratio > 0.03:
            lines.append("- 表达中存在不确定性")
        if self.certainty_ratio > 0.03:
            lines.append("- 表达较为确定/自信")
        if self.analytical_thinking > 0.5:
            lines.append("- 思维分析性强，逻辑清晰")
        return "\n".join(lines)


class LiwcAnalyzer:
    """自建中文词表的词频分析器

    口径: 类别命中词元数 / 词元总数（``segment_tokens``）。
    分类思想参考 LIWC 一系的功能词/情感词框架，但本实现 **未经中文效度验证**，
    数值不可与 LIWC2015 / LIWC-22 / CLIWC 的输出对标。
    """

    def __init__(self) -> None:
        _validate_field_map()
        self.analysis_count = 0

    def analyze(self, text: str) -> LiwcProfile:
        """分析文本，返回词表画像"""
        tokens = segment_tokens(text)

        if not tokens:
            return LiwcProfile()

        total = len(tokens)
        profile = LiwcProfile(total_words=total)

        # 统计各类别命中数（词元精确匹配；一个词元可同时计入多个类别）
        counts: dict[str, int] = {}
        for category, lexicon in _LIWC_ZH_DICT.items():
            cnt = sum(1 for t in tokens if t in lexicon)
            counts[category] = cnt
            setattr(profile, CATEGORY_FIELDS[category], cnt / total)

        # ── 聚合字段：按声明的组成类别求和（可在 to_dict() 里复算）──
        for field, source_categories in DERIVED_FIELDS.items():
            setattr(profile, field, sum(counts[c] / total for c in source_categories))

        # ── 复合指标 ──
        pos = counts.get("positive_emotion", 0)
        neg = counts.get("negative_emotion", 0)
        total_affect = pos + neg
        if total_affect > 0:
            profile.emotional_tone = (pos - neg) / total_affect
        else:
            profile.emotional_tone = 0.0

        # 分析性思维：(洞察 + 因果) / (过去 + 我 + 1) —— 简化比值，非常模校准
        cognitive_words = counts.get("insight_words", 0) + counts.get("causation_words", 0)
        story_words = counts.get("past_words", 0) + counts.get("i_words", 0) + 1
        profile.analytical_thinking = min(1.0, cognitive_words / max(1, story_words))

        # 影响力/自信：(我们 + 你 + 确定) / (我 + 犹豫 + 1) —— 简化比值，非常模校准
        clout_num = counts.get("we_words", 0) + counts.get("you_words", 0) + counts.get("certainty_words", 0)
        clout_den = counts.get("i_words", 0) + counts.get("tentative_words", 0) + 1
        profile.clout = min(1.0, clout_num / clout_den)

        # 真实性：(我 + 现在) / (他/她 + 差异 + 1) —— 简化比值，非常模校准
        auth_num = counts.get("i_words", 0) + counts.get("present_words", 0)
        auth_den = counts.get("he_she_words", 0) + counts.get("discrepancy_words", 0) + 1
        profile.authentic = min(1.0, auth_num / auth_den)

        self.analysis_count += 1
        return profile

    def health_check(self) -> dict:
        return {"analysis_count": self.analysis_count,
                "categories": len(_LIWC_ZH_DICT),
                "dictionary_size": sum(len(v) for v in _LIWC_ZH_DICT.values())}
