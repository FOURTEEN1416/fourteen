"""2026-09-28 历遍批：三条"随带观察项"缺陷收口（红先行）。

覆盖：
1. shisi 好感度手动面（GET/update/decay）与 W14 unlocks 同口径支持
   ``user_id`` 隔离键（不带 = 旧裸键兼容，带上 = 只读写 user::character）。
2. ``DELETE /api/shisi/characters/{id}`` 不再谎报「删除成功」——只移除
   运行态副本，回执必须如实指明权威卡真源不受影响（与 PUT 410 先例同语义族）。
3. orchestrator 请求级画像 ID 禁止再手拼 ``f"{character_id}:{session_id}"``，
   必须委托唯一构造器 ``persona_extractor.fusion.profile_scope``（AST 静态防护）。

P0 收尾批改写（2026-10-05）：第 1/2 条的 HTTP 用例按 P0 越权收口后的新契约
重写——键归属一律由 JWT 主体导出（非 admin 的 query ``user_id`` 被主体覆盖；
admin 显式传 user_id 走管理通道），角色 delete 走 admin 门禁
（主体模拟按 tests/test_sec_p0_shisi_surface.py 的 make_principal_client 模式）。
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth_jwt import get_current_user_id
from api.database import Base, User, get_db

_ADMIN_ID = 1
_VIEWER_ID = 2


# ────────────────────────────────────────────────────────────────
#  主体夹具：users 真库 + get_current_user_id 覆盖
#  （make_principal_client 模式，见 tests/test_sec_p0_shisi_surface.py）
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def engines():
    """收集测试期间创建的异步引擎，收尾统一 dispose（Windows 文件锁）。"""
    created: list = []
    yield created
    for eng in created:
        asyncio.run(eng.dispose())


def _principal_client(
    tmp_path: pathlib.Path, engines: list, *routers, role: str, dbname: str = "users.db"
) -> TestClient:
    """挂给定 router 的 app：users 表真库（admin+viewer），
    ``get_current_user_id`` 覆盖为指定角色的 uid（role 以库内现值生效）。

    同一用例内造多个不同主体的 client 时须传不同 ``dbname``（逐库独立建表）。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / dbname}")
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


def _migrate(tmp_path: pathlib.Path, name: str) -> str:
    """建沙箱 shisi 库（含 affinity_records/affinity_unlocks 等表）。"""
    from shisi.migrations import run_migrations

    db = str(tmp_path / name)
    run_migrations(db)
    return db


@pytest.fixture()
def affinity_client(tmp_path, engines):
    """viewer 主体面：shisi 隔离库 + affinity 路由（JWT 主体覆盖 query user_id）。"""
    from shisi.affinity.enhancer import AffinityEnhancer
    from shisi.api import affinity_routes

    db = _migrate(tmp_path, "affinity.db")
    enh = AffinityEnhancer(db_path=db)
    affinity_routes.set_enhancer(enh)
    client = _principal_client(tmp_path, engines, affinity_routes.router, role="viewer")
    return client, enh


def test_affinity_get_with_user_id_uses_isolation_key(affinity_client):
    """viewer GET：query user_id 必须被主体覆盖，读数只来自主体键 2::c1。"""
    client, enh = affinity_client
    enh.update("c1", 30.0, reason="seed", user_id="2")
    enh.update("c1", 99.0, reason="seed-victim", user_id="victim")
    r = client.get("/api/shisi/affinity/c1", params={"user_id": "victim"})
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(enh.get_value("c1", user_id="2"))
    # victim 键不可见（旧契约「query user_id 决定键」已随越权面关闭）
    assert r.json()["data"]["affinity"] != pytest.approx(enh.get_value("c1", user_id="victim"))


def test_affinity_update_with_user_id_writes_isolation_key(affinity_client):
    """viewer 写入：只允许落在主体键 2::c2；query 指定的 victim 键与裸键不得被触碰。"""
    client, enh = affinity_client
    r = client.post(
        "/api/shisi/affinity/c2/update",
        params={"user_id": "victim"},
        json={"delta": 12.0, "reason": "manual", "source": "test"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(enh.get_value("c2", user_id="2"))
    # 裸角色键不得被这次带用户的写触碰
    assert enh.get_value("c2") == pytest.approx(enh.get_value("c2", user_id=""))
    assert enh.get_value("c2", user_id="victim") == pytest.approx(
        enh.get_value("c2", user_id="")
    ), "viewer 不得借 query user_id 写入他人键"


def test_affinity_decay_with_user_id_targets_isolation_key(affinity_client):
    """viewer decay：目标键必须是主体键 2::c3；victim 键与裸键均不得被衰减。"""
    client, enh = affinity_client
    enh.update("c3", 80.0, reason="bare-seed")                  # 裸角色键
    enh.update("c3", 50.0, reason="subject-seed", user_id="2")  # 主体键
    enh.update("c3", 40.0, reason="victim-seed", user_id="victim")
    r = client.post("/api/shisi/affinity/c3/decay", params={"user_id": "victim"})
    assert r.status_code == 200
    body = r.json()["data"]
    # 回执读数必须来自主体键（≤ 种子 50），且本次衰减不得触碰他人键/裸键
    assert body["affinity"] == pytest.approx(enh.get_value("c3", user_id="2"))
    assert body["affinity"] <= 50.0
    assert enh.get_value("c3") == pytest.approx(80.0)
    assert enh.get_value("c3", user_id="victim") == pytest.approx(40.0)


# ────────────────────────────────────────────────────────────────


def test_shisi_delete_character_no_false_deleted_claim(tmp_path, engines):
    """新契约：viewer 删除 403（admin 门禁）；admin 通道 200 且回执如实——
    不谎报「删除成功」，必须披露仅移除运行态副本、权威真源不受影响。"""
    from shisi.api import character_routes

    # manager 由 registry 注入；此处给一个最小替身
    class _Mgr:
        def __init__(self):
            self._cards = {"hero": {"name": "hero"}}

        def delete_character(self, cid: str) -> bool:
            return self._cards.pop(cid, None) is not None

    character_routes._manager = _Mgr()

    viewer = _principal_client(
        tmp_path, engines, character_routes.router, role="viewer", dbname="users_v.db"
    )
    r_viewer = viewer.delete("/api/shisi/characters/hero")
    assert r_viewer.status_code == 403, (
        f"viewer 删除角色必须 403，实得 {r_viewer.status_code}"
    )

    admin = _principal_client(
        tmp_path, engines, character_routes.router, role="admin", dbname="users_a.db"
    )
    r = admin.delete("/api/shisi/characters/hero")
    assert r.status_code == 200
    msg = str(r.json()["data"])
    # 不得再出现无限定的「删除成功」宣称
    assert "删除成功" not in msg
    # 必须如实披露：仅运行态副本，权威卡在 config/characters
    assert "运行态" in msg and ("config/characters" in msg or "真源" in msg)


# ────────────────────────────────────────────────────────────────

_ORCH = pathlib.Path("orchestrator/optimized_orchestrator.py")


def test_orchestrator_delegates_profile_scope_no_manual_concat():
    tree = ast.parse(_ORCH.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            fmt = "".join(
                v.value if isinstance(v, ast.Constant) else "{…}" for v in node.values
            )
            if "…" in fmt and ":" in fmt:
                # 找 f"{character_id}:{session_id}" 形态：两个插值被冒号相连
                consts = [v.value for v in node.values if isinstance(v, ast.Constant)]
                names = [
                    ast.unparse(v.value) for v in node.values if isinstance(v, ast.FormattedValue)
                ]
                if any((c or "") == ":" for c in consts) and any(
                    "character_id" in n for n in names
                ) and any("session_id" in n for n in names):
                    offenders.append((node.lineno, fmt))
    assert not offenders, (
        "请求级画像 ID 必须委托 persona_extractor.fusion.profile_scope 唯一构造器，"
        f"发现手拼作用域键: {offenders}"
    )
    src = _ORCH.read_text(encoding="utf-8")
    assert "profile_scope" in src, "optimized_orchestrator 未引用 profile_scope"


def test_register_flow_tests_sandbox_characters_dir():
    """凡真实调用 register-invite 的测试文件必须沙箱 CHARACTERS_DIR（防再污染真卡目录）。

    背景：注册分发钩子（W12 阶段2/W16）经 `_save_character` 写 CHARACTERS_DIR；
    test_invite_codes 未沙箱时每轮回归向 config/characters/ 净增克隆卡
    （gitignored、git 不可见、随回归累积）。
    """
    offenders = []
    for f in sorted(pathlib.Path("tests").rglob("test_*.py")):
        text = f.read_text(encoding="utf-8")
        if "register-invite" not in text:
            continue
        launches_app = any(k in text for k in ("create_api_app", "TestClient", "AsyncClient"))
        if launches_app and "CHARACTERS_DIR" not in text:
            offenders.append(f.name)
    assert not offenders, f"注册流测试缺少卡目录沙箱: {offenders}"
