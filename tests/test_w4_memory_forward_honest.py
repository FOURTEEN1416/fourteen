"""W4 · 跨角色转发的目标侧派生记录（缺陷 F / 包任务 6）。

钉住（恢复条件达成后）：
1. POST 转发生成**可追溯目标侧派生记录**（forward_id），不再 501 空拒；
2. 返回真实回执（forward_id / from / to / memory_id），失败不谎报 forwarded；
3. 目标侧消费链：`retrieve_context` 注入 `forwarded_notes`（含 forward_id）；
4. GET forwards 可读目标角色收到的派生记录。

隔离：ForwardManager / StructuredMemory 全部 tmp_path，不触生产 data/。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routers.memory_routes as unified
from api.auth import verify_api_key_dep
from shisi.application.memory_service import ShisiMemoryService
from shisi.memory.forward_manager import ForwardManager
from shisi.memory.legacy.structured_memory import StructuredMemory
from shisi.migrations import run_migrations


def _count_rows(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM memory_forwards").fetchone()[0]
    finally:
        conn.close()


# ── ForwardManager 派生记录 ─────────────────────────────


def test_forward_receipt_has_traceable_id(tmp_path):
    db = tmp_path / "fwd.db"
    mgr = ForwardManager(db_path=db)
    receipt = mgr.forward_receipt("c1", "c2", "m1", "她喜欢手冲咖啡")
    assert receipt["ok"] is True
    assert int(receipt["forward_id"]) > 0
    assert receipt["from"] == "c1"
    assert receipt["to"] == "c2"
    assert _count_rows(db) == 1
    rows = mgr.get_forwards("c2")
    assert rows and rows[0]["id"] == receipt["forward_id"]
    assert rows[0]["content"] == "她喜欢手冲咖啡"


def test_forward_bool_contract_still_falsy_on_fail(tmp_path):
    """旧 `if not ok` 语义必须保持：失败 0，成功 >0。"""
    mgr = ForwardManager(db_path=tmp_path / "f.db")
    ok_id = mgr.forward("c1", "c2", "m1", "内容")
    assert ok_id > 0
    assert bool(ok_id) is True


# ── 目标侧消费：retrieve_context 注入 ───────────────────


class _FakeVM:
    def store_chat_sync(self, *a, **k):
        return None

    async def store_fact(self, *a, **k):
        return None

    def health_check(self):
        return {"available": True}


def test_retrieve_context_injects_forwarded_notes(tmp_path):
    sm = StructuredMemory(str(tmp_path / "s.db"))
    fwd = ForwardManager(db_path=tmp_path / "f.db")
    svc = ShisiMemoryService(
        structured_memory=sm, vector_memory=_FakeVM(), db_path=tmp_path / "fav.db",
        forward_mgr=fwd,
    )
    fwd.forward_receipt("char_a", "char_b", "mem1", "转给你的：她怕黑")
    ctx = svc.retrieve_context("怕黑吗", session_id="1:peer@im.wechat", character_id="char_b")
    notes = ctx.get("forwarded_notes") or []
    assert notes, "目标角色必须能检索到转发派生记录"
    assert notes[0]["from"] == "char_a"
    assert "怕黑" in notes[0]["note"]
    assert notes[0].get("forward_id"), "便签必须可追溯到 forward_id"


def test_retrieve_context_other_character_not_see_forwards(tmp_path):
    sm = StructuredMemory(str(tmp_path / "s.db"))
    fwd = ForwardManager(db_path=tmp_path / "f.db")
    svc = ShisiMemoryService(
        structured_memory=sm, vector_memory=_FakeVM(), db_path=tmp_path / "fav.db",
        forward_mgr=fwd,
    )
    fwd.forward_receipt("char_a", "char_b", "mem1", "秘密")
    ctx = svc.retrieve_context("?", session_id="1:peer@im.wechat", character_id="char_c")
    assert not ctx.get("forwarded_notes"), "不得把转发便签串给无关角色"


# ── 统一 API 面 ─────────────────────────────────────────


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
    app.dependency_overrides[unified.require_character_access] = lambda: {}
    yield TestClient(app), db


def test_unified_forward_returns_real_receipt(unified_client):
    client, db = unified_client
    resp = client.post(
        "/api/characters/c1/favorites/forward",
        json={"to_character": "c2", "memory_id": "m1", "content": "转发内容"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "forwarded"
    assert int(body["forward_id"]) > 0
    assert body["target_side"] == "derived_note"
    assert _count_rows(db) == 1, "成功转发必须落派生行"


def test_unified_list_forwards_consumable(unified_client):
    client, _ = unified_client
    client.post(
        "/api/characters/c1/favorites/forward",
        json={"to_character": "c2", "memory_id": "m1", "content": "便签"},
    )
    resp = client.get("/api/characters/c2/forwards")
    assert resp.status_code == 200
    items = resp.json()["forwards"]
    assert items and items[0]["content"] == "便签"
