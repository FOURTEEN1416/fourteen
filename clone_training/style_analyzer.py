"""
风格分析器 — 深度分析 WeChat 聊天记录中的说话风格

从对话数据中提取 12 维风格特征：
1.  句式长度分布（短句/中句/长句/超长句）
2.  标点使用习惯（频率、类型分布）
3.  语气词频率（的/了/呢/吧/嘛/啊/哦/呀）
4.  表情/颜文字使用（频率 + 类型）
5.  口头禅提取（高频词/短语）
6.  回复延迟模式（秒级/分钟级/小时级）
7.  情绪词汇分布（正面/负面/中性）
8.  人称代词偏好（我/你/他/人家/咱）
9.  主动/被动句式比例
10. 问句/祈使句/陈述句比例
11. 缩写/网络用语偏好
12. 独特性打分（该风格与"普通聊天"的差异化程度）

输出：StyleProfile 对象 + JSON 风格报告
"""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("clone.analyzer")


@dataclass
class StyleProfile:
    """完整的说话风格档案"""
    # 基本信息
    total_messages: int = 0
    total_turns: int = 0
    avg_sentence_length: float = 0.0

    # 句式分布
    sentence_length_dist: dict[str, float] = field(default_factory=dict)
    # 标点习惯
    punctuation_freq: dict[str, float] = field(default_factory=dict)
    top_punctuation: list[str] = field(default_factory=list)

    # 语气词
    particle_freq: dict[str, float] = field(default_factory=dict)
    # 表情/颜文字
    emoji_freq: float = 0.0
    emoji_types: list[str] = field(default_factory=list)
    kaomoji_freq: float = 0.0

    # 口头禅
    catchphrases: list[tuple[str, int]] = field(default_factory=list)

    # 情绪分布
    emotion_dist: dict[str, float] = field(default_factory=dict)
    # 人称
    pronoun_dist: dict[str, float] = field(default_factory=dict)

    # 句类分布
    sentence_type_dist: dict[str, float] = field(default_factory=dict)
    # 网络用语
    slang_freq: float = 0.0
    slang_examples: list[str] = field(default_factory=list)

    # 独特性
    uniqueness_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """导出为字典（用于 JSON）"""
        return {
            "total_messages": self.total_messages,
            "total_turns": self.total_turns,
            "avg_sentence_length": round(self.avg_sentence_length, 1),
            "sentence_length_dist": self.sentence_length_dist,
            "punctuation_freq": self.punctuation_freq,
            "top_punctuation": self.top_punctuation[:5],
            "particle_freq": self.particle_freq,
            "emoji_freq": round(self.emoji_freq, 3),
            "emoji_types": self.emoji_types[:10],
            "kaomoji_freq": round(self.kaomoji_freq, 3),
            "catchphrases": self.catchphrases[:10],
            "emotion_dist": self.emotion_dist,
            "pronoun_dist": self.pronoun_dist,
            "sentence_type_dist": self.sentence_type_dist,
            "slang_freq": round(self.slang_freq, 3),
            "slang_examples": self.slang_examples[:10],
            "uniqueness_score": round(self.uniqueness_score, 2),
        }

    def to_style_prompt(self) -> str:
        """将风格档案转为 LLM 可用的风格描述段"""
        lines = []
        lines.append("【说话风格分析】")

        # 句式
        if self.sentence_length_dist:
            dominant = max(self.sentence_length_dist, key=self.sentence_length_dist.get)  # type: ignore
            lines.append(f"- 偏好句式: {dominant} (占 {self.sentence_length_dist.get(dominant, 0):.0%})")

        # 标点
        if self.top_punctuation:
            lines.append(f"- 常用标点: {', '.join(self.top_punctuation[:3])}")

        # 语气词
        if self.particle_freq:
            top_particles = sorted(
                self.particle_freq.items(), key=lambda x: -x[1]
            )[:3]
            lines.append(f"- 高频语气词: {', '.join(f'{k}({v:.0%})' for k, v in top_particles)}")

        # 表情
        if self.emoji_freq > 0.01:
            lines.append(f"- 使用表情频率: {self.emoji_freq:.0%} (每 {1/self.emoji_freq:.0f} 条)")
        if self.kaomoji_freq > 0.01:
            lines.append(f"- 使用颜文字频率: {self.kaomoji_freq:.0%}")

        # 口头禅
        if self.catchphrases:
            lines.append(f"- 口头禅: {', '.join(p[0] for p in self.catchphrases[:3])}")

        # 情绪
        if self.emotion_dist:
            dominant_emo = max(self.emotion_dist, key=self.emotion_dist.get)  # type: ignore
            lines.append(f"- 主要情绪: {dominant_emo} ({self.emotion_dist[dominant_emo]:.0%})")

        # 人称
        if self.pronoun_dist:
            top_pro = sorted(
                self.pronoun_dist.items(), key=lambda x: -x[1]
            )[:2]
            lines.append(f"- 人称偏好: {', '.join(f'{k}({v:.0%})' for k, v in top_pro)}")

        # 句类
        if self.sentence_type_dist:
            top_s = max(self.sentence_type_dist, key=self.sentence_type_dist.get)  # type: ignore
            lines.append(f"- 主要句类: {top_s} ({self.sentence_type_dist[top_s]:.0%})")

        # 网络用语
        if self.slang_examples:
            lines.append(f"- 网络用语: {', '.join(self.slang_examples[:3])}")

        # 独特性
        lines.append(f"- 独特程度: {self.uniqueness_score:.0%}")

        return "\n".join(lines)


class StyleAnalyzer:
    """从聊天记录中提取说话风格"""

    # ── 关键词词典 ──

    PARTICLES = ["的", "了", "呢", "吧", "嘛", "啊", "哦", "呀", "啦", "哈", "嗯", "哎"]

    POSITIVE_EMO = ["开心", "哈哈", "嘿嘿", "嘻嘻", "好", "喜欢", "爱", "赞", "棒", "优秀", "厉害"]
    NEGATIVE_EMO = ["生气", "烦", "讨厌", "恶心", "滚", "无语", "呵呵", "难受", "伤心", "累了"]
    NEUTRAL_EMO  = ["嗯", "哦", "好吧", "知道了", "行", "可以", "随便"]

    SENTENCE_TYPES = {
        "提问": [r"[?？]$", r"吗[?？]?$", r"(怎么|啥|什么|谁|哪|几|多少).*[?？]?"],
        "感叹": [r"[!！]$", r"(啊|呀|啦|呢)[!！]?$"],
        "祈使": [r"(别|不要|请|来|去|帮我|给我).*$"],
        "陈述": [r".*[。.]$"],
    }

    SLANG = [
        "yyds", "绝绝子", "破防", "emo", "真香", "打工人", "躺平", "内卷",
        "绝了", "无语子", "栓Q", "芭比Q", "我真的会谢", "润", "卷",
        "绷不住了", "好家伙", "确实", "笑死", "离谱", "6", "xswl",
    ]

    EMOJI_PATTERN = re.compile(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        r"\u2600-\u26FF\u2700-\u27BF]"
    )

    KAOMOJI_PATTERN = re.compile(
        r"(\([^)]*\)|【[^】]*】|[\u00AF\\_][\u00AF\\_]+|\\[\(\^\^\)/\<\>]+\\)"
    )

    def __init__(self):
        pass

    def analyze(
        self, conversations: list[dict[str, Any]],
    ) -> StyleProfile:
        """
        完整分析一组对话的说话风格

        Args:
            conversations: [{"user": ..., "reply": ..., ...}, ...]

        Returns:
            StyleProfile 风格档案
        """
        profile = StyleProfile()

        # 提取所有回复文本（被克隆对象说的话）
        replies = [c["reply"] for c in conversations if c.get("reply")]
        if not replies:
            logger.warning("无有效回复数据")
            return profile

        profile.total_messages = len(replies)
        profile.total_turns = len(conversations)

        # 1. 句长分布
        lengths = [len(r) for r in replies]
        profile.avg_sentence_length = sum(lengths) / len(lengths)
        profile.sentence_length_dist = {
            "短句(<=10字)": sum(1 for ln in lengths if ln <= 10) / len(lengths),
            "中句(11-30字)": sum(1 for ln in lengths if 11 <= ln <= 30) / len(lengths),
            "长句(31-80字)": sum(1 for ln in lengths if 31 <= ln <= 80) / len(lengths),
            "超长句(>80字)": sum(1 for ln in lengths if ln > 80) / len(lengths),
        }

        # 2. 标点
        all_text = " ".join(replies)
        punct_counter = Counter(
            ch for ch in all_text if ch in "，。！？、；：""''（）…—～"
        )
        total_punct = sum(punct_counter.values())
        if total_punct > 0:
            profile.punctuation_freq = {
                k: v / total_punct for k, v in punct_counter.most_common(10)
            }
        profile.top_punctuation = [
            k for k, _ in punct_counter.most_common(5)
        ]

        # 3. 语气词
        particle_counter = Counter()
        for reply in replies:
            for p in self.PARTICLES:
                particle_counter[p] += reply.count(p)
        total_particles = sum(particle_counter.values())
        if total_particles > 0:
            profile.particle_freq = {
                k: v / total_particles
                for k, v in particle_counter.most_common(8)
            }

        # 4. 表情/颜文字
        emoji_count = sum(
            len(self.EMOJI_PATTERN.findall(r)) for r in replies
        )
        profile.emoji_freq = emoji_count / len(replies) if replies else 0
        all_emojis = []
        for r in replies:
            all_emojis.extend(self.EMOJI_PATTERN.findall(r))
        emoji_types = Counter(all_emojis)
        profile.emoji_types = [k for k, _ in emoji_types.most_common(10)]

        kaomoji_count = sum(
            len(self.KAOMOJI_PATTERN.findall(r)) for r in replies
        )
        profile.kaomoji_freq = kaomoji_count / len(replies) if replies else 0

        # 5. 口头禅（2-4字高频短语）
        catchphrase_counter = Counter()
        for reply in replies:
            for n in range(2, 5):
                for i in range(len(reply) - n + 1):
                    gram = reply[i:i+n]
                    if all("\u4e00" <= ch <= "\u9fff" or ch in "～！？" for ch in gram):
                        catchphrase_counter[gram] += 1
        common_fillers = {"我也是", "我觉得", "不知道", "为什么", "怎么了", "是什么"}
        profile.catchphrases = [
            (gram, cnt) for gram, cnt in catchphrase_counter.most_common(30)
            if gram not in common_fillers and cnt >= 2
        ][:10]

        # 6. 情绪分布
        emo_counter = Counter()
        for reply in replies:
            for kw in self.POSITIVE_EMO:
                if kw in reply:
                    emo_counter["正面"] += 1
                    break
            else:
                for kw in self.NEGATIVE_EMO:
                    if kw in reply:
                        emo_counter["负面"] += 1
                        break
                else:
                    for kw in self.NEUTRAL_EMO:
                        if kw in reply:
                            emo_counter["中性"] += 1
                            break
        total_emo = sum(emo_counter.values()) or 1
        profile.emotion_dist = {
            k: v / total_emo for k, v in emo_counter.most_common()
        }

        # 7. 人称代词
        pronoun_counter = Counter()
        for reply in replies:
            for pro in ["你", "我", "他", "她", "人家", "咱", "俺"]:
                pronoun_counter[pro] += reply.count(pro)
        total_pronouns = sum(pronoun_counter.values()) or 1
        profile.pronoun_dist = {
            k: v / total_pronouns
            for k, v in pronoun_counter.most_common()
        }

        # 8. 句类分布
        st_counter = Counter()
        for reply in replies:
            classified = False
            for st_name, patterns in self.SENTENCE_TYPES.items():
                for pat in patterns:
                    if re.search(pat, reply):
                        st_counter[st_name] += 1
                        classified = True
                        break
                if classified:
                    break
            if not classified:
                st_counter["陈述"] += 1
        total_st = sum(st_counter.values())
        profile.sentence_type_dist = {
            k: v / total_st for k, v in st_counter.most_common()
        }

        # 9. 网络用语
        slang_count = 0
        slang_found = set()
        for reply in replies:
            for s in self.SLANG:
                if s in reply:
                    slang_count += 1
                    slang_found.add(s)
        profile.slang_freq = slang_count / len(replies) if replies else 0
        profile.slang_examples = list(slang_found)[:10]

        # 10. 独特性打分
        profile.uniqueness_score = self._calc_uniqueness(profile)

        logger.info(
            "风格分析完成: %d 条消息, 平均句长=%.1f, 独特性=%.2f",
            profile.total_messages,
            profile.avg_sentence_length,
            profile.uniqueness_score,
        )

        return profile

    def _calc_uniqueness(self, profile: StyleProfile) -> float:
        """计算风格独特性（偏离"普通聊天"的程度）"""
        score = 0.0

        # 句长偏离
        length_deviation = abs(profile.avg_sentence_length - 15) / 30
        score += length_deviation * 0.15

        # 表情使用
        score += min(profile.emoji_freq * 5, 1.0) * 0.15
        score += min(profile.kaomoji_freq * 5, 1.0) * 0.10

        # 语气词多样性
        particle_variety = len(profile.particle_freq) / 8
        score += particle_variety * 0.15

        # 网络用语
        score += min(profile.slang_freq * 3, 1.0) * 0.15

        # 情绪偏离（偏离中性50%）
        neutral_ratio = profile.emotion_dist.get("中性", 0.5)
        score += abs(neutral_ratio - 0.5) * 2 * 0.15

        # 口头禅数量
        score += min(len(profile.catchphrases) / 10, 1.0) * 0.15

        return min(score, 1.0)

    def save_report(
        self, profile: StyleProfile, filepath: str,
    ) -> str:
        """将风格分析报告保存为 JSON"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("风格报告已保存: %s", filepath)
        return filepath
