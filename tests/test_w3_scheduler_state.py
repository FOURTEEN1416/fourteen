"""W3 块4 —— 调度器侧状态跨重启一致（缺陷 J / D / E）。

钉的是同一件事的三面：**决策产生的节流状态必须落盘并在重启后仍然成立**，
**策略开关必须在生成前生效**，**手动发送必须复用真实投递路径**。

- J：LLM 自判的 `wait_minutes` 此前只进内存字典，重启即归零 → 她会在模型
  明确说「X 分钟后再说」的窗口内提前开口（与退避阶梯"重启归零"同一族缺陷，
  块E 只修了失败侧，成功侧漏修）。
- D：`paused` 存引擎内存 + 网页配置，跨 worker 与重启后各说各话 → 暂停只在
  一个进程生效。
- E：手动发送绕过 `_deliver`，直接调用通道 → 不产生受理回执、不计配额、
  无角色归属。
"""

from __future__ import annotations

import json
import time

import pytest


def _quiet_window_excluding_now() -> tuple[int, int]:
    from proactive.ase_engine import _local_now

    h = _local_now().hour
    return ((h + 8) % 24, (h + 9) % 24)


@pytest.fixture()
def sched_sandbox():
    """调度器；`_CONFIG_PATH` 已由 conftest 重定向到 per-test 沙箱。"""
    from proactive.scheduler import ProactiveScheduler

    s = ProactiveScheduler()
    s._quiet_hours = _quiet_window_excluding_now()  # 当前小时落在静默窗外
    return s


def _decide(monkeypatch, ret: dict):
    import proactive.llm_proactive as lp

    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "decide_proactive", lambda llm, ctx: ret)
    monkeypatch.setattr(lp, "load_persona_hint", lambda cid="": "P")


class _Hub:
    def __init__(self):
        self.eng = type("E", (), {
            "_hours_since_last_chat": staticmethod(lambda: 9.0),
            "tick": lambda self, h, dry_run=False: None,
            "urgency": type("U", (), {"total": 5.0})(),
            "_last_user_message": "明天要体检",
            "response_rate": 0.8,
            "_knowledge_character_id": "micai",
            "get_runtime_config": lambda self: {},
            "commit_sent": lambda self, m: None,
            "apply_runtime_config": lambda self, **kw: None,
        })()
        self.applied: dict = {}

    def get(self, _key):
        return self.eng

    def apply_runtime_config(self, **kwargs) -> None:
        """ASEHub 扇出口径：只透传非 None 键（暂停开关不得顺带改写频率参数）。"""
        self.applied.update({k: v for k, v in kwargs.items() if v is not None})
        self.eng.apply_runtime_config(**kwargs)


def test_llm_wait_window_survives_restart(sched_sandbox, monkeypatch):
    """模型自判 wait_minutes=45 → 新实例（模拟重启）仍须挡住这 45 分钟。"""
    _decide(monkeypatch, {
        "should_contact": False, "reason": "他在忙", "wait_minutes": 45, "message": "",
    })
    import shisi.agent_plane.runtime as rt

    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    events: list = []
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: events.append(kw))
    sched_sandbox._resolve_proactive_llm = lambda eng=None: object()
    sched_sandbox._resolve_character_id = lambda sk: "micai"
    key = "7:wx_b@im.wechat"
    sched_sandbox._llm_proactive_one_user(_Hub(), key)

    assert sched_sandbox._llm_proactive_next_ok[key] > time.time() + 40 * 60
    data = json.loads(
        (sched_sandbox._CONFIG_PATH).read_text(encoding="utf-8"),  # type: ignore[arg-type]
    )
    assert data["throttle"]["llm_proactive_next_ok"][key] == pytest.approx(
        sched_sandbox._llm_proactive_next_ok[key], abs=1.0,
    )

    # 重启：新实例（__init__ 自带账本回放）仍挡住同一窗口
    from proactive.scheduler import ProactiveScheduler

    reborn = ProactiveScheduler()
    reborn._quiet_hours = _quiet_window_excluding_now()
    assert reborn._llm_proactive_next_ok.get(key) == pytest.approx(
        sched_sandbox._llm_proactive_next_ok[key], abs=1.0,
    )
    called: list = []
    import proactive.llm_proactive as lp

    monkeypatch.setattr(lp, "decide_proactive", lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    reborn._llm_proactive_one_user(_Hub(), key)
    assert called == [], "等待窗内不得调用决策模型"
    assert events and events[-1]["reason"] == "llm_wait_window"


# ── D：暂停必须持久化跨 worker 一致，且在生成前生效 ──────────────


def _stub_proactive_reads(monkeypatch, events: list):
    import shisi.agent_plane.runtime as rt

    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: events.append(kw))


def test_pause_persists_and_blocks_generation(sched_sandbox, monkeypatch):
    """暂停后：决策模型**不得被调用**（旧实现只在 tick 内闸，LLM 决策链整条绕过），
    且新开实例（另一 worker / 重启）从同一真源读到暂停。"""
    events: list = []
    called: list = []
    import proactive.llm_proactive as lp

    _decide(monkeypatch, {
        "should_contact": True, "reason": "想你了", "wait_minutes": None,
        "message": "在干嘛呀",
    })
    monkeypatch.setattr(
        lp, "decide_proactive", lambda llm, ctx: called.append(1) or {},
    )
    _stub_proactive_reads(monkeypatch, events)
    sched_sandbox._resolve_proactive_llm = lambda eng=None: object()
    sched_sandbox._resolve_character_id = lambda sk: "micai"
    key = "7:wx_b@im.wechat"

    sched_sandbox.set_paused(True)
    sched_sandbox._llm_proactive_one_user(_Hub(), key)
    assert called == [], "暂停后仍调用决策模型 = 暂停不生效"
    assert events and events[-1]["reason"] == "paused"

    # 真源落盘（跨 worker：非 master worker 的 POST 只写文件）
    cfg = type(sched_sandbox)._read_config_file()
    assert cfg["proactive"]["paused"] is True

    from proactive.scheduler import ProactiveScheduler

    other_worker = ProactiveScheduler()
    assert other_worker.is_paused() is True
    other_worker._quiet_hours = _quiet_window_excluding_now()
    other_worker._llm_proactive_one_user(_Hub(), key)
    assert called == [], "另一 worker 也必须停在生成之前"


def test_resume_clears_pause_in_file_and_memory(sched_sandbox):
    sched_sandbox.set_paused(True)
    assert sched_sandbox.is_paused() is True
    sched_sandbox.set_paused(False)
    assert sched_sandbox.is_paused() is False
    assert type(sched_sandbox)._read_config_file()["proactive"]["paused"] is False


def test_pause_never_resurrects_hard_frequency_gate(sched_sandbox):
    """🔴 DECISION_LEDGER:118 —— 暂停是开关，不得变成第二套硬频率闸。

    暂停/恢复都不得改写引擎的 threshold / max_daily / min_interval，
    也不得在恢复后留下任何策略性抑制（恢复即回到模型自判时机）。
    """
    hub = _Hub()
    sched_sandbox.ase = hub
    before = dict(sched_sandbox._llm_proactive_next_ok)

    sched_sandbox.set_paused(True)
    sched_sandbox.set_paused(False)
    for k in ("threshold", "max_daily_messages", "min_interval_minutes",
              "cooldown_after_reply_minutes"):
        assert hub.applied.get(k) is None, f"暂停被当成硬闸改写：{k}={hub.applied.get(k)}"
    assert hub.applied.get("paused") is False
    assert sched_sandbox._llm_proactive_next_ok == before, "暂停不得伪造等待窗"


# ── D 的路由面：POST 落在任一 worker 都必须写同一真源 ──────────────


class _Orch:
    def __init__(self, scheduler, ase):
        self.components = {"scheduler": scheduler} if scheduler is not None else {}
        self._ase = ase


def _call_pause(monkeypatch, scheduler, ase, paused: bool):
    import asyncio

    import api.routers.training_routes as tr
    from api.deps import deps

    monkeypatch.setattr(deps, "orch", _Orch(scheduler, ase), raising=False)
    req = tr.ProactivePauseRequest(paused=paused)
    return asyncio.run(tr.pause_proactive(req, _auth=True, _admin=(1, None)))


def test_pause_route_writes_truth_source_without_any_engine(monkeypatch):
    """无调度器、无引擎的 worker（生产 4 worker 里 3 个都这样）：旧实现直接抛
    503「Proactive engine not initialized」，用户以为暂停了其实没暂停。
    现在必须写文件真源并返回 ok，master 下个 tick 重载生效。"""
    from proactive.scheduler import ProactiveScheduler

    result = _call_pause(monkeypatch, scheduler=None, ase=None, paused=True)
    assert result == {"status": "ok", "paused": True}
    assert ProactiveScheduler._read_config_file()["proactive"]["paused"] is True
    assert ProactiveScheduler().is_paused() is True


def test_pause_route_prefers_master_scheduler_and_syncs_engine(monkeypatch, sched_sandbox):
    """持有调度器的 worker：走 set_paused（落盘 + 同步引擎内存闸）。"""
    hub = _Hub()
    sched_sandbox.ase = hub

    result = _call_pause(monkeypatch, scheduler=sched_sandbox, ase=hub, paused=True)
    assert result["paused"] is True
    assert sched_sandbox.is_paused() is True
    assert hub.applied.get("paused") is True
    assert type(sched_sandbox)._read_config_file()["proactive"]["paused"] is True
