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

import inspect
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


def test_llm_proactive_persona_falls_back_to_character_resolver():
    from proactive.scheduler import ProactiveScheduler

    src = inspect.getsource(ProactiveScheduler._llm_proactive_one_user)
    assert "character_resolver.resolve_character_id" in src
    assert "BUILTIN_CHARACTER_ID" in src
    # 禁止再读不存在的属性（注释里的历史说明允许出现字面串）
    assert 'getattr(eng, "_character_id"' not in src
    assert "getattr(eng, \"_character_id\"" not in src


def test_init_mixin_injects_knowledge_per_engine():
    from orchestrator import _init_mixin as mod

    src = inspect.getsource(mod)
    assert "_inject_ase_knowledge" in src
    assert "_knowledge_share_func = _share" in src or "eng._knowledge_share_func" in src
    hub_branch = src.split("if isinstance(ase_inst, ASEHub):", 1)[-1]
    if "elif ase_inst" in hub_branch:
        hub_branch = hub_branch.split("elif ase_inst", 1)[0]
    assert "ase_inst._knowledge_share_func" not in hub_branch


def test_engine_factory_sets_knowledge_character_id():
    from orchestrator import _init_mixin as mod

    src = inspect.getsource(mod)
    assert '_knowledge_character_id = str(resolved or "dynamic")' in src


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


def test_daily_maintenance_calls_request_emotion_decay():
    from proactive.scheduler import ProactiveScheduler

    src = inspect.getsource(ProactiveScheduler._run_daily_maintenance)
    assert "apply_request_emotion_decay" in src
    assert "apply_time_decay_all" in src


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

    from proactive.scheduler import ProactiveScheduler

    src = inspect.getsource(ProactiveScheduler._send_date_wish)
    assert '"default"' in src
    assert "load_persona_hint" in src
