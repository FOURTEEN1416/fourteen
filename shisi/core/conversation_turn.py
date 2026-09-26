"""对话轮与发言者归属 —— 唯一真源。

## 为什么存在（2026-09-21 重扫 + 对标实证）

生产里「模型分不清哪句话是谁说的」不是提示词没写清楚，而是**存储层丢掉了归属**：

- `chat_history` 只有 ``role``（user/assistant）+ ``content`` + ``session_id`` ——
  **没有 character_id**。同一会话里切换角色后，新角色会把上一个角色的回复
  当"自己说过的话"（assistant 行的身份不可辨）。
- 上下文重建按 ``ORDER BY created_at``（``CURRENT_TIMESTAMP`` 秒级精度）排序，
  并用 ``(created_at, content)`` 作去重键 —— 同秒消息次序不保证、**内容相同的
  两条消息被折叠成一条**。
- 历史在注入前被压成扁平消息数组，没有"一轮 = 用户一句 + 角色一句"的结构，
  于是任何"跨会话尾巴/记忆摘要"渲染都只能猜。

## 对标（读源码，非描述）

- **SillyTavern** `public/scripts/openai.js:setOpenAIMessages()`：每条历史消息都带
  ``name`` 字段，并在多人/群聊场景把发言人名字前缀拼进 content
  （``content = `${chat[j].name}: ${content}``），三档策略
  ``names_behavior = NONE | DEFAULT | CONTENT``；每条消息还带
  ``extra{type: NARRATOR|..., api, model}`` 出处元数据（叙述者以 ``system`` 角色下发）。
- **nana**（可跑的国产陪伴产品）`backend/conversation.py`：历史以
  ``ConversationTurn(ask, answer)`` **成对**存储，渲染即
  ``"user: {ask}\\nassistant: {answer}"`` —— 轮为最小单位、显式标注发言者。

本模块把这条经验落成项目唯一实现：**row → Turn（成对 + 归属）→ 显式标注渲染**。
存储侧只做加法（新列 + 按 id 排序），不重写历史数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from utils.session_key import is_wechat_key, peer_of

# 历史原文的统一窗口：摘要边界和最终 LLM 预算必须使用同一个值。
HISTORY_RECENT_LIMIT = 20


class Speaker(str, Enum):
    """发言者类别。`NARRATOR` 用于系统/旁白（对标 SillyTavern 的 system_message_types）。"""

    USER = "user"
    CHARACTER = "character"
    NARRATOR = "narrator"

    @property
    def llm_role(self) -> str:
        """映射到 OpenAI messages 的 role。"""
        return {Speaker.USER: "user", Speaker.CHARACTER: "assistant", Speaker.NARRATOR: "system"}[self]

    @classmethod
    def from_role(cls, role: str) -> Speaker:
        r = str(role or "").strip().lower()
        if r in {"user", "human"}:
            return cls.USER
        if r in {"assistant", "ai", "bot", "character"}:
            return cls.CHARACTER
        return cls.NARRATOR


@dataclass
class Turn:
    """一轮对话：用户一句（+ 角色回复一句），带完整归属。

    归属字段是**隔离与归因的载体**：``user_key``（= 完整会话键，含 owner）
    × ``character_id``，缺任一都会让"同一句话是谁说的/说给谁"失去判据。
    """

    turn_id: str = ""
    session_key: str = ""
    user_key: str = ""
    character_id: str = ""
    channel: str = ""
    user_text: str = ""
    reply_text: str = ""
    emotion_tag: str = ""
    importance: float = 0.0
    ts: str = ""
    row_ids: list[int] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """用户消息与角色回复都在（回复缺失 = 工具/错误轮，不算一轮完整对话）。"""
        return bool(self.user_text) and bool(self.reply_text)

    @property
    def is_wechat(self) -> bool:
        return is_wechat_key(self.session_key)

    @property
    def peer(self) -> str:
        return peer_of(self.session_key)


def isolation_key(user_key: str, character_id: str) -> str:
    """所有按人隔离的状态（情感/好感/画像/主动消息）共用的键。

    ⚠️ 与 `AffinityEnhancer` / `ASEHub` / 画像投影保持同一形状
    ``f"{user_key}::{character_id}"``；新增按人状态一律走本函数，禁止各自拼串。
    """
    return f"{str(user_key or '').strip()}::{str(character_id or '').strip()}"


def build_turns(rows: list[dict[str, Any]] | None) -> list[Turn]:
    """把扁平的 `chat_history` 行**按 id 顺序**聚成轮。

    Args:
        rows: ``chat_history`` 行（须含 ``id``/``role``/``content``；``id`` 缺失时
            退化为给定顺序）。

    Returns:
        Turn 列表（时间正序）。孤独的 assistant 行（无前置 user 行）自成一轮
        ``user_text=""``，便于调用方按 ``is_complete`` 过滤。
    """
    turns: list[Turn] = []
    current: Turn | None = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        speaker = Speaker.from_role(str(row.get("role") or ""))
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        row_id = int(row.get("id") or 0)
        if speaker is Speaker.USER or current is None or current.reply_text:
            current = Turn(
                turn_id=str(row.get("turn_id") or ""),
                session_key=str(row.get("session_id") or ""),
                user_key=str(row.get("user_key") or row.get("session_id") or ""),
                character_id=str(row.get("character_id") or ""),
                channel=str(row.get("channel") or ""),
                emotion_tag=str(row.get("emotion_tag") or ""),
                importance=float(row.get("importance") or 0.0),
                ts=str(row.get("created_at") or ""),
            )
            turns.append(current)
        if speaker is Speaker.USER and not current.user_text:
            current.user_text = content
        else:
            # 角色回复（或叙述者）—— 只取第一条，多段回复拼接
            current.reply_text = (
                f"{current.reply_text}\n{content}".strip() if current.reply_text else content
            )
        if row_id:
            current.row_ids.append(row_id)
    return turns


def render_turns(
    turns: list[Turn] | None,
    user_label: str = "用户",
    character_label: str = "我",
) -> list[str]:
    """渲染为**显式标注发言者**的行（供跨会话尾巴/记忆参考段注入）。

    与 SillyTavern 的 name prefix、nana 的 ``user:/assistant:`` 同规：
    每一行必须自带发言者，否则模型只能猜。
    """
    out: list[str] = []
    for turn in turns or []:
        if turn.user_text:
            out.append(f"{user_label}: {turn.user_text}")
        if turn.reply_text:
            out.append(f"{character_label}: {turn.reply_text}")
    return out


def filter_by_character(turns: list[Turn] | None, character_id: str) -> list[Turn]:
    """按已知角色过滤；历史空归属是未知，而不是所有角色的通用轮。"""
    cid = str(character_id or "").strip()
    if not cid:
        return list(turns or [])
    return [t for t in (turns or []) if t.character_id == cid]
