"""agent_plane 生产运行时 — 账本访问、画像写投影、prompt 画像槽。

用户裁决 2026-09-21：
- 写全走 EventLedger；user_profile 表仅投影/缓存
- 主动消息由 LLM 判断，系统不做策略闸
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent_plane")


def get_ledger():
    from shisi.agent_plane.event_ledger import default_ledger

    return default_ledger()


def _store():
    from shisi.memory.legacy.user_profile import default_store

    return default_store()


def ensure_profile_seeded(session_key: str) -> dict[str, Any]:
    """ledger 无画像事件时，把存量 user_profile 行 seed 为基线（一次）。"""
    from shisi.agent_plane.event_ledger import (
        EVENT_PROFILE_CORRECT,
        EVENT_PROFILE_UPDATE,
    )
    from shisi.agent_plane.profile_projection import (
        project_profile,
        seed_profile_baseline,
    )

    sk = str(session_key or "").strip()
    if not sk:
        return {}
    ledger = get_ledger()
    has_profile = bool(
        ledger.query(session_key=sk, event_type=EVENT_PROFILE_UPDATE, limit=1)
        or ledger.query(session_key=sk, event_type=EVENT_PROFILE_CORRECT, limit=1)
    )
    if has_profile:
        return project_profile(ledger, sk)
    try:
        row = _store().get(sk) or {}
    except Exception:  # noqa: BLE001
        row = {}
    if row:
        return seed_profile_baseline(ledger, sk, row)
    return project_profile(ledger, sk)


def materialize_profile_cache(session_key: str, projection: dict[str, Any]) -> None:
    """把投影物化进 user_profile 表——**只读缓存**，禁止当作写权威。"""
    sk = str(session_key or "").strip()
    if not sk or not projection:
        return
    try:
        store = _store()
        fields: dict[str, Any] = {}
        for k in ("nickname", "birthday", "occupation", "location", "notes"):
            if k in projection:
                fields[k] = str(projection.get(k) or "")
        for k in ("preferences", "commitments"):
            if k in projection and isinstance(projection[k], list):
                fields[k] = list(projection[k])
        if fields.get("birthday") == "":
            fields["clear_birthday"] = True
        if fields:
            store.upsert(sk, **fields)
    except Exception as e:  # noqa: BLE001
        logger.debug("profile cache materialize failed: %s", e)


def _detected_denied_names(session_key: str) -> list[str]:
    """源①：扫账本全量用户消息，检测「用户否认过的称呼」（首现序）。

    生产实证：用户 09-21 说「我叫十七」→ profile 写入；此后十几轮否认
    （「我不是十七」「谁是十七」）→ LLM 工具曾三次清空（10-03/10-07）又三次
    从旧历史里"纠正"写回（10-03 15:47/10-06/10-12）——写路径只认「历史自称」，
    不认「后来的否认」，否认没有记忆性。检测函数单一 owner =
    proactive.llm_proactive.detect_denied_nicknames。

    注意：用户消息会被 prune（30 天保留），所以检测结果必须经
    `_persist_denials` 落进永久保留的 profile_correct 事件。
    """
    try:
        from proactive.llm_proactive import detect_denied_nicknames
        from shisi.agent_plane.event_ledger import EVENT_USER_MESSAGE

        ledger = get_ledger()
        messages: list[dict[str, str]] = []
        cursor = 0
        for _ in range(20):  # 上限 20 页 × 500 = 10000 条，防病态账户
            page = ledger.query(
                session_key=str(session_key or ""),
                event_type=EVENT_USER_MESSAGE,
                after_id=cursor,
                limit=500,
                order="ASC",
            )
            if not page:
                break
            for ev in page:
                payload = getattr(ev, "payload", None)
                text = str(payload.get("text") or "") if isinstance(payload, dict) else ""
                if text:
                    messages.append({"role": "user", "content": text})
            cursor = int(getattr(page[-1], "id", 0) or 0)
            if len(page) < 500:
                break
        return detect_denied_nicknames(messages)
    except Exception as e:  # noqa: BLE001
        logger.debug("denied nickname scan failed: %s", e)
        return []


def _persisted_denied_names(session_key: str) -> list[str]:
    """源②：读永久保留的否决记录（profile_correct.denied_name，actor=denial_gate）。

    这是「否认记忆性」的落点：用户消息 prune 后源①失效，此源仍生效。
    """
    try:
        from shisi.agent_plane.event_ledger import EVENT_PROFILE_CORRECT

        out: list[str] = []
        rows = get_ledger().query(
            session_key=str(session_key or ""),
            event_type=EVENT_PROFILE_CORRECT,
            limit=200,
            order="DESC",
        )
        for ev in rows:
            payload = getattr(ev, "payload", None)
            if not isinstance(payload, dict):
                continue
            token = str(payload.get("denied_name") or "").strip()
            if token and token not in out:
                out.append(token)
        return out
    except Exception as e:  # noqa: BLE001
        logger.debug("persisted denied scan failed: %s", e)
        return []


def _denied_names(session_key: str) -> list[str]:
    """合并双源：检测（源①）+ 持久否决（源②）。"""
    merged = _detected_denied_names(session_key)
    for token in _persisted_denied_names(session_key):
        if token not in merged:
            merged.append(token)
    return merged[:10]


def denied_names_for(session_key: str) -> list[str]:
    """公共入口（2026-10-15）：主动消息链复用双源否决（窗口检测 + ledger
    持久否决）。主链画像槽经 get_profile_prompt_block 内部消费同一真源。"""
    return _denied_names(session_key)


def persist_detected_denials(session_key: str, *, turn_id: str = "") -> list[str]:
    """检测到的否认称呼落盘为永久 event（缺口1 根治：不再依赖 profile 写路径触发）。

    2026-10-15「夏夏」复犯根因：落盘钩子挂在 write_profile_from_tool 内联，
    而该函数仅在 profile agent 更新画像时被调用——用户否认后若从无画像写入，
    否认永不落盘，窗口滑出即失忆。本函数供每轮对话回调主动调用（见
    append_chat_events 内挂载），检测（源①）与已持久（源②）diff 后落新词。
    返回本次新落盘的词表（调用方仅日志用）。失败一律静默（转写不阻塞对话）。
    """
    sk = str(session_key or "").strip()
    if not sk:
        return []
    newly: list[str] = []
    try:
        from shisi.agent_plane.profile_projection import write_profile_event

        detected = _detected_denied_names(sk)
        persisted = set(_persisted_denied_names(sk))
        for token in detected:
            if token in persisted:
                continue
            logger.warning("[profile_gate] 记录被否认称呼 '%s'（session=%s）", token, sk)
            write_profile_event(
                get_ledger(),
                session_key=sk,
                payload={
                    "denied_name": token,
                    "reason": f"auto: 用户明确否认过该称呼「{token}」（2026-10-15 主动闭环）",
                },
                correct=True,
                turn_id=turn_id,
                actor="denial_gate",
            )
            newly.append(token)
    except Exception as e:  # noqa: BLE001
        logger.debug("persist detected denials failed: %s", e)
    return newly


def write_profile_from_tool(
    session_key: str,
    payload: dict[str, Any],
    *,
    correct: bool = False,
    turn_id: str = "",
    actor: str = "agent",
    reason: str = "",
) -> dict[str, Any]:
    """工具写权威入口：seed → ledger append → 投影 → 物化缓存。

    2026-10-14「十七」根治：写侧否决门——被用户明确否认过的名字（检测 owner =
    proactive.llm_proactive.detect_denied_nicknames）拒绝写回 nickname。生产实证
    profile agent 三次清空又三次从旧历史"纠正"写回，最终用户每轮被叫「十七」。
    否决不阻塞其余字段（birthday/commitments 等照常写）。
    """
    from shisi.agent_plane.profile_projection import write_profile_event

    sk = str(session_key or "").strip()
    if not sk:
        return {}
    ensure_profile_seeded(sk)
    body = dict(payload or {})
    # 否决门判据 = 检测（源①）+ 持久否决记录（源②）
    detected = _detected_denied_names(sk)
    persisted = _persisted_denied_names(sk)
    denied = detected + [t for t in persisted if t not in detected]
    # 检测到的新否认立刻持久化（profile_correct 永久保留；用户消息 30 天会被
    # prune，不落盘的话窗口一过就失忆——2026-10-14 自纠的真缺陷）。
    # 2026-10-15 单一 owner 重构：落盘逻辑收敛进 persist_detected_denials，
    # 并由 append_chat_events 每轮主动调用（不再依赖本函数被触发）。
    persist_detected_denials(sk, turn_id=turn_id)
    nick = str(body.get("nickname") or "").strip()
    if nick and nick in denied:
        logger.warning(
            "[profile_gate] 拒绝写回被用户否认的称呼 '%s'（session=%s）", nick, sk
        )
        body.pop("nickname", None)
    # 存量自愈：投影里若仍存着被否认的称呼，自动追加清空更正。
    proj_existing = project_profile_for(sk)
    denied_nick = str(proj_existing.get("nickname") or "").strip()
    if denied_nick and denied_nick in denied:
        logger.warning(
            "[profile_gate] 自动清空被否认的存量称呼 '%s'（session=%s）", denied_nick, sk
        )
        write_profile_event(
            get_ledger(),
            session_key=sk,
            payload={
                "nickname": "",
                # denied_name 是持久否决记录：账本 prune/窗口滑出后，_denied_names
                # 的源②仍能读回该名（否认记忆性的落点，勿删）。
                "denied_name": denied_nick,
                "reason": "auto: 该称呼已被用户明确否认，不得保留（2026-10-14 否决门）",
            },
            correct=True,
            turn_id=turn_id,
            actor="denial_gate",
        )
    if reason:
        body["reason"] = reason
    if body.get("clear_birthday") or body.get("commitments_clear"):
        correct = True
    proj = write_profile_event(
        get_ledger(),
        session_key=sk,
        payload=body,
        correct=correct,
        turn_id=turn_id,
        actor=actor,
    )
    materialize_profile_cache(sk, proj)
    return proj


def project_profile_for(session_key: str) -> dict[str, Any]:
    from shisi.agent_plane.profile_projection import project_profile

    sk = str(session_key or "").strip()
    if not sk:
        return {}
    ensure_profile_seeded(sk)
    return project_profile(get_ledger(), sk)


def format_profile_prompt_block(
    projection: dict[str, Any], denied_names: list[str] | None = None
) -> str:
    """渲染「# 用户画像」段（与 UserProfileStore.to_prompt_block 同文案契约）。

    denied_names（2026-10-14 读侧纵深）：用户明确否认过的称呼，即使脏数据仍在
    投影里也不注入 prompt（写侧否决门防新增，读侧守卫防存量继续带偏）。
    """
    p = projection or {}
    if not any(p.get(k) for k in ("nickname", "birthday", "occupation", "location", "notes")) and not (
        p.get("preferences") or p.get("commitments")
    ):
        return ""
    denied = {str(x) for x in (denied_names or []) if str(x or "").strip()}
    lines: list[str] = ["# 用户画像（稳定事实，以本段为准；段外信息禁止编造）"]
    nick = str(p.get("nickname") or "").strip()
    if nick and nick not in denied:
        lines.append(f"- 称呼：{nick}")
    if p.get("birthday"):
        lines.append(f"- 生日：{p['birthday']}")
    if p.get("occupation"):
        lines.append(f"- 身份/近况：{p['occupation']}")
    if p.get("location"):
        lines.append(f"- 常驻地：{p['location']}")
    prefs = p.get("preferences") or []
    if prefs:
        lines.append("- 偏好：" + "；".join(str(x) for x in prefs[:6]))
    commits = p.get("commitments") or []
    if commits:
        lines.append("- 你们的约定：")
        for c in commits[-5:]:
            lines.append(f"  · {c}")
    if p.get("notes"):
        lines.append(f"- 备注：{p['notes']}")
    if len(lines) == 1:
        return ""
    lines.append(
        "- 硬约束：画像未写的信息**不得编造**；用户更正时以用户刚说的为准，并当作唯一真相。"
    )
    return "\n".join(lines)


def get_profile_prompt_block(session_key: str) -> str:
    """prompt 画像槽唯一读入口（投影优先；读侧否决：否认称呼不注入）。"""
    try:
        sk = str(session_key or "").strip()
        return format_profile_prompt_block(
            project_profile_for(sk), denied_names=_denied_names(sk)
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("profile prompt block failed: %s", e)
        return ""


def append_chat_events(
    *,
    session_key: str,
    character_id: str = "",
    turn_id: str = "",
    reply_id: str = "",
    user_msg: str = "",
    reply: str = "",
    slots: dict[str, Any] | None = None,
) -> None:
    """orchestrator 每轮回调：因果账本（失败不阻塞聊天）。"""
    from shisi.agent_plane.event_ledger import (
        EVENT_ASSISTANT_REPLY,
        EVENT_PROMPT_SLOTS,
        EVENT_USER_MESSAGE,
    )

    sk = str(session_key or "").strip()
    if not sk:
        return
    ledger = get_ledger()
    try:
        if user_msg:
            ledger.append(
                session_key=sk,
                event_type=EVENT_USER_MESSAGE,
                character_id=character_id,
                turn_id=turn_id,
                reply_id=reply_id,
                actor="user",
                payload={"text": str(user_msg)[:500]},
            )
        if slots is not None:
            ledger.append(
                session_key=sk,
                event_type=EVENT_PROMPT_SLOTS,
                character_id=character_id,
                turn_id=turn_id,
                reply_id=reply_id,
                actor="system",
                payload={"slots": dict(slots)},
            )
        if reply:
            ledger.append(
                session_key=sk,
                event_type=EVENT_ASSISTANT_REPLY,
                character_id=character_id,
                turn_id=turn_id,
                reply_id=reply_id,
                actor="assistant",
                payload={"text": str(reply)[:500]},
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger chat events failed: %s", e)
    # 缺口1 根治（2026-10-15「夏夏」复犯）：否认持久化从此挂**每轮对话回调**
    # ——user_msg 非空即检测并落盘（此前钩子内联在 write_profile_from_tool，
    # 用户否认后无画像写入 = 永不落盘，窗口滑出即失忆）。persist_detected_denials
    # 自身全程静默，异常不会反噬本轮记账。
    if user_msg:
        persist_detected_denials(sk, turn_id=str(turn_id or ""))


def append_proactive_event(
    *,
    session_key: str,
    sent: bool,
    message: str = "",
    reason: str = "",
    wait_minutes: int | None = None,
    character_id: str = "",
) -> None:
    from shisi.agent_plane.event_ledger import EVENT_PROACTIVE_SEND, EVENT_PROACTIVE_SKIP

    sk = str(session_key or "").strip()
    if not sk:
        return
    try:
        get_ledger().append(
            session_key=sk,
            event_type=EVENT_PROACTIVE_SEND if sent else EVENT_PROACTIVE_SKIP,
            character_id=character_id,
            actor="proactive_llm",
            payload={
                "message": str(message or "")[:200],
                "reason": str(reason or "")[:300],
                "wait_minutes": wait_minutes,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger proactive event failed: %s", e)


def append_memory_write_event(
    *,
    session_key: str,
    facts: list[dict[str, Any]],
    action: str = "write",
    turn_id: str = "",
    reply_id: str = "",
    character_id: str = "",
) -> None:
    """记忆写入审计；缺陷 I：带关联轮/角色，回放可回答「本轮用了哪些事实版本」。"""
    from shisi.agent_plane.event_ledger import (
        EVENT_MEMORY_REINFORCE,
        EVENT_MEMORY_WRITE,
    )

    sk = str(session_key or "").strip()
    if not sk or not facts:
        return
    try:
        get_ledger().append(
            session_key=sk,
            event_type=EVENT_MEMORY_REINFORCE if action == "reinforce" else EVENT_MEMORY_WRITE,
            character_id=str(character_id or ""),
            actor="agent",
            turn_id=str(turn_id or ""),
            reply_id=str(reply_id or ""),
            payload={"facts": list(facts)[:20], "action": action},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger memory event failed: %s", e)


def append_tool_call_event(
    *,
    session_key: str,
    tool_name: str,
    call_id: str = "",
    provider: str = "",
    success: bool = True,
    error: str = "",
    turn_id: str = "",
    reply_id: str = "",
    character_id: str = "",
    args_preview: str = "",
) -> None:
    """工具调用入账（缺陷 I）：生产 dispatch 原先只记合并结果，无逐调用 call_id。"""
    from shisi.agent_plane.event_ledger import EVENT_TOOL_CALL

    sk = str(session_key or "").strip()
    if not sk or not tool_name:
        return
    try:
        get_ledger().append(
            session_key=sk,
            event_type=EVENT_TOOL_CALL,
            character_id=str(character_id or ""),
            actor="tool_dispatch",
            turn_id=str(turn_id or ""),
            reply_id=str(reply_id or ""),
            payload={
                "tool": str(tool_name),
                "call_id": str(call_id or ""),
                "provider": str(provider or ""),
                "success": bool(success),
                "error": str(error or ""),
                "args_preview": str(args_preview or "")[:200],
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger tool call event failed: %s", e)
