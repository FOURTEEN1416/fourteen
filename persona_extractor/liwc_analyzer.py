"""
LIWC风格心理语言学分析器

参考文献:
  - Pennebaker, J.W. et al. (2015). The development and psychometric
    properties of LIWC2015.
  - Tausczik & Pennebaker (2010). The psychological meaning of words.
  - Boyd et al. (2022). The development and psychometric properties of LIWC-22.

LIWC 核心类别:
  1. 功能词 (Function Words): 代词、介词、连词等
  2. 情感词 (Affect): 正面/负面情绪
  3. 社会词 (Social): 家人、朋友、人称
  4. 认知过程 (Cognitive Processes): 洞察、因果、差异
  5. 感知过程 (Perceptual): 视觉、听觉、感觉
  6. 生物过程 (Biological): 身体、健康、性
  7. 驱力 (Drives): 归属、成就、权力
  8. 时间焦点 (Time): 过去/现在/未来
  9. 相对性 (Relativity): 运动、空间、时间
  10. 个人关注 (Personal Concerns): 工作、金钱、宗教、死亡
"""

from __future__ import annotations

from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════
# LIWC 中文字典 (精选核心词)
# ═══════════════════════════════════════════════════════════

_LIWC_ZH_DICT: dict[str, set[str]] = {
    # ── 代词 (Pronouns) ──
    "i_words": {"我", "俺", "本人", "自己", "咱"},
    "we_words": {"我们", "咱们", "大家", "咱俩", "我俩"},
    "you_words": {"你", "您", "你们", "你俩"},
    "he_she_words": {"他", "她", "他们", "她们", "它", "它们"},
    " impersonal_pronouns": {"有人", "某人", "任何人", "没有人", "所有人"},

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


@dataclass
class LiwcProfile:
    """LIWC 心理语言学画像"""
    # 功能词比例
    pronoun_ratio: float = 0.0
    i_ratio: float = 0.0
    we_ratio: float = 0.0
    you_ratio: float = 0.0
    he_she_ratio: float = 0.0

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

    # 其他
    biological_ratio: float = 0.0
    health_ratio: float = 0.0
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

    # 分析元数据
    total_words: int = 0
    analytical_thinking: float = 0.0  # 分析性思维 (高=更逻辑)
    clout: float = 0.0  # 影响力/自信 (高=更自信)
    authentic: float = 0.0  # 真实性

    def to_dict(self) -> dict:
        """转字典（只保留非零字段）"""
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
        lines = ["[心理语言学画像-LIWC]"]
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
    """LIWC 心理语言学分析器

    基于 LIWC2015/LIWC-22 框架，
    使用中文字典进行词频分析。

    参考文献:
      - Pennebaker et al. (2015). LIWC2015.
      - Huang et al. (2012). Chinese LIWC dictionary (CLIWC).
    """

    def __init__(self):
        self.analysis_count = 0

    def analyze(self, text: str) -> LiwcProfile:
        """分析文本，返回 LIWC 画像"""
        # 分词 (简单按字符+空格分词)
        import re as _re
        words = _re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text.lower())

        if not words:
            return LiwcProfile()

        total = len(words)
        profile = LiwcProfile(total_words=total)

        # 统计各类别命中数
        counts: dict[str, int] = {}
        for category, lexicon in _LIWC_ZH_DICT.items():
            cnt = sum(1 for w in words if w in lexicon)
            counts[category] = cnt
            ratio = cnt / total if total > 0 else 0
            setattr(profile, f"{category}_ratio", ratio)

        # ── 复合指标 ──
        pos = counts.get("positive_emotion", 0)
        neg = counts.get("negative_emotion", 0)
        total_affect = pos + neg
        if total_affect > 0:
            profile.emotional_tone = (pos - neg) / total_affect
        else:
            profile.emotional_tone = 0.0

        # 分析性思维 (Analytical Thinking)
        # 公式: 高分析性 = 多冠词/介词, 低叙事性
        # 简化版: (因果词 + 认知词) / (叙事词)
        cognitive_words = counts.get("insight_words", 0) + counts.get("causation_words", 0)
        story_words = counts.get("past_words", 0) + counts.get("i_words", 0) + 1
        profile.analytical_thinking = min(1.0, cognitive_words / max(1, story_words))

        # 影响力/自信 (Clout)
        # 公式: 高影响力 = 多用"我们/你"少用"我", 多确定少犹豫
        clout_num = counts.get("we_words", 0) + counts.get("you_words", 0) + counts.get("certainty_words", 0)
        clout_den = counts.get("i_words", 0) + counts.get("tentative_words", 0) + 1
        profile.clout = min(1.0, clout_num / clout_den)

        # 真实性 (Authentic)
        # 公式: 高真实 = 少用"他/她/它", 多用"我"
        auth_num = counts.get("i_words", 0) + counts.get("present_words", 0)
        auth_den = counts.get("he_she_words", 0) + counts.get("discrepancy_words", 0) + 1
        profile.authentic = min(1.0, auth_num / auth_den)

        self.analysis_count += 1
        return profile

    def health_check(self) -> dict:
        return {"analysis_count": self.analysis_count,
                "dictionary_size": sum(len(v) for v in _LIWC_ZH_DICT.values())}
