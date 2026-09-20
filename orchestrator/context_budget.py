"""上下文预算与去重（包 Q · A4）。

纯函数模块，可单测。对齐 research 标准注入序列：
角色设定 → 数值/状态 → 知识库 → 记忆 → 工具结果(untrusted) → PHI → reply_mode。

禁止把 rag JSON dump 进 prompt；同一事实知识段优先，memory 段做简单去重。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextBudget:
    knowledge_chars_max: int = 4000
    memory_items_max: int = 8
    history_msgs_max: int = 20
    tool_chars_max: int = 6000
    memory_chars_max: int = 2500
    chat_summary_chars_max: int = 800


DEFAULT_BUDGET = ContextBudget()


def clip_text(text: str, max_chars: int) -> str:
    """超预算截断，保留完整行优先。"""
    if max_chars <= 0:
        return ""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    # 尽量在行边界收尾
    nl = clipped.rfind("\n")
    if nl > max_chars * 0.6:
        clipped = clipped[:nl]
    return clipped.rstrip() + "…"


def rag_payload_to_text(payload: Any) -> str:
    """把检索结果转成可读文本；禁止 json.dumps 整包进 prompt。"""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    lines: list[str] = []
    if isinstance(payload, dict):
        results = payload.get("results") or payload.get("chunks") or []
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict):
                    content = str(r.get("content") or r.get("text") or "").strip()
                    if content:
                        lines.append(content)
                elif isinstance(r, str) and r.strip():
                    lines.append(r.strip())
        # 兜底：dict 里可能直接是可读键
        if not lines:
            for key in ("context", "text", "knowledge"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    lines.append(val.strip())
    elif isinstance(payload, list):
        for r in payload:
            if isinstance(r, dict):
                content = str(r.get("content") or r.get("text") or "").strip()
                if content:
                    lines.append(content)
            elif isinstance(r, str) and r.strip():
                lines.append(r.strip())
    return "\n".join(lines)


def _normalize_for_compare(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(
        r"[，。！？、；：\"'“”‘’（）()\[\]【】]",
        "",
        text,
    )
    return text


def _grams(text: str, n: int = 3) -> set[str]:
    t = _normalize_for_compare(text)
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def memory_overlaps_knowledge(memory_text: str, knowledge_text: str) -> bool:
    """简单包含/bigram 重叠：同一事实知识段已注入时，memory 可裁掉。"""
    mem = _normalize_for_compare(memory_text)
    know = _normalize_for_compare(knowledge_text)
    if not mem or not know:
        return False
    if mem in know or know in mem:
        return True
    mg, kg = _grams(memory_text), _grams(knowledge_text)
    if not mg or not kg:
        return False
    overlap = len(mg & kg) / max(1, min(len(mg), len(kg)))
    return overlap >= 0.55


def dedup_memory_against_knowledge(
    memory_text: str,
    knowledge_text: str,
    budget: ContextBudget | None = None,
) -> str:
    """知识段已覆盖的事实从 memory 段剔除（行级，不追求完美）。"""
    budget = budget or DEFAULT_BUDGET
    mem = str(memory_text or "")
    know = str(knowledge_text or "")
    if not mem or not know:
        return clip_text(mem, budget.memory_chars_max)

    kept_lines: list[str] = []
    for line in mem.splitlines():
        s = line.strip()
        if not s:
            continue
        if memory_overlaps_knowledge(s, know):
            continue
        kept_lines.append(line)
    return clip_text("\n".join(kept_lines), budget.memory_chars_max)


def clip_history(history: list | str | None, budget: ContextBudget | None = None) -> list | str:
    """历史消息条数预算；list 取尾部，str 做字符裁剪。"""
    budget = budget or DEFAULT_BUDGET
    if history is None:
        return []
    if isinstance(history, list):
        if len(history) <= budget.history_msgs_max:
            return history
        return history[-budget.history_msgs_max :]
    return clip_text(str(history), budget.history_msgs_max * 200)


def apply_budget(
    *,
    rag_context: str = "",
    memory_context: str = "",
    chat_summary: str = "",
    chat_history: Any = None,
    tool_results: str = "",
    budget: ContextBudget | None = None,
) -> dict[str, Any]:
    """返回裁剪后的各段与长度埋点。"""
    budget = budget or DEFAULT_BUDGET
    know = clip_text(rag_context or "", budget.knowledge_chars_max)
    mem = dedup_memory_against_knowledge(memory_context or "", know, budget)
    summary = clip_text(chat_summary or "", budget.chat_summary_chars_max)
    history = clip_history(chat_history, budget)
    tools = clip_text(tool_results or "", budget.tool_chars_max)

    hist_len = len(history) if isinstance(history, list) else len(str(history or ""))
    return {
        "rag_context": know,
        "memory_context": mem,
        "chat_summary": summary,
        "chat_history": history,
        "tool_results": tools,
        "lengths": {
            "rag": len(know),
            "memory": len(mem),
            "chat_summary": len(summary),
            "history": hist_len,
            "tool": len(tools),
        },
    }
