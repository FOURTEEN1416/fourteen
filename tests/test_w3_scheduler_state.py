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
from pathlib import Path
from types import SimpleNamespace

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


# ── D 的第二面：角色绑定必须跨 worker 同源（缺陷 D：仅启动预载）──────
#
# 生产 4 worker 只有 master 跑调度器。用户换角色（PUT /peers/{wxid}/character
# 或控制台切卡）落在任一 API worker：它写 DB + 只热更**本进程**缓存与实例。
# master 的 `_bindings` 是启动快照 → 主动消息/祝福继续按**旧角色**口吻开口，
# 且新绑定用户的 wxid 在 master 里根本不存在（她永不去搭话，直到重启）。


def _make_users_db(path):
    """最小真源：wechat_bindings + wechat_peer_preferences 两张表。"""
    import sqlite3

    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE wechat_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            wxid TEXT NOT NULL UNIQUE,
            nickname TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            character_card_id TEXT DEFAULT 'default',
            bound_at TEXT
        );
        CREATE TABLE wechat_peer_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            peer_wxid TEXT NOT NULL,
            character_card_id TEXT NOT NULL,
            chosen_at TEXT
        );
        """
    )
    con.commit()
    con.close()


def _write_pref(path, owner: int, peer: str, card: str) -> None:
    import sqlite3

    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT OR REPLACE INTO wechat_peer_preferences "
        "(owner_user_id, peer_wxid, character_card_id, chosen_at) VALUES (?,?,?,?)",
        (owner, peer, card, "2026-09-27 00:00:00"),
    )
    con.commit()
    con.close()


def _mgr(bindings: list[dict]):
    import asyncio

    from user_scheduler import UserManager

    m = UserManager(SimpleNamespace(components={}))
    asyncio.run(m.load_bindings(bindings))
    return m


def test_peer_character_change_by_other_worker_reaches_master_cache(tmp_path):
    db = tmp_path / "users.db"
    _make_users_db(db)
    _write_pref(db, 7, "wx_b@im.wechat", "micai")

    from utils import session_key

    sk = session_key.build(7, "wx_b@im.wechat")
    mgr = _mgr([])
    # 回灌前：master 什么都不知道 → 回落内置角色
    assert mgr._resolve_character_id(sk) == "default"

    changed = mgr.refresh_bindings_from_db(db_path=db)
    assert changed >= 1
    assert mgr._resolve_character_id(sk) == "micai"

    # 另一 worker 再次改卡 → 再回灌即跟上（不重启、不等 master 自身被写）
    _write_pref(db, 7, "wx_b@im.wechat", "jiangtian")
    mgr.refresh_bindings_from_db(db_path=db, force=True)
    assert mgr._resolve_character_id(sk) == "jiangtian"


def test_refresh_propagates_to_live_instance_and_new_bind_target(tmp_path):
    """实例已存在时 `get_user_character` 优先信实例（缺陷 D 的 :460-471），
    回灌必须把变更同步到活实例；真源新增的偏好键必须同样进入缓存。"""
    db = tmp_path / "users.db"
    _make_users_db(db)
    import sqlite3

    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO wechat_bindings (user_id, wxid, nickname, character_card_id) "
        "VALUES (7,'wx_b@im.wechat','默默','linwanxia')"
    )
    con.commit()
    con.close()

    mgr = _mgr([{"wxid": "wx_b@im.wechat", "user_id": 7,
                 "nickname": "默默", "character_card_id": "linwanxia"}])
    inst = mgr._get_or_create("7:wx_b@im.wechat")
    assert inst.character_card_id == "linwanxia"

    # 另一 worker 上用户切卡（写 DB，本进程实例不知道）
    con = sqlite3.connect(str(db))
    con.execute("UPDATE wechat_bindings SET character_card_id='micai' WHERE wxid=?",
                ("wx_b@im.wechat",))
    con.execute("INSERT INTO wechat_peer_preferences (owner_user_id, peer_wxid, "
                "character_card_id, chosen_at) VALUES (8,'wx_new@im.wechat','chengshuang','')")
    con.commit()
    con.close()

    mgr.refresh_bindings_from_db(db_path=db, force=True)
    assert mgr.get_user_character("7:wx_b@im.wechat") == "micai"
    assert inst.character_card_id == "micai", "活实例仍是旧卡 → 本轮对话/情感继续错角色"
    # 新偏好按真源键形态进缓存（与 lifespan 启动预载同构：pref:{owner}:{peer}）
    assert "pref:8:wx_new@im.wechat" in mgr.get_bound_wxids()
    assert mgr._resolve_character_id("8:wx_new@im.wechat") == "chengshuang"


# ── D 的接线面：master 的后台任务必须**先**回灌真源再决策 ────────────


class _StubGf:
    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh_bindings_from_db(self, **_kw) -> int:
        self.refresh_calls += 1
        return 0


def _stub_gf(monkeypatch) -> _StubGf:
    from api.deps import deps

    gf = _StubGf()
    monkeypatch.setattr(deps, "gf", gf, raising=False)
    return gf


def test_proactive_tick_re_reads_binding_truth_first(monkeypatch, sched_sandbox):
    """调度器只在 master 跑，换角色的写路径落在别的 worker：
    每个 tick 开头不回灌真源，她就按启动快照的旧角色开口。"""
    gf = _stub_gf(monkeypatch)
    sched_sandbox.ase = None  # 引擎缺失时 _check_ase 立即返回，隔离被测点
    sched_sandbox._check_ase()
    assert gf.refresh_calls == 1


def test_reminder_job_re_reads_then_runs_task(monkeypatch, sched_sandbox):
    gf = _stub_gf(monkeypatch)
    ran: list[int] = []
    sched_sandbox._reminder_task = lambda: ran.append(1)
    sched_sandbox._run_reminder_check()
    assert ran == [1], "提醒任务仍须被执行（回灌不是替代）"
    assert gf.refresh_calls == 1


def test_important_dates_job_re_reads_before_early_return(monkeypatch, sched_sandbox):
    """祝福任务即使在免打扰早退，也应先完成真源回灌（回灌是任务第一步）。"""
    from proactive.ase_engine import _local_now

    gf = _stub_gf(monkeypatch)
    h = _local_now().hour
    sched_sandbox._quiet_hours = (h, (h + 1) % 24)  # 当前小时必在窗内 → 立即早退
    sched_sandbox._check_important_dates()
    assert gf.refresh_calls == 1



# ── D 第三面：ASE 交互新鲜度按版本合并（旧全量快照不得覆盖新互动）────────
#
# 状态文件是**多 worker 共享**的：聊天所在 worker 的引擎 `on_chat` → 落盘，
# 而 master 调度器每 10 分钟 `_save_state()` 把**自己那份内存**整表覆写
# （ase_engine.save_state 是完整快照）。两侧内存来自各自的加载时刻，
# master 持旧快照 → 刚发生的「用户开口」被抹回几小时前，于是她既看不见
# 「刚聊完」，注意力（response_rate）也被回退，主动消息紧迫度重新顶格。
# 口径：交互新鲜度三字段按 `last_user_interaction` 新者胜（磁盘更新则采纳
# 并回写内存），其余字段仍是写者为准（配额、冷却等是本地推进量）。


def _engine(tmp_path, name="ase.json"):
    from proactive.ase_engine import ASEEngine

    return ASEEngine(state_path=str(tmp_path / name), generation_mode="template")


def _disk(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_stale_snapshot_save_keeps_fresher_interaction(tmp_path):
    """另一 worker 刚落盘的「用户开口」，不能被本进程的陈旧快照覆盖回去。"""
    path = tmp_path / "ase.json"
    a = _engine(tmp_path)
    a.on_chat("早", "早呀")
    a.save_state()
    time.sleep(0.01)

    b = _engine(tmp_path)  # 另一进程：从盘加载后收到新的用户互动
    b.on_chat("我明天要体检", "别紧张，我在")
    b.save_state()

    assert a._last_user_message == "早"  # a 的内存确实陈旧
    a.save_state()  # 10 分钟批量持久化

    assert _disk(path)["last_user_message"] == "我明天要体检"
    assert a._last_user_message == "我明天要体检", "陈旧写者必须采纳更新的盘，而非继续陈旧"


def test_interaction_merge_does_not_freeze_other_fields(tmp_path):
    """合并只限交互新鲜度；配额等本地推进量仍以写者为准。"""
    path = tmp_path / "ase.json"
    a = _engine(tmp_path)
    a.on_chat("早", "早呀")
    a.save_state()
    time.sleep(0.01)

    b = _engine(tmp_path)  # 从盘加载后收到新的用户互动 → 磁盘更新
    b.on_chat("我明天要体检", "别紧张，我在")
    b.save_state()

    # a 仍是陈旧交互，但它自己推进了日配额
    assert a._last_user_message == "早"
    a._daily_message_count = 9
    a.save_state()

    saved = _disk(path)
    assert saved["daily_count"] == 9
    assert saved["last_user_message"] == "我明天要体检"


def test_refresh_interaction_from_disk_adopts_and_throttles(tmp_path):
    a = _engine(tmp_path)
    a.on_chat("早", "早呀")
    a.save_state()

    b = _engine(tmp_path)
    b.on_chat("我明天要体检", "别紧张，我在")
    b.save_state()

    a.refresh_interaction_from_disk()  # 首次不受节流
    assert a._last_user_message == "我明天要体检"

    c = _engine(tmp_path)
    c.on_chat("结果出来了，一切正常", "太好了")
    c.save_state()

    a.refresh_interaction_from_disk()  # 30s 节流窗内不重复读盘
    assert a._last_user_message == "我明天要体检"
    a.refresh_interaction_from_disk(force=True)
    assert a._last_user_message == "结果出来了，一切正常"


def test_hub_cache_hit_adopts_fresher_disk_interaction(tmp_path, monkeypatch):
    """hub 缓存命中直接 return（旧）→ master 的引擎永远不看文件版本。"""
    import proactive.ase_hub as hub_mod
    from proactive.ase_engine import ASEEngine
    from proactive.ase_hub import ASEHub

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")
    hub = ASEHub(lambda user_key="", state_path="", **kw: _engine(tmp_path, Path(state_path).name))
    hub._state_dir = tmp_path

    key = "7:wx_a@im.wechat"
    eng = hub.get(key)
    eng.on_chat("早", "早呀")
    eng.save_state()
    time.sleep(0.01)

    other = ASEEngine(state_path=str(eng._state_path), generation_mode="template")
    other.on_chat("我明天要体检", "别紧张，我在")
    other.save_state()

    assert hub.get(key) is eng, "缓存实例必须复用（不新建）"
    assert eng._last_user_message == "我明天要体检"
