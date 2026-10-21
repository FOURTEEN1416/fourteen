"""黄金集 runner（R6 2026-10-21）—— 确定性判定优先，判定逻辑全部委托被测真源。

设计纪律（评审要求）：
1. **黄金集不实现第二份判定逻辑**：守卫类断言一律调用公共入口
   `utils.prompt_sanitize.terminal_reply_guard()` 并只比对输入/输出，
   不断言其内部实现（守卫改版时只有行为变了才会红）。
2. **默认纯确定性**：pytest 基线只跑确定性用例；`live_llm: true` 的用例
   默认 skip（`GOLDEN_LIVE=1` 开启），LLM 判定永远不计入基线。
3. **来源可追**：每条用例必须带 `source`（生产取证 / 文档 / 对照来源），
   `test_golden_set.test_every_case_declares_supporting_evidence` 钉死。

CLI 用法::

    python tests/golden/runner.py
    python tests/golden/runner.py --report docs/verification/golden-latest.md
    python tests/golden/runner.py --live --provider agnes   # 需真实供应商

退出码：0 = 全绿；1 = 有失败；2 = 用例集为空/加载错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 仓库根（`tests/golden/runner.py` → 上两级）。直接 `python tests/golden/runner.py`
#: 时 `sys.path[0]` 是 `tests/golden`，不注入根目录就 import 不到 `utils`/`orchestrator`
#: （pytest 有 `pythonpath=["."]`，CLI 没有）。
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: 允许出现在用例 `input` 里的**公共**守卫参数（其余键一律报错，防绑死私有实现）。
PUBLIC_GUARD_KWARGS = frozenset({
    "user_msg",
    "character_name",
    "session_key",
    "character_id",
    "last_turn_age_seconds",
    "reply_mode",
})

#: 用例 `input` 里的 runner 专用键（不传给守卫）。
RUNNER_ONLY_INPUT_KEYS = frozenset({
    "history",
    "now",
    "character_id_from_name",
    "requires_character_card",
    "knowledge_context",
})

#: 断言类型白名单（新增类型必须同时在本文件实现 `_check_*`）。
ASSERTION_TYPES: tuple[str, ...] = (
    "strip_verbatim_echo",
    "no_meta_memory",
    "no_attribution_flip",
    "no_ungrounded_reciprocal",
    "no_stage_direction",
    "time_recalibrated",
    "no_system_marker",
    "guard_passthrough",
    "guard_empty",
    "non_empty",
    "guard_stable",
    "vocative_stripped",
    "topic_anchored",
    "custom_regex_forbid",
    "custom_regex_require",
    "hard_violation_stripped",
    "paragraph_breaks_preserved",
    "prompt_regex_forbid",
    "proactive_template_forbid",
    "gateway_log_present",
    "knowledge_context_stripped",
    "extra_error_sentinel",
)


@dataclass
class Case:
    case_id: str
    category: str
    source: str
    input: dict[str, Any]
    candidate_reply: str
    expect: dict[str, Any] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    llm_judge: dict[str, Any] | None = None
    live_llm: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    case_id: str
    passed: bool
    detail: str
    skipped: bool = False
    #: LLM 判定（`judges`）给出 fail —— **不计入退出码/基线**（judges 纪律：
    #: 判不了或低置信一律 borderline，且 LLM 判定永远不进基线），仅留痕。
    judge_failed: bool = False


# ── 加载 ────────────────────────────────────────────────────


def _case_from_dict(data: dict[str, Any], origin: str) -> Case:
    try:
        case_id = str(data["id"])
    except KeyError as exc:  # pragma: no cover - 数据错误
        raise ValueError(f"{origin}: 用例缺 id") from exc
    return Case(
        case_id=case_id,
        category=str(data.get("category") or ""),
        source=str(data.get("source") or ""),
        input=dict(data.get("input") or {}),
        candidate_reply=str(data.get("candidate_reply") or ""),
        expect=dict(data.get("expect") or {}),
        assertions=list(data.get("assertions") or []),
        llm_judge=data.get("llm_judge"),
        live_llm=bool(data.get("live_llm")),
        raw=data,
    )


def load_cases_file(path: Path) -> list[Case]:
    """读一个 JSONL 文件（每行一例；空行与 `#` 注释行忽略）。"""
    cases: list[Case] = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{lineno} JSON 解析失败: {exc}") from exc
        cases.append(_case_from_dict(data, f"{path.name}:{lineno}"))
    return cases


def load_cases(cases_dir: Path) -> list[Case]:
    """按文件名排序加载目录下全部 JSONL（顺序稳定，便于报告 diff）。"""
    out: list[Case] = []
    for path in sorted(Path(cases_dir).glob("*.jsonl")):
        out.extend(load_cases_file(path))
    return out


# ── 前置条件 ────────────────────────────────────────────────


_CARD_CACHE: dict[str, str] | None = None


def _character_cards() -> dict[str, str]:
    """``角色名 → character_id``（`config/characters/` 受 gitignore，环境相关）。"""
    global _CARD_CACHE
    if _CARD_CACHE is not None:
        return _CARD_CACHE
    mapping: dict[str, str] = {}
    root = Path(__file__).resolve().parent.parent.parent / "config" / "characters"
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        name = str(data.get("name") or "").strip()
        cid = str(data.get("id") or path.stem).strip()
        if name and cid:
            mapping.setdefault(name, cid)
    _CARD_CACHE = mapping
    return mapping


def resolve_character_id(case_input: dict[str, Any]) -> str | None:
    """解析用例声明的 character_id；`character_id_from_name` 找不到时返回 None。"""
    by_name = str(case_input.get("character_id_from_name") or "").strip()
    if by_name:
        return _character_cards().get(by_name)
    return str(case_input.get("character_id") or "")


def missing_requirements(case: Case) -> str:
    """返回 skip 原因；空串表示可跑（卡缺失不得让整批红）。"""
    if case.input.get("requires_character_card") and not resolve_character_id(case.input):
        return (
            "所依赖角色卡不在位（config/characters 受 gitignore，基线隐藏变量）"
        )
    return ""


def guard_kwargs_are_public(assertion: dict[str, Any]) -> bool:
    """保留给外部调用方的兼容判定（断言对象不携带 input，恒为 True）。"""
    return True


def validate_input_keys(case_input: dict[str, Any]) -> str:
    """返回非法 input 键（空串=合法）。"""
    extra = set(case_input) - PUBLIC_GUARD_KWARGS - RUNNER_ONLY_INPUT_KEYS
    return ", ".join(sorted(extra))


# ── 判定 ────────────────────────────────────────────────────


def _guard_kwargs(case: Case) -> dict[str, Any]:
    cid = resolve_character_id(case.input)
    kwargs: dict[str, Any] = {}
    for key in PUBLIC_GUARD_KWARGS:
        if key not in case.input:
            continue
        kwargs[key] = case.input[key]
    if cid:
        # `character_id_from_name` 解析出的真实 id 必须写进守卫实参——否则
        # 称呼派生集拿不到角色名，非法称呼用例会假绿（GS-GUARD-007 的初版即此坑）。
        kwargs["character_id"] = cid
    return kwargs


def _parse_now(value: Any) -> Any:
    """`now` 允许 ISO 字符串（JSON 里不能写 datetime），解析为 naive 本地时刻。"""
    if value is None or not isinstance(value, str):
        return value
    from datetime import datetime

    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def run_guard(case: Case) -> str:
    from utils.prompt_sanitize import terminal_reply_guard

    return terminal_reply_guard(case.candidate_reply, **_guard_kwargs(case))


def _check_expect(case: Case, guard_out: str) -> list[str]:
    """校验 `expect` 块（真值约束与断言类型解耦，报告里可直接看期望）。"""
    failures: list[str] = []
    expect = case.expect or {}
    if "guard_equals" in expect and guard_out != expect["guard_equals"]:
        failures.append(f"guard 输出不符：期望 {expect['guard_equals']!r}，实得 {guard_out!r}")
    for needle in expect.get("guard_not_contains") or []:
        if needle and needle in guard_out:
            failures.append(f"输出仍含禁止片段 {needle!r}: {guard_out!r}")
    for needle in expect.get("guard_contains") or []:
        if needle and needle not in guard_out:
            failures.append(f"输出缺少期望片段 {needle!r}: {guard_out!r}")
    if expect.get("guard_empty") and guard_out != "":
        failures.append(f"期望守卫输出空串，实得 {guard_out!r}")
    if expect.get("guard_non_empty") and not guard_out:
        failures.append("期望守卫输出非空，实得空串")
    for pattern in expect.get("forbidden_patterns") or []:
        if pattern and re.search(pattern, guard_out):
            failures.append(f"输出命中禁止正则 {pattern!r}: {guard_out!r}")
    for needle in expect.get("anchors_contain") or []:
        if needle not in (expect.get("_anchors") or []):
            failures.append(f"锚点未包含 {needle!r}")
    return failures


def _anchors_for(case: Case) -> list[str]:
    from utils.prompt_sanitize import _extract_user_anchor_sentences

    now = _parse_now(case.input.get("now"))
    return _extract_user_anchor_sentences(
        case.input.get("history") or [],
        current_user_message=str(case.input.get("user_msg") or ""),
        now=now,
    )


def _module_source(module: Any) -> str:
    import inspect

    return inspect.getsource(module)


# ── live 判定链路（可选，默认关闭）──────────────────────────


def _load_judges() -> Any:
    """按**文件路径**导入同目录的 `judges` 模块。

    🔴 回归（独立复验 P1 命中，2026-10-22 修复）：旧实现写
    `from tests.golden import judges` —— 而 `tests/` **没有** `__init__.py`，
    且本机 site-packages 存在**同名常规包** `tests`
    （`…\\site-packages\\tests\\__init__.py`），常规包优先于本仓的隐式命名空间包
    ⇒ 恒 `ModuleNotFoundError: No module named 'tests.golden'`。彼时这意味着
    README「③ 在线判定」声明的两条路径（`GOLDEN_LIVE=1 pytest` / `--live`）
    **从未跑通**，且「LLM 判定」那一半能力整体不可达。

    故与 `test_golden_set.py` 同纪律：按文件路径加载（`importlib.util`），
    完全不依赖 `sys.path` 上是否存在 `tests` 包。
    """
    import importlib.util

    name = "golden_judges"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "judges.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - 环境损坏
        raise ImportError(f"无法加载 judges 模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _live_judge_llm() -> Any | None:
    """live 判定用的 LLM；未指定供应商 / 构造失败 → None（判不了 ⇒ borderline）。

    供应商来自 `GOLDEN_PROVIDER`（CLI `--live --provider agnes` 会写该环境变量）。
    **只有显式 live 才会走这里**：pytest 基线永不触发，故不会发网络请求。
    """
    provider = os.environ.get("GOLDEN_PROVIDER", "").strip()
    if not provider:
        return None
    try:
        from llm_provider import get_llm

        return get_llm(provider)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[golden] live 判定 LLM 构造失败（按 borderline 处理）："
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


# ── 各类断言 ────────────────────────────────────────────────


def _check_guard_changed(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    if a.get("must_trigger") and guard_out == case.candidate_reply:
        return [f"守卫未触发（输出与候选原文相同）：{guard_out!r}"]
    return []


def _check_no_semantic(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """no_meta_memory / no_attribution_flip / no_ungrounded_reciprocal /
    no_stage_direction / no_system_marker / strip_verbatim_echo 共用骨架：

    判据 = 「expect 声明的禁止片段不再出现」+（若给了 guard_equals）精确比对。
    真实判定由 `terminal_reply_guard` 完成，黄金集只做输入/输出比对。
    """
    out = _check_expect(case, guard_out)
    if a.get("must_trigger") and guard_out == case.candidate_reply:
        out.append("守卫未触发（输出与候选原文相同）")
    return out


def _check_time_recalibrated(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    failures = _check_expect(case, guard_out)
    for bad in a.get("forbid_words") or []:
        if bad in guard_out:
            failures.append(f"时间词未降级，仍含 {bad!r}: {guard_out!r}")
    for good in a.get("require_words") or []:
        if good not in guard_out:
            failures.append(f"缺少降级后措辞 {good!r}: {guard_out!r}")
    return failures


def _check_passthrough(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    if guard_out != case.candidate_reply:
        return [f"好回复被改写：{case.candidate_reply!r} → {guard_out!r}"]
    return []


def _check_guard_empty(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    return [] if guard_out == "" else [f"期望空串（fail-closed），实得 {guard_out!r}"]


def _check_non_empty(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    return [] if guard_out else ["守卫输出为空"]


def _check_guard_stable(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """幂等：守卫输出再过一次守卫不得再变（防「剥一次漏一半」）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    again = terminal_reply_guard(guard_out, **_guard_kwargs(case))
    if again != guard_out:
        return [f"守卫非幂等：{guard_out!r} → {again!r}"]
    return []


def _check_vocative(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    failures = _check_expect(case, guard_out)
    for token in a.get("stripped_tokens") or []:
        if guard_out.startswith(token):
            failures.append(f"行首非法称呼未剥: {guard_out!r}")
    return failures


def _check_topic_anchored(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    anchors = _anchors_for(case)
    failures: list[str] = []
    for needle in a.get("must_contain") or []:
        if needle not in anchors:
            failures.append(f"锚点未包含 {needle!r}（实得 {anchors}）")
    for needle in a.get("must_not_contain") or []:
        if needle in anchors:
            failures.append(f"锚点不应包含 {needle!r}（实得 {anchors}）")
    for pattern in a.get("must_not_match") or []:
        if any(re.search(pattern, x) for x in anchors):
            failures.append(f"锚点命中排除正则 {pattern!r}（实得 {anchors}）")
    if a.get("must_be_empty") and anchors:
        failures.append(f"期望空锚点，实得 {anchors}")
    if a.get("must_be_non_empty") and not anchors:
        failures.append("期望非空锚点，实得空")
    return failures


def _check_regex(case: Case, guard_out: str, a: dict[str, Any], *, forbid: bool) -> list[str]:
    pattern = str(a.get("pattern") or "")
    if not pattern:
        return ["断言缺 pattern"]
    hit = bool(re.search(pattern, guard_out))
    if forbid and hit:
        return [f"输出命中禁止正则 {pattern!r}: {guard_out!r}"]
    if not forbid and not hit:
        return [f"输出未命中必需正则 {pattern!r}: {guard_out!r}"]
    return []


def _check_hard_violation(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """自称 AI 的句级改写（与 `_finalize_reply` 同一条链：detect → light_sanitize）。"""
    from my_character.consistency_checker import (
        detect_hard_violation,
        light_sanitize_hard_violation,
    )

    text = guard_out
    if detect_hard_violation(text):
        text = light_sanitize_hard_violation(text)
    if detect_hard_violation(text):
        return [f"硬违规未被清除: {text!r}"]
    for marker in a.get("forbid_markers") or []:
        if marker in text:
            return [f"改写后仍含 {marker!r}: {text!r}"]
    return []


def _check_paragraphs(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """段落结构不得被守卫**整段吞掉**。

    实测事实（golden 初版抓出并登记）：immersive 下 `strip_stage_directions`
    会 `splitlines()` 重组，**连续空行被压成单个换行**
    （``"A\\n\\nB"`` → ``"A\\nB"``）。这是既有行为、不在本任务白名单内，故本检查
    按真实语义断言：① 每个非空段落内容都还在；② 换行分隔数量不减少。

    设计原稿 GS-AIYU-004 写的是「>3 段时强制分段符」——本仓库**没有**这条生成侧
    硬规则（对照爱语 idx 21 属 prompt 增强），README 已登记该偏差。
    """
    failures: list[str] = []
    for line in case.candidate_reply.splitlines():
        text = line.strip()
        if text and text not in guard_out:
            failures.append(f"段落内容丢失: {text!r}")
    raw_lines = len([ln for ln in case.candidate_reply.splitlines() if ln.strip()])
    out_lines = len([ln for ln in guard_out.splitlines() if ln.strip()])
    if out_lines < raw_lines:
        failures.append(f"段落数被压缩：{raw_lines} → {out_lines}")
    return failures


def _check_prompt_forbid(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """静态扫描 prompt 常量（防已删激励措辞回潮；v1.54 R2 的负例锚）。"""
    target = str(a.get("target") or "")
    patterns = [str(p) for p in (a.get("patterns") or [])]
    if target == "immersive_instruction":
        from utils.reply_mode import _INSTRUCTION_IMMERSIVE

        text = _INSTRUCTION_IMMERSIVE
    elif target == "novel_instruction":
        from utils.reply_mode import _INSTRUCTION_NOVEL

        text = _INSTRUCTION_NOVEL
    elif target == "memory_usage_rule":
        from utils.prompt_sanitize import MEMORY_USAGE_RULE

        text = MEMORY_USAGE_RULE
    elif target == "capability_declaration":
        from shisi.application.persona_service import capability_block

        text = capability_block(True)
    else:
        return [f"未知扫描目标 {target!r}"]
    failures: list[str] = []
    for pattern in patterns:
        if re.search(pattern, text):
            failures.append(f"{target} 仍含禁止措辞 {pattern!r}")
    return failures


def _check_proactive_templates(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """主动模板池不得残留打卡式嘱咐（v1.51 F1 负例锚）。"""
    from proactive.ase_engine import PROACTIVE_MESSAGES

    pool_key = str(a.get("pool") or "care_meal")
    pools = PROACTIVE_MESSAGES.get(pool_key) or {}
    lines = [line for group in pools.values() for line in group]
    if not lines:
        return [f"模板池 {pool_key!r} 为空（对照失效）"]
    failures: list[str] = []
    for pattern in a.get("patterns") or []:
        for line in lines:
            if re.search(str(pattern), line):
                failures.append(f"模板池 {pool_key!r} 残留 {pattern!r}: {line!r}")
    return failures


def _check_gateway_log(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """降级必须可见：网关源码里必须有结构化 `quality_degraded` 事件。"""
    from llm_provider import multi_provider_gateway as gw

    src = _module_source(gw)
    missing = [t for t in (a.get("must_contain") or []) if t not in src]
    return [f"网关缺少结构化事件 {t!r}（降级不可见）" for t in missing]


def _check_knowledge_context(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """注入侧模式边界（知识库第二通道）：immersive 剥括号 / novel 保留。

    被测真源 = `CharacterAggregate.build_system_prompt` 的 knowledge_context 渲染
    （`shisi/core/models/character_aggregate.py`，v1.54 R2 修复点）——**不是**出站守卫。

    模式取值：该函数内部 `from utils.reply_mode import read_reply_mode` **在调用时**
    取模块属性，故此处临时替换 `utils.reply_mode.read_reply_mode` 即可（try/finally
    还原）。**不调用 `write_reply_mode`** —— 那会写真 `data/scheduler_config.json`
    （运行时真源，测试写真实文件正是 2026-09-22 自伤事故同型）。
    """
    from shisi.core.models.character_aggregate import CharacterAggregate
    from utils import reply_mode as rm
    from utils.reply_mode import REPLY_MODE_IMMERSIVE, REPLY_MODE_NOVEL

    mode = str(case.input.get("reply_mode") or REPLY_MODE_IMMERSIVE)
    if mode not in (REPLY_MODE_IMMERSIVE, REPLY_MODE_NOVEL):
        return [f"未知 reply_mode {mode!r}"]

    agg = CharacterAggregate(
        id="golden-kg",
        name="测试角色",
        description="她是测试角色。",
        mes_example="用户：加班到这么晚\n测试角色：又是这个时候",
    )
    original = rm.read_reply_mode
    rm.read_reply_mode = lambda: mode  # type: ignore[assignment]
    try:
        prompt = agg.build_system_prompt(
            knowledge_context=str(case.input.get("knowledge_context") or ""),
        )
    finally:
        rm.read_reply_mode = original  # type: ignore[assignment]

    failures: list[str] = []
    for needle in a.get("not_contains") or []:
        if needle in prompt:
            failures.append(f"{mode} 下注入侧仍含 {needle!r}")
    for needle in a.get("contains") or []:
        if needle not in prompt:
            failures.append(f"{mode} 下注入侧缺少 {needle!r}")
    return failures


def _check_extra_error_sentinel(case: Case, guard_out: str, a: dict[str, Any]) -> list[str]:
    """空链哨兵也必须是 fail-closed（`_EXTRA_ERROR_SENTINELS` 覆盖的形态）。"""
    from utils.prompt_sanitize import _EXTRA_ERROR_SENTINELS, terminal_reply_guard

    failures: list[str] = []
    for sentinel in _EXTRA_ERROR_SENTINELS:
        text = f"（{sentinel}）"
        if terminal_reply_guard(text, user_msg="在吗") != "":
            failures.append(f"哨兵 {text!r} 未被判死")
    return failures


_CHECKERS: dict[str, Callable[[Case, str, dict[str, Any]], list[str]]] = {
    "strip_verbatim_echo": _check_no_semantic,
    "no_meta_memory": _check_no_semantic,
    "no_attribution_flip": _check_no_semantic,
    "no_ungrounded_reciprocal": _check_no_semantic,
    "no_stage_direction": _check_no_semantic,
    "no_system_marker": _check_no_semantic,
    "time_recalibrated": _check_time_recalibrated,
    "guard_passthrough": _check_passthrough,
    "guard_empty": _check_guard_empty,
    "non_empty": _check_non_empty,
    "guard_stable": _check_guard_stable,
    "vocative_stripped": _check_vocative,
    "topic_anchored": _check_topic_anchored,
    "custom_regex_forbid": lambda c, o, a: _check_regex(c, o, a, forbid=True),
    "custom_regex_require": lambda c, o, a: _check_regex(c, o, a, forbid=False),
    "hard_violation_stripped": _check_hard_violation,
    "paragraph_breaks_preserved": _check_paragraphs,
    "prompt_regex_forbid": _check_prompt_forbid,
    "proactive_template_forbid": _check_proactive_templates,
    "gateway_log_present": _check_gateway_log,
    "knowledge_context_stripped": _check_knowledge_context,
    "extra_error_sentinel": _check_extra_error_sentinel,
}


def _apply_post_guard_transforms(case: Case, guard_out: str) -> str:
    """复刻 `_finalize_reply` 里**守卫之后**的那道链（目前只有硬违规改写）。

    顺序真源：`orchestrator/optimized_orchestrator.py::_finalize_reply` ——
    `terminal_reply_guard` → 称呼剥除 → `sanitize_reply_text` → 硬违规轻量改写 →
    `check_output`。黄金集只复刻到「硬违规」这一层（安全闸需要完整 safety 组件，
    不在本任务的确定性范围）。

    只在用例声明了 `hard_violation_stripped` 时才应用 —— 否则会改变其它用例的
    判定面（例如刻意断言「守卫原样返回」的缺口用例）。
    """
    if not any(a.get("type") == "hard_violation_stripped" for a in case.assertions):
        return guard_out
    from my_character.consistency_checker import (
        detect_hard_violation,
        light_sanitize_hard_violation,
    )

    text = guard_out
    if detect_hard_violation(text):
        text = light_sanitize_hard_violation(text)
    return text


def evaluate(case: Case, *, live: bool = False) -> Result:
    """跑一条用例（确定性部分恒跑；live 部分仅在 live=True 时追加判定）。"""
    invalid = validate_input_keys(case.input)
    if invalid:
        return Result(case.case_id, False, f"input 含非公共参数：{invalid}")
    unknown = [a.get("type") for a in case.assertions if a.get("type") not in _CHECKERS]
    if unknown:
        return Result(case.case_id, False, f"未知断言类型：{unknown}")

    try:
        guard_out = run_guard(case)
        final_out = _apply_post_guard_transforms(case, guard_out)
    except Exception as exc:  # noqa: BLE001
        return Result(case.case_id, False, f"守卫执行异常：{type(exc).__name__}: {exc}")

    failures: list[str] = []
    failures.extend(_check_expect(case, final_out))
    for a in case.assertions:
        try:
            failures.extend(_CHECKERS[str(a["type"])](case, final_out, a))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"断言 {a.get('type')} 执行异常：{type(exc).__name__}: {exc}")
    # 去重保序（不同断言可能报同一条）
    seen: set[str] = set()
    uniq = [f for f in failures if not (f in seen or seen.add(f))]

    detail = "ok" if not uniq else " | ".join(uniq)
    if uniq:
        return Result(case.case_id, False, detail)

    if live and case.llm_judge:
        try:
            judges = _load_judges()
            verdict = judges.judge(case, final_out, llm=_live_judge_llm())
        except Exception as exc:  # noqa: BLE001
            # 判定器不可用**不得**把黄金集打成红（judges 纪律第 4 条），但必须可见。
            return Result(
                case.case_id,
                True,
                f"llm_judge 不可用（{type(exc).__name__}: {exc}）",
                judge_failed=True,
            )
        if not verdict.passed:
            # LLM 判定不计基线：passed 保持 True，只用 judge_failed 留痕（CLI 报告
            # 与 pytest 的 xfail 读这个字段）。
            return Result(
                case.case_id, True, f"llm_judge={verdict.detail}", judge_failed=True
            )
        detail = f"ok (llm_judge={verdict.detail})"
    return Result(case.case_id, True, detail)


# ── 报告与 CLI ──────────────────────────────────────────────


def _format_report(results: list[Result], cases: dict[str, Case]) -> str:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    judged = [r for r in results if r.judge_failed]
    width = max((len(r.case_id) for r in results), default=8)
    lines = [
        "# 黄金集报告（R6 2026-10-21）",
        "",
        f"- 用例总数：**{total}**",
        f"- 确定性通过：**{passed}**",
        f"- 失败：**{total - passed}**",
        f"- LLM 判定失败（不计退出码）：**{len(judged)}**",
        "",
        "| case_id | 类别 | 结果 | 明细 |",
        "|---|---|---|---|",
    ]
    for r in results:
        case = cases.get(r.case_id)
        category = case.category if case else ""
        status = "FAIL" if not r.passed else ("JUDGE-FAIL" if r.judge_failed else "PASS")
        lines.append(f"| {r.case_id:<{width}} | {category} | {status} | {r.detail} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金集 runner（默认纯确定性）")
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).resolve().parent / "cases"),
        help="用例目录（默认 tests/golden/cases）",
    )
    parser.add_argument("--report", default="", help="把 Markdown 报告写到该路径")
    parser.add_argument("--live", action="store_true", help="启用 live_llm 用例与 LLM 判定")
    parser.add_argument("--provider", default="", help="live 模式指定供应商（可选）")
    parser.add_argument("--only", default="", help="只跑 id 前缀匹配的用例（如 GS-ECHO）")
    args = parser.parse_args(argv)

    cases_dir = Path(args.cases)
    if not cases_dir.exists():
        print(f"用例目录不存在: {cases_dir}", file=sys.stderr)
        return 2
    try:
        cases = load_cases(cases_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not cases:
        print("用例集为空", file=sys.stderr)
        return 2
    if args.only:
        cases = [c for c in cases if c.case_id.startswith(args.only)]
    if args.live and args.provider:
        os.environ.setdefault("GOLDEN_PROVIDER", args.provider)

    results: list[Result] = []
    for case in cases:
        missing = missing_requirements(case)
        if missing:
            results.append(Result(case.case_id, True, f"skip: {missing}", skipped=True))
            continue
        if case.live_llm and not args.live:
            results.append(
                Result(case.case_id, True, "skip: live_llm（设 --live 开启）", skipped=True)
            )
            continue
        results.append(evaluate(case, live=args.live))

    by_id = {c.case_id: c for c in cases}
    report = _format_report(results, by_id)
    print(report)
    judged_failed = [r for r in results if r.judge_failed]
    if judged_failed:
        # LLM 判定是**审计留痕**不是门禁：显式打印，但不改退出码（否则 live 通道
        # 会因既有缺口恒红 → 没人跑 → 再次腐烂；这正是 2026-10-22 复验命中的教训）。
        print(
            f"[golden] LLM 判定失败 {len(judged_failed)} 例（不计退出码）："
            + ", ".join(r.case_id for r in judged_failed)
        )
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"报告已写入 {out}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
