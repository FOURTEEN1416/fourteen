"""agent_core replay 域 —— 离线回放工具红测（B-1）。

契约：已批准实施契约「agent_core.replay」段（2026-11-11）。本文件只覆盖
``agent_core/replay.py`` 的**只读回放语义**；store 三层（events / threads /
commitments）与四个挂载点（connector / orchestrator / scheduler）由同批其它
文件负责（挂载点不在本文件所有者边界内，见 ``test_replay_real_turn_key_*``
的注记）。

三条硬要求在本文件的钉子：

1. **按 session 作用域过滤，禁止跨用户**（``test_replay_is_isolated_across_users``
   / ``test_replay_rejects_unattributable_session``）——SQL 恒带
   ``owner_user_id=? AND user_key=? AND session_key=?``。
2. **只读**（``test_replay_is_read_only_and_never_creates_schema``）——回放前后
   三表 count / sqlite_master 完全不变；全新空路径回放后**连文件都不创建**。
3. **store 异常向上抛**（``test_replay_propagates_store_errors``）——离线工具
   不 fail-open：坏库必须炸出来，不能被吞成「空结果」。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

SESSION_A = "2:o9cq80@im.wechat"
SESSION_B = "4:o9cq80other@im.wechat"
OWNER_A = 2
OWNER_B = 4

RESULT_KEYS = {
    "ok",
    "reason",
    "user_key",
    "owner_user_id",
    "session_key",
    "turn_id",
    "events",
    "thread",
    "commitments",
}
EVENT_KEYS = {
    "event_id",
    "user_key",
    "session_key",
    "owner_user_id",
    "character_id",
    "turn_id",
    "thread_id",
    "kind",
    "role",
    "source",
    "payload",
    "created_at",
}


# ---------------------------------------------------------------------------
# 夹具 / 工具
# ---------------------------------------------------------------------------


def _modules():
    from agent_core import commitments, events, threads

    return events, threads, commitments


def _reset(events) -> None:
    events._schema_ready.clear()
    events._warned.clear()
    events._fail_counts.clear()
    events._shadow_ready_logged = False


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """开启态 + 沙箱库（conftest 不在本批边界内，沙箱必须写在文件里）。"""
    monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    events, threads, commitments = _modules()
    monkeypatch.setattr(events, "_DB_PATH", tmp_path / "agent_core.db")
    _reset(events)
    yield events, threads, commitments
    _reset(events)


@pytest.fixture()
def replay(store):
    from agent_core import replay as replay_module

    return replay_module


def _query(events, sql: str, params=()) -> list[dict]:
    conn = sqlite3.connect(str(events._DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _schema_snapshot(events) -> dict:
    conn = sqlite3.connect(str(events._DB_PATH))
    try:
        objects = sorted(
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type in ('table', 'index') order by name"
            )
        )
    finally:
        conn.close()
    counts = {
        table: _query(events, f"select count(*) as c from {table}")[0]["c"]
        for table in ("agent_events", "agent_threads", "agent_commitments")
    }
    counts["agent_events_max_id"] = _query(events, "select coalesce(max(id), 0) as m from agent_events")[0]["m"]
    return {"objects": objects, "counts": counts}


def _seed_events(events, *, session_key=SESSION_A, count=2, prefix="turn"):
    """按 turn 造事件，payload 带序号便于断言顺序。"""
    return [
        events.record_event(
            "user_msg",
            session_key=session_key,
            turn_id=f"m:{prefix}-{i}",
            role="user",
            source="wechat",
            payload={"n": i},
            created_at="2026-11-11 09:00:00",
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# 模块面
# ---------------------------------------------------------------------------


def test_replay_module_exposes_both_entrypoints(replay):
    assert callable(replay.replay_turn)
    assert callable(replay.replay_session)


# ---------------------------------------------------------------------------
# replay_turn
# ---------------------------------------------------------------------------


def test_replay_turn_returns_only_that_turn_in_insert_order(store, replay):
    events, threads, _commitments = store
    tid = threads.open_thread(user_key=SESSION_A, session_key=SESSION_A)
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:A", role="user", payload={"i": 1})
    events.record_event("tool_call", session_key=SESSION_A, turn_id="m:A", role="tool", payload={"i": 2})
    events.record_event("agent_msg", session_key=SESSION_A, turn_id="m:A", payload={"i": 3, "phase": "final"})
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:B", role="user", payload={"i": 4})
    events.record_event("agent_msg", session_key=SESSION_A, turn_id="m:B", payload={"i": 5})

    result = replay.replay_turn(SESSION_A, "m:A")

    assert result["ok"] is True
    assert result["reason"] == ""
    assert result["session_key"] == SESSION_A
    assert result["user_key"] == SESSION_A
    assert result["owner_user_id"] == OWNER_A
    assert result["turn_id"] == "m:A"
    assert [e["payload"]["i"] for e in result["events"]] == [1, 2, 3]
    assert {e["turn_id"] for e in result["events"]} == {"m:A"}
    assert result["thread"]["thread_id"] == tid


def test_replay_real_turn_key_completes_one_turn(store, replay):
    """轮次身份层集成：用 ``events.turn_key``（唯一 owner）造五类事件后能一次取全。

    ⚠️ 边界：四个挂载点（connector / orchestrator / scheduler）不在本文件所有者
    边界内，其实跑由 shadow 批的 ``test_agent_core_shadow_*.py`` 负责；本用例钉的是
    「同一 turn identity 下五种 kind 都能回放出来且 turn_id 全等」。
    """
    from utils.inbound_context import bind_message_id

    events, threads, _commitments = store
    threads.open_thread(user_key=SESSION_A, session_key=SESSION_A)
    with bind_message_id("m1"):
        turn_id = events.turn_key()
        assert turn_id == "m:m1"
        events.record_event("user_msg", session_key=SESSION_A, turn_id=turn_id, role="user")
        events.record_event("tool_call", session_key=SESSION_A, turn_id=turn_id, role="tool")
        events.record_event("tool_result", session_key=SESSION_A, turn_id=turn_id, role="tool")
        events.record_event("agent_msg", session_key=SESSION_A, turn_id=turn_id, payload={"phase": "final"})
        events.record_event("agent_msg", session_key=SESSION_A, turn_id=turn_id, payload={"phase": "sent"})

    result = replay.replay_turn(SESSION_A, "m:m1")
    kinds = [e["kind"] for e in result["events"]]
    assert kinds == ["user_msg", "tool_call", "tool_result", "agent_msg", "agent_msg"]
    assert {e["turn_id"] for e in result["events"]} == {"m:m1"}
    assert [e["payload"].get("phase") for e in result["events"]][-2:] == ["final", "sent"]


def test_replay_turn_requires_turn_id_without_touching_db(store, replay, monkeypatch):
    events, _threads, _commitments = store

    def _boom(*_a, **_k):
        raise AssertionError("turn_id 为空时不得连接 DB")

    monkeypatch.setattr(events, "connect", _boom)
    result = replay.replay_turn(SESSION_A, "")

    assert result["ok"] is False
    assert result["reason"] == "turn_id_required"
    assert result["events"] == []
    assert result["thread"] is None
    assert result["commitments"] == []


def test_replay_turn_respects_limit(store, replay):
    events, _threads, _commitments = store
    for i in range(6):
        events.record_event("tool_call", session_key=SESSION_A, turn_id="m:A", role="tool", payload={"i": i})

    result = replay.replay_turn(SESSION_A, "m:A", limit=4)
    assert [e["payload"]["i"] for e in result["events"]] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# replay_session
# ---------------------------------------------------------------------------


def test_replay_session_limit_returns_last_n_ascending(store, replay):
    events, _threads, _commitments = store
    _seed_events(events, count=10)

    result = replay.replay_session(SESSION_A, limit=5)

    assert result["ok"] is True
    assert [e["payload"]["n"] for e in result["events"]] == [5, 6, 7, 8, 9]
    assert result["summary"]["event_count"] == 5


def test_replay_session_kind_filter(store, replay):
    events, _threads, _commitments = store
    events.record_event("user_msg", session_key=SESSION_A, role="user")
    events.record_event("tool_call", session_key=SESSION_A, role="tool")
    events.record_event("tool_result", session_key=SESSION_A, role="tool")
    events.record_event("agent_msg", session_key=SESSION_A)

    result = replay.replay_session(SESSION_A, kinds=["tool_call", "tool_result"])

    assert [e["kind"] for e in result["events"]] == ["tool_call", "tool_result"]
    assert result["summary"]["by_kind"] == {"tool_call": 1, "tool_result": 1}


def test_replay_session_summary_counts_turns_and_phases(store, replay):
    events, _threads, _commitments = store
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:1", role="user")
    events.record_event("tool_call", session_key=SESSION_A, turn_id="m:1", role="tool")
    events.record_event("agent_msg", session_key=SESSION_A, turn_id="m:1", payload={"phase": "final"})
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:2", role="user")
    events.record_event("agent_msg", session_key=SESSION_A, turn_id="m:2", payload={"phase": "sent"})
    events.record_event("agent_msg", session_key=SESSION_A, turn_id="m:2", payload={"phase": "sent"})

    summary = replay.replay_session(SESSION_A)["summary"]

    assert summary["event_count"] == 6
    assert summary["turn_count"] == 2
    assert summary["by_kind"] == {"user_msg": 2, "tool_call": 1, "agent_msg": 3}
    assert summary["by_phase"] == {"final": 1, "sent": 2}


def test_replay_session_empty_result_is_empty_list(store, replay):
    events, _threads, _commitments = store
    events.ensure_schema()

    result = replay.replay_session(SESSION_A)

    assert result["ok"] is True
    assert result["events"] == []
    assert result["commitments"] == []
    assert result["thread"] is None
    assert result["summary"] == {"event_count": 0, "by_kind": {}, "by_phase": {}, "turn_count": 0}


# ---------------------------------------------------------------------------
# 隔离与作用域
# ---------------------------------------------------------------------------


def test_replay_is_isolated_across_users(store, replay):
    events, _threads, _commitments = store
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:A", role="user")
    events.record_event("user_msg", session_key=SESSION_B, turn_id="m:B", role="user")

    own = replay.replay_turn(SESSION_A, "m:A")
    assert own["ok"] is True
    assert len(own["events"]) == 1

    crossed = replay.replay_turn(SESSION_A, "m:A", user_key=SESSION_B)
    assert crossed["ok"] is False
    assert crossed["reason"] == "scope_mismatch"
    assert crossed["events"] == []

    assert replay.replay_session(SESSION_B)["events"][0]["turn_id"] == "m:B"
    assert replay.replay_session(SESSION_B, user_key=SESSION_A)["reason"] == "scope_mismatch"


def test_replay_rejects_unattributable_session(store, replay):
    events, _threads, _commitments = store
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:A", role="user")

    empty = replay.replay_session("")
    assert empty["ok"] is False
    assert empty["reason"] == "session_key_required"
    assert empty["events"] == []

    bare = replay.replay_session("wxid@im.wechat")
    assert bare["ok"] is False
    assert bare["reason"] == "unattributable_session"
    assert bare["events"] == []  # 没有「全表兜底」：数据在库里，但无归属就不给


# ---------------------------------------------------------------------------
# thread / commitments
# ---------------------------------------------------------------------------


def test_replay_includes_thread_and_commitments(store, replay):
    events, threads, commitments = store
    tid = threads.open_thread(user_key=SESSION_A, session_key=SESSION_A, topic="早饭")
    events.record_event("user_msg", session_key=SESSION_A, turn_id="m:A", role="user", thread_id=tid)
    same_turn = commitments.add_commitment(
        "叫我起床",
        user_key=SESSION_A,
        session_key=SESSION_A,
        thread_id=tid,
        source_turn_id="m:A",
        due_at="2026-11-12 06:00",
        ref="reminder:12",
    )
    commitments.add_commitment(
        "另一轮的承诺",
        user_key=SESSION_A,
        session_key=SESSION_A,
        source_turn_id="m:Z",
        ref="reminder:99",
    )

    turn = replay.replay_turn(SESSION_A, "m:A")
    assert turn["thread"]["thread_id"] == tid
    assert turn["thread"]["topic"] == "早饭"
    assert [c["commitment_id"] for c in turn["commitments"]] == [same_turn]

    session = replay.replay_session(SESSION_A)
    assert {c["commitment_id"] for c in session["commitments"]} == {
        same_turn,
        _query(events, "select commitment_id from agent_commitments where ref='reminder:99'")[0]["commitment_id"],
    }


def test_replay_commitments_are_scoped_to_session(store, replay):
    events, threads, commitments = store
    threads.open_thread(user_key=SESSION_A, session_key=SESSION_A)
    threads.open_thread(user_key=SESSION_B, session_key=SESSION_B)
    mine = commitments.add_commitment("我的", user_key=SESSION_A, session_key=SESSION_A)
    commitments.add_commitment("别人的", user_key=SESSION_B, session_key=SESSION_B)

    result = replay.replay_session(SESSION_A)
    assert [c["commitment_id"] for c in result["commitments"]] == [mine]


# ---------------------------------------------------------------------------
# 只读 / 缺表 / 异常传播
# ---------------------------------------------------------------------------


def test_replay_is_read_only_and_never_creates_schema(store, replay):
    events, threads, _commitments = store
    threads.open_thread(user_key=SESSION_A, session_key=SESSION_A)
    _seed_events(events, count=3)
    before = _schema_snapshot(events)

    replay.replay_turn(SESSION_A, "m:turn-1")
    replay.replay_session(SESSION_A)

    assert _schema_snapshot(events) == before


def test_replay_never_creates_db_file(tmp_path, monkeypatch, replay):
    events, _threads, _commitments = _modules()
    monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    path = tmp_path / "absent.db"
    monkeypatch.setattr(events, "_DB_PATH", path)
    _reset(events)

    turn = replay.replay_turn(SESSION_A, "m:A")
    session = replay.replay_session(SESSION_A)

    assert turn["ok"] is False and turn["reason"] == "tables_missing"
    assert session["ok"] is False and session["reason"] == "tables_missing"
    assert not path.exists(), "回放是只读工具：库不存在时不得创建空库文件"


def test_replay_tolerates_missing_tables(store, replay):
    events, _threads, _commitments = store
    # 建库但不建表（模拟「库被别的模块创建过、agent_core 从没跑过」）
    with closing(sqlite3.connect(str(events._DB_PATH))) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()

    turn = replay.replay_turn(SESSION_A, "m:A")
    session = replay.replay_session(SESSION_A)

    assert turn["ok"] is False and turn["reason"] == "tables_missing" and turn["events"] == []
    assert session["ok"] is False and session["reason"] == "tables_missing" and session["events"] == []
    # 未顺手建表
    assert _query(events, "select name from sqlite_master where name like 'agent_%'") == []


def test_payload_parse_error_is_contained(store, replay):
    events, _threads, _commitments = store
    events.record_event("agent_msg", session_key=SESSION_A, turn_id="m:A", payload={"phase": "sent"})
    with closing(sqlite3.connect(str(events._DB_PATH))) as conn:
        conn.execute("UPDATE agent_events SET payload_json='{bad'")
        conn.commit()

    result = replay.replay_turn(SESSION_A, "m:A")

    assert result["ok"] is True
    assert len(result["events"]) == 1
    assert result["events"][0]["payload"] == {"_parse_error": True}


def test_replay_event_row_has_exact_contract_keys(store, replay):
    events, _threads, _commitments = store
    events.record_event(
        "user_msg",
        session_key=SESSION_A,
        character_id="lin",
        turn_id="m:A",
        thread_id="tid",
        role="user",
        source="wechat",
        payload={"msg_id": "m1"},
    )

    row = replay.replay_turn(SESSION_A, "m:A")["events"][0]

    assert set(row) == EVENT_KEYS
    assert row["payload"] == {"msg_id": "m1"}
    assert row["owner_user_id"] == OWNER_A
    assert set(replay.replay_turn(SESSION_A, "m:A")) == RESULT_KEYS


def test_replay_propagates_store_errors(store, replay, tmp_path, monkeypatch):
    """离线工具不 fail-open：坏库必须抛，不能吞成空结果。"""
    events, _threads, _commitments = store
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is definitely not a sqlite database" * 8)
    monkeypatch.setattr(events, "_DB_PATH", corrupt)
    _reset(events)

    with pytest.raises(sqlite3.DatabaseError):
        replay.replay_turn(SESSION_A, "m:A")
    with pytest.raises(sqlite3.DatabaseError):
        replay.replay_session(SESSION_A)


# ===========================================================================
# 2026-11-10 生产事故回放序列（B-2：承诺账本从「只写」升级为「读侧消费」）
# ===========================================================================
#
# 真实序列（主控取证）：
#   09:23  主动线产出一句含承诺语义的正文「我这边有之前整理的笔记，我帮你理理思路」
#   10:24 / 17:35 / 19:35  三次 tick —— 该承诺不得再作为 hooks 由头被复用，
#                          且 prompt 必须出现「你还没兑现的承诺」段
#   之后   用户回「笔记我收到了，谢谢」——承诺闭环（open → fulfilled）
#
# 本区块是**黑盒回放**：不复制 scheduler 的注入逻辑，只驱动
# `ProactiveScheduler._llm_proactive_one_user`（真实函数）并观察它的三个可观测面：
#   · `build_proactive_context` 的产物（decide_proactive 第一个位置参数 = 最终 prompt）
#   · `hook_keywords`（hooks 候选，供多候选竞争择优）
#   · `_deliver` 收到的正文（证明只剔由头、不拦正文）
#
# ⚠️ 依赖边界：`agent_core/commitment_gate.py`（冻结规格）与
# `commitments.close_commitment`（契约 2b）由同批其它域实施。本区块的红测**先于**
# 实现落地；在那些域就绪前这些用例应为红（ModuleNotFoundError / AttributeError /
# 断言失败），三域汇合后统一跑门禁。既有 B-1 用例不受影响（import 全在函数体内）。

PROMISE_TEXT = "我这边有之前整理的笔记，我帮你理理思路"
RECEIPT_TEXT = "笔记我收到了，谢谢"
UNRELATED_HOOK = "周末降温"
COMMIT_CHARACTER = "card_commit_001"
TICK_TIMES = ("2026-11-10 09:23", "2026-11-10 10:24", "2026-11-10 17:35", "2026-11-10 19:35")


class _SceneEngine:
    """主动线引擎替身：只实现 `_llm_proactive_one_user` 读到的属性/方法。"""

    def __init__(self) -> None:
        from collections import deque
        from types import SimpleNamespace

        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = COMMIT_CHARACTER
        self._recent_messages = deque()
        self.commits: list[dict] = []
        self._last_delivery_time = None

    def _hours_since_last_chat(self) -> float:
        return 5.0

    def tick(self, hours, dry_run=True):  # noqa: ANN001
        return None

    def commit_sent(self, result) -> None:  # noqa: ANN001
        self.commits.append(result)

    def evaluate_dice_gate(self, requested_mode="live"):  # noqa: ANN001
        return True, None


class _Scene:
    """一次回放的共享状态：时钟、投递记录、decide 观测面、账本。"""

    def __init__(self) -> None:
        from datetime import datetime

        self.now = datetime.strptime(TICK_TIMES[0], "%Y-%m-%d %H:%M")
        self.delivered: list[str] = []
        self.contexts: list[str] = []
        self.hook_keywords: list[list[str]] = []
        self.ledger: list[dict] = []
        self.decisions: list[dict] = []


@pytest.fixture()
def scene(store, monkeypatch):
    """搭好主动线回放场：沙箱库 + 真实 `_llm_proactive_one_user` + 全替身。"""
    from types import SimpleNamespace

    import proactive.ase_engine as ase_engine
    import proactive.llm_proactive as lp
    import proactive.proactive_context as proactive_context
    import proactive.proactive_hooks as proactive_hooks
    import proactive.topic_recall as topic_recall
    import shisi.agent_plane.runtime as runtime_mod
    import utils.character_settings as cs_mod
    import utils.proactive_config as pc_mod
    import utils.topic_state as topic_mod
    from proactive.proactive_hooks import LiveHooks
    from proactive.scheduler import ProactiveScheduler
    from utils.proactive_config import EffectiveConfig

    events, _threads, _commitments = store
    events.ensure_schema()

    state = _Scene()
    engine = _SceneEngine()

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._quiet_event_day = {}
    sched._wait_window_event_day = {}
    sched._deliver_fail_counts = {}
    sched._deliver_fail_seen = {}
    sched._judge_recent = {}
    sched._judge_topic = {}
    sched._last_deliver_errors = {}
    sched._last_deliver_error = ""
    sched._quiet_hours = (23, 7)
    sched._llm_provider = None
    sched._session_llm_resolver = None
    sched._memory = None

    sched._is_quiet_hours_for = lambda cfg: False
    sched._quiet_hours_for = lambda cfg: (23, 7)
    sched.get_quiet_hours = lambda: (23, 7)
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda key: COMMIT_CHARACTER
    sched._resolve_memory = lambda: None
    sched._primary_model_ok = lambda: True
    sched._apply_user_engine_config = lambda *a, **k: None
    sched._proactive_sent_today = lambda eng, now: False
    sched._proactive_window_config = lambda: (False, 0)
    sched._emit_skip_event_daily = lambda *a, **k: False
    sched._persist_throttle_ledger = lambda: None
    sched._trim_judge_stash = lambda: None
    sched._record_model_degraded = lambda *a, **k: None
    sched._resolve_proactive_llm = lambda eng: object()

    def _deliver(message, session_key="", character_id="", **kwargs):  # noqa: ANN001
        state.delivered.append(str(message))
        return True

    sched._deliver = _deliver

    def _decide(_llm, context, *, hook_keywords=None):  # noqa: ANN001
        state.contexts.append(str(context))
        state.hook_keywords.append(list(hook_keywords or []))
        return dict(state.decisions.pop(0))

    monkeypatch.setattr(runtime_mod, "append_proactive_event", lambda **kw: state.ledger.append(kw))
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
    monkeypatch.setattr(runtime_mod, "denied_names_for", lambda user_key: [])
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: dict(lp.DEFAULT_WEB_CONFIG))
    monkeypatch.setattr(lp, "load_proactive_knowledge", lambda *a, **k: "")
    monkeypatch.setattr(lp, "account_pacing_state", lambda hub, key, eng: (5.0, 0.0))
    monkeypatch.setattr(lp, "account_unanswered_streak", lambda hub, key, eng: 0)
    monkeypatch.setattr(lp, "should_skip_proactive_decision", lambda **kw: (False, ""))
    monkeypatch.setattr(lp, "recent_messages_for_dedup", lambda hub, key, eng: [])
    monkeypatch.setattr(lp, "is_duplicate_proactive", lambda msg, recent, window=0: False)
    monkeypatch.setattr(lp, "decide_proactive", _decide)
    monkeypatch.setattr(proactive_context, "load_full_persona_hint", lambda *a, **k: "")
    monkeypatch.setattr(
        proactive_hooks,
        "load_live_hooks",
        lambda key, memory: LiveHooks(
            section=(
                "【现在真正值得你接话的事】\n"
                "- 你之前说整理笔记，一直没下文\n"
                f"- {UNRELATED_HOOK}，记得添衣"
            ),
            keywords=["整理笔记", "笔记", UNRELATED_HOOK],
        ),
    )
    monkeypatch.setattr(
        topic_recall,
        "build_topic_recall",
        lambda *a, **k: SimpleNamespace(items=[], judge_context=""),
    )
    monkeypatch.setattr(topic_mod, "take_next_plan", lambda *a, **k: None)
    monkeypatch.setattr(topic_mod, "save_next_plan", lambda *a, **k: None)
    monkeypatch.setattr(cs_mod, "read", lambda *a, **k: {})
    monkeypatch.setattr(
        pc_mod,
        "effective",
        lambda *a, **k: EffectiveConfig(values={}, overridden_keys=frozenset(), global_values={}),
    )
    monkeypatch.setattr(ase_engine, "_local_now", lambda: state.now)
    # 会话键不是微信键 → 跳过 token 窗口探针（探针读真实凭证文件，回放不碰）。
    monkeypatch.setattr("utils.session_key.is_wechat_key", lambda key: False)
    import utils.reply_mode as reply_mode_mod

    monkeypatch.setattr(reply_mode_mod, "read_reply_mode", lambda *a, **k: "immersive")

    hub = SimpleNamespace(get=lambda key: engine)
    return SimpleNamespace(
        state=state,
        engine=engine,
        hub=hub,
        sched=sched,
        events=events,
        commitments=store[2],
    )


def _tick(scene, index: int) -> None:
    """把时钟拨到序列时刻并跑一拍真实 `_llm_proactive_one_user`。"""
    from datetime import datetime

    scene.state.now = datetime.strptime(TICK_TIMES[index], "%Y-%m-%d %H:%M")
    scene.sched._llm_proactive_one_user(scene.hub, SESSION_A)


def _open_commitments(scene) -> list[dict]:
    return scene.commitments.list_open_commitments(SESSION_A, character_id=COMMIT_CHARACTER)


def _commitment_status(events, commitment_id: str) -> dict | None:
    rows = _query(
        events,
        "select status, fulfilled_at, updated_at from agent_commitments where commitment_id=?",
        (commitment_id,),
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# 回放脚手架自证（不依赖 gate / close_commitment：三域未就绪时也必须绿）
# ---------------------------------------------------------------------------


def test_replay_scene_drives_real_scheduler_to_decision_point(scene):
    """回放脚手架有效性：一拍必须真正走到 decide 且带上完整 prompt 与候选。"""
    scene.state.decisions.append(
        {"should_contact": False, "message": "", "reason": "not_now", "wait_minutes": None}
    )
    _tick(scene, 1)

    assert len(scene.state.contexts) == 1, "必须真实驱动到 decide_proactive（不是空跑）"
    assert scene.state.contexts[0].strip(), "ctx 不得为空"
    assert scene.state.hook_keywords[0] == ["整理笔记", "笔记", UNRELATED_HOOK]
    # 未登记承诺时，prompt 不得凭空出现承诺段（宁缺毋滥）。
    assert "你还没兑现的承诺" not in scene.state.contexts[0]
    assert scene.state.delivered == []


# ---------------------------------------------------------------------------
# 09:23 —— 含承诺语义的出站正文被 gate 判定为「应提取」并落 open
# ---------------------------------------------------------------------------


def test_replay_0923_promise_text_is_extracted_and_opened(scene):
    from agent_core import commitment_gate as gate

    assert gate.should_extract(PROMISE_TEXT) is True
    assert gate.should_extract("") is False
    assert gate.should_extract("   ") is False
    assert gate.should_extract("我帮你整理笔记好吗？") is False, "疑问句不是承诺"

    scene.state.decisions.append(
        {"should_contact": True, "message": PROMISE_TEXT, "reason": "有具体由头", "wait_minutes": None}
    )
    _tick(scene, 0)

    assert scene.state.delivered == [PROMISE_TEXT], "回放必须先真实投递出这句正文"
    opens = _open_commitments(scene)
    matching = [c for c in opens if "笔记" in str(c.get("detail") or "")]
    assert matching, f"含承诺语义的正文必须登记为 open，实测 open={opens}"
    row = matching[0]
    assert row["status"] == "open"
    assert row["kind"] == "deliver"
    assert str(row["detail"]).strip()
    # 契约 9.3 把 TTL 时刻传给 due_at；B-1 的 expire_commitments 对 due_at /
    # expires_at 两者任一过期都会清理——断言「至少一个 TTL 字段落库」即可。
    assert str(row["due_at"] or row["expires_at"] or "") != "", "登记时必须写入 TTL 时刻"


def test_replay_ttl_due_at_survives_non_utc8_host_clock(scene, monkeypatch):
    """🔴 2026-11-11 复核修复：非 UTC+8 主机（CI / 无 TZ 容器）上 TTL 必须真落库。

    ``utils.local_time.now_local`` 在宿主时区偏离 UTC+8 超 1h 时返回 **aware**
    datetime（CI、Ubuntu、未设 TZ 的容器全命中）。旧实现把 ``aware + TTL`` 直接
    ``isoformat(sep=' ')`` → ``'2026-11-17 09:23:00+08:00'``；而 B-1 的
    ``events._normalize_ts`` 只认 ``%Y-%m-%d %H:%M[:%S]`` ⇒ ``due_at`` 落**空串**
    ⇒ ``expire_commitments`` 的两个谓词（``due_at<>''`` / ``expires_at<>''``）
    都不成立 ⇒ 该承诺**永不过期**，「你还没兑现的承诺」注入段与 hooks 剔除对该
    用户永久生效。

    本用例把 gate 的时钟钉成 aware（与 CI 主机产物同形），断言：① **冻结语义不变**
    （aware 进 aware 出，原样 +168h）；② 落库的 due_at 可解析且等于 TTL 时刻；
    ③ 到点真被清理。即：病在**落库序列化**侧，由 scheduler 的 ``strftime`` 收口，
    ``commitment_gate`` 一字未改（冻结件）。
    """
    from datetime import datetime, timedelta, timezone

    from agent_core import commitment_gate as gate

    aware_now = datetime(2026, 11, 10, 9, 23, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(gate, "now_local", lambda: aware_now)

    ttl = gate.expires_at()
    # 冻结语义：aware 进 aware 出（gate 不做时区归一，本批也不得改）
    assert ttl == aware_now + timedelta(hours=gate.COMMITMENT_TTL_HOURS)
    assert ttl.tzinfo is not None
    expected = ttl.strftime("%Y-%m-%d %H:%M:%S")
    # strftime 天然丢弃 tzinfo，且 now_local 的 aware 分支壁钟就是北京时间 ⇒ 串可解析
    assert scene.events._normalize_ts(expected) == "2026-11-17 09:23:00"

    scene.state.decisions.append(
        {"should_contact": True, "message": PROMISE_TEXT, "reason": "有具体由头", "wait_minutes": None}
    )
    _tick(scene, 0)

    row = next(c for c in _open_commitments(scene) if "笔记" in str(c.get("detail") or ""))
    assert row["due_at"] == expected, f"due_at 必须可解析落库，实测 {row['due_at']!r}"
    # 到点即清：承诺不会永久挂账（这正是空串 due_at 时失效的那一步）。
    assert scene.commitments.expire_commitments("2026-11-17 09:23:01", user_key=SESSION_A) == 1
    assert _open_commitments(scene) == []


# ---------------------------------------------------------------------------
# 10:24 / 17:35 / 19:35 —— 承诺不得再作为 hooks 由头，prompt 必须提醒兑现
# ---------------------------------------------------------------------------


def test_replay_three_ticks_drop_commitment_hook_and_warn_prompt(scene):
    from agent_core import commitment_gate as gate

    scene.state.decisions.append(
        {"should_contact": True, "message": PROMISE_TEXT, "reason": "有具体由头", "wait_minutes": None}
    )
    _tick(scene, 0)
    opens = _open_commitments(scene)
    detail = next(str(c["detail"]) for c in opens if "笔记" in str(c.get("detail") or ""))
    assert gate.references_commitment(detail, "整理笔记") is True

    for tick_index in (1, 2, 3):
        scene.state.decisions.append(
            {"should_contact": False, "message": "", "reason": "not_now", "wait_minutes": None}
        )
        _tick(scene, tick_index)

    # 三次 tick 都必须：① prompt 出现兑现提醒段；② 承诺 detail 不作为 hooks 候选。
    assert len(scene.state.contexts) == 4, "必须完成 1 次投递拍 + 3 次回放拍"
    for offset, ctx in enumerate(scene.state.contexts[1:], start=1):
        assert "你还没兑现的承诺" in ctx, f"第 {offset} 拍 prompt 缺兑现提醒段"
        assert detail[:20] in ctx or "笔记" in ctx, "承诺 detail 必须进入 prompt（提了就必须交付）"
        keywords = scene.state.hook_keywords[offset]
        assert not any("笔记" in kw for kw in keywords), (
            f"第 {offset} 拍 hooks 候选仍含承诺由头：{keywords}"
        )
        # 只剔由头、不拦正文：无关由头仍在 prompt 里。
        assert UNRELATED_HOOK in ctx
    assert scene.state.delivered == [PROMISE_TEXT], "承诺账本只能剔除由头，绝不能吞掉正文"
    assert len(_open_commitments(scene)) == 1, "三次 tick 不得重复登记"


# ---------------------------------------------------------------------------
# 用户回执 —— 承诺闭环 + 幂等
# ---------------------------------------------------------------------------


def test_replay_user_receipt_closes_commitment_then_idempotent(scene):
    from agent_core import commitment_gate as gate

    scene.state.decisions.append(
        {"should_contact": True, "message": PROMISE_TEXT, "reason": "有具体由头", "wait_minutes": None}
    )
    _tick(scene, 0)

    opens = _open_commitments(scene)
    row = next(c for c in opens if "笔记" in str(c.get("detail") or ""))
    cid = str(row["commitment_id"])
    detail = str(row["detail"])

    assert gate.should_close(detail, RECEIPT_TEXT) is True
    assert gate.should_close(detail, "今天天气不错") is False, "无引用、无回执语不得误关"

    assert scene.commitments.close_commitment(cid, reason="user_receipt") is True
    after = _commitment_status(scene.events, cid)
    assert after is not None
    assert after["status"] == "fulfilled"
    assert str(after["fulfilled_at"] or "") != ""
    assert all(str(c["commitment_id"]) != cid for c in _open_commitments(scene))

    # 幂等：判定本身是纯函数（同文本恒 True），幂等必须由写侧保证——
    # 已 fulfilled 不再发生第二次状态转换，时间戳不变。
    assert gate.should_close(detail, RECEIPT_TEXT) is True
    first_fulfilled_at = after["fulfilled_at"]
    assert scene.commitments.close_commitment(cid, reason="user_receipt") is False
    again = _commitment_status(scene.events, cid)
    assert again["status"] == "fulfilled"
    assert again["fulfilled_at"] == first_fulfilled_at


# ---------------------------------------------------------------------------
# 回执当拍：闭环必须先于消费（🔴 2026-11-11 复核修复）
# ---------------------------------------------------------------------------


def test_replay_receipt_tick_closes_before_injection(scene):
    """用户回执那一拍：闭环**先**发生，本轮 prompt 不得再说「还没兑现」。

    旧序（读快照 → 用快照建注入段/剔 hooks → 才 close）导致回执当拍仍告诉模型
    「这条没兑现」、且该承诺的 hooks 由头仍被剔 —— 用户已经收到东西，模型却可能
    再承诺一次/重复交付（正是账本要消灭的空头支票复提），要等下一拍才恢复。
    契约 9.5「入站即闭环」要求本轮即不可见。
    """
    scene.state.decisions.append(
        {"should_contact": True, "message": PROMISE_TEXT, "reason": "有具体由头", "wait_minutes": None}
    )
    _tick(scene, 0)
    assert len(_open_commitments(scene)) == 1

    # 下一拍：用户已回执（引擎可见的最后一句就是回执）。
    scene.engine._last_user_message = RECEIPT_TEXT
    scene.state.decisions.append(
        {"should_contact": False, "message": "", "reason": "not_now", "wait_minutes": None}
    )
    _tick(scene, 1)

    assert _open_commitments(scene) == [], "回执当拍必须就地闭环（入站即闭环）"
    ctx = scene.state.contexts[-1]
    assert "你还没兑现的承诺" not in ctx, "已兑现的承诺不得再出现在「还没兑现」段"
    assert "一直没下文" in ctx and UNRELATED_HOOK in ctx, "承诺已销账 → hooks 由头不再被剔除"

    # 幂等：下一拍无回执，也不会有第二条/复活。
    scene.engine._last_user_message = "今天好热啊"
    scene.state.decisions.append(
        {"should_contact": False, "message": "", "reason": "not_now", "wait_minutes": None}
    )
    _tick(scene, 2)
    assert _open_commitments(scene) == []


# ---------------------------------------------------------------------------
# SPEAKER_PROACTIVE=0 是真回滚（🔴 2026-11-11 复核修复）
# ---------------------------------------------------------------------------


def test_replay_speaker_switch_off_really_rolls_back(scene, monkeypatch):
    """``SPEAKER_PROACTIVE=0`` 必须真的关掉承诺闸（此前只被打印、无人消费）。

    回滚语义两侧都要成立：① 主动线**不登记**新承诺；② 主动线**不消费**已有承诺
    （不注入「还没兑现」段、不剔 hooks 由头）。出站正文照发（承诺账本从不拦正文）。
    """
    monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    monkeypatch.setenv("SPEAKER_PROACTIVE", "0")

    # 账本里先有一条 open（模拟开关关掉之前登记的，登记本身只受 AGENT_CORE_EVENTS 管）
    seeded = scene.commitments.add_commitment(
        PROMISE_TEXT, kind="deliver", session_key=SESSION_A, character_id=COMMIT_CHARACTER,
    )
    assert seeded

    scene.state.decisions.append(
        {"should_contact": True, "message": PROMISE_TEXT, "reason": "有具体由头", "wait_minutes": None}
    )
    _tick(scene, 0)
    assert scene.state.delivered == [PROMISE_TEXT], "开关只回滚承诺闸，不得拦出站正文"
    assert [c["commitment_id"] for c in _open_commitments(scene)] == [seeded], (
        "SPEAKER_PROACTIVE=0 时不得登记新承诺"
    )

    scene.state.decisions.append(
        {"should_contact": False, "message": "", "reason": "not_now", "wait_minutes": None}
    )
    _tick(scene, 1)
    assert "你还没兑现的承诺" not in scene.state.contexts[-1], "回滚后不得注入承诺段"
    assert scene.state.hook_keywords[-1] == ["整理笔记", "笔记", UNRELATED_HOOK], (
        "回滚后 hooks 不得被承诺剔除"
    )
    assert scene.commitments.list_open_commitments(SESSION_A), "回滚后不得消费/销账已有承诺"


# ---------------------------------------------------------------------------
# 冻结规格的边界钉子（任务点名：core_terms 长度 / 空输入 / 疑问句）
# ---------------------------------------------------------------------------


def test_frozen_core_terms_never_emit_short_terms():
    from agent_core import commitment_gate as gate

    samples = [
        PROMISE_TEXT,
        RECEIPT_TEXT,
        "我帮你整理一下之前的笔记和思路",
        "已经发给你了，查好了",
        "我们你们他们的",
        "a我b你c",
    ]
    for text in samples:
        terms = gate.core_terms(text)
        assert all(len(t) >= gate.MIN_TERM_LEN for t in terms), f"{text!r} -> {terms}"


def test_frozen_extract_and_close_boundaries():
    from agent_core import commitment_gate as gate

    # 空输入
    assert gate.core_terms("") == []
    assert gate.references_commitment("", PROMISE_TEXT) is False
    assert gate.is_receipt("") is False

    # 疑问句不是承诺；否定/完成/进行中的句子整体否决。
    assert gate.should_extract("我帮你整理笔记好吗？") is False
    assert gate.should_extract("我还没帮你整理笔记") is False
    assert gate.should_extract("我已经帮你整理好了笔记") is False
    assert gate.should_extract("我还在整理笔记") is False

    # TTL：expires_at 一定在 now 之后（默认 168h）。
    from datetime import datetime

    now = datetime(2026, 11, 10, 9, 23)
    exp = gate.expires_at(now)
    assert exp > now
    assert (exp - now).total_seconds() == gate.COMMITMENT_TTL_HOURS * 3600
