"""P1 隔离回归：ASE 分用户 / affinity user×character / 情景层 meta。"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

from proactive.ase_hub import ASEHub
from shisi.affinity.enhancer import AffinityEnhancer
from shisi.affinity.enhancer import affinity_key as ak
from shisi.affinity.mapper import AffinityMapper


class _FakeASE:
    def __init__(self, user_key: str = "", state_path: str = "", **kw):
        self.user_key = user_key
        self.state_path = state_path
        self.chats: list[tuple[str, str]] = []
        self.daily = 0
        self.paused = False
        self.urgency = type("U", (), {"total": 0.0})()
        self._daily_message_count = 0
        self._last_skip_reason = ""
        self.committed: list[dict] = []

    def on_chat(self, user_message, reply, **kw):
        self.chats.append((user_message, reply))
        self._daily_message_count = 0  # reply resets like real engine

    def tick(self, hours, dry_run=False, **kw):
        if dry_run:
            self._last_skip_reason = "dry_run"
            return None
        self._daily_message_count += 1
        return {"message": f"hello-{self.user_key}", "type": "miss_you"}

    def commit_sent(self, result):
        self.committed.append(result)
        self._last_skip_reason = "ok"

    def _hours_since_last_chat(self):
        return 99.0


def test_ase_hub_isolates_engines_and_state(tmp_path):
    made: list[_FakeASE] = []

    def factory(user_key="", state_path="", **kw):
        e = _FakeASE(user_key=user_key, state_path=state_path)
        made.append(e)
        return e

    hub = ASEHub(factory)
    hub._state_dir = Path(tmp_path) / "ase"
    a = hub.get("1:alice@im.wechat")
    b = hub.get("4:bob@im.wechat")
    assert a is not b
    assert a.user_key == "1:alice@im.wechat"
    assert b.state_path != a.state_path
    hub.on_chat("1:alice@im.wechat", "hi", "hello")
    assert a.chats == [("hi", "hello")]
    assert b.chats == []
    ra = hub.tick("1:alice@im.wechat", 5)
    rb = hub.tick("4:bob@im.wechat", 5)
    assert "1:alice" in ra["message"]
    assert "4:bob" in rb["message"]
    assert a.committed == [] and b.committed == []
    hub.commit_sent("1:alice@im.wechat", ra)
    assert len(a.committed) == 1 and b.committed == []


def test_ase_hub_factory_receives_user_key_and_state_path():
    seen = {}

    def factory(user_key="", state_path="", **kw):
        seen["user_key"] = user_key
        seen["state_path"] = state_path
        return _FakeASE(user_key=user_key, state_path=state_path)

    hub = ASEHub(factory)
    hub.get("9:x@im.wechat")
    assert seen["user_key"] == "9:x@im.wechat"
    assert seen["state_path"].endswith(".json")


def test_affinity_key_and_user_isolation():
    assert ak("char_a", "u1") == "u1::char_a"
    assert ak("char_a", "") == "char_a"
    tmp = Path(tempfile.mkdtemp()) / "aff.db"
    enh = AffinityEnhancer(db_path=tmp)
    v1, _ = enh.update("米彩", 50, user_id="1:alice@im.wechat")
    v2, _ = enh.update("米彩", 30, user_id="4:bob@im.wechat")
    assert v1 == 50
    assert v2 == 30
    assert enh.get_value("米彩", user_id="1:alice@im.wechat") == 50
    assert enh.get_value("米彩", user_id="4:bob@im.wechat") == 30
    # 有 user_id 时不回退全局键
    assert enh.get_value("米彩", user_id="9:ghost@im.wechat") == 0.0


def test_affinity_mapper_sync_per_user():
    tmp = Path(tempfile.mkdtemp()) / "aff2.db"
    enh = AffinityEnhancer(db_path=tmp)
    mapper = AffinityMapper(enhancer=enh)
    enh.update("char", 10.0, user_id="uA")
    enh.update("char", 2.0, user_id="uB")
    # 目标 shisi=50，last_uA=10，delta 上限 3 → uA=13；uB 不得被改动
    mapper.sync("char", affection_points=250, user_id="uA")
    assert abs(enh.get_value("char", user_id="uA") - 13.0) < 0.01
    assert abs(enh.get_value("char", user_id="uB") - 2.0) < 0.01
    assert enh.get_value("char", user_id="uA") != enh.get_value("char", user_id="uB")


def test_episodic_search_filters_empty_meta_session():
    from shisi.memory.legacy._legacy_episodic_memory import EpisodicMemory

    class VM:
        def search_sync(self, query, top_k=5, filter_dict=None):
            return [
                {"content": "A 的回忆", "metadata": {"session_id": "1:a@im.wechat", "type": "episode"}},
                {"content": "无归属", "metadata": {"type": "episode"}},
                {"content": "B 的回忆", "metadata": {"session_id": "2:b@im.wechat", "type": "episode"}},
            ]

        def store_text_sync(self, *a, **k):
            return None

        _collections = {}

    class SM:
        def add_episode(self, *a, **k):
            raise AssertionError("sm.add_episode 不应被调用（无此方法的库）")

    em = EpisodicMemory(VM(), SM())
    got = em.search("x", top_k=5, session_id="1:a@im.wechat")
    assert len(got) == 1
    assert got[0]["content"] == "A 的回忆"
    # 无 meta 不注入
    assert all("无归属" not in str(g) for g in got)


def test_scheduler_source_targets_session_key():
    import proactive.scheduler as sched_mod

    src = inspect.getsource(sched_mod.ProactiveScheduler._send_targeted)
    assert "session_key" in src
    # P1：定向投递在 LLM 主动路径；闸门类源码不再承担发送
    src2 = inspect.getsource(sched_mod.ProactiveScheduler._llm_proactive_one_user)
    assert "session_key=user_key" in src2 or "session_key=" in src2


def test_orchestrator_ase_call_uses_session():
    import orchestrator.optimized_orchestrator as orch_mod

    src = inspect.getsource(orch_mod)
    assert "ASEHub" in src
    assert "user_id=session_id" in src or "user_id=session_id or" in src
