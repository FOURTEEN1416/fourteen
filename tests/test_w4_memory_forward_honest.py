"""W4 · 跨角色转发的诚实语义（缺陷 F / 包任务 6）。

钉住：
1. 两个 API 面的 POST 转发改为 **501 先于任何写动作**——`memory_forwards`
   落一行后目标角色从未有任何读取路径（无 GET 端点、对话/检索链零消费者），
   旧响应却回 `forwarded` / `success:true`，属「假接线」家族（与
   `shisi/api/memory_routes.py` 删除面 501 同法）；
2. detail 必须说明真缺口（目标侧无消费链），不得是空口拒绝；
3. 反证：501 不落半写态——`memory_forwards` 保持 0 行；
4. 端点路径保持不变（路由契约不塌），仅语义转诚实。

隔离：ForwardManager 与迁移库全部指向 tmp_path，不触生产 data/。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routers.memory_routes as unified
from api.auth import verify_api_key_dep
from shisi.api.registry import setup_shisi
from shisi.memory.forward_manager import ForwardManager
from shisi.migrations import run_migrations


def _count_rows(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM memory_forwards").fetchone()[0]
    finally:
        conn.close()


# ── 旧 shisi API 面 ─────────────────────────────────────


@pytest.fixture
def legacy_client(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    app = FastAPI()
    setup_shisi(app, run_migrate=False, db_path=db)
    return TestClient(app), db


def test_legacy_forward_501_before_any_write(legacy_client):
    client, db = legacy_client
    assert _count_rows(db) == 0
    resp = client.post(
        "/api/shisi/memory/forward",
        json={"from_character": "c1", "to_character": "c2", "memory_id": "m1"},
    )
    assert resp.status_code == 501, (
        f"目标侧无消费链的转发必须 501（旧回 success:true=谎报），实得 {resp.status_code}"
    )
    assert _count_rows(db) == 0, "501 必须先于任何写动作，不得留半写行"
    detail = str(resp.json().get("detail", ""))
    assert "消费" in detail or "目标侧" in detail, f"detail 必须说明真缺口，实得 {detail!r}"


# ── 统一 API 面（桥接 ForwardManager） ──────────────────


@pytest.fixture
def unified_client(tmp_path, monkeypatch):
    db = tmp_path / "unified.db"
    run_migrations(db)

    class _Reg:
        forward_manager = ForwardManager(db_path=db)

    monkeypatch.setattr(unified.deps, "shisi_reg", _Reg(), raising=True)
    app = FastAPI()
    app.include_router(unified.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    yield TestClient(app), db


def test_unified_forward_501_and_zero_write(unified_client):
    client, db = unified_client
    resp = client.post(
        "/api/characters/c1/favorites/forward",
        json={"to_character": "c2", "memory_id": "m1", "content": "转发内容"},
    )
    assert resp.status_code == 501, (
        f"统一面转发同样必须 501 先于写（旧回 forwarded=谎报），实得 {resp.status_code}"
    )
    assert _count_rows(db) == 0, "501 必须先于任何写动作"
    detail = str(resp.json().get("detail", ""))
    assert "消费" in detail or "目标侧" in detail, f"detail 必须说明真缺口，实得 {detail!r}"
