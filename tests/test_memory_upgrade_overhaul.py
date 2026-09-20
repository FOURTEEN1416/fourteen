"""全面升级批次回归：user_facts 隔离 / B3 配置接线 / B4 回忆强化 / B5 回收站 / B6 刻度真源。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ═══════════════════════ B6 亲密度刻度唯一真源 ═══════════════════════

class TestAffinityScale:
    def test_points_bounds_and_levels(self):
        from shisi.affinity import scale

        assert scale.POINTS_MAX == 500.0
        assert scale.points_to_level(0) == 0
        assert scale.points_to_level(10) == 1
        assert scale.points_to_level(50) == 3
        assert scale.points_to_level(500) == 8
        assert scale.points_to_level(9999) == 8  # clamp

    def test_points_shisi_roundtrip(self):
        from shisi.affinity import scale

        assert scale.points_to_shisi(0) == 0.0
        assert scale.points_to_shisi(250) == 50.0
        assert scale.points_to_shisi(500) == 100.0
        assert scale.shisi_to_points(50.0) == 250.0
        assert scale.shisi_to_points(scale.points_to_shisi(123)) == pytest.approx(123)

    def test_unlock_thresholds_map_to_points(self):
        from shisi.affinity import scale

        # 25/50/75/90 on shisi 0-100 → 125/250/375/450 on points
        assert scale.unlock_threshold_to_points(25) == 125.0
        assert scale.unlock_threshold_to_points(50) == 250.0
        assert scale.unlock_threshold_to_points(75) == 375.0
        assert scale.unlock_threshold_to_points(90) == 450.0

    def test_mapper_uses_scale(self):
        from shisi.affinity import scale
        from shisi.affinity.mapper import AffinityMapper

        class _Enh:
            _min = 0.0
            _max = 100.0

        m = AffinityMapper(enhancer=_Enh())
        assert m.to_shisi(250) == scale.points_to_shisi(250)
        assert m.to_emotion(50.0) == scale.shisi_to_points(50.0)
        assert m.points_to_level(500) == 8
        assert m.emotion_max() == scale.POINTS_MAX

    def test_normalize_from_dict_prefers_points(self):
        from shisi.affinity import scale

        out = scale.normalize_from_dict({"affection_points": 50, "affinity_level": 99})
        assert out["affection_points"] == 50.0
        assert out["affinity_level"] == 3  # points win
        assert out["shisi_affinity"] == 10.0


class TestAffinityPersistence:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        import utils.affinity_state as ast

        monkeypatch.setattr(ast, "_PATH", tmp_path / "aff.json")
        ast.save_points("wxid_a", "char1", 250)
        assert ast.load_points("wxid_a", "char1") == 250.0
        assert ast.load_points("wxid_b", "char1") == 0.0  # 隔离
        assert ast.load_points("wxid_a", "char2") == 0.0
        ast.clear("wxid_a", "char1")
        assert ast.load_points("wxid_a", "char1") == 0.0

    def test_scheduler_restore_uses_scale(self, tmp_path, monkeypatch):
        import utils.affinity_state as ast
        from my_character.emotion_engine import EmotionEngine
        from user_scheduler import UserManager

        monkeypatch.setattr(ast, "_PATH", tmp_path / "aff2.json")
        ast.save_points("u1", "c1", 500)
        engine = EmotionEngine()
        UserManager._restore_affinity("u1", "c1", engine)
        state = getattr(engine, "state", None) or engine._state
        assert state.affection_points == 500.0
        assert state.affinity == 8


# ═══════════════════════ user_facts 隔离 + 回收站 + access_count ═══════════════════════

class TestUserFactsIsolation:
    @pytest.fixture
    def sm(self, tmp_path):
        from shisi.memory.legacy.structured_memory import StructuredMemory

        db = StructuredMemory(str(tmp_path / "facts.db"))
        yield db
        db.close()

    def test_user_key_from_session(self, sm):
        assert sm.user_key_from_session("N:wxid_abc") == "wxid_abc"
        assert sm.user_key_from_session("wxid_abc") == "wxid_abc"
        assert sm.user_key_from_session("") == ""

    def test_facts_isolated_by_user_key(self, sm):
        sm.add_fact("A喜欢猫", "preference", 0.9, user_key="wxid_a")
        sm.add_fact("B喜欢狗", "preference", 0.9, user_key="wxid_b")
        sm.add_fact("历史孤儿", "general", 0.5, user_key="")

        a = sm.get_facts(user_key="wxid_a")
        b = sm.get_facts(user_key="wxid_b")
        assert [f["fact"] for f in a] == ["A喜欢猫"]
        assert [f["fact"] for f in b] == ["B喜欢狗"]
        # 完整隔离：legacy 空 user_key 不注入任何会话
        assert all(f["fact"] != "历史孤儿" for f in a)

    def test_search_isolated(self, sm):
        sm.add_fact("A喜欢猫", user_key="wxid_a")
        sm.add_fact("B也喜欢猫", user_key="wxid_b")
        hits = sm.search_facts("喜欢猫", user_key="wxid_a")
        assert all(f.get("user_key") == "wxid_a" for f in hits)
        assert not any(f["fact"] == "B也喜欢猫" for f in hits)

    def test_access_count_increment(self, sm):
        fid = sm.add_fact("喜欢蓝色", user_key="wxid_a")
        sm.increment_fact_access([fid])
        sm.increment_fact_access([fid])
        row = sm.get_facts(user_key="wxid_a")[0]
        assert row["access_count"] == 2

    def test_delete_to_recycle_and_restore(self, sm):
        fid = sm.add_fact("会删除的事实", user_key="wxid_a", confidence=0.9)
        assert sm.delete_fact(fid, recycle=True, user_key="wxid_a") is True
        assert sm.get_facts(user_key="wxid_a") == []
        with sm._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_recycle_bin WHERE memory_id = ?", (str(fid),)
            ).fetchall()
        assert len(rows) == 1
        assert dict(rows[0])["character_id"] == "user_fact:wxid_a"
        new_id = sm.restore_fact_from_recycle(int(dict(rows[0])["id"]))
        assert new_id is not None
        restored = sm.get_facts(user_key="wxid_a")
        assert [f["fact"] for f in restored] == ["会删除的事实"]


# ═══════════════════════ B4 遗忘权重 ═══════════════════════

class TestForgettingRecallBonus:
    def test_access_count_boosts_weight(self):
        from shisi.memory.legacy.forgetting_manager import ForgettingManager

        fm = ForgettingManager()
        w0 = fm.retrieval_weight(0.2, days_since_access=10, access_count=0)
        w5 = fm.retrieval_weight(0.2, days_since_access=10, access_count=5)
        assert w5 > w0
        # 封顶：极端 access 不会把权重抬到 1 以外
        assert fm.effective_importance(0.9, access_count=100) <= 1.0

    def test_should_delete_considers_access(self):
        from shisi.memory.legacy.forgetting_manager import ForgettingManager

        fm = ForgettingManager()
        # 低重要性 + 很久未访问 → 无回忆时应删
        assert fm.should_delete(0.05, days_since_access=200, access_count=0) is True
        # 高 access_count 回忆强化 → 不应删
        assert fm.should_delete(0.05, days_since_access=200, access_count=8) is False


# ═══════════════════════ B3 配置接线 ═══════════════════════

class TestMemoryConfigWiring:
    def test_load_shisi_memory_config_reads_yaml(self):
        from shisi.config import reset_config
        from shisi.memory.legacy.memory_pipeline import _load_shisi_memory_config

        reset_config()
        cfg = _load_shisi_memory_config()
        assert cfg["working_memory_capacity"] == 20
        assert cfg["similarity_threshold"] == 0.85
        assert cfg["extraction_enabled"] is True

    def test_extraction_disabled_skips(self, tmp_path, monkeypatch):
        from shisi.config import reset_config
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        reset_config()
        mp = MemoryPipeline(
            vector_memory=None,
            structured_memory=None,
            fact_extract_interval=1,
            working_limit=5,
        )
        mp._config.extraction_enabled = False
        assert mp._do_fact_extraction("N:wxid_x") == 0
        mp.close() if hasattr(mp, "close") else None

    def test_working_limit_from_config(self):
        from shisi.config import reset_config
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        reset_config()
        mp = MemoryPipeline(vector_memory=None, structured_memory=None, working_limit=20)
        assert mp._config.working_limit == 20
        mp2 = MemoryPipeline(vector_memory=None, structured_memory=None, working_limit=7)
        assert mp2._config.working_limit == 7  # 显式非默认参数覆盖
        if hasattr(mp, "close"):
            mp.close()
        if hasattr(mp2, "close"):
            mp2.close()


# ═══════════════════════ 管线级隔离端到端 ═══════════════════════

class _FakeSM:
    """轻量 mock：记录 add/get 参数。"""

    def __init__(self):
        self.facts = []
        self._n = 0
        self.access_log = []

    @staticmethod
    def user_key_from_session(session_id: str) -> str:
        from shisi.memory.legacy.structured_memory import StructuredMemory
        return StructuredMemory.user_key_from_session(session_id)

    def add_fact(self, fact, category="general", confidence=0.5, source="", user_key=""):
        self._n += 1
        self.facts.append({
            "id": self._n, "fact": fact, "category": category,
            "confidence": confidence, "user_key": user_key,
            "access_count": 0, "status": "active",
        })
        return self._n

    def get_facts(self, category=None, min_confidence=0.0, limit=50,
                  user_key=None, include_legacy=False):
        out = []
        for f in self.facts:
            if f["status"] != "active":
                continue
            if f["confidence"] < min_confidence:
                continue
            if user_key is not None:
                if not include_legacy and f["user_key"] != user_key:
                    continue
                if include_legacy and f["user_key"] not in (user_key, ""):
                    continue
            out.append(dict(f))
        return out[:limit]

    def search_facts(self, keyword, user_key=None, include_legacy=False):
        return [
            dict(f) for f in self.facts
            if keyword in f["fact"] and (user_key is None or f["user_key"] == user_key)
        ]

    def increment_fact_access(self, ids):
        for f in self.facts:
            if f["id"] in ids:
                f["access_count"] += 1
                self.access_log.append(f["id"])
        return len(ids)

    def delete_fact(self, fact_id, recycle=True, user_key="", retain_days=30):
        for f in self.facts:
            if f["id"] == fact_id:
                self.facts.remove(f)
                return True
        return False

    def add_chat(self, *a, **k):
        pass

    def get_recent_chats(self, n=10):
        return []

    def get_chats_today(self):
        return []

    def get_chats_by_session_limit(self, session_id, limit):
        return []

    def health_check(self):
        return {"available": True}

    def get_connection(self, write=False):
        raise RuntimeError("no sql in fake")


class TestPipelineIsolation:
    def _pipeline(self):
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        sm = _FakeSM()

        class _VM:
            def store_chat_sync(self, *a, **k):
                return None

            def store_fact(self, *a, **k):
                return None

            def health_check(self):
                return {"available": True}

            def _search(self, *a, **k):
                return []

            def _collections(self):
                return {}

        mp = MemoryPipeline(
            vector_memory=_VM(),
            structured_memory=sm,
            fact_extract_interval=999,
            working_limit=5,
        )
        return mp, sm

    def test_context_only_injects_own_facts(self):
        mp, sm = self._pipeline()
        sm.add_fact("A喜欢猫", user_key="wxid_a")
        sm.add_fact("B喜欢狗", user_key="wxid_b")
        # pipeline session → user_key
        mp._session_id = "N:wxid_a"
        ctx = mp.get_memory_context()
        assert "A喜欢猫" in ctx["user_facts"]
        assert "B喜欢狗" not in ctx["user_facts"]
        # 注入即回忆强化
        assert sm.access_log, "注入上下文应触发 access_count 自增"

    def test_legacy_facts_not_injected(self):
        mp, sm = self._pipeline()
        sm.add_fact("旧孤儿事实", user_key="")
        mp._session_id = "N:wxid_new"
        ctx = mp.get_memory_context()
        assert "旧孤儿事实" not in ctx["user_facts"]
