"""智能体式画像/记忆工具 — 写权威=EventLedger，profile 表仅投影。

用户裁决 2026-09-21：写全走 ledger；对话后 profile_sync_agent；L1 可直接调工具。
"""

from __future__ import annotations

import logging
from typing import Any

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("profile_agent")


def _profile_store():
    from shisi.memory.legacy.user_profile import default_store

    return default_store()


def _runtime():
    from shisi.agent_plane import runtime as apruntime

    return apruntime


def _sm_from_kwargs(kwargs: dict) -> Any:
    sm = kwargs.get("structured_memory")
    if sm is not None:
        return sm
    try:
        from api.deps import deps

        orch = getattr(deps, "orch", None)
        mem = getattr(orch, "components", None)
        if isinstance(mem, dict):
            m = mem.get("memory")
            return getattr(m, "structured_memory", None) or getattr(m, "_sm", None)
    except Exception:  # noqa: BLE001
        pass
    return None


class UpdateUserProfileTool(BaseTool):
    """LLM 编辑用户画像 — 写走 EventLedger，返回投影。"""

    name = "update_user_profile"
    description = (
        "更新当前用户的画像。只写用户明确说过或明确更正的信息；"
        "不知道就不要编。clear_* 用于用户否认时清空。"
    )
    permission_level = "public"
    wants_call_context = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "称呼/昵称；清空传空串"},
            "birthday": {"type": "string", "description": "生日（用户原话表述，如 腊月初一 / 11月14）"},
            "clear_birthday": {"type": "boolean", "description": "用户否认生日时 true"},
            "occupation": {"type": "string", "description": "身份/近况：上班/上学/军训/自由职业等"},
            "location": {"type": "string", "description": "常驻地/城市"},
            "preferences_add": {
                "type": "array",
                "items": {"type": "string"},
                "description": "新增偏好（用户明确表达的喜欢/讨厌）",
            },
            "preferences_remove": {"type": "array", "items": {"type": "string"}},
            "commitments_add": {
                "type": "array",
                "items": {"type": "string"},
                "description": "你们之间的约定（叫起床、别回嗯等）",
            },
            "commitments_clear": {"type": "boolean"},
            "notes": {"type": "string", "description": "简短备注（必须来自用户原话）"},
            "reason": {"type": "string", "description": "更新依据（用户哪句话）"},
        },
        "required": [],
    }

    def execute(self, **kwargs) -> ToolResult:
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or kwargs.get("session_key") or "")
        if not session_key:
            return ToolResult(False, error="missing_session_key")
        payload: dict[str, Any] = {}
        for k in ("nickname", "birthday", "occupation", "location", "notes"):
            if k in kwargs and kwargs[k] is not None:
                payload[k] = kwargs[k]
        if kwargs.get("clear_birthday"):
            payload["clear_birthday"] = True
            payload["birthday"] = ""
        if kwargs.get("commitments_clear"):
            payload["commitments_clear"] = True
        if kwargs.get("preferences_add"):
            payload["preferences_add"] = list(kwargs.get("preferences_add") or [])
        if kwargs.get("preferences_remove"):
            payload["preferences_remove"] = list(kwargs.get("preferences_remove") or [])
        if kwargs.get("commitments_add"):
            payload["commitments_add"] = list(kwargs.get("commitments_add") or [])
        if not payload:
            return ToolResult(False, error="no_fields")
        reason = str(kwargs.get("reason") or "")
        try:
            rt = _runtime()
            profile = rt.write_profile_from_tool(
                session_key,
                payload,
                reason=reason,
                actor="profile_tool",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("profile ledger write failed: %s", e)
            return ToolResult(False, error=f"ledger_write_failed:{e}")
        logger.info(
            "[profile_agent] update user=%s reason=%s fields=%s",
            session_key,
            reason,
            list(payload.keys()),
        )
        return ToolResult(True, data={
            "profile": {k: profile.get(k) for k in (
                "nickname", "birthday", "occupation", "location",
                "preferences", "commitments", "notes",
            )},
            "user_key": session_key,
            "source": "event_ledger",
            "reason": reason,
        })


class RememberFactsTool(BaseTool):
    """LLM 写入/强化用户事实（memory 真源 + ledger 事件）。"""

    name = "remember_facts"
    description = (
        "把用户明确说过的新事实写入长期记忆。只写用户原话可支撑的信息；"
        "禁止编造。同文 near-dup 会强化而不是重复插入。"
    )
    permission_level = "public"
    wants_call_context = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "description": "事实陈述（第三人称，如：用户生日是腊月初一）"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "preference", "personal", "work", "event",
                                "commitment", "relationship", "general",
                            ],
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "续聊话题标签",
                        },
                    },
                    "required": ["fact"],
                },
            },
            "reason": {"type": "string"},
        },
        "required": ["facts"],
    }

    def execute(self, **kwargs) -> ToolResult:
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or kwargs.get("session_key") or "")
        if not session_key:
            return ToolResult(False, error="missing_session_key")
        sm = _sm_from_kwargs(kwargs)
        if sm is None:
            return ToolResult(False, error="memory_unavailable")
        from shisi.memory.legacy.structured_memory import StructuredMemory

        user_key = StructuredMemory.user_key_from_session(session_key)
        from utils.prompt_sanitize import is_injectable_fact

        written = []
        for item in kwargs.get("facts") or []:
            if not isinstance(item, dict):
                continue
            fact = str(item.get("fact") or "").strip()
            if not fact or not is_injectable_fact(fact):
                continue
            cat = str(item.get("category") or "general")
            topics = item.get("topics") or []
            try:
                fid = sm.add_fact(
                    fact,
                    category=cat,
                    confidence=0.85,
                    source="agent",
                    user_key=user_key,
                    topics=topics if isinstance(topics, list) else None,
                )
                written.append({"id": fid, "fact": fact, "category": cat})
            except Exception as e:  # noqa: BLE001
                logger.warning("remember_facts write failed: %s", e)
        if not written:
            return ToolResult(False, error="no_valid_facts")
        import contextlib

        with contextlib.suppress(Exception):
            _runtime().append_memory_write_event(
                session_key=user_key, facts=written, action="write"
            )
        logger.info("[profile_agent] remember user=%s n=%d", user_key, len(written))
        return ToolResult(True, data={"written": written, "user_key": user_key})


class ForgetFactsTool(BaseTool):
    name = "forget_facts"
    description = "删除错误/用户否认的事实（进回收站，可恢复）。"
    permission_level = "public"
    wants_call_context = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "fact_ids": {"type": "array", "items": {"type": "integer"}},
            "fact_texts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "按文本删除（先查 user_key 下匹配）",
            },
            "reason": {"type": "string"},
        },
        "required": [],
    }

    def execute(self, **kwargs) -> ToolResult:
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or kwargs.get("session_key") or "")
        sm = _sm_from_kwargs(kwargs)
        if sm is None or not session_key:
            return ToolResult(False, error="memory_unavailable")
        from shisi.memory.legacy.structured_memory import StructuredMemory

        user_key = StructuredMemory.user_key_from_session(session_key)
        removed = []
        for fid in kwargs.get("fact_ids") or []:
            try:
                if sm.delete_fact(int(fid), recycle=True, user_key=user_key):
                    removed.append(int(fid))
            except Exception:  # noqa: BLE001
                continue
        texts = [str(t or "").strip() for t in kwargs.get("fact_texts") or []]
        if texts:
            try:
                for row in sm.get_facts(user_key=user_key, min_confidence=0.0, limit=100):
                    fact = str(row.get("fact") or "")
                    if any(t and (t in fact or fact in t) for t in texts):
                        fid = int(row.get("id"))
                        if sm.delete_fact(fid, recycle=True, user_key=user_key):
                            removed.append(fid)
            except Exception as e:  # noqa: BLE001
                logger.warning("forget by text failed: %s", e)
        return ToolResult(True, data={"removed": removed, "user_key": user_key})


class QueryProfileTool(BaseTool):
    name = "query_profile"
    description = "查询当前用户画像与相关事实，用于决定是否更新/如何称呼用户。"
    permission_level = "public"
    wants_call_context = True
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> ToolResult:
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or kwargs.get("session_key") or "")
        if not session_key:
            return ToolResult(False, error="missing_session_key")
        try:
            profile = _runtime().project_profile_for(session_key)
        except Exception as e:  # noqa: BLE001
            logger.debug("query_profile projection failed: %s", e)
            profile = _profile_store().get(session_key)
        facts = []
        sm = _sm_from_kwargs(kwargs)
        if sm is not None:
            from shisi.memory.legacy.structured_memory import StructuredMemory

            uk = StructuredMemory.user_key_from_session(session_key)
            try:
                for row in sm.get_facts(user_key=uk, min_confidence=0.3, limit=15):
                    facts.append({
                        "id": row.get("id"),
                        "fact": row.get("fact"),
                        "category": row.get("category"),
                    })
            except Exception:  # noqa: BLE001
                pass
        return ToolResult(True, data={
            "profile": profile,
            "facts": facts,
            "source": "event_ledger_projection",
        })


# ── 对话后智能体同步（替代正则主路径）─────────────────────

PROFILE_SYNC_SYSTEM = """你是用户伴侣的**记忆管理员**（不可见角色）。
根据「本轮用户消息 + 角色回复」判断是否需要更新用户画像或长期记忆。

硬规则：
1. **只写用户明确说过的信息**，禁止从角色回复或常识编造。
2. 用户否认/更正 → 必须 clear_* 或 forget_facts。
3. 若无值得写入的新信息 → 不要调用任何工具。
4. 偏好/约定/生日/职业/称呼优先走 update_user_profile；细碎事实走 remember_facts。
5. 禁止把角色自己的台词当成用户事实。
"""


def profile_sync_tool_schemas() -> list[dict]:
    return [
        UpdateUserProfileTool().to_openai_fc_schema(),
        RememberFactsTool().to_openai_fc_schema(),
        ForgetFactsTool().to_openai_fc_schema(),
        QueryProfileTool().to_openai_fc_schema(),
    ]


_PROFILE_SYNC_DISP: Any = None


def _profile_sync_dispatcher() -> Any:
    """画像同步专用调度器（模块级单例，限速状态跨调用累积）。"""
    global _PROFILE_SYNC_DISP
    if _PROFILE_SYNC_DISP is None:
        from tools.base_tool import ToolDispatcher, ToolRegistry

        reg = ToolRegistry()
        reg.register(UpdateUserProfileTool())
        reg.register(RememberFactsTool())
        reg.register(ForgetFactsTool())
        reg.register(QueryProfileTool())
        _PROFILE_SYNC_DISP = ToolDispatcher(reg, rate_limit_per_minute=30)
    return _PROFILE_SYNC_DISP


def apply_profile_sync_calls(session_key: str, tool_calls: list[dict], sm: Any = None) -> list[dict]:
    """执行智能体同步产生的 tool_calls（服务端注入 session_key）。"""
    import json

    disp = _profile_sync_dispatcher()
    out = []
    for tc in tool_calls or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = str(fn.get("name") or "")
        raw = fn.get("arguments", "{}")
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:  # noqa: BLE001
            args = {}
        args["_meta"] = {"session_key": session_key}
        if sm is not None:
            args["structured_memory"] = sm
        result = disp.dispatch(
            name, args, affinity_level=0,
            caller_id=str(session_key or ""),
        )
        out.append({"name": name, "result": result.to_dict()})
    return out


async def run_profile_sync_agent(
    llm: Any,
    session_key: str,
    user_msg: str,
    reply: str,
    sm: Any = None,
) -> list[dict]:
    """对话后调用 LLM 工具链同步画像/记忆；无新信息则不调工具。"""
    if not llm or not session_key or not str(user_msg or "").strip():
        return []
    query = (
        f"【用户消息】\n{user_msg}\n\n"
        f"【角色回复】\n{reply}\n\n"
        "请判断是否更新用户画像/记忆；不需要则不调用工具。"
    )
    try:
        resp = llm.chat_with_tools(
            query=query,
            system_prompt=PROFILE_SYNC_SYSTEM,
            history=[],
            tools=profile_sync_tool_schemas(),
            temperature=0.1,
            max_tokens=512,
        )
        if hasattr(resp, "__await__"):
            resp = await resp
        if not isinstance(resp, dict):
            return []
        calls = resp.get("tool_calls") or []
        if not calls:
            return []
        # 限额：同步最多 3 次调用
        calls = calls[:3]
        results = await __import__("asyncio").to_thread(
            apply_profile_sync_calls, session_key, calls, sm
        )
        ok = [r for r in results if (r.get("result") or {}).get("success")]
        if ok:
            logger.info(
                "[profile_agent] synced session=%s tools=%s",
                session_key, [r["name"] for r in ok],
            )
        return results
    except Exception as e:  # noqa: BLE001
        logger.debug("profile sync agent failed: %s", e)
        return []
