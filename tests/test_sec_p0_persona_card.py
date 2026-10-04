"""P0 收尾批 F5：PUT persona-card 非法内容必须 400（非 500）。

缺陷：``api/routers/persona_card_routes.py::update_persona_card`` 只捕获
pydantic 校验（``CharaCardV2.model_validate``），随后**裸调**
``char_mgr.update_character``——manager 内 ``validate_card`` 不过时抛
shisi ``ValidationError``，逃逸到全局 500 handler。与
``api/routers/character_routes._validate_card_or_400`` 的统一 400 出口
同语义族（写路径安全校验失败一律 400，不落盘、不半写真源）。

环境纪律（沿 tests/test_w8_card_write_paths.py 同款）：真源目录与
sqlite store 全部钉 tmp_path；无 Bearer 走机器面（require_character_access
不干预），聚焦 handler 内的异常转译契约。
"""

from __future__ import annotations

import copy
import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shisi.character.manager import CharacterManager
from shisi.character.models import CharaCardV2
from shisi.character.store import CharacterStore
from shisi.migrations import run_migrations

_CARD: dict[str, Any] = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "f5卡",
        "description": "她是 f5 卡，初始描述。",
        "personality": "温和",
        "scenario": "",
        "first_mes": "早。",
        "mes_example": "",
        "creator_notes": "",
        "tags": [],
    },
}


def test_persona_card_put_injection_content_400_not_500(tmp_path, monkeypatch):
    """携带注入内容的合法 JSON 卡：pydantic 层放行、安全校验层拦截。

    修复前：manager 内 validate_card 抛 shisi ValidationError → 全局 500；
    修复后：路由捕获转 400，且真源文件不被半写。
    """
    import api.routers.character_routes as app_chars
    import api.routers.persona_card_routes as card_routes

    chars_dir = tmp_path / "config" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "f5.db"
    run_migrations(db)
    mgr = CharacterManager(store=CharacterStore(str(db)))
    mgr.initialize()
    cid = mgr.store.save_character(
        CharaCardV2.model_validate(copy.deepcopy(_CARD))
    )

    monkeypatch.setattr(app_chars, "CHARACTERS_DIR", chars_dir)
    # 处理器内是惰性 import（from api.routers.character_routes import …），
    # 打桩必须落在被引模块上（与 test_w8 同款注释纪律）
    monkeypatch.setattr(app_chars, "_invalidate_knowledge_index", lambda *_a, **_k: None)

    class _Reg:
        character_manager = mgr

    class _Deps:
        shisi_reg = _Reg()
        orch = None

    monkeypatch.setattr(card_routes, "deps", _Deps())

    payload = copy.deepcopy(_CARD)
    # pydantic 校验合法（纯字符串），仅 shisi 安全校验（注入/XSS 模式）拦截
    payload["data"]["description"] = "正常开头 <script>alert(1)</script> 注入尾巴"

    client = TestClient(FastAPI())
    client.app.include_router(card_routes.router)
    resp = client.put(f"/api/characters/{cid}/persona-card", json={"card": payload})

    assert resp.status_code == 400, (
        f"非法内容必须 400（安全校验失败统一出口），实得 {resp.status_code}: {resp.text[:200]}"
    )
    # 错误信息如实指出安全校验未过（与 _validate_card_or_400 文案同族）
    assert "安全校验" in resp.json()["detail"], resp.json()["detail"]
    # 不半写真源：拦截发生在落盘之前
    assert not (chars_dir / f"{cid}.json").exists(), "安全校验失败不得写真源文件"


def test_persona_card_put_valid_content_still_200(tmp_path, monkeypatch):
    """语义不变量：合法内容 PUT 照常 200 双写（收口不得误伤正常写路径）。"""
    import api.routers.character_routes as app_chars
    import api.routers.persona_card_routes as card_routes

    chars_dir = tmp_path / "config" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "f5ok.db"
    run_migrations(db)
    mgr = CharacterManager(store=CharacterStore(str(db)))
    mgr.initialize()
    cid = mgr.store.save_character(
        CharaCardV2.model_validate(copy.deepcopy(_CARD))
    )

    monkeypatch.setattr(app_chars, "CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr(app_chars, "_invalidate_knowledge_index", lambda *_a, **_k: None)

    class _Reg:
        character_manager = mgr

    class _Deps:
        shisi_reg = _Reg()
        orch = None

    monkeypatch.setattr(card_routes, "deps", _Deps())

    payload = copy.deepcopy(_CARD)
    payload["data"]["description"] = "合法改写后的描述。"

    client = TestClient(FastAPI())
    client.app.include_router(card_routes.router)
    resp = client.put(f"/api/characters/{cid}/persona-card", json={"card": payload})

    assert resp.status_code == 200, resp.text
    written = json.loads((chars_dir / f"{cid}.json").read_text(encoding="utf-8"))
    assert written["description"] == "合法改写后的描述。"
