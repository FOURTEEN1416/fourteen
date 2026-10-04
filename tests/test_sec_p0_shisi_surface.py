"""P0 越权收口 — shisi 面收口域（红测先行）。

缺陷链：``api/auth.py::verify_api_key_dep`` 把「任意注册用户的 JWT」当机器凭证
放行（仅验签），而 shisi 全组路由（``shisi/api/registry._mount_routes``）只挂
此依赖 → ``role=viewer`` 的普通注册用户可以：

1. 以**任意** ``user_id`` query 参数读写他人好感度隔离键（F1 前）；
2. 调角色 switch / import / export / delete（F2 前）；
3. 调表情包 import / bind / delete，且 category 可携带 ``../`` 路径穿越（F3 前）；
4. 调记忆 favorite / unfavorite / favorites / forward（F4 前，过渡期收口）；
5. 读全局统计（F5 前）；
6. 读任意角色的情感阶段 / 生理指标会话派生状态（F6 前）。

主体模拟统一走 ``dependency_overrides[get_current_user_id]`` + 真实 users 表
（admin/viewer 各一行，role 以库内现值生效，与 ``require_role`` 语义一致）；
另设一条真实 JWT 链路用例钉住「无 Bearer 必须 401」的接线。
测试全量 ``tmp_path`` 隔离，禁止写真实 ``data/``。
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth_jwt import create_access_token, get_current_user_id
from api.database import Base, User, get_db

_ADMIN_ID = 1
_VIEWER_ID = 2


# ────────────────────────────────────────────────────────────────
#  主体夹具：users 真库 + get_current_user_id 覆盖
# ────────────────────────────────────────────────────────────────

@pytest.fixture
def engines():
    """收集测试期间创建的异步引擎，收尾统一 dispose。"""
    created: list = []
    yield created
    for eng in created:
        asyncio.run(eng.dispose())


def make_principal_client(
    tmp_path: Path,
    engines: list,
    *routers,
    role: str,
) -> TestClient:
    """构建挂给定 router 的 app：users 表真库（admin+viewer），
    ``get_current_user_id`` 覆盖为指定角色的 uid。"""
    db = str(tmp_path / "users.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    engines.append(engine)
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

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = (
        lambda: _ADMIN_ID if role == "admin" else _VIEWER_ID
    )
    return TestClient(app)


def _migrate(tmp_path: Path, name: str) -> str:
    """建沙箱 shisi 库（含 stickers/affinity_unlocks/emotion_stage_state 等表）。"""
    from shisi.migrations import run_migrations

    db = str(tmp_path / name)
    run_migrations(db)
    return db


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.png", b"\x89PNG\r\n\x1a\nfake-image")
    return buf.getvalue()


# ────────────────────────────────────────────────────────────────
#  F1 affinity 主体化（🔴 最高危）
# ────────────────────────────────────────────────────────────────

def _affinity_app(tmp_path: Path, engines: list, role: str) -> tuple[TestClient, object]:
    from shisi.affinity.enhancer import AffinityEnhancer
    from shisi.api import affinity_routes

    db = _migrate(tmp_path, "affinity.db")
    enh = AffinityEnhancer(db_path=db)
    affinity_routes.set_enhancer(enh)
    client = make_principal_client(tmp_path, engines, affinity_routes.router, role=role)
    return client, enh


def test_f1_affinity_get_viewer_query_user_id_ignored_uses_subject(
    tmp_path, engines
):
    """viewer 带 user_id=victim 读取：必须落到主体键 2::c1，victim 键不可见。"""
    client, enh = _affinity_app(tmp_path, engines, role="viewer")
    enh.update("c1", 30.0, reason="seed-subject", user_id="2")
    enh.update("c1", 99.0, reason="seed-victim", user_id="victim")

    r = client.get("/api/shisi/affinity/c1", params={"user_id": "victim"})
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(
        enh.get_value("c1", user_id="2")
    ), "viewer 的 query user_id 必须被 JWT 主体覆盖"


def test_f1_affinity_update_viewer_writes_subject_key_only(tmp_path, engines):
    """viewer 写入：只允许落在 2::c2，query 指定的 victim 键不得被触碰。"""
    client, enh = _affinity_app(tmp_path, engines, role="viewer")
    enh.update("c2", 50.0, reason="seed-victim", user_id="victim")

    r = client.post(
        "/api/shisi/affinity/c2/update",
        params={"user_id": "victim"},
        json={"delta": 10.0, "reason": "sec", "source": "test"},
    )
    assert r.status_code == 200
    # 修复后：写入落主体键 2::c2（无种子 → 起点 + delta）；victim 键保持种子原值
    assert r.json()["data"]["affinity"] == pytest.approx(
        enh.get_value("c2", user_id="2")
    )
    assert enh.get_value("c2", user_id="victim") == pytest.approx(
        50.0
    ), "viewer 不得借 query user_id 写入他人键（修复前 victim 键被 +10）"


def test_f1_affinity_decay_viewer_targets_subject_key(tmp_path, engines):
    """viewer decay：目标键必须是 2::c3，victim 键不得被衰减。"""
    client, enh = _affinity_app(tmp_path, engines, role="viewer")
    enh.update("c3", 80.0, reason="seed-subject", user_id="2")
    enh.update("c3", 50.0, reason="seed-victim", user_id="victim")

    r = client.post("/api/shisi/affinity/c3/decay", params={"user_id": "victim"})
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(
        enh.get_value("c3", user_id="2")
    )
    # victim 键读数必须仍等于其种子（衰减不得触碰他人键）
    assert enh.get_value("c3", user_id="victim") == pytest.approx(50.0)


def test_f1_affinity_admin_can_manage_others(tmp_path, engines):
    """admin 显式传 user_id：允许管理他人键（显式管理通道保留）。"""
    client, enh = _affinity_app(tmp_path, engines, role="admin")

    r = client.post(
        "/api/shisi/affinity/c4/update",
        params={"user_id": "target-user"},
        json={"delta": 7.0, "reason": "admin-op", "source": "test"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(
        enh.get_value("c4", user_id="target-user")
    )


def test_f1_affinity_unlocks_viewer_subject_scoped(tmp_path, engines):
    """viewer unlocks：value/recorded 必须按主体键 2::c1 读。"""
    from shisi.affinity.enhancer import affinity_key

    client, enh = _affinity_app(tmp_path, engines, role="viewer")
    enh.update("c1", 30.0, reason="seed", user_id="2")

    r = client.get("/api/shisi/affinity/c1/unlocks", params={"user_id": "victim"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["affinity"] == pytest.approx(enh.get_value("c1", user_id="2"))
    assert data["recorded"] == enh.unlock_manager.recorded_unlocks(
        affinity_key("c1", "2")
    )


def test_f1_affinity_real_jwt_chain_no_token_401_viewer_token_subject_key(
    tmp_path, engines
):
    """真实链路钉死：无 Bearer → 401；viewer 真实 token → 主体键生效。

    本例不覆盖 get_current_user_id，走 verify_token → DB → token_version
    完整链条，证明端点依赖在生产装配下真实生效。
    """
    from shisi.affinity.enhancer import AffinityEnhancer
    from shisi.api import affinity_routes

    db = _migrate(tmp_path, "affinity_chain.db")
    enh = AffinityEnhancer(db_path=db)
    affinity_routes.set_enhancer(enh)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    engines.append(engine)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as s:
            s.add(User(id=_VIEWER_ID, email="v@test", username="v",
                       hashed_password="x", role="viewer",
                       is_active=True, is_verified=True, token_version=0))
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_maker() as s:
            yield s

    app = FastAPI()
    app.include_router(affinity_routes.router)
    app.dependency_overrides[get_db] = _get_db
    client = TestClient(app)

    enh.update("c1", 30.0, reason="seed", user_id="2")

    r0 = client.get("/api/shisi/affinity/c1")
    assert r0.status_code == 401, "无 Bearer 必须拒绝（修复前=200 旧裸键口径）"

    token = create_access_token({"sub": str(_VIEWER_ID), "tv": 0})
    r1 = client.get(
        "/api/shisi/affinity/c1",
        headers={"Authorization": f"Bearer {token}"},
        params={"user_id": "victim"},
    )
    assert r1.status_code == 200
    assert r1.json()["data"]["affinity"] == pytest.approx(
        enh.get_value("c1", user_id="2")
    ), "真实 JWT viewer：query user_id 必须被主体覆盖"


# ────────────────────────────────────────────────────────────────
#  F2 character_routes（shisi 版）admin 门禁
# ────────────────────────────────────────────────────────────────

class _CharMgrStub:
    """最小替身：只覆盖本域四端点触达的方法。"""

    def __init__(self):
        self._cards = {"hero": {"name": "hero"}}

    def switch_character(self, cid: str) -> tuple[bool, str]:
        return (False, "stub-no-such-card") if cid not in self._cards else (True, "ok")

    def import_character(self, path: str) -> tuple[None, str]:
        return None, "stub-reject"

    def export_character(self, cid: str) -> None:
        return None

    def delete_character(self, cid: str) -> bool:
        return self._cards.pop(cid, None) is not None

    def list_characters(self) -> list:
        return []


def _character_client(tmp_path, engines, role: str) -> TestClient:
    from shisi.api import character_routes

    character_routes._manager = _CharMgrStub()
    return make_principal_client(
        tmp_path, engines, character_routes.router, role=role
    )


def test_f2_character_switch_requires_admin(tmp_path, engines):
    client = _character_client(tmp_path, engines, "viewer")
    r = client.post("/api/shisi/characters/switch", json={"character_id": "hero"})
    assert r.status_code == 403, f"viewer 切换角色必须 403，实得 {r.status_code}"


def test_f2_character_switch_admin_passes_gate(tmp_path, engines):
    client = _character_client(tmp_path, engines, "admin")
    r = client.post("/api/shisi/characters/switch", json={"character_id": "hero"})
    assert r.status_code == 200


def test_f2_character_import_requires_admin(tmp_path, engines):
    client = _character_client(tmp_path, engines, "viewer")
    r = client.post(
        "/api/shisi/characters/import",
        files={"file": ("c.json", b"{}", "application/json")},
    )
    assert r.status_code == 403, f"viewer 导入必须 403，实得 {r.status_code}"


def test_f2_character_export_requires_admin(tmp_path, engines):
    client = _character_client(tmp_path, engines, "viewer")
    r = client.post("/api/shisi/characters/export/hero")
    assert r.status_code == 403, f"viewer 导出必须 403，实得 {r.status_code}"


def test_f2_character_delete_requires_admin(tmp_path, engines):
    client = _character_client(tmp_path, engines, "viewer")
    r = client.delete("/api/shisi/characters/hero")
    assert r.status_code == 403, f"viewer 删除必须 403，实得 {r.status_code}"


def test_f2_character_delete_admin_passes_gate(tmp_path, engines):
    client = _character_client(tmp_path, engines, "admin")
    r = client.delete("/api/shisi/characters/hero")
    assert r.status_code == 200


def test_f2_character_list_stays_open_for_logged_in(tmp_path, engines):
    """边界钉：list 不在 F2 收口清单内，登录用户（viewer）仍可读。"""
    client = _character_client(tmp_path, engines, "viewer")
    r = client.get("/api/shisi/characters")
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────
#  F3 sticker_routes admin 门禁 + importer category 消毒
# ────────────────────────────────────────────────────────────────

def _sticker_client(tmp_path, engines, role: str) -> TestClient:
    from shisi.api import sticker_routes
    from shisi.sticker.sticker_manager import StickerManager

    db = _migrate(tmp_path, "stickers.db")
    sticker_routes._manager = StickerManager(
        db_path=db, data_dir=str(tmp_path / "stk")
    )
    return make_principal_client(
        tmp_path, engines, sticker_routes.router, role=role
    )


def test_f3_sticker_import_requires_admin(tmp_path, engines):
    client = _sticker_client(tmp_path, engines, "viewer")
    r = client.post(
        "/api/shisi/stickers/import",
        files={"file": ("s.zip", _zip_bytes(), "application/zip")},
        data={"category": "cat1"},
    )
    assert r.status_code == 403, f"viewer 导入表情包必须 403，实得 {r.status_code}"


def test_f3_sticker_bind_requires_admin(tmp_path, engines):
    client = _sticker_client(tmp_path, engines, "viewer")
    r = client.put(
        "/api/shisi/stickers/characters/c1/stickers",
        json={"sticker_ids": ["s1"], "unlock_threshold": 0},
    )
    assert r.status_code == 403, f"viewer 绑定表情包必须 403，实得 {r.status_code}"


def test_f3_sticker_delete_requires_admin(tmp_path, engines):
    client = _sticker_client(tmp_path, engines, "viewer")
    r = client.delete("/api/shisi/stickers/stk_x")
    assert r.status_code == 403, f"viewer 删除表情包必须 403，实得 {r.status_code}"


def test_f3_sticker_import_admin_passes_gate(tmp_path, engines):
    client = _sticker_client(tmp_path, engines, "admin")
    r = client.post(
        "/api/shisi/stickers/import",
        files={"file": ("s.zip", _zip_bytes(), "application/zip")},
        data={"category": "cat1"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["accepted"] == 1


def test_f3_sticker_import_illegal_category_400(tmp_path, engines):
    """category 路径穿越：路由层必须 400，且数据目录外不得落盘。

    注：路由签名里 ``category`` 是普通 str 参数（= query 参数），
    必须经 ``params=`` 传递（multipart body 字段不会绑定到它）。
    """
    client = _sticker_client(tmp_path, engines, "admin")
    r = client.post(
        "/api/shisi/stickers/import",
        files={"file": ("s.zip", _zip_bytes(), "application/zip")},
        params={"category": "../evil"},
    )
    assert r.status_code == 400, f"非法 category 必须 400，实得 {r.status_code}"
    assert not (tmp_path / "evil").exists(), "穿越目录不得被创建"


def test_f3_importer_rejects_traversal_category(tmp_path):
    """importer 层：``../evil`` 必须拒绝（ValueError），数据目录外零落盘。"""
    from shisi.migrations import run_migrations
    from shisi.sticker.importer import StickerImporter

    db = str(tmp_path / "imp.db")
    run_migrations(db)
    data_dir = tmp_path / "stickers"
    imp = StickerImporter(db_path=db, data_dir=str(data_dir))

    zip_path = tmp_path / "s.zip"
    zip_path.write_bytes(_zip_bytes())

    with pytest.raises(ValueError):
        imp.import_zip(zip_path, "../evil")
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize("bad", ["", "a/b", "a\\b", "..", "白 色", "x" * 65])
def test_f3_importer_rejects_non_whitelist_category(tmp_path, bad):
    from shisi.migrations import run_migrations
    from shisi.sticker.importer import StickerImporter

    db = str(tmp_path / "imp2.db")
    run_migrations(db)
    imp = StickerImporter(db_path=db, data_dir=str(tmp_path / "stk2"))
    with pytest.raises(ValueError):
        imp.import_zip(tmp_path / "no-such.zip", bad)


def test_f3_importer_containment_assertion(tmp_path, monkeypatch):
    """纵深防御钉：即便白名单被绕过，resolve 后包含性断言仍拦截出界目录。"""
    from shisi.migrations import run_migrations
    from shisi.sticker.importer import StickerImporter, sanitize_category

    db = str(tmp_path / "imp3.db")
    run_migrations(db)
    data_dir = tmp_path / "stk3"
    imp = StickerImporter(db_path=db, data_dir=str(data_dir))

    # 模拟白名单失效：消毒函数被替换为恒等（原样放行恶意值）
    monkeypatch.setattr(
        "shisi.sticker.importer.sanitize_category", lambda c: c
    )
    with pytest.raises(ValueError):
        imp.import_zip(tmp_path / "no-such.zip", "../evil")
    assert not (tmp_path / "evil").exists()
    # 白名单函数本身仍被正常导出可用
    assert sanitize_category("cat-1_x") == "cat-1_x"


# ────────────────────────────────────────────────────────────────
#  F4 memory_routes（shisi 版）admin 门禁（过渡期收口）
# ────────────────────────────────────────────────────────────────

def _memory_client(tmp_path, engines, role: str):
    from shisi.api import memory_routes
    from shisi.memory.favorite_manager import FavoriteManager
    from shisi.memory.forward_manager import ForwardManager

    db = _migrate(tmp_path, "memory.db")
    memory_routes.set_managers(FavoriteManager(db_path=db), ForwardManager(db_path=db))
    client = make_principal_client(tmp_path, engines, memory_routes.router, role=role)
    return client, db


def test_f4_memory_favorite_requires_admin(tmp_path, engines):
    client, _ = _memory_client(tmp_path, engines, "viewer")
    r = client.post(
        "/api/shisi/memory/favorite",
        json={"character_id": "c1", "memory_id": "m1"},
    )
    assert r.status_code == 403, f"viewer 收藏必须 403，实得 {r.status_code}"


def test_f4_memory_unfavorite_requires_admin(tmp_path, engines):
    client, _ = _memory_client(tmp_path, engines, "viewer")
    r = client.delete("/api/shisi/memory/favorite/1")
    assert r.status_code == 403, f"viewer 取消收藏必须 403，实得 {r.status_code}"


def test_f4_memory_list_favorites_requires_admin(tmp_path, engines):
    client, _ = _memory_client(tmp_path, engines, "viewer")
    r = client.get("/api/shisi/memory/favorites", params={"character_id": "c1"})
    assert r.status_code == 403, f"viewer 读收藏列表必须 403，实得 {r.status_code}"


def test_f4_memory_forward_requires_admin(tmp_path, engines):
    client, _ = _memory_client(tmp_path, engines, "viewer")
    r = client.post(
        "/api/shisi/memory/forward",
        json={"from_character": "c1", "to_character": "c2",
              "memory_id": "m1", "content": "x"},
    )
    assert r.status_code == 403, f"viewer 转发必须 403，实得 {r.status_code}"


def test_f4_memory_admin_favorite_roundtrip(tmp_path, engines):
    """admin 门禁通过性：收藏→列表→按主键取消 全链路 200。"""
    client, _ = _memory_client(tmp_path, engines, "admin")
    r1 = client.post(
        "/api/shisi/memory/favorite",
        json={"character_id": "c1", "memory_id": "m1"},
    )
    assert r1.status_code == 200 and r1.json()["data"]["success"] is True
    r2 = client.get("/api/shisi/memory/favorites", params={"character_id": "c1"})
    assert r2.status_code == 200 and len(r2.json()["data"]) == 1
    fav_id = r2.json()["data"][0]["id"]
    r3 = client.delete(f"/api/shisi/memory/favorite/{fav_id}")
    assert r3.status_code == 200 and r3.json()["data"]["success"] is True


# ────────────────────────────────────────────────────────────────
#  F5 stats_routes admin 门禁
# ────────────────────────────────────────────────────────────────

def _stats_client(tmp_path, engines, role: str) -> TestClient:
    from shisi.api import stats_routes
    from shisi.stats.analytics import AnalyticsService

    stats_routes._service = AnalyticsService()
    return make_principal_client(tmp_path, engines, stats_routes.router, role=role)


def test_f5_stats_requires_admin(tmp_path, engines):
    client = _stats_client(tmp_path, engines, "viewer")
    r = client.get("/api/shisi/stats")
    assert r.status_code == 403, f"viewer 读全局统计必须 403，实得 {r.status_code}"


def test_f5_stats_admin_passes_gate(tmp_path, engines):
    client = _stats_client(tmp_path, engines, "admin")
    r = client.get("/api/shisi/stats")
    assert r.status_code == 200


# ────────────────────────────────────────────────────────────────
#  F6 emotion-stage / vital-signs / persona 逐端点判定
# ────────────────────────────────────────────────────────────────

def test_f6_emotion_stage_character_read_requires_admin(tmp_path, engines):
    from shisi.api import emotion_stage_routes
    from shisi.emotion_stage.stage_engine import EmotionStageEngine

    db = _migrate(tmp_path, "stage.db")
    emotion_stage_routes.set_engine(EmotionStageEngine(db_path=db))
    client = make_principal_client(
        tmp_path, engines, emotion_stage_routes.router, role="viewer"
    )
    r = client.get("/api/shisi/emotion-stage/c1")
    assert r.status_code == 403, \
        f"viewer 读他人角色情感阶段必须 403，实得 {r.status_code}"


def test_f6_emotion_stage_character_read_admin_passes(tmp_path, engines):
    from shisi.api import emotion_stage_routes
    from shisi.emotion_stage.stage_engine import EmotionStageEngine

    db = _migrate(tmp_path, "stage2.db")
    emotion_stage_routes.set_engine(EmotionStageEngine(db_path=db))
    client = make_principal_client(
        tmp_path, engines, emotion_stage_routes.router, role="admin"
    )
    r = client.get("/api/shisi/emotion-stage/c1")
    assert r.status_code == 200


def test_f6_emotion_stage_stages_and_evaluate_stay_open(tmp_path, engines):
    """边界钉：/stages（前端 StatusCenter 消费）与 /evaluate（纯计算，
    不读任何存量键）保持登录可用——修复不得误收口。"""
    from shisi.api import emotion_stage_routes
    from shisi.emotion_stage.stage_engine import EmotionStageEngine

    db = _migrate(tmp_path, "stage3.db")
    emotion_stage_routes.set_engine(EmotionStageEngine(db_path=db))
    client = make_principal_client(
        tmp_path, engines, emotion_stage_routes.router, role="viewer"
    )
    assert client.get("/api/shisi/emotion-stage/stages").status_code == 200
    r = client.post(
        "/api/shisi/emotion-stage/c1/evaluate", params={"affinity": 60}
    )
    assert r.status_code == 200


def test_f6_vital_signs_character_read_requires_admin(tmp_path, engines):
    from shisi.api import vital_signs_routes
    from shisi.vital_signs.vital_engine import VitalSignsEngine

    db = _migrate(tmp_path, "vital.db")
    vital_signs_routes.set_engine(VitalSignsEngine(db_path=db))
    client = make_principal_client(
        tmp_path, engines, vital_signs_routes.router, role="viewer"
    )
    r = client.get("/api/shisi/vital-signs/c1")
    assert r.status_code == 403, \
        f"viewer 读他人角色生理指标必须 403，实得 {r.status_code}"


def test_f6_vital_signs_admin_passes(tmp_path, engines):
    from shisi.api import vital_signs_routes
    from shisi.vital_signs.vital_engine import VitalSignsEngine

    db = _migrate(tmp_path, "vital2.db")
    vital_signs_routes.set_engine(VitalSignsEngine(db_path=db))
    client = make_principal_client(
        tmp_path, engines, vital_signs_routes.router, role="admin"
    )
    assert client.get("/api/shisi/vital-signs/c1").status_code == 200


def test_f6_persona_reads_stay_open_for_logged_in(tmp_path, engines):
    """边界钉：persona 两读端点与 F2 的 list/get 同语义（全局卡池共享内容），
    登录用户（viewer）保持可读；PUT 已是 410 不变。"""
    from shisi.api import persona_routes
    from shisi.character.models import CharaCardV2, CharacterData

    card = CharaCardV2(data=CharacterData(name="hero"))

    class _Mgr:
        def load_character(self, cid: str):
            return card if cid == "hero" else None

    persona_routes._manager = _Mgr()
    client = make_principal_client(
        tmp_path, engines, persona_routes.router, role="viewer"
    )
    assert client.get("/api/shisi/persona/characters/hero").status_code == 200
    assert client.get("/api/shisi/persona/characters/hero/preview").status_code == 200
    r = client.put(
        "/api/shisi/persona/characters/hero", json={"card": {}}
    )
    assert r.status_code == 410


# ────────────────────────────────────────────────────────────────
#  生产装配级：经 shisi/api/registry.setup_shisi 真实挂载
#  （_mount_routes 路由级 verify_api_key_dep + 端点级依赖叠加）
# ────────────────────────────────────────────────────────────────

def test_registry_assembly_full_auth_chain(tmp_path, engines):
    """registry 真实挂载下的三段认证契约（P0 收尾批 F3）：

    1. 无凭证 → 401：路由级 verify_api_key_dep（configure_auth(True) 非放行态）
       与端点级依赖在生产装配链下真实生效；
    2. viewer 真实 JWT 打 affinity update：query user_id 被主体覆盖，
       只落主体键 2::c9，他人键不被触碰；
    3. admin 真实 JWT 显式 user_id：管理通道 200，键 = 显式值。
    """
    from api.auth import configure_auth
    from shisi.api.registry import setup_shisi

    configure_auth(True, "registry-asm-machine-key-32chars-minimum!")

    shisi_db = _migrate(tmp_path, "registry_asm.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    engines.append(engine)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as s:
            s.add(User(id=_ADMIN_ID, email="a@test", username="a",
                       hashed_password="x", role="admin",
                       is_active=True, is_verified=True, token_version=0))
            s.add(User(id=_VIEWER_ID, email="v@test", username="v",
                       hashed_password="x", role="viewer",
                       is_active=True, is_verified=True, token_version=0))
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_maker() as s:
            yield s

    app = FastAPI()
    reg = setup_shisi(app, run_migrate=False, db_path=shisi_db)
    app.dependency_overrides[get_db] = _get_db
    client = TestClient(app)

    # 1) 无凭证 → 401
    r0 = client.get("/api/shisi/affinity/c9")
    assert r0.status_code == 401, f"无凭证必须 401，实得 {r0.status_code}"

    # 2) viewer 真实 JWT：主体键生效，victim 键不被触碰
    reg.affinity_enhancer.update("c9", 70.0, reason="seed-victim", user_id="victim")
    viewer_token = create_access_token({"sub": str(_VIEWER_ID), "tv": 0})
    r1 = client.post(
        "/api/shisi/affinity/c9/update",
        params={"user_id": "victim"},
        json={"delta": 5.0, "reason": "sec-asm", "source": "test"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["affinity"] == pytest.approx(5.0), (
        "viewer 写入必须落主体键 2::c9（无种子起点 + delta）"
    )
    assert reg.affinity_enhancer.get_value("c9", user_id="victim") == pytest.approx(
        70.0
    ), "生产装配链下 viewer 同样不得借 query user_id 写他人键"

    # 3) admin 显式 user_id 管理通道
    admin_token = create_access_token({"sub": str(_ADMIN_ID), "tv": 0})
    r2 = client.post(
        "/api/shisi/affinity/c9/update",
        params={"user_id": "target-user"},
        json={"delta": 7.0, "reason": "admin-op", "source": "test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["affinity"] == pytest.approx(
        reg.affinity_enhancer.get_value("c9", user_id="target-user")
    ), "admin 显式 user_id 管理通道必须生效（键 = 显式值）"
