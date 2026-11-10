"""N 规模批（2026-11-10）scheduler 域红测：有界并发 / 账本锁 / 错误归因隔离 / 账本驱逐。

四组改动各配红测（审阅 Q4 覆盖缺口已补 A3 归因隔离）：
  A1 有界并发   → R1 并发峰值 / R2 慢用户不阻塞 / R2b N=2 barrier（现行恒串行 = 红）；
  A2 账本锁     → R10 注入式并发变异下 persist 快照不炸（现行 "Set/Dict changed
                  size during iteration" 被 except 吞成告警 = 红）；
  A3 归因隔离   → R11/R12 per-session 错误镜像（现行无镜像，跨用户互串 = 红）；
  A4 账本驱逐   → R6-R9 四组（现行无驱逐 = 红）。
护栏钉子（本就绿，防并发化回归时被打破）：R3 异常隔离、R4 N=1 内联线程同一性、
R5 env=1 回滚路径。

覆盖声明（如实）：R10 的并发变异是**合成竞争**（persist 快照一致性），purge/
祝福线/手动线三个真实跨线程源由实现层按 grep 全量写点落锁保证（写点清单见
proactive/scheduler.py 的 A2 注释块），本文件不逐一合成——purge 自身对账本
迭代前已 ``list()`` 预拷贝、本就不炸 persist。R1/R2/R2b 为 2-4 用户小 N；N>w
的「每用户恰一次、账本零串写」由 R2（3 用户、w 默认 4）与 R3 全序断言近似覆盖。

回滚矩阵（审阅 Q5，如实表述）：A1 = env ``PROACTIVE_TICK_CONCURRENCY=1``；
A4 = env ``SCHEDULER_LEDGER_EVICT=0``；A2 账本锁与 A3 错误字典**无独立开关**——
事故根因落在锁/字典改动时只能单文件回退 ``proactive/scheduler.py``，且本测试
文件必须随回退一并 revert（断言新行为，不随单文件回退消失）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time as _time
from datetime import datetime, timedelta

from proactive import scheduler as sched_mod
from proactive.scheduler import ProactiveScheduler
from utils import json_state

# ── 构造替身 ──────────────────────────────────────────────

def _stub_sched(tmp_path=None, **extra) -> ProactiveScheduler:
    """最小 __new__ 替身（对齐 test_proactive_jitter 先例；只补被测路径所需属性）。"""
    s = ProactiveScheduler.__new__(ProactiveScheduler)
    s.ase = object()
    s._paused = False
    s._quiet_hours = (23, 7)
    # 账本族（A2/A4 被测对象）
    s._llm_proactive_next_ok = {}
    s._deliver_fail_counts = {}
    s._deliver_fail_seen = {}
    s._disabled_event_day = {}
    s._wait_window_event_day = {}
    s._quiet_event_day = {}
    s._model_degraded_event_day = {}
    s._gate_skip_event_day = {}
    s._important_dates_sent = set()
    s._judge_recent = {}
    s._judge_topic = {}
    s._ledger_lock = threading.Lock()
    # 投递面（R11/R12）
    s._last_deliver_error = ""
    s._last_deliver_errors = {}
    s._channel_instances = {}
    s._is_quiet_hours = lambda: False
    if tmp_path is not None:
        s._CONFIG_PATH = tmp_path / "scheduler_config.json"
        s._THROTTLE_KEY = "throttle"
    for key, value in extra.items():
        setattr(s, key, value)
    return s


def _wire_tick(s: ProactiveScheduler, keys: list[str], fake) -> None:
    """把 _check_ase_per_user 的三个依赖钉成替身，只留 fan-out 逻辑为真实代码。"""
    s._collect_ase_user_keys = lambda: list(keys)  # type: ignore[method-assign]
    s._global_engine_values = lambda hub: {}  # type: ignore[method-assign]
    s._llm_proactive_one_user = fake  # type: ignore[method-assign]


# ── A1：有界并发 ─────────────────────────────────────────

def test_tick_concurrency_env_semantics(monkeypatch):
    """_tick_concurrency：默认 4；显式值生效；≤1 钳到 1；非法回落默认。"""
    monkeypatch.delenv("PROACTIVE_TICK_CONCURRENCY", raising=False)
    s = _stub_sched()
    assert s._tick_concurrency() == 4
    monkeypatch.setenv("PROACTIVE_TICK_CONCURRENCY", "8")
    assert s._tick_concurrency() == 8
    for bad in ("0", "-3"):
        monkeypatch.setenv("PROACTIVE_TICK_CONCURRENCY", bad)
        assert s._tick_concurrency() == 1, bad
    monkeypatch.setenv("PROACTIVE_TICK_CONCURRENCY", "abc")
    assert s._tick_concurrency() == 4


def test_r1_parallel_users_reach_concurrency_peak():
    """R1（红）：多用户 tick 并发峰值 ≥2——现行串行恒 1。"""
    s = _stub_sched()
    keys = ["u1", "u2", "u3"]
    peak = 0
    active = 0
    lock = threading.Lock()

    def fake(hub, key, engine_globals=None):
        nonlocal peak, active
        with lock:
            active += 1
            peak = max(peak, active)
        _time.sleep(0.15)
        with lock:
            active -= 1

    _wire_tick(s, keys, fake)
    s._check_ase_per_user()
    assert peak >= 2, f"并发峰值={peak}，仍为串行（A1 未生效）"


def test_r2_slow_user_does_not_block_following_users():
    """R2（红）：慢用户不阻塞后序用户——按完成时间戳判序，无绝对时长假设。"""
    s = _stub_sched()
    keys = ["slow", "fast1", "fast2"]
    done: list[str] = []

    def fake(hub, key, engine_globals=None):
        if key == "slow":
            _time.sleep(0.8)
        done.append(key)

    _wire_tick(s, keys, fake)
    s._check_ase_per_user()
    assert sorted(done) == sorted(keys), "三个用户都必须被处理"
    assert done.index("fast1") < done.index("slow"), (
        f"完成顺序 {done}：fast 用户被 slow 阻塞（串行路径）"
    )
    assert done.index("fast2") < done.index("slow"), (
        f"完成顺序 {done}：fast 用户被 slow 阻塞（串行路径）"
    )


def test_r2b_n2_users_synchronize_on_barrier(monkeypatch):
    """R2b（红，审阅 Q1 N=2 全链分支）：恰 2 用户（生产 v1.70 实况）默认并发
    即走并行分支——两线程在 barrier 会师。串行路径下第一个 wait 必超时
    （BrokenBarrierError，被隔离吞掉），passed 缺人 = 红；barrier 带 timeout
    保证串行下快速失败、绝不挂死。"""
    monkeypatch.delenv("PROACTIVE_TICK_CONCURRENCY", raising=False)
    s = _stub_sched()
    keys = ["u1", "u2"]
    barrier = threading.Barrier(2, timeout=3)
    passed: list[str] = []

    def fake(hub, key, engine_globals=None):
        barrier.wait(timeout=3)
        passed.append(key)

    _wire_tick(s, keys, fake)
    s._check_ase_per_user()
    assert sorted(passed) == keys, (
        f"N=2 未走并行分支（passed={passed}，串行下 barrier 必 broken）"
    )


def test_r3_user_exception_does_not_kill_remaining_users():
    """R3（护栏）：单用户异常被隔离、日志与旧串行逐字同型，后序用户照常处理。"""
    s = _stub_sched()
    keys = ["A", "B", "C"]
    processed: list[str] = []

    def fake(hub, key, engine_globals=None):
        processed.append(key)
        if key == "B":
            raise RuntimeError("boom-B")

    _wire_tick(s, keys, fake)
    s._check_ase_per_user()
    assert processed == keys, "B 抛异常不得阻断 A/C（提交顺序逐个收结果）"


def test_r4_single_user_runs_inline_on_caller_thread():
    """R4（N=1 零变化钉子）：单用户走原串行内联分支——同线程执行，不开新线程。"""
    s = _stub_sched()
    caller_tid = threading.get_ident()
    seen_tids: list[int] = []

    def fake(hub, key, engine_globals=None):
        seen_tids.append(threading.get_ident())

    _wire_tick(s, ["only-user"], fake)
    s._check_ase_per_user()
    assert seen_tids == [caller_tid], "N=1 必须内联在调用线程执行（零变化证明）"


def test_r5_env_one_rolls_back_to_serial_inline(monkeypatch):
    """R5（回滚钉子）：env=1 → 全部用户在调用线程串行执行（行为逐字回旧）。"""
    monkeypatch.setenv("PROACTIVE_TICK_CONCURRENCY", "1")
    s = _stub_sched()
    caller_tid = threading.get_ident()
    seen: list[tuple[str, int]] = []
    peak = 0
    active = 0
    lock = threading.Lock()

    def fake(hub, key, engine_globals=None):
        nonlocal peak, active
        with lock:
            active += 1
            peak = max(peak, active)
        _time.sleep(0.05)
        seen.append((key, threading.get_ident()))
        with lock:
            active -= 1

    _wire_tick(s, ["u1", "u2", "u3"], fake)
    s._check_ase_per_user()
    assert peak == 1, f"env=1 时并发峰值必须为 1（got {peak}）"
    assert {tid for _, tid in seen} == {caller_tid}, "env=1 必须全部内联在调用线程"


# ── A4：账本驱逐 ─────────────────────────────────────────

_FAKE_NOW = datetime(2026, 11, 10, 12, 0, 0)
_TODAY = _FAKE_NOW.date().isoformat()
_YESTERDAY = (_FAKE_NOW - timedelta(days=1)).date().isoformat()


def _evict_sched(monkeypatch) -> ProactiveScheduler:
    monkeypatch.setattr(sched_mod, "_local_now", lambda: _FAKE_NOW)
    return _stub_sched()


def test_r6_evict_removes_non_today_day_entries(monkeypatch):
    """R6（红）：五个 day dict 只删 value≠今天——闸位只判 == today，语义零变化。"""
    s = _evict_sched(monkeypatch)
    s._disabled_event_day = {"old@im.wechat": _YESTERDAY, "now@im.wechat": _TODAY}
    s._wait_window_event_day = {"old@im.wechat": _YESTERDAY, "now@im.wechat": _TODAY}
    s._quiet_event_day = {"old@im.wechat": _YESTERDAY, "now@im.wechat": _TODAY}
    s._model_degraded_event_day = {"old@im.wechat": _YESTERDAY, "now@im.wechat": _TODAY}
    s._gate_skip_event_day = {
        "old@im.wechat|min_interval": _YESTERDAY,
        "now@im.wechat|quiet_hours": _TODAY,
    }
    s._evict_stale_ledger_entries()
    assert s._disabled_event_day == {"now@im.wechat": _TODAY}
    assert s._wait_window_event_day == {"now@im.wechat": _TODAY}
    assert s._quiet_event_day == {"now@im.wechat": _TODAY}
    assert s._model_degraded_event_day == {"now@im.wechat": _TODAY}
    assert s._gate_skip_event_day == {"now@im.wechat|quiet_hours": _TODAY}


def test_r7_evict_next_ok_seven_day_ttl(monkeypatch):
    """R7（红）：next_ok 按 7 天 TTL 驱逐（ts ≤ now-7d 删；窗口内/未来留）。"""
    s = _evict_sched(monkeypatch)
    now = _time.time()
    s._llm_proactive_next_ok = {
        "8d@im.wechat": now - 8 * 86400.0,
        "7d+10s@im.wechat": now - 7 * 86400.0 - 10.0,  # 恰过线 → 删
        "7d-10s@im.wechat": now - 7 * 86400.0 + 10.0,  # 未到线 → 留
        "future@im.wechat": now + 600.0,
    }
    s._evict_stale_ledger_entries()
    assert set(s._llm_proactive_next_ok) == {"7d-10s@im.wechat", "future@im.wechat"}


def test_r8a_evict_fail_counts_paired_with_seen_stamps(monkeypatch):
    """R8（红）：fail_counts 不做目标外驱逐（P1-22 退避阶梯跨重启保留），
    仅按 sidecar 盖章超 7 天**成对**删除；缺章（旧数据）永不驱逐。"""
    s = _evict_sched(monkeypatch)
    now = _time.time()
    s._deliver_fail_counts = {"stale@im.wechat": 3, "fresh@im.wechat": 2, "nostamp@im.wechat": 1}
    s._deliver_fail_seen = {"stale@im.wechat": now - 8 * 86400.0, "fresh@im.wechat": now}
    s._evict_stale_ledger_entries()
    assert "stale@im.wechat" not in s._deliver_fail_counts
    assert "stale@im.wechat" not in s._deliver_fail_seen
    assert s._deliver_fail_counts == {"fresh@im.wechat": 2, "nostamp@im.wechat": 1}
    assert s._deliver_fail_seen == {"fresh@im.wechat": now}


def test_r8b_fail_seen_sidecar_persists_and_reloads(tmp_path):
    """R8b（红）：盖章 sidecar 随 throttle 段持久化为 deliver_fail_counts_ts；
    _load_throttle_ledger 容错回填（缺 ts 字段=不驱逐，键不出现）。"""
    s = _stub_sched(tmp_path)
    s._deliver_fail_counts = {"u@im.wechat": 2}
    s._deliver_fail_seen = {"u@im.wechat": 1699483200.5}
    s._persist_throttle_ledger()
    data = json_state.read_json(s._CONFIG_PATH, default={})
    assert data["throttle"].get("deliver_fail_counts_ts") == {"u@im.wechat": 1699483200.5}, (
        "sidecar 必须随 throttle 段落盘（重启后驱逐判据不丢）"
    )
    # 回读：同路径重载 → _deliver_fail_seen 回填
    s2 = _stub_sched(tmp_path)
    s2._load_throttle_ledger()
    assert s2._deliver_fail_seen == {"u@im.wechat": 1699483200.5}
    # 容错：文件缺 ts 字段 → 不回填（缺 ts=不驱逐），且不抛异常
    (tmp_path / "scheduler_config.json").write_text(
        json.dumps({"throttle": {"deliver_fail_counts": {"v@im.wechat": 1}}}),
        encoding="utf-8",
    )
    s3 = _stub_sched(tmp_path)
    s3._load_throttle_ledger()
    assert s3._deliver_fail_seen == {}
    assert s3._deliver_fail_counts == {"v@im.wechat": 1}


def test_r9_judge_stash_capped_at_64(monkeypatch):
    """R9（红）：judge stash 超上限弹最旧键（dict 保插入序）；cap=64。"""
    s = _evict_sched(monkeypatch)
    s._judge_recent = {f"k{i}": [str(i)] for i in range(70)}
    s._judge_topic = {f"t{i}": f"v{i}" for i in range(70)}
    s._evict_stale_ledger_entries()
    assert len(s._judge_recent) == 64
    assert "k0" not in s._judge_recent and "k69" in s._judge_recent
    assert len(s._judge_topic) == 64
    assert "t0" not in s._judge_topic and "t69" in s._judge_topic


def test_r9b_stash_trim_bounded_even_when_evict_disabled(monkeypatch):
    """R9b：驱逐开关关断时 stash 也不无界——写点裁剪独立生效（直接调 trim）。"""
    monkeypatch.setenv("SCHEDULER_LEDGER_EVICT", "0")
    s = _stub_sched()
    s._judge_recent = {f"k{i}": [str(i)] for i in range(70)}
    s._trim_judge_stash()
    assert len(s._judge_recent) == 64
    assert "k69" in s._judge_recent


def test_r10_concurrent_mutation_does_not_break_persist_snapshot(tmp_path, caplog):
    """R10（红，审阅意见对应）：注入式并发变异下 persist 快照不炸——现行
    _mutate 在 update_json 内迭代真实容器，与祝福线 ``_important_dates_sent.add``
    / tick 写点并发即 "Set/Dict changed size during iteration"，被 except 吞成
    "持久化节流账本失败" 告警 = 落盘丢失。新实现锁内快照 → 恒无该告警。"""
    s = _stub_sched(tmp_path)
    stop = threading.Event()
    # 强制高频 GIL 切换，把 persist 快照迭代与写点变异的竞态窗口放大到必现
    # （默认 5ms 切换粒度下 300 次快照未必撞上一次变异，首轮红跑实证漏过）
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def mutator():
        # 模拟**改造后的生产写点形态**（祝福线 add / tick 写点：持账本锁写，
        # 见 proactive/scheduler.py 各写点的 `with self._ledger_scope()`）——
        # 红绿差异由 persist 侧单独决定：现行 persist 不取锁（快照直接迭代
        # 真实容器，与持锁写点仍并发 → 炸）；新实现 persist 锁内快照 → 互斥。
        import contextlib as _cl

        lock = getattr(s, "_ledger_lock", None)
        j = 0
        while not stop.is_set():
            k = f"u{j % 40}"
            with (lock if lock is not None else _cl.nullcontext()):
                s._llm_proactive_next_ok[k] = float(j)
                s._llm_proactive_next_ok.pop(f"u{(j + 1) % 40}", None)
                s._disabled_event_day[k] = _TODAY
                s._important_dates_sent.add(f"e{j % 60}")
                s._important_dates_sent.discard(f"e{(j + 7) % 60}")
            j += 1
            if j % 200 == 0:
                _time.sleep(0)

    threads = [threading.Thread(target=mutator) for _ in range(2)]
    for t in threads:
        t.start()
    try:
        with caplog.at_level(logging.WARNING, logger="proactive.scheduler"):
            for _ in range(300):
                s._persist_throttle_ledger()
                _time.sleep(0.002)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
        sys.setswitchinterval(old_interval)
    failures = [r for r in caplog.records if "持久化节流账本失败" in r.getMessage()]
    assert not failures, f"persist 快照与并发变异竞争（{len(failures)} 次落盘丢失告警）"


# ── A3：投递错误归因隔离 ─────────────────────────────────

_WECHAT_OK = lambda message, session_key=None: None  # noqa: E731


def _wechat_boom(text: str):
    def _sender(message, session_key=None):
        raise RuntimeError(text)

    return _sender


def test_r11_per_session_deliver_error_isolation():
    """R11（红，审阅 Q4 补的 A3 归因红测）：A 失败后 B 失败，B 的错误归因必须是
    B 自己的（现行共享标量 → B 读到 A 的 "boom-A"）；成功路径复位同步清镜像。"""
    s = _stub_sched()
    key_a, key_b = "4:a@im.wechat", "4:b@im.wechat"
    # A 失败 → 镜像 + 标量双写
    s._channel_instances["wechat"] = _wechat_boom("boom-A")
    assert asyncio.run(s._send_targeted("hi", key_a)) is False
    assert s._last_deliver_errors[key_a] == "boom-A"
    assert s._last_deliver_error == "boom-A"  # 标量契约保持（test_proactive_user_scope）
    # B 成功 → 入口复位清 B 镜像，A 镜像不受影响
    s._channel_instances["wechat"] = _WECHAT_OK
    assert asyncio.run(s._send_targeted("hi", key_b)) is True
    assert key_b not in s._last_deliver_errors
    # B 失败 → 归因 B 自己的错误类，不串 A 的
    s._channel_instances["wechat"] = _wechat_boom("boom-B")
    assert asyncio.run(s._send_targeted("hi", key_b)) is False
    assert s._last_deliver_errors[key_b] == "boom-B"
    assert s._last_deliver_errors[key_a] == "boom-A"
    # A 再成功 → A 镜像被清（复位同步清 dict）
    s._channel_instances["wechat"] = _WECHAT_OK
    assert asyncio.run(s._send_targeted("hi", key_a)) is True
    assert key_a not in s._last_deliver_errors


def test_r11b_quiet_hours_broadcast_writes_empty_key_mirror():
    """R11b：广播判静默（session_key=None，user_scope 契约）也走镜像——键为
    空串，不与任何用户键冲突。"""
    s = _stub_sched()
    s._is_quiet_hours = lambda: True
    assert asyncio.run(s._send_targeted("晚安", None)) is False
    assert s._last_deliver_error == "quiet_hours"
    assert s._last_deliver_errors.get("") == "quiet_hours"


def test_r12_send_targeted_reset_clears_mirror_for_web_path():
    """R12（红，审阅 A3 复位缺口）：web 通道失败路径既不写标量也不写镜像——
    入口复位必须同步 pop 镜像，否则同用户上一轮的错误类泄漏进本轮分类。"""
    s = _stub_sched()
    key_w = "u1"  # 非 wechat → 走 websocket 定向分支
    s._last_deliver_errors[key_w] = "stale-from-last-round"
    s._channel_instances["websocket"] = _wechat_boom("web-down")
    assert asyncio.run(s._send_targeted("hi", key_w)) is False
    assert key_w not in s._last_deliver_errors, (
        "web 失败路径不写镜像，入口复位必须清掉上一轮残留"
    )
    assert s._last_deliver_error == ""  # web 失败路径不写标量（既有行为）
