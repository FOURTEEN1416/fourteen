"""黄金集 pytest 入口（R6 2026-10-21）。

逐例参数化 `tests/golden/cases/*.jsonl`，**只跑确定性断言**；判定逻辑全部委托
被测真源（`utils.prompt_sanitize.terminal_reply_guard`、`utils.reply_mode.
strip_stage_directions`、`utils.prompt_sanitize._extract_user_anchor_sentences`、
`my_character.consistency_checker`），黄金集自身**不实现第二份判定**。

在线用例（`live_llm: true`，默认 skip，`GOLDEN_LIVE=1` 才跑）不进基线。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ⚠️ 不能写 `from tests.golden import runner`：本机 site-packages 里有一个
# **同名常规包** `tests`（`C:\Program Files\Python312\Lib\site-packages\tests\
# __init__.py`），常规包优先于本仓的隐式命名空间包 —— 实测
# `import tests.golden` 报 ModuleNotFoundError，而 `import tests` 解析到
# site-packages。`tests/` 也没有 `__init__.py`。故按**文件路径**导入同目录模块。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import runner  # noqa: E402  （同目录模块，路径注入见上）

CASES_DIR = _HERE / "cases"

_ALL_CASES = runner.load_cases(CASES_DIR)


def _ids(cases):
    return [c.case_id for c in cases]


@pytest.mark.parametrize("case", _ALL_CASES, ids=_ids(_ALL_CASES))
def test_golden_case(case):
    """一条黄金集用例：所有断言必须同时通过。

    live（`GOLDEN_LIVE=1`）时额外跑 LLM 判定；判定 fail **只 xfail 留痕、不进
    基线**（`judges` 纪律：判不了/低置信一律 borderline，LLM 判定永不 gate）。
    """
    live = os.environ.get("GOLDEN_LIVE") == "1"
    if case.live_llm and not live:
        pytest.skip("live_llm 用例默认不跑（设 GOLDEN_LIVE=1 开启，需真实供应商）")
    missing = runner.missing_requirements(case)
    if missing:
        pytest.skip(missing)
    result = runner.evaluate(case, live=live)
    assert result.passed, f"{case.case_id}: {result.detail}"
    if result.judge_failed:
        pytest.xfail(f"{case.case_id} llm_judge 不计基线：{result.detail}")


def test_case_files_exist_and_are_non_empty():
    """防「黄金集变空集」：8 个用例文件都必须存在且有条目。"""
    expected = {
        "echo_attribution.jsonl",
        "meta_memory.jsonl",
        "mode_boundary.jsonl",
        "ungrounded_reciprocal.jsonl",
        "topic_awareness.jsonl",
        "degradation_quality.jsonl",
        "guard_regression.jsonl",
        "aiyu_contrast.jsonl",
    }
    present = {p.name for p in CASES_DIR.glob("*.jsonl")}
    assert expected <= present, f"缺少用例文件: {sorted(expected - present)}"
    for p in CASES_DIR.glob("*.jsonl"):
        assert runner.load_cases_file(p), f"{p.name} 是空文件"


def test_case_ids_are_unique_and_well_formed():
    """id 唯一且形如 GS-XXX-###（runner 报告与失败定位都靠它）。"""
    import re

    ids = [c.case_id for c in _ALL_CASES]
    assert len(ids) == len(set(ids)), f"重复 id: {[i for i in ids if ids.count(i) > 1]}"
    for cid in ids:
        assert re.fullmatch(r"GS-[A-Z]+-\d{3}", cid), f"id 形态不合法: {cid}"


def test_every_case_declares_supporting_evidence():
    """每条用例必须写 `source`（生产取证/文档/对照来源）——无来源的断言不可维护。"""
    for case in _ALL_CASES:
        assert case.source and case.source.strip(), f"{case.case_id} 缺 source"


def test_no_case_asserts_private_internals_except_anchor_and_hard_violation():
    """断言类型白名单：只允许已知类型（新增类型必须同时在本 runner 实现）。"""
    allowed = set(runner.ASSERTION_TYPES)
    for case in _ALL_CASES:
        for a in case.assertions:
            assert a.get("type") in allowed, (
                f"{case.case_id} 使用了未实现的断言类型 {a.get('type')!r}"
            )


def test_guard_cases_use_public_entry_only():
    """守卫类断言只能经公共入口（`terminal_reply_guard`）——防黄金集绑死私有函数。

    实现方式：`input` 里除 runner 专用键外必须都是公共守卫参数；未知键直接报错
    （`runner.evaluate` 也会拒绝执行）。
    """
    for case in _ALL_CASES:
        invalid = runner.validate_input_keys(case.input)
        assert not invalid, f"{case.case_id} 的 input 含非公共参数：{invalid}"


# ── runner/judges 自身的自检（黄金集的「金丝雀」）─────────────


def test_runner_cli_runs_and_reports(tmp_path, capsys):
    """独立 CLI 必须能跑通并输出报告（部署后冒烟入口）。"""
    report = tmp_path / "golden.md"
    code = runner.main(["--report", str(report)])
    assert code == 0, "runner CLI 有失败用例"
    body = report.read_text(encoding="utf-8")
    assert "黄金集报告" in body and "PASS" in body


def test_runner_rejects_unknown_assertion_type(tmp_path):
    """未知断言类型必须报错，不得静默放过（否则「空断言」= 假绿）。"""
    path = tmp_path / "bad.jsonl"
    path.write_text(
        '{"id":"GS-BAD-001","category":"x","source":"s","input":{},"candidate_reply":"ok",'
        '"assertions":[{"type":"no_such_check"}]}\n',
        encoding="utf-8",
    )
    case = runner.load_cases_file(path)[0]
    result = runner.evaluate(case)
    assert not result.passed and "未知断言类型" in result.detail


def test_runner_rejects_non_public_input_keys(tmp_path):
    """`input` 里混入非公共守卫参数必须拒绝（防黄金集依赖私有实现）。"""
    path = tmp_path / "bad2.jsonl"
    path.write_text(
        '{"id":"GS-BAD-002","category":"x","source":"s",'
        '"input":{"some_private_kwarg":1},"candidate_reply":"ok","assertions":[]}\n',
        encoding="utf-8",
    )
    case = runner.load_cases_file(path)[0]
    result = runner.evaluate(case)
    assert not result.passed and "非公共参数" in result.detail


def test_runner_detects_real_failure(tmp_path):
    """关键：runner 必须能**真的判红**（防「永远 pass」的假黄金集）。"""
    path = tmp_path / "fail.jsonl"
    path.write_text(
        '{"id":"GS-FAIL-001","category":"x","source":"s",'
        '"input":{"user_msg":"今天好累"},"candidate_reply":"好的，注意保暖哦",'
        '"expect":{"guard_equals":"完全不同"},"assertions":[]}\n',
        encoding="utf-8",
    )
    case = runner.load_cases_file(path)[0]
    result = runner.evaluate(case)
    assert not result.passed, "runner 对错误的 guard_equals 仍报绿（判定逻辑失效）"


def test_runner_skips_when_character_card_missing(tmp_path):
    """卡缺失只 skip 该例，不得整批红（config/characters 受 gitignore）。"""
    path = tmp_path / "skip.jsonl"
    path.write_text(
        '{"id":"GS-SKIP-001","category":"x","source":"s",'
        '"input":{"requires_character_card":true,"character_id_from_name":"不存在的角色"},'
        '"candidate_reply":"ok","assertions":[]}\n',
        encoding="utf-8",
    )
    case = runner.load_cases_file(path)[0]
    assert runner.missing_requirements(case), "卡缺失未被识别为 skip 条件"


# ── judges：低置信一律 borderline，borderline 不计 fail ──────


def test_judge_low_confidence_never_fails():
    from judges import normalize

    assert normalize("fail", 0.2) == ("borderline", True)
    assert normalize("pass", 0.5) == ("borderline", True)
    assert normalize("fail", 0.9) == ("fail", False)
    assert normalize("pass", 0.9) == ("pass", True)


def test_judge_service_tone_is_deterministic_fail():
    from judges import SERVICE_TONE_MARKERS, judge

    class _Case:
        llm_judge = {"dimensions": ["warmth"]}
        input = {"user_msg": "我心情不好"}

    verdict = judge(_Case(), "好的，注意保暖哦")
    assert not verdict.passed and verdict.verdict == "fail"
    assert any(m in "好的，注意保暖哦" for m in SERVICE_TONE_MARKERS)


def test_judge_without_llm_is_borderline_not_fail():
    from judges import judge

    class _Case:
        llm_judge = {"dimensions": ["warmth"]}
        input = {"user_msg": "在吗"}

    verdict = judge(_Case(), "在的，怎么啦")
    assert verdict.passed and verdict.verdict == "borderline"


def test_judge_parse_tolerates_fenced_json():
    from judges import _parse_judge_output

    parsed = _parse_judge_output(
        '```json\n{"verdict": "pass", "confidence": 0.85, "evidence": "在的"}\n```'
    )
    assert parsed == {"verdict": "pass", "confidence": 0.85, "evidence": "在的"}
    assert _parse_judge_output("not json") is None
    assert _parse_judge_output('{"verdict": "maybe"}') is None


def test_judge_empty_reply_is_borderline():
    from judges import judge

    class _Case:
        llm_judge = {"dimensions": ["warmth"]}
        input = {"user_msg": "在吗"}

    verdict = judge(_Case(), "")
    assert verdict.passed and verdict.verdict == "borderline"


#: 不依赖 `candidate_reply` 的断言类型（判的是 prompt/模板池/网关/锚点/注入侧）。
_CANDIDATE_INDEPENDENT_TYPES = frozenset({
    "prompt_regex_forbid",
    "proactive_template_forbid",
    "gateway_log_present",
    "topic_anchored",
    "knowledge_context_stripped",
    "extra_error_sentinel",
    "non_empty",
    "guard_stable",
})


def test_guard_dependent_cases_really_fail_when_mutated():
    """突变验红：凡是判「守卫输出」的用例，换成一段无关文本必须判红。

    防「永远 pass」的假黄金集 —— 这条在初版抓出了 `_guard_kwargs` 漏把
    `character_id_from_name` 解析结果写进守卫实参（GS-GUARD-007 假绿）。
    """
    weak: list[str] = []
    for case in _ALL_CASES:
        if case.live_llm:
            continue
        types = {a.get("type") for a in case.assertions}
        if types and types <= _CANDIDATE_INDEPENDENT_TYPES:
            continue
        expect_keys = set(case.expect or {}) - {"guard_non_empty"}
        if not types and not expect_keys:
            continue
        original = case.candidate_reply
        try:
            case.candidate_reply = "啊，是这样啊。"
            result = runner.evaluate(case)
        finally:
            case.candidate_reply = original
        if result.passed:
            weak.append(case.case_id)
    assert not weak, f"这些用例换掉候选回复仍判绿（检查器空转）：{weak}"


def test_semantic_guard_cases_cover_all_five_rule_families():
    """覆盖面自检：v1.54 的五类语义缺陷 + 模式边界都必须有确定性用例。"""
    types = {a.get("type") for c in _ALL_CASES for a in c.assertions}
    for required in (
        "strip_verbatim_echo",       # 逐字复述
        "no_attribution_flip",       # 归属翻转
        "no_meta_memory",            # 元记忆解释
        "no_ungrounded_reciprocal",  # 虚构反问
        "no_stage_direction",        # 模式边界
    ):
        assert required in types, f"缺少 {required} 类用例"


# ── live 判定链路（回归 2026-10-22：整条 live 通道曾因导入恒崩）─────────


def test_judges_loads_without_tests_package():
    """`judges` 必须按**文件路径**加载，不得写 `from tests.golden import judges`。

    回归（2026-10-22 独立复验 P1）：本机 site-packages 有同名常规包 `tests`，
    旧写法恒 `ModuleNotFoundError: No module named 'tests.golden'` —— 于是
    `GOLDEN_LIVE=1 pytest` 与 `runner.py --live` 这两条**文档里声明可用**的路径
    从未跑通（GS-DEG-001/GS-AIYU-006/GS-MODE-005 三例全崩）。
    """
    module = runner._load_judges()
    assert Path(module.__file__).resolve() == (_HERE / "judges.py").resolve()
    assert module.normalize("pass", 0.9) == ("pass", True)


def test_live_judge_path_reaches_judges_with_stub_llm(monkeypatch):
    """live 判定必须真的走到 `judges`，且**不改变退出码**（LLM 判定不计基线）。"""
    case = next(c for c in _ALL_CASES if c.case_id == "GS-AIYU-006")

    class _LLM:
        def chat(self, **kwargs):
            return '{"verdict": "fail", "confidence": 0.95, "evidence": "是哪个环节"}'

    monkeypatch.setattr(runner, "_live_judge_llm", lambda: _LLM())
    result = runner.evaluate(case, live=True)
    assert result.passed, "LLM 判定不得进基线（judges 纪律：borderline/fail 都不算 gate）"
    assert result.judge_failed
    assert "fail" in result.detail and "是哪个环节" in result.detail


def test_live_deterministic_tone_probe_needs_no_llm():
    """GS-DEG-001 的客服腔判定是**确定性**旁证：无 LLM 也必须判出 fail 并留痕。

    该用例 `live_llm: true`，默认 skip；只有 live 通道可用时才会执行到这里。
    """
    case = next(c for c in _ALL_CASES if c.case_id == "GS-DEG-001" and c.live_llm)
    result = runner.evaluate(case, live=True)
    assert result.passed, "not 基线：客服腔缺口是既有事实，不得把 live 跑成全红"
    assert result.judge_failed
    assert "客服腔" in result.detail


def test_runner_cli_live_mode_runs_and_reports(capsys):
    """CLI `--live` 必须跑通：报告含判定结论，退出码仍为 0（判定不计基线）。"""
    code = runner.main(["--live", "--only", "GS-DEG-001"])
    assert code == 0, "LLM 判定失败不得改变退出码（否则 live 通道永远红、没人会跑）"
    out = capsys.readouterr().out
    assert "GS-DEG-001" in out
    assert "客服腔" in out
    assert "LLM 判定失败 1 例" in out


def test_live_judge_llm_never_goes_out_unless_provider_given(monkeypatch):
    """基线永不出网：`GOLDEN_PROVIDER` 未设 → 判定 LLM 为 None（borderline）。

    构造失败（供应商名无效/缺 key）同样只返回 None 且不抛 —— 判不了不算不合格。
    """
    monkeypatch.delenv("GOLDEN_PROVIDER", raising=False)
    assert runner._live_judge_llm() is None
    monkeypatch.setenv("GOLDEN_PROVIDER", "definitely_not_a_provider")
    assert runner._live_judge_llm() is None


def test_judge_async_llm_works_inside_running_loop():
    """判定器必须能在**运行中的事件循环**里调用异步网关（旧实现必抛 RuntimeError）。

    真源：`utils.async_utils.run_async`（AGENTS 指定的公共异步桥）。
    """
    import asyncio

    from judges import judge

    class _Case:
        llm_judge = {"dimensions": ["warmth"]}
        input = {"user_msg": "在吗"}

    class _LLM:
        async def chat(self, **kwargs):
            return '{"verdict": "pass", "confidence": 0.9, "evidence": "在的"}'

    async def _go():
        return judge(_Case(), "在的，怎么啦", llm=_LLM())

    verdict = asyncio.run(_go())
    assert verdict.passed and verdict.verdict == "pass", verdict.detail


def test_golden_set_catches_guard_regressions(monkeypatch):
    """突变验红（反向）：把守卫的某一规则停用，黄金集必须有用例变红。

    四个语义族各停用一次 —— 这证明黄金集测的是**行为**，不是常量文本。
    2026-10-21 实测命中：verbatim→3 例、meta→4 例、reciprocal→2 例、stage→3 例。
    """
    import utils.prompt_sanitize as ps

    def _failing_ids() -> list[str]:
        return [c.case_id for c in _ALL_CASES if not c.live_llm and not runner.evaluate(c).passed]

    baseline = {c.case_id for c in _ALL_CASES if not c.live_llm and runner.evaluate(c).passed}

    mutations = {
        "_strip_verbatim_echo": lambda t, u: t,
        "_strip_meta_memory_talk": lambda t: t,
        "_strip_ungrounded_reciprocal": lambda t, u: t,
        "_strip_immersive_stage_directions": lambda t, m: t,
    }
    for attr, replacement in mutations.items():
        original = getattr(ps, attr)
        monkeypatch.setattr(ps, attr, replacement)
        try:
            caught = baseline & set(_failing_ids())
        finally:
            monkeypatch.setattr(ps, attr, original)
        assert caught, f"停用 {attr} 后黄金集全绿（该规则未被覆盖）"
