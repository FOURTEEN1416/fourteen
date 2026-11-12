"""commitment_gate 冻结规格 —— B-2 红测（12 组用例）。

冻结规格由主控定稿（2026-11-11），本文件只做**规格忠实性**断言：

1. 词表/阈值逐字相等（``test_frozen_tables_are_verbatim``）—— 防止后续批次
   偷偷扩词（weak-term / 同义词 / 后缀拼接）改变判定面；
2. 文件顶部 import 只允许两条（AST 断言）—— gate 必须是纯函数模块，不连库、
   不读环境、不 import orchestrator；
3. ``core_terms`` 的每个结果长度 >= 2（「原子词」契约）：1 字词会让
   ``references_commitment`` 大面积误命中；
4. 空输入 / 疑问句 / 完成 / 否定 / 进行中 五个负面边界。

``should_extract`` 的判定顺序也是规格的一部分：疑问句 → 自我标记 → 逐句排除
完成/否定/进行 → 承诺动词 → 词项非空，任一步失败即 False。
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent_core import commitment_gate as gate
from utils.local_time import now_local

# ---------------------------------------------------------------------------
# 1. 冻结常量（逐字）
# ---------------------------------------------------------------------------


def test_frozen_tables_are_verbatim():
    assert gate.MIN_TERM_LEN == 2
    assert gate.COMMITMENT_TTL_HOURS == 168
    assert gate.COMPLETION_MARKERS == (
        "了", "过", "已经", "已发", "已投递", "已送达", "已整理", "好了", "弄完",
        "做完", "搞定", "完成", "记好了", "查好了", "看好了", "发过", "给过",
        "刚才", "刚刚",
    )
    assert gate.NEGATION_MARKERS == ("不", "别", "没", "没有", "尚未")
    assert gate.ONGOING_MARKERS == ("还在弄", "仍在", "正在", "还在整")
    assert gate.RECEIPT_MARKERS == ("收到", "拿到了", "看到了", "谢谢", "好的", "已读", "用上了", "看了")
    assert gate.PROMISE_VERBS == (
        "发", "给你", "帮你", "整理", "查", "写", "找", "标", "传", "准备", "弄", "安排",
    )
    assert gate.SELF_MARKERS == ("我", "帮你", "给你", "发你", "发给你", "传你")
    assert gate.STOP_TOKENS == (
        "我们", "你们", "我", "你", "他", "她", "它", "帮你", "给你", "这个", "那个",
        "一下", "的", "了", "过", "已", "刚", "刚才", "好了", "就", "都", "也", "还",
        "在", "和", "把", "是", "吧", "呢",
    )
    assert frozenset(
        (
            "我帮", "帮你", "你的", "我的", "这个", "那个", "就是", "还是", "已经",
            "刚才", "不过", "然后", "因为", "所以", "如果", "一下",
        )
    ) == gate.GENERIC_BIGRAMS
    assert gate.SENTENCE_DELIMS == "。！？!?；;\n，,"


def test_module_has_exactly_two_imports():
    """gate 是纯函数模块：只允许 datetime.timedelta 与 now_local 两条 import。"""
    source = Path(gate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 2, [ast.dump(n) for n in imports]
    first, second = imports
    assert isinstance(first, ast.ImportFrom) and first.module == "datetime"
    assert [a.name for a in first.names] == ["timedelta"]
    assert isinstance(second, ast.ImportFrom) and second.module == "utils.local_time"
    assert [a.name for a in second.names] == ["now_local"]


# ---------------------------------------------------------------------------
# 2. split_sentences
# ---------------------------------------------------------------------------


def test_split_sentences_cjk_and_ascii_delims():
    assert gate.split_sentences("我帮你整理报告。明天发给你！") == ["我帮你整理报告", "明天发给你"]
    assert gate.split_sentences("a?b!c;d,e") == ["a", "b", "c", "d", "e"]
    assert gate.split_sentences("第一句；第二句，第三句") == ["第一句", "第二句", "第三句"]


def test_split_sentences_newline_is_boundary_and_blanks_dropped():
    assert gate.split_sentences("第一行\n第二行\n\n第三行") == ["第一行", "第二行", "第三行"]
    assert gate.split_sentences("。。。") == []
    assert gate.split_sentences("") == []


def test_split_sentences_trailing_fragment_kept():
    assert gate.split_sentences("说完了。还有尾巴") == ["说完了", "还有尾巴"]


# ---------------------------------------------------------------------------
# 3. core_terms
# ---------------------------------------------------------------------------

CORE_TERM_CORPUS = [
    "我帮你整理一下体检报告",
    "明天发给你会议纪要",
    "查一下杭州天气",
    "帮你找那本书",
    "准备周五的汇报材料",
    "我帮你标一下重点",
    "传你原图",
    "安排下周末的安排",
    "mixed english 体检报告 and 会议纪要",
    "一",
    "啊",
]


@pytest.mark.parametrize("text", CORE_TERM_CORPUS)
def test_core_terms_every_term_is_at_least_two_chars(text):
    terms = gate.core_terms(text)
    assert isinstance(terms, list)
    for term in terms:
        assert len(term) >= gate.MIN_TERM_LEN, (text, terms)
        assert term not in gate.GENERIC_BIGRAMS
        assert "\n" not in term and " " not in term


def test_core_terms_dedupes_and_drops_generic_bigrams():
    terms = gate.core_terms("我帮你整理整理体检报告体检报告")
    assert terms == list(dict.fromkeys(terms))
    assert "整理" in terms and "体检" in terms and "报告" in terms
    assert not [t for t in terms if t in gate.GENERIC_BIGRAMS]


def test_core_terms_empty_and_single_char_inputs():
    assert gate.core_terms("") == []
    assert gate.core_terms("我") == []
    assert gate.core_terms("的了吧呢") == []
    assert gate.core_terms("！？，。") == []


def test_core_terms_sliding_bigram_for_long_run():
    # 「体检报告」→ 体检 / 检报 / 报告（滑窗 2-gram，不是分词）
    assert gate.core_terms("体检报告") == ["体检", "检报", "报告"]


# ---------------------------------------------------------------------------
# 4. should_extract
# ---------------------------------------------------------------------------


def test_should_extract_true_for_promise():
    assert gate.should_extract("我帮你整理一下体检报告，明天发给你") is True
    assert gate.should_extract("我帮你查一下天气") is True
    assert gate.should_extract("我给你准备汇报材料") is True


@pytest.mark.parametrize("text", ["", "   ", "\n\t ", None])
def test_should_extract_false_for_empty(text):
    assert gate.should_extract(text) is False


@pytest.mark.parametrize("text", ["我帮你查一下天气？", "我帮你整理报告?", "发给你了吗？"])
def test_should_extract_false_for_question(text):
    assert gate.should_extract(text) is False


def test_should_extract_requires_self_marker():
    assert gate.should_extract("明天要下雨") is False
    assert gate.should_extract("查一下杭州天气") is False  # 无「我/帮你/给你…」自我标记


def test_should_extract_rejects_completion_negation_ongoing_per_sentence():
    assert gate.should_extract("我帮你整理好了报告") is False  # 完成
    assert gate.should_extract("我帮你整理报告了") is False  # 单字「了」
    assert gate.should_extract("我还没帮你整理报告") is False  # 否定
    assert gate.should_extract("别帮你整理报告") is False  # 否定
    assert gate.should_extract("我正在帮你整理报告") is False  # 进行中
    assert gate.should_extract("我还在弄你的报告") is False  # 进行中


def test_should_extract_requires_promise_verb_and_terms():
    # 有自我标记但无承诺动词 → False
    assert gate.should_extract("我很难过") is False
    # 有承诺动词但词项被 STOP/GENERIC 全剥 → False
    assert gate.should_extract("我帮你的") is False


# ---------------------------------------------------------------------------
# 5. references_commitment / is_receipt / should_close
# ---------------------------------------------------------------------------


def test_references_commitment_hit_and_miss():
    detail = "我帮你整理体检报告"
    assert gate.references_commitment(detail, "体检报告整理好了") is True
    assert gate.references_commitment(detail, "今天天气不错") is False
    # 空 detail → 无词项 → 恒 False（不得退化成「空串在任意文本里」）
    assert gate.references_commitment("", "任何回复") is False
    assert gate.references_commitment("我", "任何回复") is False


def test_is_receipt_positive_and_negated():
    assert gate.is_receipt("收到啦，谢谢") is True
    assert gate.is_receipt("好的") is True
    assert gate.is_receipt("我没收到") is False  # 句内否定 → 该句不算回执
    assert gate.is_receipt("还没看到") is False
    assert gate.is_receipt("今天天气不错") is False


def test_should_close_requires_reference_then_receipt():
    detail = "我帮你整理体检报告"
    assert gate.should_close(detail, "今天天气不错") is False
    assert gate.should_close(detail, "体检报告我还在看") is False  # 引用但无回执
    assert gate.should_close(detail, "体检报告收到了") is True


def test_should_close_acted_on_shortcut():
    assert gate.should_close("任何承诺", "完全无关", delivered_payload="x", acted_on=True) is True
    # acted_on 但没交付物 → 不短路
    assert gate.should_close("我帮你整理体检报告", "完全无关", delivered_payload=None, acted_on=True) is False


def test_should_close_delivered_payload_requires_both_sides_reference():
    detail = "体检报告"
    assert gate.should_close(detail, "体检报告在这", delivered_payload="体检报告") is True
    assert gate.should_close(detail, "在这", delivered_payload="体检报告") is False


# ---------------------------------------------------------------------------
# 6. expires_at
# ---------------------------------------------------------------------------


def test_expires_at_exact_ttl():
    base = datetime(2026, 11, 11, 10, 0, 0)
    assert gate.expires_at(base) == base + timedelta(hours=168)
    assert gate.COMMITMENT_TTL_HOURS == 168


def test_expires_at_defaults_to_now_local():
    delta = gate.expires_at() - (now_local() + timedelta(hours=168))
    assert abs(delta.total_seconds()) < 5
