"""W8 缺陷 G：心理测量口径诚实化。

两块独立缺陷，同一条根因（口径与实现脱节却对外宣称权威量表）：

1. ``liwc_analyzer``：字典声明 34 类，``analyze()`` 用 ``setattr(profile, f"{category}_ratio")``
   写回，而 ``LiwcProfile`` 只声明其中 2 个字段；``to_dict()`` 只遍历
   ``__dataclass_fields__`` → 32 类**静默丢弃**（实测 40 字的焦虑句只剩
   ``{'total_words': 5}``）。``pronoun/cognitive/biological`` 三个聚合字段则从未赋值。
2. ``mental_health``：词典命中计数（1 词 0.25 封顶 1.0）却自称 DSM-5 / GAD-7 / PHQ-9，
   前端再把它显示成「PHQ-9 口径 …/27」「GAD-7 口径 …/21」——量纲完全不同的伪造对标。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from persona_extractor import liwc_analyzer
from persona_extractor.liwc_analyzer import (
    _LIWC_ZH_DICT,
    LiwcAnalyzer,
    LiwcProfile,
    segment_tokens,
)
from persona_extractor.mental_health import (
    _ANXIETY_LEXICON,
    _DEPRESSION_LEXICON,
    AnxietyIndicators,
    DepressionIndicators,
    MentalHealthScreener,
)

# 实测过会失真的句子：归属、焦虑、负面情感都应当被读到
ANXIOUS_TEXT = "我最近很焦虑，工作压力大，睡不好，担心自己做不好，也很害怕。"


def _representative_word(lexicon: set[str] | list[str]) -> str:
    """取类别内「最长、字典序最小」的条目，避免分词把它切开。"""
    words = sorted(lexicon, key=lambda w: (-len(w), w))
    return words[0]


def _full_coverage_text() -> str:
    """每类各放一个代表词，用标点隔开，保证逐类都被切为独立词元。"""
    return "，".join(_representative_word(lex) for lex in _LIWC_ZH_DICT.values()) + "。"


# ═══════════════════════════════════════════════════════════
# 1. LIWC：类别 → 字段必须双向闭合，且真的落到 to_dict()
# ═══════════════════════════════════════════════════════════


def test_liwc_dictionary_is_cleanly_keyed():
    """类别名不得带首尾空白（旧实现有 ``" impersonal_pronouns"``，派生字段名跟着错）。"""
    assert [k for k in _LIWC_ZH_DICT if k != k.strip()] == []


def test_liwc_categories_have_declared_target_fields():
    """每个字典类别都必须显式声明落地字段，且字段真的是 dataclass 成员。

    旧实现没有这张映射表：类别名 + ``"_ratio"`` 直接 setattr，字段不存在就永不出现在
    ``to_dict()`` 里，也不报错——这就是「32 类静默丢弃」的机制。
    """
    mapping = getattr(liwc_analyzer, "CATEGORY_FIELDS", None)
    assert isinstance(mapping, dict) and mapping, "缺少 CATEGORY_FIELDS 单源映射表"

    fields = set(LiwcProfile.__dataclass_fields__)
    for category in _LIWC_ZH_DICT:
        target = mapping.get(category)
        assert target is not None, f"类别 {category} 未声明落地字段 → analyze() 会静默丢弃"
        assert target in fields, f"类别 {category} 映射到未声明字段 {target}"


def test_liwc_ratio_fields_are_all_covered():
    """反向闭合：每个 ``*_ratio`` 字段要么来自某个类别，要么是声明过的派生聚合。"""
    mapping = getattr(liwc_analyzer, "CATEGORY_FIELDS", {})
    derived = getattr(liwc_analyzer, "DERIVED_FIELDS", {})
    uncovered = [
        f
        for f in LiwcProfile.__dataclass_fields__
        if f.endswith("_ratio") and f not in set(mapping.values()) and f not in derived
    ]
    assert uncovered == [], f"字段无来源类别（写了就恒 0）：{uncovered}"


def test_liwc_analyze_does_not_set_undeclared_attributes():
    """analyze() 只能写已声明字段，杜绝「属性在对象上、字典里没有」的半失忆态。"""
    profile = LiwcAnalyzer().analyze(_full_coverage_text())
    undeclared = set(vars(profile)) - set(LiwcProfile.__dataclass_fields__)
    assert not undeclared, f"未声明属性（to_dict 会丢弃）：{sorted(undeclared)}"


def test_liwc_every_category_lands_in_to_dict():
    """行为契约：逐类给一个代表词，每一类都必须在 ``to_dict()`` 里非零。"""
    profile = LiwcAnalyzer().analyze(_full_coverage_text())
    payload = profile.to_dict()
    missing = [
        f"{category}→{field}"
        for category, field in liwc_analyzer.CATEGORY_FIELDS.items()
        if not payload.get(field)
    ]
    assert missing == [], f"命中却未进入画像的类别：{missing}"


def test_liwc_anxious_sentence_reports_first_person_and_anxiety():
    payload = LiwcAnalyzer().analyze(ANXIOUS_TEXT).to_dict()
    assert payload.get("total_words", 0) > 0
    assert payload.get("i_ratio", 0) > 0, "「我/自己」未被读到"
    assert payload.get("anxiety_ratio", 0) > 0, "「焦虑/担心/害怕」未被读到"
    assert payload.get("negative_emotion_ratio", 0) > 0


def test_liwc_denominator_is_segmented_word_tokens():
    """分母口径必须写明：切分后的词元数（不含标点），不是连续串数。"""
    tokens = liwc_analyzer.segment_tokens(ANXIOUS_TEXT)
    payload = LiwcAnalyzer().analyze(ANXIOUS_TEXT).to_dict()
    assert payload["total_words"] == len(tokens)
    assert all(t.strip() for t in tokens)
    assert not any(t in "，。！？、；：" for t in tokens)
    # 旧实现把「我最近很焦虑」整串当一个词元 → 分母 5，比例全部失真
    assert len(tokens) > 5


def test_liwc_derived_aggregates_equal_documented_sum():
    """聚合字段必须是声明过的类别之和（可复算），不能是恒 0 的空壳。"""
    payload = LiwcAnalyzer().analyze(_full_coverage_text()).to_dict()
    for field, sources in liwc_analyzer.DERIVED_FIELDS.items():
        expected = sum(payload.get(liwc_analyzer.CATEGORY_FIELDS[c], 0.0) for c in sources)
        assert field in payload, f"聚合字段 {field} 从未赋值"
        assert payload[field] == pytest.approx(expected, abs=0.003), (
            f"{field}={payload[field]} 与 {sources} 之和 {expected} 不符"
        )


def test_liwc_segmenter_keeps_lexicon_entries_but_leaves_global_dict_alone():
    """分词器必须词典感知（否则「意识到/不舒服」既命中不了词表又抬高分母），
    且只作用于自有实例，不污染进程级 jieba 默认词典。"""
    import jieba

    text = "我意识到他很不舒服"
    assert "意识到" in segment_tokens(text)
    assert "不舒服" in segment_tokens(text)
    assert "意识到" not in jieba.lcut(text)
    assert "不舒服" not in jieba.lcut(text)


def test_liwc_module_does_not_claim_validated_instrument():
    """文档不得把自建小词表说成 LIWC2015/LIWC-22/CLIWC 的实现或验证结果。"""
    src = Path(liwc_analyzer.__file__).read_text(encoding="utf-8")
    assert "基于 LIWC2015" not in src and "基于 LIWC2015/LIWC-22" not in src
    assert "自建" in src or "未经中文效度" in src
    assert "未经中文效度验证" in src or "不代表 LIWC 官方结果" in src


# ═══════════════════════════════════════════════════════════
# 2. 心理健康：词典命中信号必须自报量纲与方法，不得冒充临床量表
# ═══════════════════════════════════════════════════════════


def test_mental_health_lexicon_dimensions_match_fields():
    """词典维度与指标字段必须一一对应（否则命中同样静默丢弃）。"""
    for lexicon, cls in ((_DEPRESSION_LEXICON, DepressionIndicators), (_ANXIETY_LEXICON, AnxietyIndicators)):
        fields = set(cls.__dataclass_fields__)
        orphan = [k for k in lexicon if k not in fields]
        assert orphan == [], f"词典维度无对应字段：{orphan}"


def test_depression_payload_declares_scale_and_method():
    text = "最近完全睡不着，对什么都提不起兴趣，觉得自己很没用，累得不想动。"
    dep = MentalHealthScreener().quick_screen(text).to_dict()["depression"]
    assert dep.get("scale") == "0-1"
    assert dep.get("max_score") == 1.0
    assert dep.get("method") == "keyword_hit"
    assert dep.get("items_total") == 8
    hit_count = sum(
        1
        for k in ("sleep", "interest", "guilt", "energy", "concentration",
                  "appetite", "psychomotor", "suicidal")
        if dep.get(k, 0)
    )
    assert dep.get("items_hit") == hit_count, "items_hit 必须等于实际命中维度数"
    assert dep.get("matched"), "必须回显命中的关键词作为证据"
    assert dep["total_score"] <= 1.0


def test_anxiety_payload_declares_scale_and_method():
    anx = MentalHealthScreener().quick_screen(ANXIOUS_TEXT).to_dict()["anxiety"]
    assert anx.get("scale") == "0-1"
    assert anx.get("method") == "keyword_hit"
    assert anx.get("items_total") == 7
    assert anx.get("items_hit", 0) >= 1


def test_mental_health_snapshot_carries_caveat():
    snapshot = MentalHealthScreener().quick_screen(ANXIOUS_TEXT).to_dict()
    caveat = snapshot.get("caveat", "")
    assert caveat, "缺少口径说明"
    assert "不是" in caveat and "量表" in caveat


def test_mental_health_payload_never_names_clinical_instruments():
    """对外载荷里不得出现 PHQ/GAD/DSM——那是量具名，本模块没有实现也没有验证。"""
    screener = MentalHealthScreener()
    texts = [
        ANXIOUS_TEXT,
        "我活着没意思，不想活了。",
        "今天很开心，和朋友一起吃了饭。",
        "总觉得要出事，控制不住地想。",
    ]
    for text in texts:
        payload = json.dumps(screener.quick_screen(text).to_dict(), ensure_ascii=False)
        hit = re.findall(r"PHQ|GAD|DSM", payload)
        assert not hit, f"载荷中出现了临床量表名 {set(hit)}：{text}"


def test_mental_health_module_does_not_claim_clinical_instruments():
    src = Path(liwc_analyzer.__file__).with_name("mental_health.py").read_text(encoding="utf-8")
    # 量具名只能以「本模块不是它」的口径出现，且不得作为框架自居
    assert "基于 DSM-5 标准" not in src
    assert "PHQ-9 抑郁症筛查量表" not in src
    assert "GAD-7 广泛性焦虑筛查量表" not in src
    assert "接受过DSM-5培训" not in src
    assert "不能替代专业" in src, "免责声明必须保留"


@pytest.mark.parametrize(
    "text,expect_hit",
    [
        ("今天天气不错", False),
        (ANXIOUS_TEXT, True),
    ],
)
def test_quick_screen_level_is_band_for_zero_signal(text: str, expect_hit: bool):
    snap = MentalHealthScreener().quick_screen(text).to_dict()
    if not expect_hit:
        assert snap["anxiety"]["level"] == "none"
        assert snap["anxiety"]["total_score"] == 0.0
    else:
        assert snap["anxiety"]["level"] != "none"


# ═══════════════════════════════════════════════════════════
# 3. 跨语言契约：后端 payload 字段 ⇄ 前端类型 / 页面文案
#    （后端加了字段而前端类型没跟上，等于字段到了 UI 又丢一次）
# ═══════════════════════════════════════════════════════════

FRONTEND_API_TYPES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "types" / "api.ts"
FRONTEND_PSCH_PAGE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "PsychProfilePage.tsx"


def _ts_interface_fields(name: str) -> set[str]:
    """从 api.ts 抠出某个 interface 的字段名，并展开单层 extends。"""
    src = FRONTEND_API_TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name}[^{{]*\{{\n(.*?)\n\}}", src, re.S)
    assert match, f"frontend/src/types/api.ts 缺少 interface {name}"
    fields: set[str] = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        for token in line.split(";"):
            declared = re.match(r"^([A-Za-z_][\w]*)\??\s*:", token.strip())
            if declared:
                fields.add(declared.group(1))
    parent = re.match(r"^[^{]*extends\s+([A-Za-z_][\w]*)", match.group(0))
    if parent:
        fields |= _ts_interface_fields(parent.group(1))
    return fields


def test_liwc_dataclass_and_frontend_type_are_field_identical():
    backend = set(LiwcProfile.__dataclass_fields__)
    frontend = _ts_interface_fields("LiwcProfile")
    assert backend - frontend == set(), f"后端有、前端类型漏了：{sorted(backend - frontend)}"
    assert frontend - backend == set(), f"前端类型残留后端已不产出的字段：{sorted(frontend - backend)}"


def test_mental_health_payload_fields_are_declared_in_frontend_types():
    snap = MentalHealthScreener().quick_screen(ANXIOUS_TEXT).to_dict()
    assert set(snap) <= _ts_interface_fields("MentalHealthSnapshot"), (
        f"snapshot 键未被前端类型覆盖：{sorted(set(snap) - _ts_interface_fields('MentalHealthSnapshot'))}"
    )
    for key, interface in (("depression", "DepressionIndicators"), ("anxiety", "AnxietyIndicators")):
        declared = _ts_interface_fields(interface)
        missing = sorted(set(snap[key]) - declared)
        assert not missing, f"{key} 载荷字段在前端 {interface} 中缺失：{missing}"


def test_psych_page_does_not_display_instrument_denominators():
    """页面源码级护栏：0-1 的关键词信号不得再套临床量表分母。"""
    src = FRONTEND_PSCH_PAGE.read_text(encoding="utf-8")
    for banned in ("PHQ", "GAD", "DSM", "/ 27", "/ 21"):
        assert banned not in src, f"心理画像页仍在显示临床量表口径：{banned}"
    assert "命中维度" in src and "caveat" in src, "页面未显示后端自报的命中口径与免责"
