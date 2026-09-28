"""W15 · D12-L 生理指标从假读数变真演算 —— 验收用例（红测先行）。

覆盖任务书四条验收：
  1. 情绪事件 → 落库 → `get_current` 变化 round-trip；
  2. 无状态时的「示意值，非真实生理信号」标注（API 与 `format_wechat_message` 同步）；
  3. 小说模式注入有 / 沉浸式无；
  4. 接线位置的行为断言（scheduler 同一拍、ASE 事件面、orchestrator 注入面）。

状态键唯一形状 = `isolation_key(会话键, 角色 id)`，与 ASEHub / 好感度 / 画像同构。
所有用例显式传 `db_path`（临时库经正典 `run_migrations` 建表），不写宿主 `data/`。
"""

import ast
import sys
import types
from pathlib import Path

sys.path.insert(0, ".")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shisi.api import vital_signs_routes
from shisi.api.registry import setup_shisi
from shisi.core.conversation_turn import isolation_key
from shisi.migrations import run_migrations
from shisi.vital_signs import vital_engine as ve_mod
from shisi.vital_signs.vital_engine import VitalSignsEngine

SESSION = "7:o123@im.wechat"
CID = "c-w15"
STATE_KEY = isolation_key(SESSION, CID)
LABEL = "示意值，非真实生理信号"


def _db(tmp_path):
    db = tmp_path / "w15.db"
    run_migrations(db)
    return db


def _row(db, key):
    import sqlite3

    with sqlite3.connect(str(db)) as conn:
        return conn.execute(
            "SELECT heart_rate, temperature, breath_rate, last_emotion FROM vital_signs_state WHERE character_id=?",
            (key,),
        ).fetchone()


def _stub_gf(monkeypatch, cid=CID):
    """把 `api.deps.deps.gf` 钉成"该会话绑定了 cid"的桩（角色解析唯一 owner 走它）。"""
    from api.deps import deps

    stub = types.SimpleNamespace(get_user_character=lambda k: cid)
    monkeypatch.setattr(deps, "gf", stub, raising=False)
    return stub


# ── 1. 事件驱动 + 落库 round-trip ─────────────────────────────

class TestPersistenceRoundTrip:
    def test_update_on_emotion_writes_vital_signs_state(self, tmp_path):
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        eng.update_on_emotion(STATE_KEY, "生气")

        row = _row(db, STATE_KEY)
        assert row is not None, "情绪事件必须落库（此前全仓零写入者 → 表恒空）"
        assert row[0] > 90, f"生气应使心率上抬，实得 {row[0]}"
        assert row[3] == "生气"

    def test_state_survives_new_instance(self, tmp_path):
        """落库不是写完即丢：另一实例（跨进程/重启）必须回放得到同一读数。"""
        db = _db(tmp_path)
        VitalSignsEngine(db_path=db).update_on_emotion(STATE_KEY, "生气")

        fresh = VitalSignsEngine(db_path=db)
        state = fresh.get_current(STATE_KEY)
        assert state.heart_rate > 90
        assert state.is_default is False
        assert state.last_emotion == "生气"

    def test_tick_persists(self, tmp_path):
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        eng.update_on_emotion(STATE_KEY, "生气")
        after = eng.tick(STATE_KEY).heart_rate

        row = _row(db, STATE_KEY)
        assert row is not None
        assert row[0] == pytest.approx(after), "tick 结果必须落库，否则重启/跨进程读回的是旧值"


# ── 2. 无状态标注（API 与微信文案同步）───────────────────────

class TestNoStateLabeling:
    def test_get_current_marks_default(self, tmp_path):
        eng = VitalSignsEngine(db_path=_db(tmp_path))
        state = eng.get_current(isolation_key("9:nobody", "x"))
        assert state.is_default is True
        assert state.heart_rate == pytest.approx(72.0)

    def test_wechat_message_carries_label_when_no_state(self, tmp_path):
        eng = VitalSignsEngine(db_path=_db(tmp_path))
        text = eng.format_wechat_message(isolation_key("9:nobody", "x"))
        assert LABEL in text, f"无状态时微信文案必须自证是示意值，实得：{text}"
        assert "心率" in text

    def test_label_disappears_once_state_exists(self, tmp_path):
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        assert LABEL in eng.format_wechat_message(STATE_KEY)
        eng.update_on_emotion(STATE_KEY, "开心")
        assert LABEL not in eng.format_wechat_message(STATE_KEY)

    def test_api_payload_syncs_label(self, tmp_path):
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        app = FastAPI()
        app.include_router(vital_signs_routes.router)
        vital_signs_routes.set_engine(eng)
        try:
            client = TestClient(app)
            body = client.get(f"/api/shisi/vital-signs/{STATE_KEY}").json()["data"]
            assert body["is_default"] is True
            assert LABEL in body["note"]
            assert LABEL in body["wechat_format"]

            eng.update_on_emotion(STATE_KEY, "生气")
            body = client.get(f"/api/shisi/vital-signs/{STATE_KEY}").json()["data"]
            assert body["is_default"] is False
            assert LABEL not in body["note"]
            assert body["heart_rate"] > 90
        finally:
            vital_signs_routes.set_engine(None)


# ── 3. ASE 事件面：情绪事件驱动生理读数 ───────────────────────

def _ase_engine(tmp_path):
    from proactive.ase_engine import ASEEngine

    return ASEEngine(state_path=str(Path(tmp_path) / "ase_state.json"))


class TestAseEventDriven:
    def test_on_chat_dict_emotion_lands_in_db(self, tmp_path, monkeypatch):
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        monkeypatch.setattr(ve_mod, "get_vital_engine", lambda db_path=None: eng)
        _stub_gf(monkeypatch)

        ase = _ase_engine(tmp_path)
        ase.set_user_key(SESSION)
        ase.on_chat("你今天怎么这么冲", "对不起", emotion_state={"primary": {"type": "生气", "intensity": 0.8}})

        row = _row(db, STATE_KEY)
        assert row is not None, "ASE 情绪事件必须写进 vital_signs_state（键=user×character）"
        assert row[0] > 90
        assert row[3] == "生气"

    def test_on_chat_object_emotion_lands_in_db(self, tmp_path, monkeypatch):
        """编排器传来的是 `EmotionState` 对象（`.primary_emotion.value`），不是 dict。

        只认 dict 的话这条链在生产里永不触发 —— 假接线。
        """
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        monkeypatch.setattr(ve_mod, "get_vital_engine", lambda db_path=None: eng)
        _stub_gf(monkeypatch)

        emotion = types.SimpleNamespace(primary_emotion=types.SimpleNamespace(value="开心"))
        ase = _ase_engine(tmp_path)
        ase.set_user_key(SESSION)
        ase.on_chat("好消息", "太好了", emotion_state=emotion)

        row = _row(db, STATE_KEY)
        assert row is not None
        assert row[3] == "开心"

    def test_normal_emotion_falls_back_to_calm_map(self, tmp_path, monkeypatch):
        """「平常」（my_character 的 NEUTRAL）不在 EMOTION_VITAL_MAP 里，
        靠引擎既有 ``get(emotion, get("平静"))`` 兜底取基准值——不能因此不落库。"""
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        monkeypatch.setattr(ve_mod, "get_vital_engine", lambda db_path=None: eng)
        _stub_gf(monkeypatch)

        ase = _ase_engine(tmp_path)
        ase.set_user_key(SESSION)
        ase.on_chat("在吗", "在", emotion_state={"primary": {"type": "平常"}})

        assert _row(db, STATE_KEY) is not None


# ── 4. scheduler 同一拍追挂 vital tick ────────────────────────

class _FakeEngine:
    def __init__(self):
        self.tick_calls = []

    def _hours_since_last_chat(self):
        return 3.0

    def tick(self, hours, dry_run=False):
        self.tick_calls.append((hours, dry_run))
        return None


class _FakeHub:
    def __init__(self, eng):
        self._eng = eng

    def get(self, key):
        return self._eng


def _run_scheduler_beat(tmp_path, monkeypatch, *, key=SESSION):
    """跑真实的 per-user 主动决策链（跑到 llm_unavailable/llm_false 自然收尾）。"""
    from proactive.scheduler import ProactiveScheduler

    db = _db(tmp_path)
    vital = VitalSignsEngine(db_path=db)
    vital.update_on_emotion(isolation_key(key, CID), "生气")  # tick 只在有状态时演算
    monkeypatch.setattr(ve_mod, "get_vital_engine", lambda db_path=None: vital)
    _stub_gf(monkeypatch)

    from proactive import llm_proactive as lp

    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "decide_proactive", lambda llm, ctx: {"should_contact": False, "reason": "w15-stub"})

    fake = _FakeEngine()
    sched = ProactiveScheduler(ase_engine=_FakeHub(fake))
    monkeypatch.setattr(sched, "_is_quiet_hours", lambda: False)
    sched._llm_proactive_one_user(_FakeHub(fake), key)
    return db, vital, fake


class TestSchedulerBeat:
    def test_vital_tick_on_existing_beat(self, tmp_path, monkeypatch):
        db, vital, fake = _run_scheduler_beat(tmp_path, monkeypatch)

        assert fake.tick_calls, "引擎既有 tick 必须照跑（不能换成第二条调度）"
        reloaded = VitalSignsEngine(db_path=db).get_current(STATE_KEY)
        assert reloaded.is_default is False
        assert reloaded.heart_rate == pytest.approx(vital.get_current(STATE_KEY).heart_rate), (
            "同一拍的 vital tick 必须落库，否则读侧永远停在事件时刻的旧值"
        )

    def test_scheduler_key_equals_ase_key(self, tmp_path, monkeypatch):
        """两条链必须写同一个键，否则 tick 与事件各说各话。"""
        from proactive.scheduler import ProactiveScheduler

        _stub_gf(monkeypatch)
        eng = VitalSignsEngine(db_path=_db(tmp_path))
        monkeypatch.setattr(ve_mod, "get_vital_engine", lambda db_path=None: eng)

        sched = ProactiveScheduler(ase_engine=_FakeHub(_FakeEngine()))
        sched._vital_tick_for_user(SESSION)

        ase = _ase_engine(tmp_path)
        ase.set_user_key(SESSION)
        ase.on_chat("hi", "hi", emotion_state={"primary": {"type": "伤心"}})

        assert _row(eng._db_path, STATE_KEY) is not None, f"两侧键须一致，实得键集 {list(eng._states)}"
        assert eng.get_current(STATE_KEY).last_emotion == "伤心"


# ── 5. 小说模式消费 ───────────────────────────────────────────

class TestNovelModeInjection:
    def _engine_with_state(self, tmp_path):
        db = _db(tmp_path)
        eng = VitalSignsEngine(db_path=db)
        eng.update_on_emotion(STATE_KEY, "生气")
        return eng

    def test_novel_mode_injects_reading(self, tmp_path):
        from shisi.vital_signs.vital_prompt import novel_vital_prompt_section

        eng = self._engine_with_state(tmp_path)
        block = novel_vital_prompt_section(SESSION, CID, mode="novel", engine=eng)
        assert "心率" in block, "小说式必须读到当前生理读数"
        assert "非真实医疗信号" in block, "注入段必须自带非医疗标注（LLM 透明边界）"

    def test_immersive_mode_does_not_inject(self, tmp_path):
        from shisi.vital_signs.vital_prompt import novel_vital_prompt_section

        eng = self._engine_with_state(tmp_path)
        assert novel_vital_prompt_section(SESSION, CID, mode="immersive", engine=eng) == ""

    def test_no_state_does_not_inject(self, tmp_path):
        """无演算结果时宁缺毋串：不把示意值当事实喂给生成链。"""
        from shisi.vital_signs.vital_prompt import novel_vital_prompt_section

        eng = VitalSignsEngine(db_path=_db(tmp_path))
        assert novel_vital_prompt_section(SESSION, CID, mode="novel", engine=eng) == ""

    def test_mode_default_reads_reply_mode(self, tmp_path, monkeypatch):
        from utils import reply_mode

        monkeypatch.setattr(reply_mode, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
        reply_mode.write_reply_mode("novel")

        from shisi.vital_signs.vital_prompt import novel_vital_prompt_section

        eng = self._engine_with_state(tmp_path)
        assert "心率" in novel_vital_prompt_section(SESSION, CID, engine=eng)
        reply_mode.write_reply_mode("immersive")
        assert novel_vital_prompt_section(SESSION, CID, engine=eng) == ""


# ── 6. 接线守卫（断开即红的结构面）────────────────────────────

def _calls_named(tree, suffix):
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "attr", None) or getattr(f, "id", None) or ""
        if isinstance(f, ast.Attribute):
            parts = []
            cur = f
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            name = ".".join(reversed(parts))
        if name.endswith(suffix):
            out.append((node, name))
    return out


class TestWiringGuards:
    def test_orchestrator_injects_novel_vital_block(self):
        src = Path("orchestrator/optimized_orchestrator.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        call_nodes = {id(n) for n, _ in _calls_named(tree, "novel_vital_prompt_section")}
        assert call_nodes, "orchestrator 必须调用 novel_vital_prompt_section，否则小说模式消费是孤岛"

        def targets(node):
            return getattr(node, "targets", None) or [node.target]

        # 调用了但不拼进 system = 同样假接线，故先收集"接住返回值"的变量名
        held = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)) and any(
                id(v) in call_nodes for v in ast.walk(node.value)
            ):
                for t in targets(node):
                    if isinstance(t, ast.Name):
                        held.add(t.id)

        wired = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AugAssign)):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "system_prompt" for t in targets(node)):
                continue
            names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            if names & held or any(id(v) in call_nodes for v in ast.walk(node.value)):
                wired.append(node.lineno)
        assert wired, "生理读数段必须拼进 system_prompt"

    def test_orchestrator_passes_emotion_state_to_ase(self):
        src = Path("orchestrator/optimized_orchestrator.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        hits = [
            n
            for n, _ in _calls_named(tree, "on_chat")
            if any(kw.arg == "emotion_state" for kw in n.keywords)
        ]
        assert hits, "ASE 事件面必须收到 emotion_state，否则 update_on_emotion 永不被触发"

    def test_registry_uses_shared_vital_engine(self, tmp_path):
        """路由读到的必须是 ASE/scheduler 写的那个实例（各造各的 = 表写了没人读）。"""
        db = _db(tmp_path)
        app = FastAPI()
        reg = setup_shisi(app, run_migrate=False, db_path=db)
        assert reg.vital_engine is ve_mod.get_vital_engine(db_path=db)

        reg.vital_engine.update_on_emotion(STATE_KEY, "生气")
        assert ve_mod.get_vital_engine(db_path=db).get_current(STATE_KEY).heart_rate > 90

    def test_vital_engine_default_db_is_shared_owner(self):
        """默认库须复用 shisi 状态库真源（conftest 已把该真源沙箱化，测试不写宿主）。"""
        from shisi.affinity.enhancer import default_db_path

        eng = VitalSignsEngine()
        assert Path(eng._db_path).resolve() == Path(default_db_path()).resolve()


# ── 7. 既有契约不得回归（刻度、文案要素）─────────────────────

class TestExistingContract:
    def test_defaults_unchanged(self, tmp_path):
        eng = VitalSignsEngine(db_path=_db(tmp_path))
        state = eng.get_current("bare-legacy-key")
        assert (state.heart_rate, state.temperature, state.breath_rate) == (72.0, 36.5, 16.0)

    def test_angry_still_clamps_and_smooths(self, tmp_path):
        eng = VitalSignsEngine(db_path=_db(tmp_path))
        first = eng.update_on_emotion("k", "生气")
        assert first.heart_rate > 90
        again = eng.update_on_emotion("k", "生气")
        assert again.heart_rate <= 120

