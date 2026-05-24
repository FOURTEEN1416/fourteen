"""集成测试：FastAPI端点 + E2E流程 + Orchestrator。"""

import sys

sys.path.insert(0, ".")


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator import Orchestrator
from shisi.api.registry import setup_shisi
from shisi.character.manager import CharacterManager
from shisi.character.models import CharaCardV2, CharacterData
from shisi.config import reset_config
from shisi.migrations import run_migrations


@pytest.fixture(autouse=True)
def reset_config_each():
    reset_config()


@pytest.fixture
def app_and_reg(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    app = FastAPI()
    reg = setup_shisi(app, run_migrate=False)
    client = TestClient(app)
    return app, reg, client


class TestCharacterAPIEndpoints:
    def test_list_characters(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.get("/api/shisi/characters")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_switch_nonexistent(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.post("/api/shisi/characters/switch", json={"character_id": "nonexistent"})
        assert resp.status_code == 400

    def test_get_nonexistent(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.get("/api/shisi/characters/nonexistent")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.delete("/api/shisi/characters/nonexistent")
        assert resp.status_code == 404


class TestAffinityAPIEndpoints:
    def test_get_affinity(self, app_and_reg):
        _, reg, client = app_and_reg
        reg.affinity_enhancer.update("test_char", 50, "chat")
        resp = client.get("/api/shisi/affinity/test_char")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["affinity"] == 50.0

    def test_update_affinity(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.post("/api/shisi/affinity/test_char/update", json={"delta": 10, "reason": "test", "source": "test"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["affinity"] == 10.0


class TestEmotionStageAPIEndpoints:
    def test_list_stages(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.get("/api/shisi/emotion-stage/stages")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 4

    def test_evaluate_stage(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.post("/api/shisi/emotion-stage/test_char/evaluate?affinity=60")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["stage"] == "亲密"


class TestVitalSignsAPIEndpoints:
    def test_get_vital_signs(self, app_and_reg):
        _, reg, client = app_and_reg
        reg.vital_engine.update_on_emotion("test_char", "生气")
        resp = client.get("/api/shisi/vital-signs/test_char")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["heart_rate"] > 90
        assert "wechat_format" in data


class TestStatsAPIEndpoints:
    def test_get_stats(self, app_and_reg):
        _, reg, client = app_and_reg
        reg.analytics_service.record_message("c1", "开心", 60)
        resp = client.get("/api/shisi/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_messages"] == 1


class TestMemoryAPIEndpoints:
    def test_favorite(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.post("/api/shisi/memory/favorite", json={"character_id": "c1", "memory_id": "m1"})
        assert resp.status_code == 200

    def test_list_favorites(self, app_and_reg):
        _, _, client = app_and_reg
        client.post("/api/shisi/memory/favorite", json={"character_id": "c1", "memory_id": "m1"})
        resp = client.get("/api/shisi/memory/favorites?character_id=c1")
        assert resp.status_code == 200

    def test_forward(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.post("/api/shisi/memory/forward", json={"from_character": "c1", "to_character": "c2", "memory_id": "m1"})
        assert resp.status_code == 200

    def test_delete_requires_confirm(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.delete("/api/shisi/memory/m1?character_id=c1&confirm=false")
        assert resp.status_code == 400


class TestOrchestratorIntegration:
    def test_orchestrator_has_character_manager(self):
        orc = Orchestrator()
        assert hasattr(orc, '_character_manager')
        assert orc._character_manager is None

    def test_orchestrator_with_character_manager(self, tmp_path):
        from shisi.character.store import CharacterStore
        db = tmp_path / "test.db"
        run_migrations(db)
        mgr = CharacterManager(store=CharacterStore(db))
        mgr.initialize()
        orc = Orchestrator(character_manager=mgr)
        assert orc._character_manager is mgr

    def test_orchestrator_none_character_manager_fallback(self):
        orc = Orchestrator(character_manager=None)
        assert orc._character_manager is None


class TestE2EFlow:
    def test_full_character_lifecycle(self, app_and_reg):
        """E2E: 创建角色 → 切换 → 好感度更新 → 情感阶段推进 → 查询"""
        _, reg, client = app_and_reg

        card = CharaCardV2(data=CharacterData(name="椎名真昼", description="完美", personality="温柔"))
        cid = reg.character_manager.store.save_character(card)

        resp = client.post("/api/shisi/characters/switch", json={"character_id": cid})
        assert resp.status_code == 200

        for _i in range(6):
            resp = client.post(f"/api/shisi/affinity/{cid}/update", json={"delta": 10, "reason": "chat", "source": "test"})
            assert resp.status_code == 200

        resp = client.post(f"/api/shisi/emotion-stage/{cid}/evaluate?affinity=60")
        assert resp.status_code == 200
        assert resp.json()["data"]["stage"] == "亲密"

        ok, msg = reg.wechat_handler.handle("好感度", cid)
        assert ok is True
        assert "60" in msg

    def test_vital_signs_emotion_flow(self, app_and_reg):
        """E2E: 情感→生理指标→微信格式化"""
        _, reg, _ = app_and_reg
        reg.vital_engine.update_on_emotion("c1", "生气")
        msg = reg.vital_engine.format_wechat_message("c1")
        assert "心率" in msg
        state = reg.vital_engine.get_current("c1")
        assert state.heart_rate > 90

    def test_proactive_message_with_context(self, app_and_reg):
        """E2E: 主动消息附带情感摘要"""
        _, reg, _ = app_and_reg
        reg.affinity_enhancer.update("c1", 72, "chat")
        reg.stage_engine.evaluate("c1", 72)
        result = reg.proactive_messenger.enhance_proactive_message("早安~", "c1", "开心")
        assert result["summary"] is not None
        assert "开心" in result["summary"]
