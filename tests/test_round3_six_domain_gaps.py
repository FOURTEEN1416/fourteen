"""六域第三轮排查回归（2026-09-22）。

根因清单（本轮实锤）：
1. ASEHub 工厂不给引擎注入 `_knowledge_character_id` / `_knowledge_share_func`
   —— hub 级 setattr 不会传到引擎实例；persona 三源仍全空。
2. `load_persona_hint("default")` 查 `config/characters/default.json` 必 miss
   —— 内置十四主动决策人设恒空。
3. `get_user_character` 实例未建即返 `"default"`，不查绑定表
   —— 主动/提醒/祝福在「已绑定但未聊过」时错绑内置角色。
4. 每日情绪衰减只打 UserManager 引擎，不打 orchestrator 请求级缓存
   —— web 路径情绪永不冷却（审计 item45 半修）。
"""

from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

import pytest


def test_persist_mirror_replay_roundtrip_single_scale(tmp_path, monkeypatch):
    """写侧镜像 → enhancer 重启回放：**刻度只允许过一道换算**。

    df59752 起写侧已写 shisi；读侧若仍按旧 reason 无差别换算，新行会被
    二次 ÷5（250 points → 镜像 50 → 回放 10）——刻度混用的新变体。
    旧 points 行的换算由 test_block_e 钉住，此处钉 round-trip 恒等。
    """
    from shisi.affinity import enhancer as enh_mod
    from shisi.affinity import scale as affinity_scale
    from user_scheduler import UserManager
    from utils import affinity_state

    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE affinity_records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "character_id TEXT, old_value REAL, new_value REAL, delta REAL, "
            "reason TEXT, source TEXT, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.commit()
    monkeypatch.setattr(affinity_state, "_PATH", tmp_path / "affinity_state.json")
    monkeypatch.setattr(enh_mod, "_DB_DEFAULT", db)

    class _State:
        affection_points = 250.0

    class _Engine:
        state = _State()

    UserManager._persist_affinity("r3@im.wechat", "micai", _Engine())

    with sqlite3.connect(db) as conn:
        reason, new_value = conn.execute(
            "SELECT reason, new_value FROM affinity_records"
        ).fetchone()
    assert reason == enh_mod.MIRROR_REASON_SHISI, (
        "写侧必须用新刻度标记；判据常量唯一真源在 enhancer.MIRROR_REASON_*"
    )

    enh = enh_mod.AffinityEnhancer(db_path=db)
    expected = affinity_scale.points_to_shisi(250.0)
    assert enh.get_value("micai", "r3@im.wechat") == pytest.approx(expected), (
        f"round-trip 应恒等于 {expected}（一次换算），二次换算会得到 {expected / 5}"
    )
    assert new_value == pytest.approx(expected)


def test_load_persona_hint_default_reads_persona_yaml():
    from proactive.llm_proactive import load_persona_hint

    hint = load_persona_hint("default")
    assert hint, "内置 default 必须非空（persona.yaml 同源）"
    assert "十四" in hint or "角色：" in hint


def test_load_persona_hint_empty_cid_returns_empty():
    from proactive.llm_proactive import load_persona_hint

    assert load_persona_hint("") == ""
    assert load_persona_hint(None) == ""


def test_get_user_character_falls_back_to_bindings():
    from user_scheduler import UserManager

    mgr = UserManager.__new__(UserManager)
    mgr._users = {}
    mgr._bindings = {
        "4:wxid_x@im.wechat": {"character_card_id": "micai"},
        "micai": {"character_card_id": "micai"},
        "4": {"character_card_id": "owner_default_card"},
    }
    got = mgr.get_user_character("4:wxid_x@im.wechat")
    assert got != "default", "实例未建时不得直接回落 default"
    assert got in ("micai", "owner_default_card")


def test_get_user_character_prefers_live_instance():
    from user_scheduler import UserInstance, UserManager

    mgr = UserManager.__new__(UserManager)
    inst = UserInstance(
        user_id="1:a",
        nickname="",
        character_card_id="live_card",
        session_id="1:a",
        emotion_engine=None,
        emotion_engines={},
    )
    mgr._users = {"1:a": inst}
    mgr._bindings = {"1:a": {"character_card_id": "binding_card"}}
    assert mgr.get_user_character("1:a") == "live_card"


def _bare_scheduler():
    from proactive.scheduler import ProactiveScheduler

    s = ProactiveScheduler()
    s._llm_proactive_next_ok.clear()
    s._deliver_fail_counts.clear()
    s._important_dates_sent.clear()
    s._disabled_event_day.clear()
    return s


def test_llm_proactive_persona_falls_back_to_character_resolver(monkeypatch):
    """引擎无 `_knowledge_character_id` 时，决策层必须真实走 resolver→load_persona_hint。

    行为断言（不再读源码文本）：跑一次 `_llm_proactive_one_user`，
    钉 load_persona_hint 收到的是 resolver 返回的卡 id，且消息按决策送达。
    """
    import types

    import proactive.llm_proactive as lp_mod
    import proactive.scheduler as sched_mod
    import shisi.agent_plane.runtime as apr
    import utils.character_resolver as resolver_mod

    s = _bare_scheduler()
    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(s, "_is_quiet_hours", lambda: False)
    delivered: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        s, "_deliver",
        lambda msg, session_key=None, character_id=None: delivered.append((msg, session_key)) or True,
    )
    monkeypatch.setattr(s, "_resolve_proactive_llm", lambda eng=None: object())

    hints: list[str] = []
    monkeypatch.setattr(
        lp_mod, "load_persona_hint",
        lambda cid="": (hints.append(cid), "persona文本")[1],
    )
    monkeypatch.setattr(
        lp_mod, "decide_proactive",
        lambda llm, ctx: {"should_contact": True, "message": "在忙什么呢", "reason": "test", "wait_minutes": None},
    )
    monkeypatch.setattr(sched_mod, "sanitize_message", lambda t: t)
    monkeypatch.setattr(resolver_mod, "resolve_character_id", lambda key, user_manager=None: "bound_card")
    monkeypatch.setattr(apr, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(apr, "get_profile_prompt_block", lambda uk: "")
    events: list[dict] = []
    monkeypatch.setattr(apr, "append_proactive_event", lambda **kw: events.append(kw))

    eng = types.SimpleNamespace(
        _hours_since_last_chat=lambda: 3.0,
        tick=lambda h, dry_run=False: False,
    )
    hub = types.SimpleNamespace(get=lambda uk: eng)
    s._llm_proactive_one_user(hub, "4:wxid_x@im.wechat")

    assert hints == ["bound_card"], (
        f"persona 必须以 resolver 回落解析的卡为源，实得 {hints}"
    )
    assert delivered and delivered[0][0] == "在忙什么呢"
    assert delivered[0][1] == "4:wxid_x@im.wechat"
    assert any(ev.get("sent") for ev in events), "送达必须记账本事件"


def test_init_mixin_injects_knowledge_per_engine(tmp_path, monkeypatch):
    """工厂真实创建引擎后，知识三源必须挂在**引擎实例**上（hub 级 setattr 读不到）。"""
    from types import SimpleNamespace

    from orchestrator._init_mixin import _InitPhasesMixin as Mixin
    from proactive import ase_hub
    hub_dir = tmp_path / "ase_states"
    monkeypatch.setattr(ase_hub, "_STATE_DIR", hub_dir)
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", hub_dir / "index.json")

    self_ = SimpleNamespace(components={"llm": None})
    cfg = SimpleNamespace(proactive=SimpleNamespace(
        max_daily_messages=8,
        min_interval_minutes=30,
        cooldown_after_reply_minutes=5,
        urgency_threshold=5.0,
    ))
    Mixin._init_ase_and_scheduler(self_, cfg, {})

    hub = self_.components["ase"]
    from proactive.ase_hub import ASEHub

    assert isinstance(hub, ASEHub)
    eng = hub.get("4:wxid_x@im.wechat")
    # 注入成功的判据：引擎自身持有可调用 share + 非空角色 id
    assert callable(getattr(eng, "_knowledge_share_func", None)), "知识分享函数必须挂在引擎实例上"
    assert str(getattr(eng, "_knowledge_character_id", "")), (
        "工厂必须逐引擎写入角色 id（未绑定用户解析为内置 default 也算注入成功）；"
        "hub 级 setattr 假接线会让引擎属性根本不存在"
    )


def test_engine_factory_sets_knowledge_character_id(tmp_path, monkeypatch):
    """resolver 解析出真实卡时，引擎角色 id 必须等于该卡（不是 'dynamic' 兜底）。"""
    from types import SimpleNamespace

    import utils.character_resolver as resolver_mod
    from orchestrator._init_mixin import _InitPhasesMixin as Mixin
    from proactive import ase_hub

    hub_dir = tmp_path / "ase_states"
    monkeypatch.setattr(ase_hub, "_STATE_DIR", hub_dir)
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", hub_dir / "index.json")
    monkeypatch.setattr(resolver_mod, "resolve_character_id", lambda key, user_manager=None: "micai")

    self_ = SimpleNamespace(components={"llm": None})
    cfg = SimpleNamespace(proactive=SimpleNamespace(
        max_daily_messages=8,
        min_interval_minutes=30,
        cooldown_after_reply_minutes=5,
        urgency_threshold=5.0,
    ))
    Mixin._init_ase_and_scheduler(self_, cfg, {})

    eng = self_.components["ase"].get("9:wxid_y@im.wechat")
    assert eng._knowledge_character_id == "micai"


def test_apply_request_emotion_decay_exists_and_iterates():
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    assert hasattr(OptimizedOrchestrator, "apply_request_emotion_decay")
    inst = SimpleNamespace(
        _request_emotion_engines={
            "a": SimpleNamespace(apply_time_decay=lambda h: None),
            "b": SimpleNamespace(apply_time_decay=lambda h: None),
        },
        _request_emotion_engines_lock=threading.Lock(),
    )
    n = OptimizedOrchestrator.apply_request_emotion_decay(inst, 1.0)
    assert n == 2
    assert OptimizedOrchestrator.apply_request_emotion_decay(inst, 0) == 0


def test_daily_maintenance_applies_request_emotion_decay(monkeypatch):
    """每日维护必须**真实调用**请求级衰减与 UserManager 全量衰减，并推进基准。

    行为断言：钉 24h 前的衰减基准跑一次维护，断言两侧衰减各被调一次、
    收到的 hours≈24、基准被推进（旧文本断言只防删行，不防接线被改坏）。
    """
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    import api.deps as deps_mod
    import proactive.scheduler as sched_mod

    calls: dict[str, float] = {}
    user_mgr = SimpleNamespace(
        apply_time_decay_all=lambda h: (calls.__setitem__("mgr", h), 3)[1]
    )
    orch = SimpleNamespace(
        apply_request_emotion_decay=lambda h: (calls.__setitem__("orch", h), 2)[1]
    )
    monkeypatch.setattr(deps_mod, "deps", SimpleNamespace(gf=user_mgr, orch=orch, shisi_reg=None))
    monkeypatch.setattr(sched_mod, "run_achievement_maintenance", lambda: 0)

    s = _bare_scheduler()
    monkeypatch.setattr(s, "_check_important_dates", lambda: None)
    s._last_emotion_decay = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    base_before = s._last_emotion_decay

    s._run_daily_maintenance()

    assert "mgr" in calls and "orch" in calls, (
        f"两侧衰减必须都被调用，实得 {list(calls)}"
    )
    assert 23.5 <= calls["orch"] <= 24.5, f"衰减时长应以独立基准计算，实得 {calls['orch']}"
    assert s._last_emotion_decay > base_before, "成功后基准必须推进"


def test_emotion_stage_route_evaluate_is_pure_query(tmp_path, monkeypatch):
    """/evaluate 端点不得以裸角色键写 emotion_stage_state（污染 track 键回放）。

    本端点无用户维度；旧实现走 `engine.evaluate(character_id,…)` → UPSERT 裸键行，
    与 mapper 的 `user::character` 键同表混存且被构造回放。收口为纯映射查询。
    """
    import sqlite3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from shisi.api import emotion_stage_routes
    from shisi.emotion_stage.stage_engine import EmotionStageEngine
    from shisi.migrations import run_migrations

    db = tmp_path / "stage.db"
    run_migrations(db)
    engine = EmotionStageEngine(db_path=db)
    monkeypatch.setattr(emotion_stage_routes, "_engine", engine)

    app = FastAPI()
    app.include_router(emotion_stage_routes.router)
    client = TestClient(app)
    resp = client.post("/api/shisi/emotion-stage/test_char/evaluate?affinity=60")
    assert resp.status_code == 200
    assert resp.json()["data"]["stage"] == "亲密"

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT character_id FROM emotion_stage_state"
        ).fetchall()
    assert rows == [], f"evaluate 端点不得落阶段表，实得 {rows}"
    assert engine.get_progress("test_char")["stage_index"] == 0


def test_send_date_wish_uses_bound_persona(monkeypatch):
    """祝福必须以会话绑定角色的口吻生成（default/缺失回落链真实生效）。

    行为断言：deps.gf 返回 "default" 而 hub 键带 `|char` 自选后缀时，
    load_persona_hint 必须收到后缀角色；LLM system_prompt 收到人设文本。
    """
    from types import SimpleNamespace

    import api.deps as deps_mod
    import proactive.llm_proactive as lp_mod
    import proactive.scheduler as sched_mod

    s = _bare_scheduler()
    monkeypatch.setattr(deps_mod, "deps", SimpleNamespace(
        gf=SimpleNamespace(get_user_character=lambda uk: "default")
    ))
    hints: list[str] = []
    monkeypatch.setattr(
        lp_mod, "load_persona_hint",
        lambda cid="": (hints.append(str(cid)), "昭阳人设")[1],
    )
    seen: dict[str, str] = {}

    class _LLM:
        def chat_sync(self, query="", system_prompt="", **kw):
            seen["system"] = system_prompt
            return "生日快乐呀，今天属于你。"

    monkeypatch.setattr(s, "_resolve_proactive_llm", lambda: _LLM())
    monkeypatch.setattr(sched_mod, "sanitize_message", lambda t: t)
    delivered: list[str] = []
    monkeypatch.setattr(
        s, "_deliver",
        lambda msg, session_key=None, character_id=None: delivered.append(msg) or True,
    )

    s._send_date_wish(
        "4:wxid_x@im.wechat|昭阳", "2026-09-22", label="birthday", kind="birthday", names="默默",
    )

    assert hints == ["昭阳"], f"hub 键自选角色后缀必须优先于 default，实得 {hints}"
    assert seen.get("system") == "昭阳人设", "人设必须进 LLM system_prompt"
    assert delivered == ["生日快乐呀，今天属于你。"]
    assert any("birthday" in k for k in s._important_dates_sent), "送达后当日幂等必须记录"


def test_ase_hub_health_check_empty_engines_degrades():
    """部署验收 500 复现：`GET /api/proactive/state` → `hub.health_check()`，
    旧 hub 无显式方法、`__getattr__` 无引擎即抛 AttributeError（生产 traceback
    实锤 `ase_hub.py:280`）→ 端点 500。空引擎必须降级为可消费的零态。
    """
    from proactive.ase_hub import ASEHub

    hub = ASEHub(engine_factory=lambda **kw: None)
    assert hub.health_check() == {"engine_count": 0}


def test_ase_hub_health_check_delegates_latest_engine():
    """有引擎时委托最近使用引擎的 health_check，并附引擎计数（多用户真源）。"""
    from proactive.ase_hub import ASEHub

    class _Eng:
        def health_check(self):
            return {"initialized": True, "max_daily": 8}

    hub = ASEHub(engine_factory=lambda **kw: None)
    hub._engines["u1"] = _Eng()
    state = hub.health_check()
    assert state["max_daily"] == 8 and state["engine_count"] == 1
