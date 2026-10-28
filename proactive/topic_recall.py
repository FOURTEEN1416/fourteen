"""主动线 parked 话题复提候选 —— 三重闸的候选侧（2026-10-27 话题状态机 Q3）。

只读 :func:`utils.topic_state.select_proactive_recall`；不自己查库、不自己判静默。
三重闸的归属：

1. **距上次话题 ≥6h + 非静默 + 最多 2 条 + 仅 parked**：
   ``utils.topic_state.select_proactive_recall``（closed/active 永不返回）。
2. **主动 judge allow**：本模块只产出候选与 ``judge_context``，由
   ``proactive.post_draft_judge.judge_proactive_message`` 在投递前终审。
   judge 关闭 / 不可调用时**第三闸不存在** → 直接返回空候选（fail-closed），
   不能靠 ``judge_not_configured`` 的放行语义绕过 Q3。

设计依据：``docs/reports/2026-10-27-话题状态机实施计划.md`` §7.1–7.2。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from proactive.post_draft_judge import read_judge_config
from utils import topic_state

logger = logging.getLogger("proactive.topic_recall")


@dataclass(frozen=True)
class TopicRecall:
    """复提候选的只读快照。空实例 = 本轮不允许 parked 复提。"""

    items: tuple[dict[str, Any], ...] = ()
    keywords: tuple[str, ...] = ()
    judge_context: str = ""


def build_topic_recall(
    session_key: str,
    character_id: str = "default",
    *,
    quiet_hours: tuple[int, int] | None = None,
    now: Any | None = None,
    judge_available: bool = False,
) -> TopicRecall:
    """构造 parked 复提候选；任何一闸不满足 → 空实例。

    ``judge_available`` 由 scheduler 传入
    ``bool(self._llm_provider or self._session_llm_resolver)``：没有可调用的
    judge 就没有第三闸，Q3 不允许 parked 复提。
    """
    judge_enabled, _timeout = read_judge_config()
    if not judge_enabled or not judge_available:
        return TopicRecall()
    try:
        items = topic_state.select_proactive_recall(
            session_key,
            character_id,
            now=now,
            quiet_hours=quiet_hours,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_recall][step=select] degraded=%s", type(e).__name__)
        return TopicRecall()
    if not items:
        return TopicRecall()

    judge: list[str] = []
    keywords: list[str] = []
    # 2026-10-28 复核修复：title/anchors 是用户原话，judge_context 会进入
    # judge prompt —— 必须复用 topic_state 的同一清洗真源，不能直接拼原文
    # （# / < > / [INST] / system: / ignore previous 等指令标记）。
    try:
        from utils.topic_state import _sanitize_topic_text as _clean_topic
    except Exception:  # noqa: BLE001
        def _clean_topic(value: Any, limit: int) -> str:
            return str(value or "").strip()[:limit]

    kept: list[dict[str, Any]] = []
    for item in items[:2]:
        title = _clean_topic(item.get("title"), 40)
        if not title:
            continue
        anchors = [
            cleaned for cleaned in (
                _clean_topic(a, 40) for a in (item.get("anchors") or [])
            ) if cleaned
        ]
        anchor = anchors[0] if anchors else ""
        age = float(item.get("age_hours") or 0.0)
        judge.append(f"{title}（约 {age:.1f} 小时前）")
        keywords.extend([title, anchor])
        safe_item = dict(item)
        safe_item["title"] = title
        safe_item["anchors"] = anchors
        kept.append(safe_item)
    return TopicRecall(
        keywords=tuple(k for k in keywords if len(k) >= 2),
        items=tuple(kept),
        judge_context="；".join(judge),
    )
