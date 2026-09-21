"""P1 批4（4b/4c）回归 —— 审查报告 2026-09-21 items 18-25 + 48-53。

覆盖：
- ASEHub 全局操作真扇出/新引擎回放/LRU 淘汰先落盘/索引回收（P1-18）
- 手动发送落**具体引擎**、hub 上禁影子属性（P1-19）
- adaptive 频控 on_no_reply 真正接线 + min_interval/cooldown 配置生效（P1-20/25）
- 定向投递拒绝旧签名静默转广播（P1-21）
- 投递失败指数退避（P1-22）
- _deliver 桥接注入循环、不再跨循环 asyncio.run（P1-23）
- 重要日期本地钟（P1-24）
- persona_hint 读 _knowledge_character_id、静默前置 LLM 闸（P1-48/49）
- web_disabled 事件每日一条 + event_ledger 保留清理（P1-51）
- scheduler_config 原子读改写（P1-53）
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import proactive.ase_hub as hub_mod
from proactive.ase_engine import ASEEngine
from proactive.ase_hub import ASEHub
from proactive.scheduler import ProactiveScheduler


class FakeEngine:
    def __init__(self, user_key: str = "", state_path: str = ""):
        self.user_key = user_key
        self.state_path = state_path
        self.saved = 0
        self.closed = False
        self.quiet: tuple[int, int] | None = None
        self.cfg: dict = {}
        self.daily = 0
        self._knowledge_character_id = ""

    def save_state(self, path: str = "") -> None:
        self.saved += 1

    def close(self) -> None:
        self.closed = True

    def set_quiet_hours(self, start: int, end: int) -> None:
        self.quiet = (start, end)

    def apply_runtime_config(self, **kwargs) -> None:
        self.cfg.update({k: v for k, v in kwargs.items() if v is not None})

    def get_runtime_config(self) -> dict:
        return {
            "threshold": None, "max_daily_messages": None,
            "min_interval_minutes": None, "cooldown_after_reply_minutes": None,
            "paused": None, **self.cfg,
        }

    def reset_daily_count(self) -> None:
        self.daily = 0


@pytest.fixture()
def hub_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")
    hub = ASEHub(FakeEngine)
    hub._state_dir = tmp_path
    return hub


# ── P1-18：hub 扇出 / 回放 / LRU 落盘 / 索引回收 ──────────────────


def test_hub_global_ops_fanout_to_all_engines(hub_tmp):
    e1 = hub_tmp.get("1:a")
    e2 = hub_tmp.get("2:b")
    hub_tmp.set_quiet_hours(1, 2)
    hub_tmp.apply_runtime_config(paused=True, threshold=3.5)
    hub_tmp.reset_daily_count()
    for e in (e1, e2):
        assert e.quiet == (1, 2)
        assert e.cfg.get("paused") is True
        assert e.cfg.get("threshold") == 3.5


def test_hub_replays_config_to_new_engines(hub_tmp):
    hub_tmp.set_quiet_hours(23, 6)
    hub_tmp.apply_runtime_config(max_daily_messages=5)
    late = hub_tmp.get("3:c")  # 配置之后新建的引擎也必须带上
    assert late.quiet == (23, 6)
    assert late.cfg.get("max_daily_messages") == 5


def test_hub_lru_eviction_saves_state(hub_tmp, monkeypatch):
    monkeypatch.setattr(hub_mod, "_MAX_ENGINES", 2)
    e1 = hub_tmp.get("1:a")
    hub_tmp.get("2:b")
    hub_tmp.get("3:c")  # 挤掉 e1
    assert e1.saved >= 1, "LRU 淘汰前必须先落盘（旧实现直接丢，冷却/配额蒸发）"
    assert e1.closed


def test_hub_known_keys_prunes_missing_state(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")
    alive = tmp_path / "u1.json"
    alive.write_text("{}", encoding="utf-8")
    hub_mod._update_index(
        lambda data: data.update({"u1": str(alive), "u2": str(tmp_path / "gone.json")})
    )
    hub = ASEHub(FakeEngine)
    hub._state_dir = tmp_path
    assert hub.known_user_keys() == ["u1"]
    assert "u2" not in hub_mod._read_index_raw(), "状态文件已删 → 索引项必须回收"


def test_hub_get_runtime_config_merges_replay(hub_tmp):
    hub_tmp.set_quiet_hours(22, 7)
    hub_tmp.apply_runtime_config(paused=True)
    cfg = hub_tmp.get_runtime_config()
    assert cfg.get("paused") is True


# ── P1-19：手动发送落具体引擎、禁 hub 影子属性 ────────────────────


def test_resolve_engine_for_manual_send_hits_concrete_engine(hub_tmp):
    hub_tmp.get("1:a")
    e2 = hub_tmp.get("2:b")
    key, eng = hub_tmp.resolve_engine_for_manual_send()
    assert key == "2:b" and eng is e2
    # 端点配额归还必须赋在**具体引擎**上；经 hub 代理读同属性即见真值
    eng._daily_message_count = 3
    assert hub_tmp._daily_message_count == 3
    # 危害钉死：赋在 hub 实例上会写进 hub.__dict__、永久遮蔽 __getattr__ 代理
    # （旧端点写法 ase = orch._ase; ase._daily_message_count -= 1 即此模式，
    # 引擎侧配额从未真正归还）
    hub_tmp._daily_message_count = 0
    assert hub_tmp.__dict__["_daily_message_count"] == 0
    assert eng._daily_message_count == 3


def test_resolve_engine_empty_hub_returns_none(hub_tmp):
    assert hub_tmp.resolve_engine_for_manual_send() is None


# ── P1-20/25：adaptive 频控接线 + 配置生效 ────────────────────────


def _engine(tmp_path, **kw) -> ASEEngine:
    return ASEEngine(
        state_path=str(tmp_path / "ase.json"), generation_mode="template", **kw
    )


def test_adaptive_no_reply_streak_wired(tmp_path):
    eng = _engine(tmp_path, frequency_mode="adaptive")
    assert eng._freq_adapter is not None
    # 首次投递：上一条并非「未应答」（初始视为已应答）→ 不 +1
    eng.commit_sent({"message": "早啊", "type": "morning"})
    assert eng._freq_adapter._unanswered_count == 0
    # 无人回复又投一条 → 未应答计数 +1（旧实现 on_no_reply 全仓零调用）
    eng.commit_sent({"message": "吃了吗", "type": "meal"})
    assert eng._freq_adapter._unanswered_count == 1
    eng.on_chat("回了", "真好")
    eng.commit_sent({"message": "晚安", "type": "night"})
    assert eng._freq_adapter._unanswered_count == 0  # 回复过 → 再投不罚


def test_adaptive_honors_min_interval_and_cooldown_config(tmp_path):
    now = datetime.now(tz=timezone.utc)
    eng = _engine(
        tmp_path, frequency_mode="adaptive",
        min_interval_minutes=10, cooldown_after_reply=5,
    )
    eng._last_delivery_time = now
    assert eng._check_frequency() == (False, "min_interval")
    eng._last_delivery_time = now - timedelta(minutes=11)
    eng._last_chat_time = now
    assert eng._check_frequency() == (False, "cooldown")
    eng._last_chat_time = now - timedelta(minutes=6)
    assert eng._check_frequency() == (True, "ok")
    cfg = eng.get_runtime_config()
    assert cfg["min_interval_minutes"] == 10
    assert cfg["cooldown_after_reply_minutes"] == 5


def test_adaptive_runtime_config_updates_and_preserves_level(tmp_path):
    eng = _engine(tmp_path, frequency_mode="adaptive")
    eng._freq_adapter._unanswered_count = 4
    eng._freq_adapter._current_level = "low"
    eng.apply_runtime_config(min_interval_minutes=7, max_daily_messages=6)
    assert eng._min_interval_minutes == 7
    assert eng._freq_adapter._current_level == "low", "重建适配器不得清零降档状态"
    assert eng._freq_adapter._unanswered_count == 4
    assert eng._freq_adapter.normal_daily == 6


def test_state_roundtrip_keeps_delivery_time_and_config(tmp_path):
    eng = _engine(tmp_path, frequency_mode="adaptive", min_interval_minutes=9)
    eng.commit_sent({"message": "嗨", "type": "care"})
    eng.save_state()
    eng2 = _engine(tmp_path, frequency_mode="adaptive")
    assert eng2._last_delivery_time is not None
    assert eng2._min_interval_minutes == 9
    assert eng2._replied_since_proactive is False


# ── P1-21/22/23：定向隔离 / 退避 / 跨循环投递 ────────────────────


def _nonquiet(s: ProactiveScheduler) -> None:
    from proactive.ase_engine import _local_now

    h = _local_now().hour
    s._quiet_hours = ((h + 3) % 24, (h + 5) % 24)


@pytest.fixture()
def sched() -> ProactiveScheduler:
    s = ProactiveScheduler()
    _nonquiet(s)
    return s


def test_send_targeted_rejects_legacy_signature(sched):
    calls: list = []

    async def legacy(msg: str):  # 不收 session_key（main.py 旧签名）
        calls.append(msg)

    sched._channel_instances["wechat"] = legacy
    sched._send_to_all = lambda m: calls.append("BROADCAST") or asyncio.sleep(0)
    ok = asyncio.run(sched._send_targeted("私信内容", "4:peer@im.wechat"))
    assert ok is False, "旧签名通道下定向消息必须判失败，绝不静默转广播"
    assert calls == [], "不得发生任何广播"


def test_send_targeted_wechat_fail_no_broadcast(sched):
    seen: list = []

    async def sender(msg: str, session_key: str | None = None):
        raise RuntimeError("通道失效")

    async def fake_all(msg: str) -> bool:
        seen.append(msg)
        return True

    sched._channel_instances["wechat"] = sender
    sched._send_to_all = fake_all
    ok = asyncio.run(sched._send_targeted("m", "4:peer@im.wechat"))
    assert ok is False and seen == []
    assert sched._channel_instances["wechat"] is None, "运行时失败仍标记通道待重建"


def test_send_targeted_passes_session_key(sched):
    got: dict = {}

    async def sender(msg: str, session_key: str | None = None):
        got["sk"] = session_key

    sched._channel_instances["wechat"] = sender
    ok = asyncio.run(sched._send_targeted("m", "7:peer@im.wechat"))
    assert ok is True and got["sk"] == "7:peer@im.wechat"


def test_deliver_bridges_to_injected_loop(sched):
    import threading

    loop = asyncio.new_event_loop()
    started = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.call_soon(started.set)
        loop.run_forever()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    assert started.wait(5), "投递循环未启动"

    hits: dict = {}

    async def targeted(msg: str, session_key: str | None = None) -> bool:
        hits["same_loop"] = asyncio.get_running_loop() is loop
        return True

    try:
        sched.set_delivery_loop(lambda: loop)
        sched._send_targeted = targeted
        assert sched._deliver("hi", "4:p") is True
        assert hits.get("same_loop") is True, "P1-23：协程必须跑在通道所属循环"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout=5)
        loop.close()


# ── P1-48/49/51：persona 取真属性 / 静默前置闸 / web_disabled 限频 ──


def _proactive_sched(monkeypatch, events: list, decide_ret: dict, persona_seen: dict):
    import proactive.llm_proactive as lp
    import shisi.agent_plane.runtime as rt

    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})

    def _load_hint(cid: str = "") -> str:
        persona_seen["cid"] = cid
        return "P:" + str(cid)

    monkeypatch.setattr(lp, "load_persona_hint", _load_hint)
    monkeypatch.setattr(lp, "decide_proactive", lambda llm, ctx: decide_ret)
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: events.append(kw))
    return lp


def test_llm_proactive_quiet_pre_gate_skips_llm(sched, hub_tmp, monkeypatch):
    import proactive.llm_proactive as lp
    from proactive.ase_engine import _local_now

    called: list = []
    _proactive_sched(monkeypatch, [], {}, {})
    monkeypatch.setattr(lp, "decide_proactive", lambda *a, **k: called.append(1) or {})
    h = _local_now().hour
    sched._quiet_hours = (h, (h + 1) % 24)  # 当前小时在静默窗内
    sched._llm_proactive_one_user(hub_tmp, "4:peer@im.wechat")
    assert called == [], "P1-49：静默时段不得先烧 LLM 再在投递层丢弃"


def test_llm_proactive_web_disabled_event_once_per_day(sched, hub_tmp, monkeypatch):
    import proactive.llm_proactive as lp
    import shisi.agent_plane.runtime as rt

    events: list = []
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": False})
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: events.append(kw))
    sched._llm_proactive_one_user(hub_tmp, "4:peer@im.wechat")
    sched._llm_proactive_one_user(hub_tmp, "4:peer@im.wechat")
    assert len(events) == 1, "P1-51：关闭态账本事件每用户每日至多一条"
    assert events[0]["reason"] == "web_disabled"


def test_llm_proactive_persona_uses_knowledge_character_id(sched, hub_tmp, monkeypatch):
    decide = {"should_contact": False, "reason": "test", "wait_minutes": None, "message": ""}
    seen: dict = {}
    events: list = []
    _proactive_sched(monkeypatch, events, decide, seen)
    sched._resolve_proactive_llm = lambda eng=None: object()

    eng = hub_tmp.get("4:peer@im.wechat")
    eng._knowledge_character_id = "micai"
    sched._llm_proactive_one_user(hub_tmp, "4:peer@im.wechat")
    assert seen.get("cid") == "micai", "P1-48：真实属性是 _knowledge_character_id"


def test_llm_proactive_deliver_failure_backoff(sched, hub_tmp, monkeypatch):
    decide = {
        "should_contact": True, "reason": "想你了",
        "wait_minutes": None, "message": "在干嘛呀，想你了",
    }
    _proactive_sched(monkeypatch, [], decide, {})
    sched._resolve_proactive_llm = lambda eng=None: object()
    sched._deliver = lambda msg, session_key=None: False
    key = "4:peer@im.wechat"
    sched._llm_proactive_one_user(hub_tmp, key)
    assert key in sched._llm_proactive_next_ok, "P1-22：投递失败必须退避，不许 5 分钟后再烧 LLM"
    first = sched._llm_proactive_next_ok[key]
    sched._llm_proactive_one_user(hub_tmp, key)  # 退避窗内直接返回
    assert sched._llm_proactive_next_ok[key] == first
    assert sched._deliver_fail_counts[key] == 1
    # 成功送达 → 计数清零
    sched._deliver = lambda msg, session_key=None: True
    sched._llm_proactive_next_ok.pop(key)
    sched._llm_proactive_one_user(hub_tmp, key)
    assert key not in sched._deliver_fail_counts


# ── P1-53：scheduler_config 原子读改写 ────────────────────────────


def test_write_config_file_atomic_and_merged(tmp_path, monkeypatch):
    cfg_path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg_path)
    ProactiveScheduler.write_config_file(quiet_hours=(1, 2))
    ProactiveScheduler.write_config_file(vault_enabled=True, vault_interval=30)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["quiet_hours"] == {"start": 1, "end": 2}, "RMW 必须保留前一次写入"
    assert data["vault"]["enabled"] is True
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [], "临时文件必须被 rename 消耗掉"


def test_hub_save_state_fanout(hub_tmp):
    e1 = hub_tmp.get("1:a")
    e2 = hub_tmp.get("2:b")
    hub_tmp.save_state()
    assert e1.saved == 1 and e2.saved == 1
