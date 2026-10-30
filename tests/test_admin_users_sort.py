"""用户列表全局排序（NEW-22）— 后端 sort / sort_dir 白名单参数的行为测试。

自带**独立 fixture**（名字 `sort_app`）：既不 import 也不复用 tests/test_admin_ops.py
或 tests/test_admin_routes_last_admin.py 的任何夹具，与它们返回值形状无关；
本文件 yield (client, session_factory)，另用 `_read_db_rows` 直读 DB 列值作为断言真源。

覆盖：
① 七个排序键 × asc/desc：响应里的列值比对 DB 列值，且全量结果单调有序（不是只排当前页）；
② 跨页：page_size=2 取 3 页拼接 == 一次性全量排序；
③ 非法 sort / sort_dir → 422；源码不用动态取列属性（注入面）；
④ 不带参 = created_at desc（与改动前一致）；相同 created_at 的行以 id desc 作次键，翻页无重无缺。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.app_factory import create_api_app
from api.auth_jwt import get_current_user_id, hash_password
from api.database import Base, User, get_db

# 请求方（id=1 的行必须是 active admin，require_role 从库内现值校验角色）
_ADMIN_ID = 1
_ADMIN_PASSWORD = "admin123"

SORT_FIELDS = ("id", "email", "username", "display_name", "role", "is_active", "created_at")

_TIE_CREATED_AT = datetime(2026, 1, 3, 3, 0, 0)

# 6 行种子：email / username / display_name 全异；role、is_active、created_at 刻意并列，
# 其中 id=2/3/5 三行 created_at 完全相同 —— 用于验「id 作确定性次键」。
_SEED: tuple[tuple[int, str, str, str, str, bool, datetime], ...] = (
    (1, "aaron@example.com", "zeta", "阿尔法", "admin", True, datetime(2026, 1, 2, 3, 0, 0)),
    (2, "bravo@example.com", "yankee", "贝塔", "viewer", False, _TIE_CREATED_AT),
    (3, "charlie@example.com", "xray", "伽马", "editor", True, _TIE_CREATED_AT),
    (4, "delta@example.com", "whiskey", "德尔塔", "viewer", True, datetime(2026, 1, 4, 3, 0, 0)),
    (5, "echo@example.com", "victor", "艾普西龙", "editor", False, _TIE_CREATED_AT),
    (6, "foxtrot@example.com", "uniform", "泽塔", "admin", True, datetime(2026, 1, 1, 3, 0, 0)),
)


# ═══════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def sort_app(tmp_path):
    """返回 (client, session_factory)；请求方固定为 id=1 的 active admin。"""
    db_path = tmp_path / "test_admin_users_sort.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    hashed = hash_password(_ADMIN_PASSWORD)

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add_all([
                User(
                    id=uid,
                    email=email,
                    username=username,
                    hashed_password=hashed,
                    display_name=display_name,
                    role=role,
                    is_active=is_active,
                    is_verified=True,
                    created_at=created_at,
                )
                for uid, email, username, display_name, role, is_active, created_at in _SEED
            ])
            await session.commit()

    asyncio.run(_init())
    # 初始化用的是独立的临时事件循环：立刻回池，避免把该循环里的连接带进用例循环
    asyncio.run(engine.dispose())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user_id] = lambda: _ADMIN_ID

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, session_factory

    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════


async def _read_db_rows(session_factory) -> dict[int, dict[str, Any]]:
    """直读 DB 列值（断言真源，非渲染文本）。"""
    async with session_factory() as session:
        rows = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return {u.id: {f: getattr(u, f) for f in SORT_FIELDS} for u in rows}


def _api_value(field: str, item: dict[str, Any]) -> Any:
    raw = item[field]
    if field == "created_at":
        return datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    return raw


def _db_value(field: str, raw: Any) -> Any:
    if field == "created_at":
        return raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
    return raw


def _assert_monotonic(values: list[Any], direction: str) -> None:
    for left, right in zip(values, values[1:], strict=False):
        if direction == "asc":
            assert left <= right, f"asc 全局顺序被破坏: {values}"
        else:
            assert left >= right, f"desc 全局顺序被破坏: {values}"


async def _fetch_ids(client, **params: Any) -> list[int]:
    resp = await client.get("/api/admin/users", params=params)
    assert resp.status_code == 200, resp.text
    return [u["id"] for u in resp.json()["users"]]


# ═══════════════════════════════════════════════════════════
# ① 七列 × 双方向：全局排序 + 值取自 DB 列
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("field", SORT_FIELDS)
async def test_each_sort_field_orders_globally(sort_app, field, direction):
    client, session_factory = sort_app
    db_rows = await _read_db_rows(session_factory)

    resp = await client.get(
        "/api/admin/users",
        params={"page_size": 100, "sort": field, "sort_dir": direction},
    )
    assert resp.status_code == 200, resp.text
    users = resp.json()["users"]

    # 全量覆盖（不是只排当前页）
    assert {u["id"] for u in users} == set(db_rows)
    # 每条响应的该列值 == DB 列值
    for u in users:
        assert _api_value(field, u) == _db_value(field, db_rows[u["id"]][field]), f"{field} 列值应取自 DB"
    # 顺序单调（允许并列）
    _assert_monotonic([_api_value(field, u) for u in users], direction)


# ═══════════════════════════════════════════════════════════
# ①b 验收原句：page_size=2 下 sort=email 的首条即全局极值
# ═══════════════════════════════════════════════════════════


async def test_email_sort_first_row_is_global_extreme(sort_app):
    client, _ = sort_app
    asc = await client.get(
        "/api/admin/users", params={"sort": "email", "sort_dir": "asc", "page_size": 2}
    )
    assert asc.status_code == 200, asc.text
    assert [u["email"] for u in asc.json()["users"]] == ["aaron@example.com", "bravo@example.com"]

    desc = await client.get(
        "/api/admin/users", params={"sort": "email", "sort_dir": "desc", "page_size": 2}
    )
    assert desc.status_code == 200, desc.text
    assert [u["email"] for u in desc.json()["users"]] == ["foxtrot@example.com", "echo@example.com"]


# ═══════════════════════════════════════════════════════════
# ② 跨页拼接 == 一次性全量排序
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("field", "direction"),
    [("email", "asc"), ("role", "desc"), ("username", "asc"), ("created_at", "asc")],
)
async def test_cross_page_order_matches_full_sort(sort_app, field, direction):
    client, _ = sort_app
    full_ids = await _fetch_ids(
        client, page_size=100, sort=field, sort_dir=direction
    )
    assert len(full_ids) == len(_SEED)

    paged: list[int] = []
    for page in (1, 2, 3):
        resp = await client.get(
            "/api/admin/users",
            params={"page_size": 2, "page": page, "sort": field, "sort_dir": direction},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page"] == page
        assert body["page_size"] == 2
        paged.extend(u["id"] for u in body["users"])

    assert paged == full_ids, "跨页顺序必须与一次性全量排序完全一致（不再只排当前页）"


# ═══════════════════════════════════════════════════════════
# ③ 非法参数 422 + 源码无动态取列
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "1;DROP TABLE users"},
        {"sort": "id desc"},
        {"sort": "hashed_password"},
        {"sort": ""},
        {"sort_dir": "ascending"},
        {"sort_dir": "DESC"},
    ],
)
async def test_invalid_sort_params_rejected(sort_app, params):
    client, session_factory = sort_app
    resp = await client.get("/api/admin/users", params=params)
    assert resp.status_code == 422, resp.text
    # 库未被触碰（注入尝试没有生效面）
    assert len(await _read_db_rows(session_factory)) == len(_SEED)


def test_sort_columns_are_explicit_literals():
    """排序列必须是显式字面量映射——禁止按用户输入动态取属性或拼列名（注入面）。"""
    src = (Path(__file__).resolve().parents[1] / "api" / "routers" / "admin_routes.py").read_text(
        encoding="utf-8"
    )
    assert "getattr(User" not in src, "排序列必须查白名单表，禁止动态取列属性"
    assert "_USER_SORT_COLUMNS[sort]" in src, "排序必须走白名单查表"
    for field in SORT_FIELDS:
        assert f'"{field}": User.{field}' in src, f"白名单缺列映射: {field}"


# ═══════════════════════════════════════════════════════════
# ④ 默认 created_at desc（改动前口径）+ 次键 id desc
# ═══════════════════════════════════════════════════════════


async def test_default_order_is_created_at_desc(sort_app):
    client, session_factory = sort_app
    db_rows = await _read_db_rows(session_factory)

    default_ids = await _fetch_ids(client, page_size=100)
    assert len(default_ids) == len(_SEED)

    # 期望顺序由 DB 列值推出：created_at desc，同值以 id desc 兜底
    expected = [
        r["id"]
        for r in sorted(
            db_rows.values(), key=lambda r: (r["created_at"], r["id"]), reverse=True
        )
    ]
    assert default_ids == expected, "不带 sort/sort_dir 时行序仍为 created_at desc"

    # 与显式参数完全等价（默认值没有偷偷改变语义）
    explicit_ids = await _fetch_ids(client, page_size=100, sort="created_at", sort_dir="desc")
    assert explicit_ids == default_ids


async def test_tied_created_at_pages_use_id_desc_and_never_duplicate(sort_app):
    client, session_factory = sort_app
    db_rows = await _read_db_rows(session_factory)

    tie_ids = sorted(
        uid for uid, row in db_rows.items() if _db_value("created_at", row["created_at"]) == _TIE_CREATED_AT
    )
    # 种子显式写入同 created_at 的三行（不依赖默认值碰巧相等）
    assert tie_ids == [2, 3, 5]

    collected: list[int] = []
    for page in (1, 2, 3):
        resp = await client.get(
            "/api/admin/users",
            params={"page_size": 2, "page": page, "sort": "created_at", "sort_dir": "desc"},
        )
        assert resp.status_code == 200, resp.text
        collected.extend(u["id"] for u in resp.json()["users"])

    assert len(collected) == len(set(collected)) == len(db_rows), "翻页无重复、无缺行"
    tie_in_response = [uid for uid in collected if uid in tie_ids]
    assert tie_in_response == [5, 3, 2], "同 created_at 的行以 id desc 作确定性次键"
