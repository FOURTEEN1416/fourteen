"""集成测试：FastAPI端点 + E2E流程 + Orchestrator。"""

import sys

sys.path.insert(0, ".")

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth_jwt import create_access_token
from api.database import Base, User, get_db
from orchestrator import Orchestrator
from shisi.api.registry import setup_shisi
from shisi.character.manager import CharacterManager
from shisi.character.models import CharaCardV2, CharacterData
from shisi.config import reset_config
from shisi.migrations import run_migrations

_ADMIN_ID = 1
_VIEWER_ID = 2


def _bearer(role: str = "admin") -> dict[str, str]:
    """P0 收口后的 shisi 端点认证契约：管理面（stats/vital-signs/memory 写、
    character switch/delete、sticker import）走 admin 通道；affinity 面
    登录即可（键归属由主体导出）。token 与 users 沙箱库种行同 uid。"""
    uid = _ADMIN_ID if role == "admin" else _VIEWER_ID
    token = create_access_token({"sub": str(uid), "tv": 0})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def reset_config_each():
    reset_config()


@pytest.fixture
def app_and_reg(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    app = FastAPI()
    reg = setup_shisi(app, run_migrate=False, db_path=db)

    # P0 收口批：端点级依赖（get_current_user / require_role）经
    # api.database.get_db 查主体——钉到 tmp_path 沙箱 users 库（admin+viewer
    # 各一行，role 以库内现值生效），集成测试零接触真实 data/users.db。
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as s:
            s.add(User(id=_ADMIN_ID, email="a@test", username="a",
                       hashed_password="x", role="admin",
                       is_active=True, is_verified=True))
            s.add(User(id=_VIEWER_ID, email="v@test", username="v",
                       hashed_password="x", role="viewer",
                       is_active=True, is_verified=True))
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db

    client = TestClient(app)
    yield app, reg, client
    asyncio.run(engine.dispose())


class TestCharacterAPIEndpoints:
    def test_list_characters(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.get("/api/shisi/characters")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_switch_nonexistent(self, app_and_reg):
        """P0 收口后：switch 走 admin 门禁（admin 通道维持 400 语义）。"""
        _, _, client = app_and_reg
        resp = client.post(
            "/api/shisi/characters/switch",
            json={"character_id": "nonexistent"},
            headers=_bearer("admin"),
        )
        assert resp.status_code == 400

    def test_get_nonexistent(self, app_and_reg):
        _, _, client = app_and_reg
        resp = client.get("/api/shisi/characters/nonexistent")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, app_and_reg):
        """P0 收口后：delete 走 admin 门禁（admin 通道维持 404 语义）。"""
        _, _, client = app_and_reg
        resp = client.delete("/api/shisi/characters/nonexistent", headers=_bearer("admin"))
        assert resp.status_code == 404


class TestAffinityAPIEndpoints:
    def test_get_affinity(self, app_and_reg):
        """P0 收口后：affinity 读走登录主体键（uid::cid）——种子写主体键、
        带 viewer Bearer 读回，断言主体键生效。"""
        _, reg, client = app_and_reg
        reg.affinity_enhancer.update("test_char", 50, "chat", user_id=str(_VIEWER_ID))
        resp = client.get("/api/shisi/affinity/test_char", headers=_bearer("viewer"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["affinity"] == 50.0

    def test_update_affinity(self, app_and_reg):
        """P0 收口后：viewer 写入落主体键 2::test_char（无种子起点 + delta）。"""
        _, _, client = app_and_reg
        resp = client.post(
            "/api/shisi/affinity/test_char/update",
            json={"delta": 10, "reason": "test", "source": "test"},
            headers=_bearer("viewer"),
        )
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
        """P0 收口后：vital-signs 读面为 admin 通道（裸角色键他人数据面）。"""
        _, reg, client = app_and_reg
        reg.vital_engine.update_on_emotion("test_char", "生气")
        resp = client.get("/api/shisi/vital-signs/test_char", headers=_bearer("admin"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["heart_rate"] > 90
        assert "wechat_format" in data


class TestStatsAPIEndpoints:
    def test_get_stats(self, app_and_reg):
        """P0 收口后：全局统计走 admin 门禁。"""
        _, reg, client = app_and_reg
        reg.analytics_service.record_message("c1", "开心", 60)
        resp = client.get("/api/shisi/stats", headers=_bearer("admin"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_messages"] == 1


class TestMemoryAPIEndpoints:
    """P0 收口后：memory favorite/favorites/forward 走 admin 门禁（过渡期收口）。"""

    def test_favorite(self, app_and_reg, tmp_path, monkeypatch):
        """fixture 未传 fav 时 registry 无参构造 FavoriteManager 指向宿主
        data/sqlite.db —— 用例内钉到 tmp_path 隔离库，避免集成测试写真库（仿 test_forward）。
        隔离库经正典 run_migrations 建 memory_favorites 表（FavoriteManager 自身不建表，
        空库 INSERT 报错会被吞成 success=False）。"""
        from shisi.api import memory_routes
        from shisi.memory.favorite_manager import FavoriteManager
        from shisi.migrations import run_migrations

        run_migrations(tmp_path / "fav.db")
        _, _, client = app_and_reg
        monkeypatch.setattr(
            memory_routes, "_fav_mgr", FavoriteManager(db_path=tmp_path / "fav.db")
        )
        resp = client.post(
            "/api/shisi/memory/favorite",
            json={"character_id": "c1", "memory_id": "m1"},
            headers=_bearer("admin"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["success"] is True

    def test_list_favorites(self, app_and_reg, tmp_path, monkeypatch):
        """同 test_favorite：隔离到 tmp_path，并断言写读一致（收藏可见）。"""
        from shisi.api import memory_routes
        from shisi.memory.favorite_manager import FavoriteManager
        from shisi.migrations import run_migrations

        run_migrations(tmp_path / "fav.db")
        _, _, client = app_and_reg
        monkeypatch.setattr(
            memory_routes, "_fav_mgr", FavoriteManager(db_path=tmp_path / "fav.db")
        )
        client.post(
            "/api/shisi/memory/favorite",
            json={"character_id": "c1", "memory_id": "m1"},
            headers=_bearer("admin"),
        )
        resp = client.get(
            "/api/shisi/memory/favorites?character_id=c1", headers=_bearer("admin")
        )
        assert resp.status_code == 200
        favs = resp.json()["data"]
        assert isinstance(favs, list) and len(favs) == 1

    def test_forward(self, app_and_reg, tmp_path, monkeypatch):
        """W4 缺陷 F 恢复后契约（1773970）：转发落目标侧派生记录并返回真实回执。

        fixture 未传 memory_service 时 registry 无参构造 ForwardManager 指向宿主
        data/sqlite.db —— 用例内钉到 tmp_path 隔离库，避免集成测试写真库。
        """
        from shisi.api import memory_routes
        from shisi.memory.forward_manager import ForwardManager

        _, _, client = app_and_reg
        fwd = ForwardManager(db_path=tmp_path / "fwd.db")
        monkeypatch.setattr(memory_routes, "_fwd_mgr", fwd)
        resp = client.post(
            "/api/shisi/memory/forward",
            json={"from_character": "c1", "to_character": "c2", "memory_id": "m1",
                  "content": "她喜欢手冲咖啡"},
            headers=_bearer("admin"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ok"] is True
        assert int(data["forward_id"]) > 0
        assert data["from"] == "c1"
        assert data["to"] == "c2"
        assert data["memory_id"] == "m1"
        # 读回（GET /forwards 真源）：派生记录与回执同源可追溯
        rows = fwd.get_forwards("c2")
        assert rows and rows[0]["id"] == data["forward_id"]
        assert rows[0]["from"] == "c1"
        assert rows[0]["content"] == "她喜欢手冲咖啡"

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
        """E2E: 创建角色 → 切换 → 好感度更新 → 情感阶段推进 → 查询

        P0 收口后：switch 走 admin 门禁、affinity update 走登录主体键
        （admin 主体同属登录面），全程带 admin Bearer。"""
        _, reg, client = app_and_reg
        headers = _bearer("admin")

        card = CharaCardV2(data=CharacterData(name="椎名真昼", description="完美", personality="温柔"))
        cid = reg.character_manager.store.save_character(card)

        resp = client.post(
            "/api/shisi/characters/switch", json={"character_id": cid}, headers=headers
        )
        assert resp.status_code == 200

        for _i in range(6):
            resp = client.post(
                f"/api/shisi/affinity/{cid}/update",
                json={"delta": 10, "reason": "chat", "source": "test"},
                headers=headers,
            )
            assert resp.status_code == 200

        resp = client.post(f"/api/shisi/emotion-stage/{cid}/evaluate?affinity=60")
        assert resp.status_code == 200
        assert resp.json()["data"]["stage"] == "亲密"

    def test_vital_signs_emotion_flow(self, app_and_reg):
        """E2E: 情感→生理指标→微信格式化"""
        _, reg, _ = app_and_reg
        reg.vital_engine.update_on_emotion("c1", "生气")
        msg = reg.vital_engine.format_wechat_message("c1")
        assert "心率" in msg
        state = reg.vital_engine.get_current("c1")
        assert state.heart_rate > 90

