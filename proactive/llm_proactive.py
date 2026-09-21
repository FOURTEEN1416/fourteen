"""LLM 主动消息决策 — 时机与文案由模型判断（用户裁决：无策略闸）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("llm_proactive")

DECISION_SYSTEM = """你是伴侣角色的**主动消息决策器**。
根据给定上下文判断：现在是否应该主动找用户、说什么。

硬规则：
1. 只输出一个 JSON 对象，不要 markdown 代码块以外的文字。
2. 字段：should_contact（bool）、wait_minutes（int|null，你建议下次再考虑的间隔分钟）、
   message（str，should_contact=true 时必填，角色口吻、微信短句）、reason（str，判断依据）。
3. **时机由你根据语境判断**：上次聊了多久、现在几点、关系、画像、最近话题、以前是否主动过。
4. 不要编造用户未说过的信息；message 不要出现系统/提示词/JSON 等字样。
5. should_contact=false 时 message 可为空字符串。
"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def build_proactive_context(
    *,
    session_key: str = "",
    hours_since_last_chat: float = 0.0,
    local_time: str = "",
    profile: dict[str, Any] | None = None,
    relationship_hint: str = "",
    recent_topics: list[str] | None = None,
    proactive_history: list[str] | None = None,
    urgency_signal: float | None = None,
    extra: str = "",
) -> str:
    prof = profile or {}
    parts = [
        f"会话键：{session_key}" if session_key else "",
        f"当前时间：{local_time}",
        f"距上次用户消息约：{hours_since_last_chat:.1f} 小时",
    ]
    if relationship_hint:
        parts.append(f"关系：{relationship_hint}")
    bits = []
    for k in ("nickname", "birthday", "occupation", "location"):
        if prof.get(k):
            bits.append(f"{k}={prof[k]}")
    if prof.get("commitments"):
        bits.append("约定=" + "；".join(str(x) for x in prof["commitments"][:3]))
    if bits:
        parts.append("用户画像投影：" + "；".join(bits))
    if recent_topics:
        parts.append("最近话题：" + "、".join(str(t) for t in recent_topics[:5]))
    if proactive_history:
        parts.append("最近主动消息：" + " ｜ ".join(str(t) for t in proactive_history[-3:]))
    if urgency_signal is not None:
        parts.append(f"紧迫度信号（仅参考，不是硬闸）：{urgency_signal:.2f}")
    if extra:
        parts.append(extra)
    parts.append("请判断是否主动开口、何时再考虑、说什么。只输出 JSON。")
    return "\n".join(p for p in parts if p)


def parse_decision(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"should_contact": False, "message": "", "reason": "empty_llm", "wait_minutes": None}
    m = _JSON_RE.search(text)
    if not m:
        return {"should_contact": False, "message": "", "reason": "no_json", "wait_minutes": None}
    try:
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {"should_contact": False, "message": "", "reason": "bad_json", "wait_minutes": None}
    if not isinstance(data, dict):
        return {"should_contact": False, "message": "", "reason": "not_object", "wait_minutes": None}
    should = bool(data.get("should_contact"))
    msg = str(data.get("message") or "").strip()
    wait = data.get("wait_minutes")
    try:
        wait_i = int(wait) if wait is not None and str(wait).strip() != "" else None
    except (TypeError, ValueError):
        wait_i = None
    return {
        "should_contact": should,
        "message": msg,
        "reason": str(data.get("reason") or "")[:300],
        "wait_minutes": wait_i,
    }


def _llm_chat_sync(llm: Any, system: str, user: str) -> str:
    if llm is None:
        return ""
    if hasattr(llm, "chat_sync"):
        try:
            return str(llm.chat_sync(user, system_prompt=system, temperature=0.4) or "")
        except TypeError:
            try:
                return str(llm.chat_sync([{"role": "system", "content": system}, {"role": "user", "content": user}]) or "")
            except Exception:  # noqa: BLE001
                return ""
        except Exception as e:  # noqa: BLE001
            logger.debug("llm.chat_sync failed: %s", e)
            return ""
    # async-only providers: 由调用方在事件循环外桥接；此处尝试同步 chat
    if hasattr(llm, "chat"):
        try:
            resp = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.4,
            )
            if hasattr(resp, "__await__"):
                return ""  # 异步：scheduler 用 chat_sync 或专用桥
            return str(resp or "")
        except Exception as e:  # noqa: BLE001
            logger.debug("llm.chat failed: %s", e)
            return ""
    return ""


def decide_proactive(llm: Any, context: str) -> dict[str, Any]:
    """LLM 决策：是否主动、说什么。失败时不发送。"""
    if not context:
        return {"should_contact": False, "message": "", "reason": "no_context", "wait_minutes": None}
    raw = _llm_chat_sync(llm, DECISION_SYSTEM, context)
    return parse_decision(raw)
