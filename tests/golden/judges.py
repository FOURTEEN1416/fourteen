"""黄金集可选的 LLM 判定（默认关闭，**不计入 pytest 基线**）。

纪律（评审要求）：
1. 只有三类维度：`naturalness`（像不像真人微信）、`topic_continuity`（接不接住上一条）、
   `warmth`（有没有温度）；
2. 输出必须带 `verdict` / `confidence` / `evidence`；
3. `confidence < 0.7` **强制** `borderline`，且 borderline **不算 fail**（不计基线）；
4. 判定器不可用（无 LLM / 调用失败）时返回 `borderline` + 原因，绝不把「判不了」
   当成「不合格」。

本模块不发网络请求除非显式调用 `judge(...)`，且只在 runner `--live` 时被调用。
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any

#: 允许的判定维度（新增维度需先改本常量与 `JUDGE_SYSTEM`）。
JUDGE_DIMENSIONS = ("naturalness", "topic_continuity", "warmth")

#: 置信度门槛：低于此值一律记 borderline。
CONFIDENCE_FLOOR = 0.7

#: 客服腔词表（生产降级轮实证，AGENTS v1.55：「好的，注意保暖哦」族）。
#: 这层是**确定性**旁证，不依赖 LLM 判定。
SERVICE_TONE_MARKERS = (
    "好的，注意保暖",
    "请注意身体",
    "希望对你有帮助",
    "还有什么可以帮",
    "祝您",
    "请问还有什么",
)

JUDGE_SYSTEM = """你是一个对话质量评审员。只按三个维度打分：
- naturalness：像不像真人在微信上说话（不客服腔、不解释机制、不复述对方）；
- topic_continuity：有没有接住对方上一条的具体内容（而不是另起话题）；
- warmth：有没有情绪温度（不是机械关心）。
只输出 JSON：{"verdict": "pass|borderline|fail", "confidence": 0-1,
"evidence": "引用回复里的原文片段（<=40字）"}
不确定就给 borderline 并说明原因，不要猜。"""


@dataclass
class JudgeVerdict:
    passed: bool
    verdict: str
    detail: str


def _service_tone_hits(text: str) -> list[str]:
    return [m for m in SERVICE_TONE_MARKERS if m in str(text or "")]


def _parse_judge_output(raw: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON（容忍 ```json 围栏与前后废话）。"""
    if not raw:
        return None
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if not fenced:
        return None
    try:
        data = json.loads(fenced.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "borderline", "fail"):
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "verdict": verdict,
        "confidence": max(0.0, min(1.0, confidence)),
        "evidence": str(data.get("evidence") or "")[:80],
    }


def normalize(verdict: str, confidence: float) -> tuple[str, bool]:
    """置信度不足强制 borderline；borderline 不计 fail。

    Returns:
        ``(最终 verdict, 是否算通过)``
    """
    if confidence < CONFIDENCE_FLOOR and verdict != "pass":
        return "borderline", True
    if confidence < CONFIDENCE_FLOOR and verdict == "pass":
        # 低置信的 pass 也不进基线（避免「瞎蒙也算过」）
        return "borderline", True
    return verdict, verdict != "fail"


def judge(case: Any, reply: str, *, llm: Any = None) -> JudgeVerdict:
    """对一条用例的回复做可选判定。

    Args:
        case: `tests.golden.runner.Case`（读 `llm_judge` 与 `input.user_msg`）。
        reply: 守卫之后的回复正文。
        llm: 可选网关替身（需实现 ``chat(query=..., system_prompt=...)``）；
            None 时尝试从 `components["llm"]` 不可得 → 返回 borderline。

    永远不抛异常（判定失败不得把黄金集打成红）。
    """
    spec = getattr(case, "llm_judge", None) or {}
    dimensions = [d for d in (spec.get("dimensions") or JUDGE_DIMENSIONS) if d in JUDGE_DIMENSIONS]
    if not dimensions:
        return JudgeVerdict(True, "borderline", "无有效判定维度")

    # ① 确定性旁证：客服腔词表一旦命中直接 fail（不依赖 LLM）
    hits = _service_tone_hits(reply)
    if hits:
        return JudgeVerdict(False, "fail", f"客服腔词表命中 {hits}")

    if not str(reply or "").strip():
        return JudgeVerdict(True, "borderline", "回复为空（由 empty_reply 兜底，无需判定）")

    if llm is None:
        return JudgeVerdict(True, "borderline", "未提供 LLM（判定跳过）")

    prompt = (
        f"维度：{', '.join(dimensions)}\n"
        f"用户上一条：{str((getattr(case, 'input', {}) or {}).get('user_msg') or '')[:200]}\n"
        f"候选回复：{reply[:400]}"
    )
    try:
        if inspect.iscoroutinefunction(getattr(llm, "chat", None)):
            # 公共异步桥（`utils.async_utils.run_async`）：无运行中循环 → asyncio.run；
            # 有 → 投递常驻循环。旧实现用 `asyncio.get_event_loop().run_until_complete`，
            # 在**已运行**的循环里（异步调用方 / pytest-asyncio）必抛
            # 「This event loop is already running」→ 判定被吞成 borderline（2026-10-22 修）。
            from utils.async_utils import run_async

            raw = run_async(llm.chat(query=prompt, system_prompt=JUDGE_SYSTEM))
        else:
            raw = llm.chat(query=prompt, system_prompt=JUDGE_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        return JudgeVerdict(True, "borderline", f"判定调用失败（{type(exc).__name__}）")

    parsed = _parse_judge_output(str(raw or ""))
    if parsed is None:
        return JudgeVerdict(True, "borderline", "判定输出无法解析")
    final_verdict, passed = normalize(parsed["verdict"], parsed["confidence"])
    detail = (
        f"{final_verdict} conf={parsed['confidence']:.2f} "
        f"evidence={parsed['evidence']!r}"
    )
    return JudgeVerdict(passed, final_verdict, detail)
