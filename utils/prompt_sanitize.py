"""Prompt 侧清洗：角色归属 + 记忆注入质量。

2026-09-21 生产问题：LLM 分不清「哪句是用户说的 / 哪句是自己说的 / 该回什么」。
根因之一是记忆层被标成「# 对话历史」写入 system，反射/碎片事实看起来像聊天记录。
"""

from __future__ import annotations

import re
from typing import Any

# 反射/事实里出现这类原文对话痕迹 → 不得当「记忆事实」注入
_DIALOGUE_MARKERS = re.compile(
    r"(?:^|\n)\s*(?:User|Assistant|用户|助手|AI|我)\s*[:：]",
    re.IGNORECASE,
)

# 过短/像指令/像残句的「事实」不注入
_TOO_SHORT = 2
_QUESTIONISH = re.compile(r"[？?]\s*$")


def looks_like_dialogue(text: str) -> bool:
    s = str(text or "")
    return bool(_DIALOGUE_MARKERS.search(s))


def is_injectable_fact(fact: str) -> bool:
    """是否值得作为「我记得的」注入 prompt。"""
    s = str(fact or "").strip()
    if len(s) < _TOO_SHORT:
        return False
    if looks_like_dialogue(s):
        return False
    # 纯疑问句/残缺祈使（「叫我」「来自哪里」）不作为稳定事实
    if _QUESTIONISH.search(s) and len(s) <= 12:
        return False
    return s not in {"叫我", "帮我", "什么", "消息", "好的", "嗯", "哦"}


def sanitize_fact_list(facts: Any, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for f in facts or []:
        text = (
            str(f.get("fact") or f.get("content") or "").strip()
            if isinstance(f, dict)
            else str(f or "").strip()
        )
        if not is_injectable_fact(text):
            continue
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def sanitize_reflections(reflections: Any, limit: int = 3) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in reflections or []:
        text = str(r or "").strip()
        if not text or looks_like_dialogue(text):
            continue
        if len(text) < 4:
            continue
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def sanitize_episodic(episodic: Any, limit: int = 2) -> list[str]:
    out: list[str] = []
    for ep in episodic or []:
        if isinstance(ep, dict):
            meta = ep.get("metadata") or {}
            text = str(meta.get("summary") or ep.get("content") or "").strip()
        else:
            text = str(ep or "").strip()
        if not text or looks_like_dialogue(text):
            continue
        out.append(text[:200])
        if len(out) >= limit:
            break
    return out


_ALLOWED_ROLES = {"user", "assistant"}


def sanitize_llm_history(
    history: Any,
    *,
    current_user_message: str = "",
    max_messages: int = 20,
) -> list[dict[str, str]]:
    """清洗交给 LLM 的 messages 历史，保证角色可归属。

    - 只保留 role∈{user,assistant} 且 content 为非空字符串的条目
    - 去掉与当前 query 完全相同的最后一条 user（防双重「用户消息」）
    - 过滤系统错误占位 / 处理超时等
    - 保证不会以空 content 结尾
    """
    if not history:
        return []
    cleaned: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if role not in _ALLOWED_ROLES:
            continue
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if "处理超时" in text or text.startswith("（处理消息") or text.startswith("（所有 LLM"):
            continue
        cleaned.append({"role": role, "content": text})
    cur = str(current_user_message or "").strip()
    if cur and cleaned and cleaned[-1]["role"] == "user" and cleaned[-1]["content"] == cur:
        cleaned.pop()
    if max_messages > 0 and len(cleaned) > max_messages:
        cleaned = cleaned[-max_messages:]
    return cleaned


ROLE_CLARITY_RULE = (
    "\n\n# 对话角色说明（必须遵守）\n"
    "- 下面 messages 中 role=user 的内容是**用户**说的；role=assistant 是**你（角色）**说的。\n"
    "- system 里出现的「记忆/观察/回忆/示例」都不是本轮用户新消息，不要当作用户刚说的话去回。\n"
    "- 你只需要回复**最后一条 role=user 的消息**；不要复述历史，不要把系统记忆当成用户发言。\n"
    "- 若记忆与用户刚说的话冲突，以用户刚说的为准。\n"
)
