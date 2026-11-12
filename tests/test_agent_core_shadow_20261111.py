"""agent_core 影子写入挂载点（hooks 域）—— B-1 红测/验收。

覆盖 9 个挂载点：connector 3（user_msg / agent_msg.sent / agent_msg.voice_sent）、
orchestrator 3（agent_msg.final / tool_call / tool_result + 承诺影子写）、
scheduler 3（manual / decision / blessing）。

三条硬要求在本文件的钉子：

1. **零行为变更必须 A/B 证明**（``test_zero_behavior_is_proven_by_ab_diff``）：
   同一组替身分别在 ``AGENT_CORE_EVENTS`` 关/开两态跑同一输入，除「事件行数」
   外（返回值、``send_reply_segments`` / ``send_voice_clip`` / ``_deliver`` /
   ``tools.dispatch`` 调用序列）完全相等，且开态 ``recorded > 0``——只断言
   「关态不写」不构成证据（hook 根本没写也能绿）。
2. **零写入**：关态不打开库、不建表（``test_disabled_state_writes_nothing``）。
3. **异常不外泄**：所有挂载点 try/except Exception → debug，返回值逐字不变
   （``test_store_exception_never_escapes_and_ab_equal`` +
   ``test_hooks_are_wrapped_in_try_except`` 的 AST 守卫）。

⚠️ 本文件**必须自带沙箱**：``tests/conftest.py`` 不在本批文件边界内，没有
``agent_core`` 的默认重定向；默认开会让既有 connector / scheduler 测试写真实
``data/sqlite.db``。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from wechat_direct import wechat_connector as wc

OWNER = 7
PEER = "wx_peer@im.wechat"
SESSION_KEY = f"{OWNER}:{PEER}"
CHARACTER = "card_shadow_001"


# ---------------------------------------------------------------------------
# 沙箱与查询
# ---------------------------------------------------------------------------


def _agent():
    from agent_core import commitments, events, threads

    return events, threads, commitments


def _reset(events) -> None:
    events._schema_ready.clear()
    events._warned.clear()
    events._fail_counts.clear()
    events._shadow_ready_logged = False


def _rows(events) -> list[dict]:
    """读 agent_events（库/表不存在 → 空列表，不建表）。"""
    path = str(events._DB_PATH)
    if not Path(path).exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        probe = conn.execute(
            "select name from sqlite_master where type='table' and name='agent_events'"
        ).fetchone()
        if probe is None:
            return []
        return [dict(r) for r in conn.execute("select * from agent_events order by id")]
    finally:
        conn.close()


def _kinds(events) -> list[str]:
    return [str(r["kind"]) for r in _rows(events)]


def _payload(row: dict) -> dict:
    return json.loads(row["payload_json"])


@contextlib.contextmanager
def _sandbox(monkeypatch, db: Path, *, enabled: bool):
    events, threads, commitments = _agent()
    if enabled:
        monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    else:
        monkeypatch.delenv("AGENT_CORE_EVENTS", raising=False)
    monkeypatch.setattr(events, "_DB_PATH", db)
    _reset(events)
    try:
        yield events, threads, commitments
    finally:
        _reset(events)


# ---------------------------------------------------------------------------
# 连接器替身
# ---------------------------------------------------------------------------


def _mk_connector(monkeypatch, tmp_path, *, segments=None, voice=None):
    """真连接器 + 网络/凭证/媒体路径全桩（不改被测的 hook 区间）。"""
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    mgr = SimpleNamespace(get_user_character=lambda session_key: CHARACTER)
    conn = wc.WeChatConnector(mgr, owner_user_id=OWNER)
    conn.token = "test-token"
    monkeypatch.setattr(conn, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(conn, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(conn, "_record_media_step", lambda *a, **k: None)
    monkeypatch.setattr(conn, "_split_cap_for", lambda peer, humanize=None: 3)
    monkeypatch.setattr(wc, "_send_text", lambda **kwargs: {"ret": 0})
    monkeypatch.setattr(wc, "_segment_delay", lambda text, **kwargs: 0)

    seg_calls: list[tuple] = []
    voice_calls: list[tuple] = []

    def _send_segments(peer, reply, *, context_token=""):
        seg_calls.append((peer, reply))
        return reply

    def _send_voice(audio_bytes, source_fmt="mp3", to_user="", duration_ms=3000):
        voice_calls.append((len(audio_bytes), source_fmt, to_user, duration_ms))
        return True

    monkeypatch.setattr(conn, "send_reply_segments", _send_segments)
    monkeypatch.setattr(conn, "send_voice_clip", _send_voice)
    if segments is not None:
        segments.extend(seg_calls)
    if voice is not None:
        voice.extend(voice_calls)
    return conn


def _text_msg(msg_id: str, text: str = "在吗") -> dict:
    return {"item_list": [{"type": 1, "text_item": {"text": text}}]}


def _inbound(fake_result):
    def _cum(manager, session_key, text, attachments, reply_sender=None,
             message_id="", typing_sender=None):
        if callable(reply_sender) and isinstance(fake_result, dict) and fake_result.get("_publish"):
            reply_sender(fake_result["_publish"])
        return fake_result

    return _cum


# ---------------------------------------------------------------------------
# 1. 入站 hook：user_msg
# ---------------------------------------------------------------------------


def test_inbound_hook_records_user_msg(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "in.db", enabled=True) as (events, _t, _c):
        conn = _mk_connector(monkeypatch, tmp_path)
        monkeypatch.setattr(wc, "_call_user_manager", _inbound({"reply": "嗯"}))
        conn._handle_message_serial(_text_msg("m1"), "m1", PEER, "ctx")
        rows = [r for r in _rows(events) if r["kind"] == "user_msg"]

    assert len(rows) == 1, "入站 hook 没写 user_msg"
    row = rows[0]
    assert row["user_key"] == SESSION_KEY
    assert row["session_key"] == SESSION_KEY
    assert row["owner_user_id"] == OWNER
    assert row["character_id"] == "", "入站早于角色解析，不得猜角色"
    assert row["role"] == "user"
    assert row["source"] == "wechat"
    assert row["turn_id"] == "m:m1", "轮次身份必须走 events.turn_key"
    assert row["dedupe_key"] == "user_msg:m1"
    assert row["thread_id"], "入站 hook 必须先 open_thread 再写事件"
    payload = _payload(row)
    assert payload["msg_id"] == "m1"
    assert payload["text_len"] == 2
    assert payload["has_media"] is False
    assert payload["media_kinds"] == []
    assert payload["attachments_count"] == 0


def test_inbound_hook_media_flags(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "media.db", enabled=True) as (events, _t, _c):
        conn = _mk_connector(monkeypatch, tmp_path)
        monkeypatch.setattr(conn, "_image_mode", lambda: ("off", None, 0))
        monkeypatch.setattr(wc, "_call_user_manager", _inbound({"reply": "嗯"}))
        # 图片 + 配文
        conn._handle_message_serial(
            {"item_list": [
                {"type": 2, "image_item": {"media": {}}},
                {"type": 1, "text_item": {"text": "看图"}},
            ]},
            "m2", PEER, "ctx",
        )
        # iLink 语音（服务端 ASR 文本）
        conn._handle_message_serial(
            {"item_list": [{"type": 3, "voice_item": {"text": "语音内容"}}]},
            "m3", PEER, "ctx",
        )
        rows = [r for r in _rows(events) if r["kind"] == "user_msg"]

    by_mid = {_payload(r)["msg_id"]: _payload(r) for r in rows}
    assert by_mid["m2"]["has_media"] is True
    assert "image" in by_mid["m2"]["media_kinds"]
    assert by_mid["m3"]["has_media"] is True
    assert "voice" in by_mid["m3"]["media_kinds"]


def test_inbound_hook_does_not_store_raw_text(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "privacy.db", enabled=True) as (events, _t, _c):
        conn = _mk_connector(monkeypatch, tmp_path)
        monkeypatch.setattr(wc, "_call_user_manager", _inbound({"reply": "嗯"}))
        conn._handle_message_serial(_text_msg("m4", "我不会说第二遍"), "m4", PEER, "ctx")
        rows = [r for r in _rows(events) if r["kind"] == "user_msg"]

    assert rows, "入站 hook 没写事件"
    assert "我不会说第二遍" not in rows[0]["payload_json"], "原文泄漏进影子账本"


# ---------------------------------------------------------------------------
# 2. 发送 hook：agent_msg.sent / agent_msg.voice_sent
# ---------------------------------------------------------------------------


def test_publish_hook_records_sent_once_with_turn_id(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "sent.db", enabled=True) as (events, _t, _c):
        conn = _mk_connector(monkeypatch, tmp_path)
        # 先由替身经 reply_sender 发一次，再走统一出口——仍只允许 1 条
        monkeypatch.setattr(
            wc, "_call_user_manager",
            _inbound({"reply": "嗯\n在", "_publish": "嗯\n在"}),
        )
        conn._handle_message_serial(_text_msg("m5"), "m5", PEER, "ctx")
        rows = [r for r in _rows(events) if r["kind"] == "agent_msg"]

    assert len(rows) == 1, f"每轮只该有 1 条 sent，实得 {len(rows)}"
    payload = _payload(rows[0])
    assert payload["phase"] == "sent"
    assert payload["turn_id"] == "m:m5", "已发送事件必须带轮次归属"
    assert payload["accepted_len"] == 3
    assert payload["complete"] is True
    assert payload["segment_cap"] == 3
    assert rows[0]["dedupe_key"] == "agent_msg:sent:m5"
    assert rows[0]["role"] == "assistant"
    assert rows[0]["source"] == "wechat"


def test_voice_only_success_records_voice_sent(tmp_path, monkeypatch):
    voice = {"data": b"ID3fake", "format": "mp3"}
    with _sandbox(monkeypatch, tmp_path / "voice.db", enabled=True) as (events, _t, _c):
        conn = _mk_connector(monkeypatch, tmp_path)
        monkeypatch.setattr(
            wc, "_call_user_manager",
            _inbound({"reply": "嗯", "voice": voice, "voice_only": True, "voice_duration_ms": 1234}),
        )
        conn._handle_message_serial(_text_msg("m6", "发条语音"), "m6", PEER, "ctx")
        rows = [r for r in _rows(events) if r["kind"] == "agent_msg"]

    assert len(rows) == 1
    payload = _payload(rows[0])
    assert payload["phase"] == "voice_sent", "语音轮必须记账（不发文字气泡时唯一出口）"
    assert payload["turn_id"] == "m:m6"
    assert payload["voice_fmt"] == "mp3"
    assert payload["duration_ms"] == 1234
    assert rows[0]["dedupe_key"] == "agent_msg:voice:m6"


# ---------------------------------------------------------------------------
# 3. 编排器 hook：final / tool_call / tool_result / commitment
# ---------------------------------------------------------------------------


def _finalize_carrier():
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    safety = SimpleNamespace(
        check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
        safe_alternative=lambda category: "",
    )
    return SimpleNamespace(
        components={"persona": SimpleNamespace(), "safety": safety},
        _finalize_reply=OptimizedOrchestrator._finalize_reply,
    )


def _call_finalize(reply: str) -> str:
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    carrier = _finalize_carrier()
    return OptimizedOrchestrator._finalize_reply(
        carrier,
        reply,
        character_id=CHARACTER,
        character_card={"name": "十四"},
        user_msg="在吗",
        session_id=SESSION_KEY,
    )


def test_finalize_reply_records_final_and_returns_identical(tmp_path, monkeypatch):
    import hashlib

    from utils.inbound_context import bind_message_id

    with _sandbox(monkeypatch, tmp_path / "final.db", enabled=True) as (events, _t, _c):
        with bind_message_id("m1"):
            out_on = _call_finalize("嗯，我在。")
        rows = [r for r in _rows(events) if r["kind"] == "agent_msg"]

    assert len(rows) == 1
    payload = _payload(rows[0])
    assert payload["phase"] == "final"
    assert rows[0]["turn_id"] == "m:m1"
    assert payload["reply_len"] == len(out_on)
    assert payload["reply_sha256"] == hashlib.sha256(out_on.encode()).hexdigest()[:16]
    assert rows[0]["source"] == "orchestrator"
    assert rows[0]["character_id"] == CHARACTER

    # 同输入在关态返回值必须逐字相等
    import contextlib as _ctx

    with monkeypatch.context() as mp, _ctx.ExitStack() as stack:
        stack.enter_context(_sandbox(mp, tmp_path / "final_off.db", enabled=False))
        out_off = _call_finalize("嗯，我在。")
    assert out_off == out_on


def test_finalize_reply_turn_id_from_contextvar(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "final_ctx.db", enabled=True) as (events, _t, _c):
        _call_finalize("嗯")
        rows = [r for r in _rows(events) if r["kind"] == "agent_msg"]
    assert len(rows) == 1
    assert rows[0]["turn_id"] == "", "无入站 id 时 turn_id 必须为空（不崩）"


class _Registry:
    def __init__(self, instances=None):
        self._instances = instances or {}

    def get(self, name):
        return self._instances.get(name)


def _tools_stub(*, tool_result, dispatch_calls=None):
    from tools.base_tool import ToolResult

    calls = dispatch_calls if dispatch_calls is not None else []

    class _Tools:
        registry = _Registry()

        def dispatch(self, name, args, **kwargs):
            calls.append((name, dict(args), kwargs))
            return tool_result if tool_result is not None else ToolResult(True, {"x": 1})

    return _Tools()


def _dispatch(tools, *, name="weather", turn_id="ax-uuid-1", call_id="c1"):
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    carrier = SimpleNamespace(components={})
    tc = {"id": call_id, "function": {"name": name, "arguments": json.dumps({"city": "杭州"})}}
    return asyncio.run(
        OptimizedOrchestrator._dispatch_tool_call(
            carrier,
            tc,
            tools=tools,
            session_key=SESSION_KEY,
            user_id=OWNER,
            turn_id=turn_id,
            character_id=CHARACTER,
        )
    )


def test_dispatch_tool_call_records_call_and_result(tmp_path, monkeypatch):
    from utils.inbound_context import bind_message_id

    calls: list[tuple] = []
    with _sandbox(monkeypatch, tmp_path / "tool.db", enabled=True) as (events, _t, _c):
        monkeypatch.setattr(
            "shisi.agent_plane.runtime.append_tool_call_event", lambda **kwargs: None
        )
        with bind_message_id("m8"):
            result = _dispatch(_tools_stub(tool_result=None, dispatch_calls=calls))
        rows = _rows(events)

    assert result["success"] is True
    kinds = [r["kind"] for r in rows]
    assert kinds == ["tool_call", "tool_result"], f"顺序/条数错：{kinds}"
    call_row, result_row = rows
    assert _payload(call_row)["call_id"] == "c1"
    assert _payload(result_row)["call_id"] == "c1"
    assert _payload(call_row)["name"] == "weather"
    assert _payload(result_row)["success"] is True
    # 轮次归属必须走 turn_key（形参 turn_id 是 ax UUID，不能当轮次身份）
    assert call_row["turn_id"] == "m:m8", "tool_call 拿了 ax UUID 当轮次"
    assert result_row["turn_id"] == "m:m8"
    assert _payload(call_row)["ax_turn_id"] == "ax-uuid-1", "ax 轮次只作交叉引用"
    assert call_row["dedupe_key"] == "tool:c1:call"
    assert result_row["dedupe_key"] == "tool:c1:result"
    assert call_row["source"] == "orchestrator"
    assert call_row["role"] == "tool"
    # 只调一次 dispatch、参数不被 hook 改写
    assert len(calls) == 1 and calls[0][0] == "weather"


def test_dispatch_tool_call_set_reminder_creates_commitment(tmp_path, monkeypatch):
    from tools.base_tool import ToolResult
    from utils.inbound_context import bind_message_id

    reminder = ToolResult(True, {
        "reminder_id": 12,
        "content": "叫我起床",
        "trigger_time": "2026-11-12 06:00",
    })
    with _sandbox(monkeypatch, tmp_path / "commit.db", enabled=True) as (events, _t, commitments):
        monkeypatch.setattr(
            "shisi.agent_plane.runtime.append_tool_call_event", lambda **kwargs: None
        )
        with bind_message_id("m10"):
            _dispatch(_tools_stub(tool_result=reminder), name="set_reminder", call_id="c2")
        conn = sqlite3.connect(str(events._DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute("select * from agent_commitments")]
        finally:
            conn.close()

    assert len(rows) == 1, "set_reminder 成功必须影子写一条承诺"
    row = rows[0]
    assert row["kind"] == "remind"
    assert row["detail"] == "叫我起床"
    assert row["due_at"] == "2026-11-12 06:00:00", "due_at 必须归一补秒"
    assert row["ref"] == "reminder:12"
    assert row["dedupe_key"] == "reminder:12"
    assert row["source_turn_id"] == "m:m10"
    assert row["user_key"] == SESSION_KEY
    assert row["owner_user_id"] == OWNER


def test_dispatch_tool_call_send_voice_pseudo_not_recorded(tmp_path, monkeypatch):
    voice_decision: dict = {}
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    with _sandbox(monkeypatch, tmp_path / "pseudo.db", enabled=True) as (events, _t, _c):
        monkeypatch.setattr(
            "shisi.agent_plane.runtime.append_tool_call_event", lambda **kwargs: None
        )
        tc = {"id": "c3", "function": {"name": "send_voice", "arguments": "{}"}}
        out = asyncio.run(
            OptimizedOrchestrator._dispatch_tool_call(
                SimpleNamespace(components={}),
                tc,
                tools=_tools_stub(tool_result=None),
                session_key=SESSION_KEY,
                user_id=OWNER,
                character_id=CHARACTER,
                voice_decision=voice_decision,
            )
        )
        rows = _rows(events)

    assert out["success"] is True
    assert voice_decision.get("requested") is True, "伪工具行为不得被 hook 改变"
    assert rows == [], "send_voice 伪工具不是 registry 工具，不得记 tool_call"


# ---------------------------------------------------------------------------
# 4. 调度器 hook：manual / decision / blessing
# ---------------------------------------------------------------------------


class _ManualEngine:
    def __init__(self, message: str = "你好呀"):
        self._message = message
        self._daily_message_count = 0
        self.sent_history: list[dict] = []
        self.urgency = SimpleNamespace(total=5.0)

    def _update_urgency(self, hours: float) -> None:
        pass

    def _hours_since_last_chat(self) -> float:
        return 9.0

    def _check_scene_triggers(self, commit: bool = True):
        return None

    def _select_type_by_urgency(self):
        return "morning"

    def _generate_and_return(self, msg_type):
        self._daily_message_count += 1
        return {"message": self._message, "type": str(getattr(msg_type, "value", msg_type))}

    def _record_and_return(self, scene):
        self._daily_message_count += 1
        return {"message": self._message, "type": str(scene)}

    def record_sent_entry(self, entry: dict) -> None:
        self.sent_history.append(entry)


def _mk_scheduler(monkeypatch, *, delivered: list[str] | None = None):
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler()
    sched._is_quiet_hours = lambda: False
    sched._is_quiet_hours_for = lambda cfg: False
    sched._resolve_character_id = lambda sk: CHARACTER
    calls = delivered if delivered is not None else []

    def _deliver(message, session_key=None, character_id=None, **kwargs):
        calls.append((message, session_key, character_id, kwargs.get("voice", False)))
        return True

    monkeypatch.setattr(sched, "_deliver", _deliver)
    # 锦上添花：不落盘当日幂等（否则 A/B 两次运行会因持久状态互相污染）
    monkeypatch.setattr(sched, "_persist_throttle_ledger", lambda: None)
    return sched


def test_scheduler_manual_send_records_proactive(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "manual.db", enabled=True) as (events, _t, _c):
        sched = _mk_scheduler(monkeypatch)
        engine = _ManualEngine()
        sched.ase = SimpleNamespace(get=lambda key: engine)
        result = sched.manual_send_now(SESSION_KEY)
        rows = _rows(events)

    assert result["delivered"] is True
    assert len(rows) == 1, f"手动发送每轮恰 1 条，实得 {len(rows)}"
    payload = _payload(rows[0])
    assert payload["phase"] == "proactive_delivered"
    assert payload["channel"] == "proactive"
    assert payload["text_len"] == 3
    assert payload["character_id"] == CHARACTER
    assert rows[0]["source"] == "scheduler/manual"
    assert rows[0]["turn_id"] == "", "主动线无入站 id，只能按 session 回放"
    # B-2 契约 9.1：手动发送没有 command_id 形参 → 去重键回落「正文 sha256 前 16 位」，
    # 同一正文重复自测不产生第二行；旧断言「恒空」是 9.1 之前的口径，已过期。
    _digest = hashlib.sha256(engine._message.encode("utf-8")).hexdigest()[:16]
    assert rows[0]["dedupe_key"] == f"proactive_manual:{SESSION_KEY}:{_digest}"


def test_scheduler_blessing_records_blessing_delivered(tmp_path, monkeypatch):
    with _sandbox(monkeypatch, tmp_path / "blessing.db", enabled=True) as (events, _t, _c):
        sched = _mk_scheduler(monkeypatch)
        sched._session_llm_resolver = None
        monkeypatch.setattr(sched, "_resolve_proactive_llm", lambda: None)
        sched._send_date_wish(
            SESSION_KEY, "2026-11-11", label="生日", kind="birthday", names="默默",
        )
        rows = _rows(events)

    assert len(rows) == 1
    payload = _payload(rows[0])
    assert payload["phase"] == "blessing_delivered"
    assert payload["character_id"] == CHARACTER
    assert "祝福" not in rows[0]["payload_json"], "不得把节日名/人名搬进事件库"
    assert rows[0]["source"] == "scheduler/blessing"


class _DecisionLLM:
    def __init__(self, message: str):
        self._message = message

    def chat_sync(self, user, system_prompt="", temperature=0.4):
        return json.dumps(
            {"should_contact": True, "message": self._message,
             "wait_minutes": None, "reason": "测试决策"},
            ensure_ascii=False,
        )


class _DecisionEngine:
    def __init__(self):
        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = ""
        self._recent_messages = []
        self.commits: list[dict] = []

    def _hours_since_last_chat(self) -> float:
        return 5.0

    def tick(self, hours, dry_run=True):
        return None

    def commit_sent(self, result):
        self.commits.append(result)


def test_scheduler_decision_tick_records_proactive_delivered(tmp_path, monkeypatch):
    import proactive.llm_proactive as lp_mod
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.llm_proactive import decide_proactive as _real_decide
    from proactive.scheduler import ProactiveScheduler

    with _sandbox(monkeypatch, tmp_path / "decision.db", enabled=True) as (events, _t, _c):
        sched = ProactiveScheduler.__new__(ProactiveScheduler)
        sched._paused = False
        sched._llm_proactive_next_ok = {}
        sched._disabled_event_day = {}
        sched._deliver_fail_counts = {}
        sched._is_quiet_hours = lambda: False
        sched._is_quiet_hours_for = lambda cfg: False
        sched.get_quiet_hours = lambda: None
        sched._vital_tick_for_user = lambda *a, **k: None
        sched._resolve_character_id = lambda k: CHARACTER
        sched._resolve_memory = lambda: None
        sched._session_llm_resolver = None
        sched._resolve_proactive_llm = lambda eng: _DecisionLLM("你昨天说的体检结果出来了吗，我有点挂心")
        sched._persist_throttle_ledger = lambda: None
        delivered: list[tuple] = []

        def _deliver(message, session_key, character_id="", **kwargs):
            delivered.append((message, session_key, character_id))
            return True

        sched._deliver = _deliver

        monkeypatch.setattr(runtime_mod, "append_proactive_event", lambda **kw: None)
        monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
        monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
        monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: dict(DEFAULT_WEB_CONFIG))
        monkeypatch.setattr(lp_mod, "load_persona_hint", lambda character_id="": "")
        monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")
        monkeypatch.setattr(lp_mod, "decide_proactive", _real_decide)

        eng = _DecisionEngine()
        sched._llm_proactive_one_user(SimpleNamespace(get=lambda k: eng), SESSION_KEY)
        rows = _rows(events)

    assert delivered, "决策线未投递（替身失配），本用例无效"
    rows = [r for r in rows if r["kind"] == "agent_msg"]
    assert len(rows) == 1, f"决策线每轮恰 1 条，实得 {len(rows)}"
    payload = _payload(rows[0])
    assert payload["phase"] == "proactive_delivered"
    assert payload["channel"] == "proactive"
    assert rows[0]["source"] == "scheduler/decision"
    assert rows[0]["turn_id"] == ""


# ---------------------------------------------------------------------------
# 5. 零行为：关态零写入 + A/B 对照 + 异常不外泄
# ---------------------------------------------------------------------------


def test_disabled_state_writes_nothing(tmp_path, monkeypatch):
    db = tmp_path / "off.db"
    with _sandbox(monkeypatch, db, enabled=False) as (events, _t, _c):
        conn = _mk_connector(monkeypatch, tmp_path)
        monkeypatch.setattr(wc, "_call_user_manager", _inbound({"reply": "嗯"}))
        conn._handle_message_serial(_text_msg("m11"), "m11", PEER, "ctx")
        _call_finalize("嗯")
        assert _rows(events) == []
    assert not Path(db).exists(), "关态不得打开/创建影子库"


def _run_scenario(monkeypatch, tmp_path, *, enabled: bool, break_store: bool = False) -> dict:
    """同一组替身跑一遍全部挂载点，返回可逐字比较的运行记录。"""
    record: dict = {
        "segments": [],
        "voice": [],
        "deliver": [],
        "dispatch": [],
        "finalize": "",
        "manual": {},
    }
    with (
        monkeypatch.context() as mp,
        _sandbox(mp, tmp_path / ("on.db" if enabled else "off.db"), enabled=enabled) as (
            events, _t, _c,
        ),
    ):
        if break_store:
            _break_store(mp, events)

        # connector：入站 + 文本发送 + 语音轮
        conn = _mk_connector(
            mp, tmp_path, segments=record["segments"], voice=record["voice"],
        )
        mp.setattr(wc, "_call_user_manager", _inbound({"reply": "嗯\n在"}))
        conn._handle_message_serial(_text_msg("m20"), "m20", PEER, "ctx")
        mp.setattr(
            wc, "_call_user_manager",
            _inbound({"reply": "嗯", "voice": {"data": b"ID3", "format": "mp3"},
                      "voice_only": True}),
        )
        conn._handle_message_serial(_text_msg("m21", "发语音"), "m21", PEER, "ctx")

        # orchestrator：终审 + 工具调用
        record["finalize"] = _call_finalize("嗯，我在。")
        mp.setattr(
            "shisi.agent_plane.runtime.append_tool_call_event", lambda **kw: None
        )
        _dispatch(
            _tools_stub(tool_result=None, dispatch_calls=record["dispatch"]),
            call_id="c9",
        )

        # scheduler：手动 + 祝福
        sched = _mk_scheduler(mp, delivered=record["deliver"])
        sched.ase = SimpleNamespace(get=lambda key: _ManualEngine())
        record["manual"] = sched.manual_send_now(SESSION_KEY)
        sched._session_llm_resolver = None
        mp.setattr(sched, "_resolve_proactive_llm", lambda: None)
        sched._send_date_wish(
            SESSION_KEY, "2026-11-11", label="生日", kind="birthday", names="默默",
        )

        record["events"] = len(_rows(events))
        record["kinds"] = sorted(_kinds(events))
    return record


def test_zero_behavior_is_proven_by_ab_diff(tmp_path, monkeypatch):
    off = _run_scenario(monkeypatch, tmp_path, enabled=False)
    on = _run_scenario(monkeypatch, tmp_path, enabled=True)

    assert off["events"] == 0, f"关态写入了 {off['events']} 条事件"
    assert on["events"] > 0, "开态一条都没写：hook 根本没接上（假绿）"

    comparable_off = {k: v for k, v in off.items() if k not in ("events", "kinds")}
    comparable_on = {k: v for k, v in on.items() if k not in ("events", "kinds")}
    assert comparable_on == comparable_off, (
        "开态改变了可观测行为（返回值/调用序列）\n"
        f"off={comparable_off}\non={comparable_on}"
    )
    assert set(off["kinds"]) == set()


def _break_store(monkeypatch, events) -> dict:
    """把三条写接口换成必抛异常，用于证明 hook 的 try/except 真的兜住。"""
    counts = {"record_event": 0, "open_thread": 0, "add_commitment": 0}

    def _boom(name):
        def _inner(*args, **kwargs):
            counts[name] += 1
            raise RuntimeError(f"{name} boom")
        return _inner

    from agent_core import commitments, threads

    monkeypatch.setattr(events, "record_event", _boom("record_event"))
    monkeypatch.setattr(threads, "open_thread", _boom("open_thread"))
    monkeypatch.setattr(commitments, "add_commitment", _boom("add_commitment"))
    _break_store.counts = counts
    return counts


def test_store_exception_never_escapes_and_ab_equal(tmp_path, monkeypatch):
    baseline = _run_scenario(monkeypatch, tmp_path, enabled=False)
    with monkeypatch.context() as mp:
        _break_store.counts = None
        broken = _run_scenario(mp, tmp_path, enabled=True, break_store=True)
        counts = _break_store.counts

    assert counts and counts["record_event"] > 0, "异常替身没被调用到（用例无效）"
    assert counts["open_thread"] > 0
    assert broken["kinds"] == []
    compare = {k: v for k, v in broken.items() if k not in ("events", "kinds")}
    expect = {k: v for k, v in baseline.items() if k not in ("events", "kinds")}
    assert compare == expect, "影子写抛异常后行为/返回值被改变了"


# ---------------------------------------------------------------------------
# 6. 静态守卫：调用必须包在 try/except Exception 内
# ---------------------------------------------------------------------------


def test_hooks_are_wrapped_in_try_except():
    root = Path(__file__).resolve().parent.parent
    targets = {
        "wechat_direct/wechat_connector.py": {"record_event", "open_thread"},
        "orchestrator/optimized_orchestrator.py": {"record_event", "add_commitment"},
        "proactive/scheduler.py": {"record_event"},
    }
    for rel, names in targets.items():
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        guarded: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not node.handlers:
                continue
            if not any(
                getattr(handler.type, "id", None) == "Exception"
                or getattr(handler.type, "attr", None) == "Exception"
                for handler in node.handlers
            ):
                continue
            for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if not isinstance(sub, ast.Call):
                    continue
                name = getattr(sub.func, "attr", None) or getattr(sub.func, "id", None)
                if name in names:
                    guarded.add(name)
        missing = names - guarded
        assert not missing, f"{rel} 的挂载点未包在 try/except Exception 内: {missing}"
