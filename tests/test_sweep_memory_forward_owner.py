"""sweep · PRIV-2 转发便签归属收口（红测先行）。

攻击面（finding PRIV-2，P1）：``POST /api/characters/{源卡}/favorites/forward``
的 ``req.to_character`` 此前零归属校验直 INSERT 全局 ``memory_forwards``
（表无 owner 列）——持 JWT 的 viewer 可向**他人私人卡**的转发便签表写入
任意 ≤300 字内容；该表已被 ``retrieve_context`` 挂进目标角色 context
（``forwarded_notes``），prompt 渲染层一旦接线即成跨用户存储型注入。

修复契约（与 ``character_routes.require_character_access`` /
``card_access_allowed`` 同一 owner、同一口径）：
1. 有 Bearer 主体时：目标卡不存在 / 无权一律 404（同文案防枚举）；
2. 公共目标卡（owner 为空）与源卡同口径（card_access_allowed 判定，不特判）；
3. 机器面（无 Bearer）不收窄——与 W1 归属体系机器面契约一致；
4. ``retrieve_context`` 的 forwarded_notes 挂载行为不回归（storage→context
   链不动）；ForwardManager 保持纯存储（仅 to_character 非空纵深，不引入认证）。

隔离纪律：角色真源目录（``CHARACTERS_DIR``）与 ForwardManager 落库全部
tmp_path，不触生产 ``config/characters/`` 与 ``data/``。
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routers.character_routes as char_routes
import api.routers.memory_routes as unified
from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.database import User
from shisi.memory.forward_manager import ForwardManager


def _write_card(chars_dir, cid: str, user_id: str) -> None:
    """写一张最小卡进 tmp 真源目录（user_id 决定归属：'default'=公共模板）。"""
    card = {"id": cid, "name": cid, "description": "sweep 卡", "user_id": user_id}
    (chars_dir / f"{cid}.json").write_text(json.dumps(card), encoding="utf-8")


def _principal(user_id: int, role: str) -> AuthPrincipal:
    """构造内存主体（不落库——归属判定只读 user_id/role）。"""
    user = User(
        id=user_id,
        email=f"u{user_id}@sweep.test",
        username=f"u{user_id}",
        hashed_password="x",
        display_name=f"u{user_id}",
        role=role,
        is_active=True,
        is_verified=True,
        token_version=0,
    )
    return AuthPrincipal(user_id=user_id, role=role, token_version=0, user=user)


def _count_rows(db_path) -> int:
    """统计 memory_forwards 行数；文件/表不存在 = 0（ForwardManager 懒建表）。"""
    if not Path(db_path).exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            row = conn.execute("SELECT COUNT(*) FROM memory_forwards").fetchone()
        except sqlite3.OperationalError:
            return 0  # 表未建 = 拦截发生在落库之前 = 零写入
        return int(row[0])
    finally:
        conn.close()


def _make_client(monkeypatch, tmp_path, principal: AuthPrincipal | None):
    """统一 API 面夹具：tmp 卡目录 + tmp 转发库 + 指定主体（None=机器面）。

    ``require_character_access`` **不 override**——源卡与目标卡都走 W1 真实
    归属链（get_optional_principal 被 override 后源/目标拿到同一主体）。
    """
    chars_dir = tmp_path / "config" / "characters"
    chars_dir.mkdir(parents=True)
    fwd_db = tmp_path / "fwd.db"

    monkeypatch.setattr(char_routes, "CHARACTERS_DIR", chars_dir)

    class _Reg:
        forward_manager = ForwardManager(db_path=fwd_db)

    monkeypatch.setattr(unified.deps, "shisi_reg", _Reg(), raising=True)

    app = FastAPI()
    app.include_router(unified.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True

    async def _optional_principal():
        return principal

    app.dependency_overrides[get_optional_principal] = _optional_principal
    return TestClient(app), chars_dir, fwd_db


# ── 攻击面实证（修复前必须红）────────────────────────────


def test_forward_to_private_card_of_other_user_404(tmp_path, monkeypatch):
    """viewer A 向他人私人卡转发：必须 404 且零落库（攻击面本体）。"""
    client, chars_dir, fwd_db = _make_client(monkeypatch, tmp_path, _principal(2, "viewer"))
    _write_card(chars_dir, "mine_a", "2")  # 源卡：A 自己的
    _write_card(chars_dir, "theirs_b", "3")  # 目标：他人 B 的私人卡
    resp = client.post(
        "/api/characters/mine_a/favorites/forward",
        json={"to_character": "theirs_b", "memory_id": "m1", "content": "注入payload"},
    )
    assert resp.status_code == 404, (
        f"他人私人卡必须 404，实得 {resp.status_code}: {resp.text[:200]}"
    )
    assert _count_rows(fwd_db) == 0, "拦截必须发生在落库之前（零写入）"


def test_forward_to_nonexistent_card_404(tmp_path, monkeypatch):
    """目标卡不存在：404 且与 require_character_access 同文案（防枚举）。"""
    client, chars_dir, fwd_db = _make_client(monkeypatch, tmp_path, _principal(2, "viewer"))
    _write_card(chars_dir, "mine_a", "2")
    resp = client.post(
        "/api/characters/mine_a/favorites/forward",
        json={"to_character": "ghost_id", "memory_id": "m1", "content": "x"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "角色不存在: ghost_id", "必须与 W1 同文案防状态码枚举"
    assert _count_rows(fwd_db) == 0, "目标不存在不得落死行"


def test_viewer_forward_to_public_card_same_policy_as_source(tmp_path, monkeypatch):
    """公共目标卡（owner 空）与源卡同口径：viewer 无权（对照 GET favorites 404）。"""
    client, chars_dir, fwd_db = _make_client(monkeypatch, tmp_path, _principal(2, "viewer"))
    _write_card(chars_dir, "mine_a", "2")
    _write_card(chars_dir, "public_c", "default")
    ref = client.get("/api/characters/public_c/favorites")
    assert ref.status_code == 404, "对照：源卡口径下 viewer 访问公共卡本就 404"
    resp = client.post(
        "/api/characters/mine_a/favorites/forward",
        json={"to_character": "public_c", "memory_id": "m1", "content": "x"},
    )
    assert resp.status_code == ref.status_code, "目标卡必须与源卡同口径，不得特判放行"
    assert _count_rows(fwd_db) == 0


# ── 语义不变量（修复前后都必须绿，防误伤）────────────────


def test_forward_to_own_card_ok(tmp_path, monkeypatch):
    """自己的卡正常转发：200 且落库（viewer 主链路不受收口误伤）。"""
    client, chars_dir, fwd_db = _make_client(monkeypatch, tmp_path, _principal(2, "viewer"))
    _write_card(chars_dir, "mine_a", "2")
    _write_card(chars_dir, "mine_b", "2")
    resp = client.post(
        "/api/characters/mine_a/favorites/forward",
        json={"to_character": "mine_b", "memory_id": "m1", "content": "正常转发"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "forwarded"
    assert int(resp.json()["forward_id"]) > 0
    assert _count_rows(fwd_db) == 1


def test_admin_forward_to_public_card_ok(tmp_path, monkeypatch):
    """admin 向公共目标卡转发放行（card_access_allowed 同口径）。"""
    client, chars_dir, fwd_db = _make_client(monkeypatch, tmp_path, _principal(1, "admin"))
    _write_card(chars_dir, "admin_src", "1")
    _write_card(chars_dir, "public_c", "default")
    resp = client.post(
        "/api/characters/admin_src/favorites/forward",
        json={"to_character": "public_c", "memory_id": "m1", "content": "x"},
    )
    assert resp.status_code == 200, resp.text
    assert _count_rows(fwd_db) == 1


def test_machine_face_forward_not_narrowed(tmp_path, monkeypatch):
    """机器面（无 Bearer 主体）契约不收窄：与 W1 全族一致，目标存在性不校验。"""
    client, chars_dir, fwd_db = _make_client(monkeypatch, tmp_path, None)
    resp = client.post(
        "/api/characters/machine_src/favorites/forward",
        json={"to_character": "anything", "memory_id": "m1", "content": "x"},
    )
    assert resp.status_code == 200, resp.text
    assert _count_rows(fwd_db) == 1


def test_retrieve_context_mount_unchanged(tmp_path):
    """回归钉：storage→context 挂载链不因归属收口被破坏（test_w4 同模式复钉）。"""
    from shisi.application.memory_service import ShisiMemoryService
    from shisi.memory.legacy.structured_memory import StructuredMemory

    class _FakeVM:
        def store_chat_sync(self, *a, **k):
            return None

        async def store_fact(self, *a, **k):
            return None

        def health_check(self):
            return {"available": True}

    sm = StructuredMemory(str(tmp_path / "s.db"))
    fwd = ForwardManager(db_path=tmp_path / "f.db")
    svc = ShisiMemoryService(
        structured_memory=sm,
        vector_memory=_FakeVM(),
        db_path=tmp_path / "fav.db",
        forward_mgr=fwd,
    )
    fwd.forward_receipt("char_a", "char_b", "mem1", "转给你的便签")
    ctx = svc.retrieve_context("q", session_id="1:peer@im.wechat", character_id="char_b")
    notes = ctx.get("forwarded_notes") or []
    assert notes, "目标角色必须仍能从 context 读到转发便签（挂载链不回归）"
    assert notes[0]["from"] == "char_a"
    assert int(notes[0]["forward_id"]) > 0


# ── 纵深与卫生守卫 ───────────────────────────────────────


def test_forward_receipt_rejects_empty_to_character(tmp_path):
    """纵深（存储层）：to_character 非空——空目标行按 to_character 过滤永不可读，禁落死行。

    ForwardManager 保持纯存储：只做非空完整性校验，不引入认证依赖。
    """
    mgr = ForwardManager(db_path=tmp_path / "f.db")
    receipt = mgr.forward_receipt("a", "", "m1", "x")
    assert receipt["ok"] is False, "空 to_character 必须拒绝落库"
    assert "to_character" in str(receipt.get("error") or "")
    assert int(receipt["forward_id"]) == 0


def test_forward_endpoint_docstring_must_not_claim_consumer_wired():
    """卫生守卫：docstring 不得宣称「消费链已接入」——prompt 渲染层实况零消费。

    防「按错误宣称直接接线」复活存储型注入（PRIV-2 附带卫生项）。
    """
    doc = inspect.getdoc(unified.forward_favorite) or ""
    assert "已接入" not in doc, (
        "不得宣称消费链已接入——如实描述：context 已挂载、prompt 渲染层尚未消费"
    )
    assert "尚未消费" in doc, "必须如实标注渲染层未消费的实况"
