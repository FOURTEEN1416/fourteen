"""W4 · 记忆控制台唯一真源回归（缺陷 E）。

钉住：
1. `data/character_memory/{cid}.json` 是第二记忆真源——生成侧从不读取它，
   旧 POST/DELETE 却返回「created/deleted」：写进去的事实下一轮 prompt
   永远不出现，删掉也拦不住真记忆里的复现。三条写面必须在**任何写动作
   之前** 410 作废（与 8b83b4f 卡写入面同法），detail 指向唯一写权威
   （会话 remember_facts/forget_facts → ShisiMemoryService 真实回执）；
2. GET 必须回读唯一真源 `user_facts`（按 misc_routes 归属谓词限定本人），
   控制台展示的记忆条数 = 下一轮 prompt 真正会用的记忆；
3. 旧 JSON 面不可用（orch._memory 缺席）时 GET 返回空集，不得伪造；
4. 反证：410 不落任何半写态（逐字节不变）。

隔离：临时 `MEMORY_FACTS_DIR` + 临时 SQLite；不触生产 data/。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routers.character_routes as cr
import api.routers.misc_routes as misc
from api.auth import verify_api_key_dep
from shisi.memory.legacy.structured_memory import StructuredMemory

LEGACY_FACT = {"id": "abcd1234", "content": "手动写进旧JSON面的事实", "category": "general"}


@pytest.fixture
def legacy_dir(tmp_path, monkeypatch):
    d = tmp_path / "character_memory"
    d.mkdir()
    (d / "hero.json").write_text(json.dumps([LEGACY_FACT], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cr, "MEMORY_FACTS_DIR", d)
    return d


@pytest.fixture
def client(tmp_path, monkeypatch, legacy_dir):
    """真实 user_facts 落在临时库；归属谓词钉成 user 7（本人范围）。"""
    sm = StructuredMemory(str(tmp_path / "facts.sqlite"))
    sm.add_fact("用户生日是腊月初一", category="personal", user_key="7:peer@im.wechat")
    sm.add_fact("隔壁用户喜欢猫", category="preference", user_key="9:peer@im.wechat")

    class _Mem:
        structured_memory = sm

    class _Orch:
        _memory = _Mem()

    monkeypatch.setattr(cr.deps, "orch", _Orch())
    monkeypatch.setattr(misc, "_memory_scope_prefix", lambda request: "7:")
    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    c = TestClient(app)
    yield c
    sm.close()


# ── 1. 三条假写面显式作废，且不落半写态 ──────────────────


def test_post_fact_deprecated_before_any_write(client, legacy_dir):
    before = (legacy_dir / "hero.json").read_bytes()
    resp = client.post(
        "/api/characters/hero/memory/facts",
        json={"content": "控制台手动加一条事实", "category": "personal", "tags": []},
    )
    assert resp.status_code == 410, (
        f"期望 410（旧写面只写生成侧永不读取的 JSON，201=谎报），实得 {resp.status_code}"
    )
    assert (legacy_dir / "hero.json").read_bytes() == before, "410 必须先于写动作"
    detail = str(resp.json().get("detail", ""))
    assert "remember_facts" in detail, "作废响应必须指向唯一写权威入口"


def test_delete_fact_deprecated(client, legacy_dir):
    before = (legacy_dir / "hero.json").read_bytes()
    resp = client.delete("/api/characters/hero/memory/facts/abcd1234")
    assert resp.status_code == 410
    assert (legacy_dir / "hero.json").read_bytes() == before


def test_clear_memory_deprecated(client, legacy_dir):
    resp = client.delete("/api/characters/hero/memory")
    assert resp.status_code == 410
    assert (legacy_dir / "hero.json").exists(), "作废端点不得顺手销毁遗留文件"


# ── 2. GET 回读唯一真源、按归属限定 ─────────────────────


def test_get_facts_reads_primary_source(client):
    resp = client.get("/api/characters/hero/memory/facts")
    assert resp.status_code == 200
    body = resp.json()
    facts = body["facts"]
    assert body["total"] == len(facts) == 1, (
        f"应只回读本人（前缀 7:）的真实事实：{facts}"
    )
    assert any("腊月初一" in str(f) for f in facts)
    # 旧 JSON 面的内容不再被冒充为记忆
    assert "手动写进旧JSON面的事实" not in json.dumps(facts, ensure_ascii=False)
    # 他人事实不得出现
    assert "隔壁用户喜欢猫" not in json.dumps(facts, ensure_ascii=False)


def test_get_facts_without_memory_backend_is_empty_not_fake(monkeypatch, legacy_dir):
    class _Orch:
        _memory = None

    monkeypatch.setattr(cr.deps, "orch", _Orch())
    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    resp = TestClient(app).get("/api/characters/hero/memory/facts")
    assert resp.status_code == 200
    assert resp.json() == {"facts": [], "total": 0}
