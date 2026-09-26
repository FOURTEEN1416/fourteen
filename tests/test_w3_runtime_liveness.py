"""W3 缺陷 A 收口 —— 驻留后台运行时的**跨进程可观测心跳**。

调度器由 flock 选出的 master worker 持有（四 worker 部署里另三个
`components["scheduler"] is None`）。此前「后台运行时到底活没活」只能各 worker
读**本进程**状态回答：master 的 APScheduler 停摆（线程死、job 不再触发）时，
别的 worker 依旧毫无察觉，用户侧表现为「主动消息与叫醒一起消失而服务全绿」——
与 v1.13「配额被静默吃掉」同族：**故障不可见比故障本身更难查**。

本文件钉三件事：

1. 控制面 `runtime_beat/runtime_status/runtime_beats`：任意进程可写、任意进程
   可读，过期即判不活（租约语义，复用 SQLite，不引入新组件）；
2. 调度器**每个后台任务**经过 `_safe_job_wrapper` 这一唯一咽喉时续心跳
   （谁真正跑过 job 谁才算活着，而不是「进程还在」）；`start()` 当场打一拍，
   新起的 master 不得因「首个 job 还要等 5 分钟」被判僵尸；
3. 读面把真相透出去：`/api/proactive/state` 的 `runtime` 字段来自控制面，
   **非 master worker 也能报出宿主 pid 与心跳年龄**；调度器自身 `health_check`
   带上驻留判据（readiness 里 scheduler 属可降级项——后台运行时死了不该把还能
   对话的 worker 摘出负载均衡，但必须看得见）。

数据全部合成（tmp 库 + conftest 沙箱控制面）。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from proactive import runtime_plane as rp

BEAT = "scheduler"


@pytest.fixture(autouse=True)
def plane_tmp(tmp_path):
    prev = rp.db_path()
    rp.set_db_path(tmp_path / "runtime_plane.db")
    yield
    rp.set_db_path(prev)


@pytest.fixture()
def sched():
    from proactive.scheduler import ProactiveScheduler

    return ProactiveScheduler()


# ── 1. 控制面租约 ──────────────────────────────────────────


def test_no_beat_means_no_resident_runtime():
    st = rp.runtime_status(BEAT)
    assert st["alive"] is False
    assert st["holder"] == ""
    assert st["age_seconds"] is None
    assert rp.runtime_beats() == []


def test_beat_reports_holder_and_age():
    rp.runtime_beat(BEAT)
    st = rp.runtime_status(BEAT)
    assert st["alive"] is True
    assert st["holder"].startswith(f"{os.getpid()}:")
    assert st["age_seconds"] is not None and st["age_seconds"] < 5.0
    assert [b["name"] for b in rp.runtime_beats()] == [BEAT]


def test_beat_overwrites_previous_holder():
    """master 更替（原宿主退出、别的 worker 抢到锁）后读数必须指向新宿主。"""
    rp.runtime_beat(BEAT, holder="111:aaa")
    rp.runtime_beat(BEAT, holder="222:bbb")
    st = rp.runtime_status(BEAT)
    assert st["holder"] == "222:bbb"
    assert st["age_seconds"] is not None and st["age_seconds"] < 5.0


def test_stale_beat_is_not_alive(monkeypatch):
    """心跳过期即判不活——「进程还在但 job 不再跑」不得继续报 alive。"""
    rp.runtime_beat(BEAT)
    base = rp._now()
    monkeypatch.setattr(rp, "_now", lambda: base + rp.RUNTIME_BEAT_TTL + 1)
    assert rp.runtime_status(BEAT)["alive"] is False
    # 年龄仍可读（运维要看得见"多久没心跳"）
    assert (rp.runtime_status(BEAT)["age_seconds"] or 0) > rp.RUNTIME_BEAT_TTL


# ── 2. 调度器心跳接线（唯一咽喉） ─────────────────────────


def test_every_scheduled_job_touches_the_beat(sched):
    """job 真被触发才算活着：`_safe_job_wrapper` 是全部后台任务的公共入口。"""
    assert rp.runtime_status(BEAT)["alive"] is False
    ran: list[str] = []
    sched._safe_job_wrapper(lambda: ran.append("ase"), "ase_check")()
    assert ran == ["ase"]
    assert rp.runtime_status(BEAT)["alive"] is True


def test_failing_job_still_proves_liveness(sched):
    """任务抛错被包装器兜住，但「它跑了」仍是运行时活着的证据（不得因异常失联）。"""
    def _boom() -> None:
        raise RuntimeError("job 内部失败")

    sched._safe_job_wrapper(_boom, "reminder_check")()  # 不外抛（既有契约）
    assert rp.runtime_status(BEAT)["alive"] is True


def test_start_beats_immediately(sched, monkeypatch):
    """新 master 装配完即有一拍：否则首跑前的 5 分钟窗口被误判僵尸。"""
    import proactive.scheduler as ps

    class _Fake:
        running = True

        def add_job(self, *a, **k):
            pass

        def start(self):
            pass

        def get_jobs(self):
            return [1]

    monkeypatch.setattr(ps, "BackgroundScheduler", lambda **kw: _Fake())
    monkeypatch.setattr(sched, "_sync_vault_job", lambda: None)
    assert rp.runtime_status(BEAT)["alive"] is False
    assert sched.start() is True
    assert rp.runtime_status(BEAT)["alive"] is True


# ── 3. 读面透出 ────────────────────────────────────────────


def test_proactive_state_surfaces_control_plane_truth():
    """非 master worker（本进程无引擎）也必须报出真实驻留态。"""
    from api.routers import training_routes as tr

    rp.runtime_beat(BEAT, holder="999:zzz")
    saved = tr.deps.orch
    tr.deps.orch = SimpleNamespace(_ase=None, components={})
    try:
        state = asyncio.run(tr.proactive_state(_auth=True))
    finally:
        tr.deps.orch = saved
    assert state["runtime"]["holder"] == "999:zzz"
    assert state["runtime"]["alive"] is True


def test_scheduler_health_check_reports_residency(sched, monkeypatch):
    """调度器自检带驻留判据：running 只说明本进程线程还活着，不等于 job 在跑。"""
    monkeypatch.setattr(sched, "_scheduler", SimpleNamespace(running=True))
    monkeypatch.setattr(sched, "get_jobs", lambda: [1, 2, 3])
    rp.runtime_beat(BEAT, holder="4242:abc")
    hc = sched.health_check()
    assert hc["running"] is True and hc["jobs"] == 3
    assert hc["beat_alive"] is True
    assert hc["beat_holder"] == "4242:abc"
    assert hc["beat_age_seconds"] is not None and hc["beat_age_seconds"] < 5.0
