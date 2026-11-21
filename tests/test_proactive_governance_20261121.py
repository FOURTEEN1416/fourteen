"""主动线治理红测（2026-11-21）：目标集合资格门 + 连续投递失败熔断。

生产审计结论（本批依据）：
- 目标集合包含历史 ASE 索引键（`9:web:wbtest-1` 这类已无 wechat_bindings
  的 web 键），每 5 分钟 tick 仍会写 vital_signs、烧决策 LLM 与 judge；
- 投递连续失败只有指数退避到 120 分钟封顶，永不熔断，死会话仍会被周期性
  重新决策。

测试隔离：users/memory/runtime 三个库全部落 ``tmp_path``；调度器用例用
``ProactiveScheduler.__new__`` 手工桩，不触真实 data/ 与 provider。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from proactive import target_eligibility as te
from utils.local_time import now_utc_naive

# ═══════════════════════════════════════════════════════════
# 合成库
# ═══════════════════════════════════════════════════════════


def _make_users_db(path: Path, rows: list[tuple]) -> Path:
    """rows: (id, username, email, last_login_at)"""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS wechat_bindings;
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            last_login_at TEXT
        );
        CREATE TABLE wechat_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            wxid TEXT NOT NULL UNIQUE
        );
        """
    )
    conn.executemany(
        "INSERT INTO users (id, username, email, last_login_at) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def _bind(users_db: Path, uid: int, wxid: str) -> None:
    conn = sqlite3.connect(str(users_db))
    conn.execute(
        "INSERT INTO wechat_bindings (user_id, wxid) VALUES (?,?)", (uid, wxid)
    )
    conn.commit()
    conn.close()


def _make_memory_db(path: Path, rows: list[tuple]) -> Path:
    """rows: (session_id, role, created_at)"""
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE IF EXISTS chat_history")
    conn.execute(
        "CREATE TABLE chat_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL,"
        "session_id TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.executemany(
        "INSERT INTO chat_history (session_id, role, content, created_at) VALUES (?,?,?,?)",
        [(sid, role, "x", ts) for sid, role, ts in rows],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def gate_env(tmp_path, monkeypatch):
    """资格门三库沙箱 + 清空进程缓存。"""
    users_db = _make_users_db(
        tmp_path / "users.db",
        [
            (9, "nine", "nine@example.com", "2026-11-20 10:00:00"),
            (10, "ten", "ten@example.com", "2026-11-20 10:00:00"),
            (11, "never", "never@example.com", None),
        ],
    )
    memory_db = _make_memory_db(tmp_path / "memory.db", [])
    runtime_db = tmp_path / "runtime_plane.db"
    from proactive import runtime_plane

    runtime_plane.set_db_path(runtime_db)
    te.clear_cache()
    yield SimpleNamespace(
        users_db=users_db, memory_db=memory_db, runtime_db=runtime_db
    )
    runtime_plane.set_db_path(None)
    te.clear_cache()


def _reason(key: str, env: SimpleNamespace, **kw) -> str:
    return te.skip_reason(
        key,
        users_db=env.users_db,
        memory_db=env.memory_db,
        now=now_utc_naive(),
        **kw,
    )


# ═══════════════════════════════════════════════════════════
# 1. 资格门纯函数
# ═══════════════════════════════════════════════════════════


class TestTargetEligibility:
    def test_unknown_and_never_logged_in_are_skipped(self, gate_env):
        assert _reason("99:web:abcdef12", gate_env) == "never_logged_in"
        assert _reason("11:web:abcdef12", gate_env) == "never_logged_in"

    def test_test_namespace_key_is_skipped_even_if_live(self, gate_env):
        # 先给一个在线租约，证明命名空间闸优先于租约闸
        from proactive import runtime_plane

        runtime_plane.session_touch("9:web:wbtest-1")
        assert _reason("9:web:wbtest-1", gate_env) == "test_namespace"
        assert _reason("9:web:TEST_abc", gate_env) == "test_namespace"
        assert _reason("9:web:e2e-smoke", gate_env) == "test_namespace"

    def test_web_unbound_idle_is_skipped(self, gate_env):
        assert _reason("9:web:abc12345", gate_env) == "web_unbound_idle"

    def test_web_bound_but_idle_is_skipped(self, gate_env):
        _bind(gate_env.users_db, 10, "wxid_ten@im.wechat")
        assert _reason("10:web:abc12345", gate_env) == "web_session_idle"

    def test_web_live_lease_is_eligible(self, gate_env):
        from proactive import runtime_plane

        runtime_plane.session_touch("9:web:abc12345")
        assert _reason("9:web:abc12345", gate_env) == ""

    def test_web_recent_inbound_is_eligible(self, gate_env):
        recent = now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")
        _make_memory_db(
            gate_env.memory_db, [("9:web:abc12345", "user", recent)]
        )
        assert _reason("9:web:abc12345", gate_env) == ""

    def test_web_old_inbound_without_lease_is_skipped(self, gate_env):
        old = "2026-01-01 00:00:00"
        _make_memory_db(gate_env.memory_db, [("9:web:abc12345", "user", old)])
        assert _reason("9:web:abc12345", gate_env) == "web_unbound_idle"

    def test_wechat_key_with_login_is_eligible(self, gate_env):
        _bind(gate_env.users_db, 9, "wxid_nine@im.wechat")
        assert _reason("9:wxid_nine@im.wechat", gate_env) == ""

    def test_stale_wechat_peer_without_binding_is_skipped(self, gate_env):
        """2026-11-21 用户裁决「无绑定即跳过」：账号存在且登录过，但
        wechat_bindings 无活跃行（绑定已解/从未绑定）的 stale wechat peer
        必须被资格门跳过——旧实现直接放行，验收实测
        skip_reason('7:wxid_nonexistent@im.wechat') 返回空串。
        """
        # user 10 在夹具中存在、有 last_login_at、无任何绑定
        assert _reason("10:wxid_nonexistent@im.wechat", gate_env) == "wechat_unbound"
        # 解绑后（删绑定行）同键立即变回跳过——资格门读库判定，不依赖缓存旧值
        _bind(gate_env.users_db, 10, "wxid_ten@im.wechat")
        te.clear_cache()
        assert _reason("10:wxid_ten@im.wechat", gate_env) == ""
        conn = sqlite3.connect(str(gate_env.users_db))
        conn.execute("DELETE FROM wechat_bindings WHERE user_id=10")
        conn.commit()
        conn.close()
        te.clear_cache()
        assert _reason("10:wxid_ten@im.wechat", gate_env) == "wechat_unbound"

    def test_test_account_username_is_skipped(self, gate_env):
        # 账号名命中测试命名空间：即便键本身干净、有租约也必须跳过
        _make_users_db(
            gate_env.users_db,
            [(12, "wbtest-owner", "x@example.com", "2026-11-20 10:00:00")],
        )
        assert _reason("12:wxid_x@im.wechat", gate_env) == "test_namespace"

    def test_like_prefix_owner_is_not_confused(self, gate_env):
        # 9 与 90：前缀匹配必须带冒号，不能把 90 当 9 的账号
        _make_users_db(
            gate_env.users_db,
            [(90, "ninety", "90@example.com", "2026-11-20 10:00:00")],
        )
        assert _reason("90:web:abc12345", gate_env) == "web_unbound_idle"


# ═══════════════════════════════════════════════════════════
# 2. 目标集合接线：历史索引键被过滤，活绑定键保留
# ═══════════════════════════════════════════════════════════


def test_collect_targets_applies_gate_to_index_keys(monkeypatch, gate_env):
    from proactive import ase_hub as hub_mod
    from proactive.ase_hub import ASEHub
    from proactive.scheduler import ProactiveScheduler

    class _FakeASE:
        def __init__(self, user_key: str = "", state_path: str = ""):
            self.user_key = user_key
            self.state_path = state_path

    hub = ASEHub(_FakeASE)
    monkeypatch.setattr(
        hub_mod,
        "load_user_key_index",
        lambda: ["9:web:wbtest-1", "10:wxid_ten@im.wechat"],
    )
    # 只让测试命名空间命中，避免依赖合成库细节；活绑定键必须保留
    monkeypatch.setattr(
        te,
        "skip_reason",
        lambda key, **kw: "test_namespace" if "wbtest" in key else "",
    )
    import api.deps as deps_mod

    monkeypatch.setattr(deps_mod.deps, "gf", None)
    import wechat_direct.connector_registry as reg_mod

    monkeypatch.setattr(
        reg_mod,
        "get_registry",
        lambda: SimpleNamespace(all=lambda: [], online_count=lambda: 0),
    )

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched.ase = hub
    keys = sched._collect_ase_user_keys()
    assert "9:web:wbtest-1" not in keys
    assert "10:wxid_ten@im.wechat" in keys


# ═══════════════════════════════════════════════════════════
# 3. 熔断：决策前置、跨重启持久化、成功自动解除
# ═══════════════════════════════════════════════════════════


def _circuit_sched(monkeypatch):
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._deliver_circuit_open = {}
    sched._deliver_fail_counts = {}
    sched._deliver_fail_seen = {}
    sched._gate_skip_event_day = {}
    sched._persist_throttle_ledger = lambda: None
    sched._resolve_character_id = lambda key: "default"
    ledgers: list[dict] = []
    import shisi.agent_plane.runtime as runtime_mod

    monkeypatch.setattr(
        runtime_mod, "append_proactive_event", lambda **kw: ledgers.append(kw)
    )
    return sched, ledgers


def test_open_circuit_skips_before_vital_and_llm(monkeypatch):
    sched, ledgers = _circuit_sched(monkeypatch)
    key = "9:web:dead1234"
    sched._deliver_circuit_open[key] = time.time() + 600
    vital_calls: list[str] = []
    sched._vital_tick_for_user = lambda uk, cid="": vital_calls.append(uk)
    sched._llm_proactive_one_user(
        SimpleNamespace(get=lambda k: None), key
    )
    assert vital_calls == [], "熔断跳过必须发生在 vital_signs 写入之前"
    assert [e.get("reason") for e in ledgers] == ["deliver_circuit_open"]


def test_five_consecutive_failures_open_circuit():
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._deliver_circuit_open = {}
    sched._deliver_fail_counts = {}
    sched._deliver_fail_seen = {}
    sched._llm_proactive_next_ok = {}
    key = "9:web:dead1234"
    for _ in range(4):
        fails, _backoff = sched._register_deliver_failure(key)
        assert fails < 5
        assert key not in sched._deliver_circuit_open
    fails, _backoff = sched._register_deliver_failure(key)
    assert fails == 5
    assert key in sched._deliver_circuit_open
    assert sched._deliver_circuit_open[key] > time.time()


def test_success_clears_circuit_and_failure_count():
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._deliver_circuit_open = {"k": time.time() + 60}
    sched._deliver_fail_counts = {"k": 5}
    sched._deliver_fail_seen = {"k": time.time()}
    assert sched._clear_deliver_failure("k") is True
    assert "k" not in sched._deliver_circuit_open
    assert "k" not in sched._deliver_fail_counts
    assert "k" not in sched._deliver_fail_seen


def test_circuit_persists_across_restart(monkeypatch, tmp_path):
    from proactive.scheduler import ProactiveScheduler

    cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg, raising=False)

    def _new():
        s = ProactiveScheduler.__new__(ProactiveScheduler)
        s._llm_proactive_next_ok = {}
        s._deliver_fail_counts = {}
        s._deliver_fail_seen = {}
        s._disabled_event_day = {}
        s._wait_window_event_day = {}
        s._quiet_event_day = {}
        s._model_degraded_event_day = {}
        s._gate_skip_event_day = {}
        s._important_dates_sent = set()
        s._deliver_circuit_open = {}
        return s

    s1 = _new()
    until = time.time() + 1800
    s1._deliver_fail_counts["9:web:dead1234"] = 5
    s1._deliver_circuit_open["9:web:dead1234"] = until
    s1._persist_throttle_ledger()

    s2 = _new()
    s2._load_throttle_ledger()
    assert s2._deliver_fail_counts["9:web:dead1234"] == 5
    assert abs(s2._deliver_circuit_open["9:web:dead1234"] - until) < 0.01


# ═══════════════════════════════════════════════════════════
# 4. 半开探测未投递 → 重置回完整冷却（2026-11-21 治理批缺陷 2）
# ═══════════════════════════════════════════════════════════


def test_circuit_expiry_releases_probe_and_marks_key(monkeypatch):
    """熔断到期：第一拍原子领取 60s 探测租约并放行，同时登记探测标记。"""
    sched, _ledgers = _circuit_sched(monkeypatch)
    key = "9:web:dead1234"
    sched._deliver_circuit_open[key] = time.time() - 1.0
    sched._deliver_fail_counts[key] = 5
    assert sched._deliver_circuit_active(key) is False, "到期必须放行一次探测"
    assert 0 < sched._deliver_circuit_open[key] - time.time() <= 60.0, (
        "放行同时写入 60s 探测租约"
    )
    assert key in getattr(sched, "_deliver_circuit_probe_keys", set()), (
        "本轮是半开探测放行，必须登记探测标记"
    )
    # 租约期内（下一拍）：继续拦截，且不重复放行
    assert sched._deliver_circuit_active(key) is True


def test_rearm_after_probe_sets_full_cooldown(monkeypatch):
    """半开探测未投递 → 按当前失败计数重置回**完整冷却**（非 60s 租约）。"""
    sched, _ledgers = _circuit_sched(monkeypatch)
    key = "9:web:dead1234"
    sched._deliver_fail_counts[key] = 5
    sched._llm_proactive_next_ok = {}
    sched._deliver_circuit_probe_keys = {key}
    before = time.time()
    sched._rearm_deliver_circuit_after_probe(key)
    until = sched._deliver_circuit_open[key]
    # fails=5 → 基础冷却 30 分钟（不是 60s 探测租约）
    assert until >= before + 29 * 60.0
    assert until <= time.time() + 31 * 60.0
    # 半开时刻必须同时覆盖 wait-window 闸（与 _register_deliver_failure 同纪律）
    assert sched._llm_proactive_next_ok[key] == pytest.approx(until)
    # 探测标记已消费：再次调用是 no-op（不重复刷新冷却）
    sched._rearm_deliver_circuit_after_probe(key)
    assert sched._deliver_circuit_open[key] == pytest.approx(until)


def test_rearm_scales_with_failure_count_and_caps(monkeypatch):
    """失败数越高冷却越长（翻倍阶梯），且封顶 6 小时。"""
    sched, _ledgers = _circuit_sched(monkeypatch)
    key = "9:web:dead1234"
    sched._llm_proactive_next_ok = {}
    sched._deliver_fail_counts[key] = 6
    sched._deliver_circuit_probe_keys = {key}
    sched._rearm_deliver_circuit_after_probe(key)
    assert sched._deliver_circuit_open[key] >= time.time() + 59 * 60.0, (
        "fails=6 → 60 分钟冷却"
    )
    sched._deliver_fail_counts[key] = 20
    sched._deliver_circuit_probe_keys = {key}
    sched._rearm_deliver_circuit_after_probe(key)
    assert sched._deliver_circuit_open[key] <= time.time() + 361 * 60.0, (
        "冷却封顶 6 小时"
    )


def test_rearm_ignores_non_probe_key(monkeypatch):
    """非探测轮次（标记不在册）是 no-op——正常 skip 不应被误重开熔断。"""
    sched, _ledgers = _circuit_sched(monkeypatch)
    key = "9:web:dead1234"
    sched._deliver_fail_counts[key] = 5
    sched._llm_proactive_next_ok = {}
    original_until = time.time() + 600.0
    sched._deliver_circuit_open[key] = original_until
    sched._rearm_deliver_circuit_after_probe(key)
    # 冷却时刻未被改写（仍是原值）
    assert sched._deliver_circuit_open[key] == pytest.approx(original_until)


def test_real_delivery_paths_consume_probe_marker(monkeypatch):
    """真实投递成功/失败都会消费探测标记（闭环，不留跨轮残留）。"""
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._deliver_circuit_open = {}
    sched._deliver_fail_counts = {}
    sched._deliver_fail_seen = {}
    sched._llm_proactive_next_ok = {}
    sched._deliver_circuit_probe_keys = {"k"}
    sched._register_deliver_failure("k")
    assert "k" not in sched._deliver_circuit_probe_keys
    # 成功路径：上一步注册过失败，清掉计数与 seen → True；探测标记再次被消费
    sched._deliver_circuit_probe_keys = {"k"}
    assert sched._clear_deliver_failure("k") is True
    assert "k" not in sched._deliver_circuit_probe_keys
    # 已无失败计数可清 → False；对不在册的探测键也是安全 no-op
    assert sched._clear_deliver_failure("k") is False
    assert "k" not in sched._deliver_circuit_probe_keys


def test_no_delivery_decision_rearms_circuit(monkeypatch, tmp_path):
    """集成回归：半开探测放行的决策若 should_contact=False 且 wait<=0，
    必须把该会话重置回完整熔断冷却——否则 60s 租约到期后下一拍再次放行，
    死会话每分钟白烧一次决策 LLM（本用例即缺陷 2 的复现锚点）。
    """
    # topic_state 沙箱（决策分支会经 speaker.ThreadState 读写计划）
    import utils.topic_state as ts_mod

    monkeypatch.setattr(
        ts_mod, "_CONFIG_PATH", tmp_path / "sched_cfg.json", raising=False
    )
    monkeypatch.setattr(
        ts_mod, "_DB_PATH", tmp_path / "topic_state.db", raising=False
    )
    import proactive.ase_engine as ase
    import proactive.llm_proactive as lp
    import proactive.proactive_context as pctx
    import proactive.proactive_hooks as hooks_mod
    import shisi.agent_plane.runtime as rt

    monkeypatch.setattr(ase, "_local_now", lambda: now_utc_naive())
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_proactive_knowledge", lambda cid: "")
    monkeypatch.setattr(lp, "recent_messages_for_dedup", lambda hub, key, eng: [])
    monkeypatch.setattr(lp, "is_duplicate_proactive", lambda *a, **k: False)
    monkeypatch.setattr(pctx, "load_full_persona_hint", lambda cid, uk=None: "")
    monkeypatch.setattr(
        hooks_mod,
        "load_live_hooks",
        lambda key, memory: type("H", (), {"section": "", "keywords": ()})(),
    )
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    ledger: list[dict] = []
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: ledger.append(kw))

    class _FakeEng:
        def __init__(self):
            self._last_delivery_time = None
            self._unanswered_streak = 0
            self._last_user_message = ""
            self._knowledge_character_id = "default"
            self.urgency = type("U", (), {"total": 2.5})()

        def _hours_since_last_chat(self):
            return 90.0

        def tick(self, hours, dry_run=False):
            return None

        def commit_sent(self, result):
            pass

    eng = _FakeEng()
    key = "9:web:deadbeef"
    hub = type("Hub", (), {"get": lambda self, k: eng if k == key else None})()

    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler(ase_engine=None)
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._paused = False
    sched._is_quiet_hours = lambda: False
    sched._is_quiet_hours_for = lambda cfg: False
    sched._primary_model_ok = lambda: True
    sched.get_quiet_hours = lambda: None
    sched._resolve_character_id = lambda sk: "default"
    sched._resolve_proactive_llm = lambda eng=None: object()
    sched._session_llm_resolver = lambda sk: object()
    sched._resolve_memory = lambda: None
    sched._persist_throttle_ledger = lambda: None
    sched._proactive_window_config = lambda: (False, 0)
    sched._deliver = lambda message, session_key=None, character_id=None: True

    # 前置态：连续失败 5 次 → 熔断已开；熔断时刻拨到过去 → 本拍领取探测租约放行
    sched._deliver_fail_counts[key] = 5
    sched._deliver_circuit_open[key] = time.time() - 1.0

    # 决策桩：不发送、不给等待窗（wait=0）——正是重复烧决策的形态
    decide_calls: list[dict] = []

    def _decide(llm, ctx):
        decide_calls.append({"ctx": ctx})
        return {
            "should_contact": False,
            "message": "",
            "reason": "先不打扰",
            "wait_minutes": 0,
            "next_topic": "",
            "next_intent": "",
        }

    monkeypatch.setattr(lp, "decide_proactive", _decide)

    sched._llm_proactive_one_user(hub, key)

    assert len(decide_calls) == 1, "半开探测必须放行一次决策（走到决策 LLM）"
    assert "先不打扰" in [e.get("reason") for e in ledger], "skip 决策必须落账本事件"
    until = sched._deliver_circuit_open.get(key)
    assert until is not None, "未投递的半开探测必须重开熔断"
    assert until > time.time() + 25 * 60.0, (
        "必须重置回完整冷却（fails=5 → 30 分钟），而不是 60s 探测租约"
    )
    assert sched._llm_proactive_next_ok.get(key) == pytest.approx(until)


# ═══════════════════════════════════════════════════════════
# 5. _deliver 透传会话历史给终审守卫（2026-11-21 治理批缺陷 3）
# ═══════════════════════════════════════════════════════════


def test_recent_assistant_texts_for_guard_extracts_and_fails_open():
    """历史读取辅助：取到 → 提取最近 assistant 正文；取不到 → None（fail-open）。"""
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    rows = [
        {"role": "user", "content": "在忙什么"},
        {"role": "assistant", "content": "在等你呀。"},
        {"role": "assistant", "content": "把手机放下，早点休息。"},
        {"role": "assistant", "content": ""},  # 空正文剔除
    ]
    sched._resolve_memory = lambda: SimpleNamespace(
        get_chat_context=lambda **kw: (rows, "")
    )
    got = sched._recent_assistant_texts_for_guard("9:web:abc12345", "default")
    assert got == ["在等你呀。", "把手机放下，早点休息。"]
    # memory 缺位 / 读库异常 / 无会话键 / 无 assistant 行 → 一律 None
    sched._resolve_memory = lambda: None
    assert sched._recent_assistant_texts_for_guard("9:web:abc12345") is None

    def _boom(**kw):
        raise RuntimeError("db down")

    sched._resolve_memory = lambda: SimpleNamespace(get_chat_context=_boom)
    assert sched._recent_assistant_texts_for_guard("9:web:abc12345") is None
    assert sched._recent_assistant_texts_for_guard("") is None
    sched._resolve_memory = lambda: SimpleNamespace(
        get_chat_context=lambda **kw: ([{"role": "user", "content": "只有用户行"}], "")
    )
    assert sched._recent_assistant_texts_for_guard("9:web:abc12345") is None


def test_deliver_passes_session_history_to_terminal_guard(monkeypatch):
    """_deliver 必须把该会话最近 assistant 正文透传给 terminal_reply_guard
    （历史重复闸/模板句去重在主动线生效）。守卫 spy 返回空串 → _deliver
    在守卫后立即返回，无需桩后续 judge/投递链路。
    """
    import utils.prompt_sanitize as ps
    from proactive import scheduler as sched_mod
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    rows = [
        {"role": "assistant", "content": "把手机放下，早点休息。"},
        {"role": "assistant", "content": "在等你呀。"},
    ]
    sched._resolve_memory = lambda: SimpleNamespace(
        get_chat_context=lambda **kw: (rows, "")
    )
    captured: dict = {}

    def _spy(reply, **kw):
        captured.update(kw)
        return ""  # 模拟守卫拦截 → 短路返回

    monkeypatch.setattr(ps, "terminal_reply_guard", _spy)
    # reply_mode 走进程内替身，避免测试触真实配置库
    monkeypatch.setattr(sched_mod, "read_reply_mode", lambda *a, **k: "immersive")

    ok = sched._deliver(
        "把手机放下。", session_key="9:web:abc12345", character_id="default"
    )
    assert ok is False, "守卫拦截 → 不投递"
    assert captured["session_key"] == "9:web:abc12345"
    assert captured["recent_assistant_messages"] == [
        "把手机放下，早点休息。",
        "在等你呀。",
    ]
