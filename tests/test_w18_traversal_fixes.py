"""2026-09-28 历遍批：三条"随带观察项"缺陷收口（红先行）。

覆盖：
1. shisi 好感度手动面（GET/update/decay）与 W14 unlocks 同口径支持
   ``user_id`` 隔离键（不带 = 旧裸键兼容，带上 = 只读写 user::character）。
2. ``DELETE /api/shisi/characters/{id}`` 不再谎报「删除成功」——只移除
   运行态副本，回执必须如实指明权威卡真源不受影响（与 PUT 410 先例同语义族）。
3. orchestrator 请求级画像 ID 禁止再手拼 ``f"{character_id}:{session_id}"``，
   必须委托唯一构造器 ``persona_extractor.fusion.profile_scope``（AST 静态防护）。
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ────────────────────────────────────────────────────────────────
#  夹具：只挂 shisi affinity/character 两面，绕开全量 registry 装配
# ────────────────────────────────────────────────────────────────

@pytest.fixture()
def affinity_client(tmp_path):
    from shisi.affinity.enhancer import AffinityEnhancer
    from shisi.api import affinity_routes

    enh = AffinityEnhancer(db_path=str(tmp_path / "affinity.db"))
    affinity_routes.set_enhancer(enh)
    app = FastAPI()
    app.include_router(affinity_routes.router)
    return TestClient(app), enh


def test_affinity_get_with_user_id_uses_isolation_key(affinity_client):
    client, enh = affinity_client
    enh.update("c1", 30.0, reason="seed", user_id="uA")
    r = client.get("/api/shisi/affinity/c1", params={"user_id": "uA"})
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(enh.get_value("c1", user_id="uA"))
    assert r.json()["data"]["affinity"] != pytest.approx(enh.get_value("c1"))


def test_affinity_update_with_user_id_writes_isolation_key(affinity_client):
    client, enh = affinity_client
    r = client.post(
        "/api/shisi/affinity/c2/update",
        params={"user_id": "uB"},
        json={"delta": 12.0, "reason": "manual", "source": "test"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["affinity"] == pytest.approx(enh.get_value("c2", user_id="uB"))
    # 裸角色键不得被这次带用户的写触碰
    assert enh.get_value("c2") == pytest.approx(enh.get_value("c2", user_id=""))


def test_affinity_decay_with_user_id_targets_isolation_key(affinity_client):
    client, enh = affinity_client
    enh.update("c3", 80.0, reason="bare-seed")            # 裸角色键
    enh.update("c3", 50.0, reason="user-seed", user_id="uC")  # 隔离键
    r = client.post("/api/shisi/affinity/c3/decay", params={"user_id": "uC"})
    assert r.status_code == 200
    body = r.json()["data"]
    # 回执读数必须来自隔离键（≤ 种子 50），且本次衰减不得触碰裸键
    assert body["affinity"] == pytest.approx(enh.get_value("c3", user_id="uC"))
    assert body["affinity"] <= 50.0
    assert enh.get_value("c3") == pytest.approx(80.0)


# ────────────────────────────────────────────────────────────────


@pytest.fixture()
def character_client():
    from shisi.api import character_routes

    app = FastAPI()
    app.include_router(character_routes.router)
    # manager 由 registry 注入；此处给一个最小替身
    class _Mgr:
        def __init__(self):
            self._cards = {"hero": {"name": "hero"}}

        def delete_character(self, cid: str) -> bool:
            return self._cards.pop(cid, None) is not None

    character_routes._manager = _Mgr()
    return TestClient(app)


def test_shisi_delete_character_no_false_deleted_claim(character_client):
    r = character_client.delete("/api/shisi/characters/hero")
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
