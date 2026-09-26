"""W8 · 角色卡写入面与画像控制面回归（缺陷 B / E / J / D）。

全部使用临时数据根与合成卡；不写真实 `config/characters`、不读真实卡正文。
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shisi.api.registry import setup_shisi
from shisi.character.models import CharaCardV2, CharacterData
from shisi.config import reset_config
from shisi.migrations import run_migrations

CARD_V2: dict[str, Any] = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "写入面甲",
        "description": "她是写入面甲，初始描述。",
        "personality": "温和",
        "scenario": "",
        "first_mes": "早。",
        "mes_example": "",
        "creator_notes": "只回一句。",
        "tags": [],
    },
}


@pytest.fixture(autouse=True)
def _reset_shisi_config():
    reset_config()


@pytest.fixture
def shisi_client(tmp_path):
    """隔离的 shisi API 面（临时 db，不触生产库）。"""
    db = tmp_path / "shisi.db"
    run_migrations(db)
    app = FastAPI()
    reg = setup_shisi(app, run_migrate=False, db_path=db)
    return TestClient(app), reg


def _create_card(manager) -> str:
    card = CharaCardV2.model_validate(copy.deepcopy(CARD_V2))
    return manager.store.save_character(card)


# ── 缺陷 B：旧 shisi 人设 PUT 只写 SQLite 副本却谎报「立即生效」────


def test_legacy_sqlite_is_a_copy_not_the_authority(shisi_client) -> None:
    """前置事实：SQLite `characters` 表不是生成侧读的权威真源。

    生成侧（`PersonaService._load_character_card`）读 `config/characters/*.json`；
    旧 PUT 只 UPDATE SQLite 并返回「人设已更新，对话中立即生效」——下一轮对话
    仍用文件里的旧人设，且下次从真源重载即静默回滚用户改动。
    """
    _, reg = shisi_client
    cid = _create_card(reg.character_manager)
    stored = reg.store.get_character(cid) if hasattr(reg, "store") else reg.character_manager.store.get_character(cid)
    assert stored is not None and isinstance(stored.data, CharacterData)
    assert stored.data.description == "她是写入面甲，初始描述。"


@pytest.mark.parametrize(
    "path",
    [
        "/api/shisi/persona/characters/{cid}",
        "/api/shisi/characters/{cid}",
    ],
)
def test_legacy_card_put_is_deprecated_not_falsely_effective(shisi_client, path) -> None:
    """旧卡 PUT 必须显式作废（410）并指向真源入口，不得再谎报即时生效。"""
    client, reg = shisi_client
    cid = _create_card(reg.character_manager)
    payload = copy.deepcopy(CARD_V2)
    payload["data"]["description"] = "被旧 PUT 改写的描述。"

    resp = client.put(path.format(cid=cid), json={"card": payload})

    assert resp.status_code == 410, (
        f"{path}: 期望 410 显式作废，实得 {resp.status_code}"
        "（200+『立即生效』即为谎报，因该写路径不触权威真源）"
    )
    body = resp.json()
    detail = str(body.get("detail", body))
    assert "/api/characters/" in detail, "作废响应必须指出可用的真源入口"


def test_legacy_card_put_does_not_write_the_copy(shisi_client) -> None:
    """410 必须在写副本之前发生：不能出现「副本改了、真源没改」的半写态。"""
    client, reg = shisi_client
    cid = _create_card(reg.character_manager)
    before = reg.character_manager.load_character(cid)
    payload = copy.deepcopy(CARD_V2)
    payload["data"]["description"] = "半写态探针。"

    resp = client.put(f"/api/shisi/characters/{cid}", json={"card": payload})
    assert resp.status_code == 410

    after = reg.character_manager.load_character(cid)
    assert after is not None and before is not None
    assert after.data.description == before.data.description


def test_legacy_card_get_still_works(shisi_client) -> None:
    """只作废写路径；读路径（旧控制面回显）保持可用。"""
    client, reg = shisi_client
    cid = _create_card(reg.character_manager)
    resp = client.get(f"/api/shisi/persona/characters/{cid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["data"]["name"] == "写入面甲"


def test_canonical_card_put_writes_authority_source(tmp_path, monkeypatch) -> None:
    """可用入口的既有语义（反证，不重复报告）：persona-card PUT 双写并失效缓存。

    把真源目录指向临时根，确认这条路径才是「改完就生效」的正解。
    """
    import api.routers.character_routes as app_chars
    import api.routers.persona_card_routes as card_routes

    chars_dir = tmp_path / "config" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    cid = "w8canon"
    flat = {
        "id": cid,
        "name": "写入面甲",
        "description": "她是写入面甲，初始描述。",
        "personality": {"warmth": 0.4},
    }
    (chars_dir / f"{cid}.json").write_text(json.dumps(flat, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(app_chars, "CHARACTERS_DIR", chars_dir)
    # 处理器内是**惰性 import**（`from api.routers.character_routes import …`），
    # 所以要打在被引模块上而不是 persona_card_routes 命名空间。
    monkeypatch.setattr(app_chars, "_invalidate_knowledge_index", lambda *_a, **_k: None)

    class _Mgr:
        def __init__(self) -> None:
            self.saved: list[Any] = []

        def update_character(self, character_id: str, card: Any) -> bool:
            self.saved.append((character_id, card))
            return True

    class _Reg:
        character_manager = _Mgr()

    class _Deps:
        shisi_reg = _Reg()
        orch = None

    monkeypatch.setattr(card_routes, "deps", _Deps())

    payload = copy.deepcopy(CARD_V2)
    payload["data"]["description"] = " canonical 改写后的描述。"
    client = TestClient(FastAPI())
    client.app.include_router(card_routes.router)
    resp = client.put(f"/api/characters/{cid}/persona-card", json={"card": payload})
    assert resp.status_code == 200

    written = json.loads((chars_dir / f"{cid}.json").read_text(encoding="utf-8"))
    assert written["description"] == " canonical 改写后的描述。"
    assert written["id"] == cid, "扩展字段不得丢失"
