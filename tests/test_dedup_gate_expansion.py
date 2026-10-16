"""去重硬闸长句扩面（2026-10-16 三连同义根治批 C）红测。

生产实证：会话4 当日三条同义主动消息——① 10:36「早上好，你今天感觉怎么样？」
② 11:41「早上好，你今天感觉怎么样？今天有没有什么特别的事情想要和我分享的呢？」
（①作前缀续写，归一化 11/31 字）③ 16:14「今天感觉怎么样？有没有什么特别的事情想要
和我分享的呢？」（②去前缀重排，归一化 25/31 字）。既有规则（完全相同 + ≤20 字短句
前缀相似）只拦下了 16:04 的逐字复读，②③ 全部放行。

新增两条确定性结构判据（无模糊阈值——2026-09-19 已实证 difflib 阈值不可靠）：
- 包含：较短句 ≥10 字且为较长句子串（整句复读/续写型）；
- 重叠：最长公共子串 ≥12 字（重排复读型）。

误伤保护：含既有钉死例（短开场白加长版=正常延续）+ 异话题/模板池/合法追问对照组。
"""

from __future__ import annotations

from proactive.llm_proactive import (
    DEDUP_CONTAIN_CHARS,
    DEDUP_OVERLAP_CHARS,
    is_duplicate_proactive,
)

M1 = "早上好，你今天感觉怎么样？"
M2 = "早上好，你今天感觉怎么样？今天有没有什么特别的事情想要和我分享的呢？"
M3 = "今天感觉怎么样？有没有什么特别的事情想要和我分享的呢？"


class TestContainmentRule:
    def test_production_prefix_extension_caught(self):
        """生产 11:41：m1（11 字）被 m2（31 字）完整包含 → 拦。"""
        assert is_duplicate_proactive(M2, [M1]) is True

    def test_containment_reverse_direction(self):
        """反向：新短消息是已发长消息的子句 → 同样拦。"""
        assert is_duplicate_proactive(M1, [M2]) is True

    def test_short_opener_extension_passes(self):
        """既有钉死例：6 字短开场白加长版 = 正常话题延续 → 放行。"""
        assert is_duplicate_proactive("终于想起我了呀今天过得好不好呀跟我说说嘛", ["终于想起我了"]) is False

    def test_containment_min_length_guard(self):
        """包含判据下限：9 字子串共享不拦（< DEDUP_CONTAIN_CHARS）。"""
        short = "你今天感觉还好吗"  # 8 字
        long = "你今天感觉还好吗要不要出来走走"
        assert len(short) < DEDUP_CONTAIN_CHARS
        assert is_duplicate_proactive(long, [short]) is False


class TestOverlapRule:
    def test_production_reorder_caught(self):
        """生产 16:14：m3 与 m2 最长公共子串 18 字（≥12）→ 拦。"""
        assert is_duplicate_proactive(M3, [M1, M2]) is True

    def test_overlap_below_threshold_passes(self):
        """LCS 7 字（<12）：合法追问复用短语 → 放行。"""
        assert is_duplicate_proactive("你昨天说的那个面试，今天感觉怎么样？", [M1]) is False

    def test_long_topic_continuation_passes(self):
        """既有钉死例：24/25 字不同话题长句（LCS 8 字）→ 放行。"""
        a = "今天天气不错我们一起去公园散步顺便买点好吃的吧"
        b = "今天天气不错我们要不要出门走走看看樱花开了没有呢"
        assert is_duplicate_proactive(b, [a]) is False


class TestFalsePositiveGuards:
    def test_pool_variants_not_cross_flagged(self):
        """模板池同族句互不误伤（打卡/问候族内部是不同话题）。"""
        assert is_duplicate_proactive(
            "到饭点了，吃的什么呀，给我看看", ["到饭点了，你吃了吗"]
        ) is False
        assert is_duplicate_proactive(
            "饿不饿？别问我怎么知道的", ["今天吃了什么，好奇"]
        ) is False

    def test_exact_repeat_still_caught(self):
        """精确重复（16:04 生产实证形态）仍命中——旧规则不回归。"""
        assert is_duplicate_proactive(M2, [M1, M2]) is True

    def test_empty_and_no_history(self):
        """空消息/无历史不拦。"""
        assert is_duplicate_proactive("", [M1, M2]) is False
        assert is_duplicate_proactive(M3, []) is False


class TestConstantsPinned:
    def test_thresholds_pinned(self):
        """阈值钉死：包含 ≥10、重叠 ≥12（调阈值必须同步重审本文件用例）。"""
        assert DEDUP_CONTAIN_CHARS == 10
        assert DEDUP_OVERLAP_CHARS == 12
