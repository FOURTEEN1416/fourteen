"""风格提取（原「克隆」）数据集 per-user 化 — 归属隔离契约测试（红测先行）。

背景（本域规格取证结论）：`data/clone/` 原本**没有 user 维度**——`*_raw.json`
全部平铺在 `CLONE_DATA_DIR` 根下（`voice/clone_data_manager.py:32/97-138`），
`get_contacts` 同样平铺（`:46-95`）。若直接拆除 `require_role("admin")`，
任何登录用户都能列出并**删除**全部人的私密聊天数据集（含正文）——高危越权。

本文件钉死三条契约：
1. 分区：普通用户写/读/删都在 `data/clone/<user_id>/` 内（服务端从 token 派生，
   客户端自报归属面根本不存在）；
2. 存量扁平数据 = 无主 → 仅 admin / 机器面可见（**不猜归属**，D15 显式声明）；
3. 越权一律 404（与「不存在」同码，天然防枚举）——他人数据集既不可见也不可删。

追加（2026-10-31 clone-hardening）：
4. R3-1 匿名面收紧——「机器面」必须显式携带有效 X-API-Key，即便
   `API_KEY_ENABLED=false`；无主体且无有效 key → 401，无效/过期 Bearer 不得
   退化成根目录全量视图；
5. R3-2 Windows 盘符相对路径 `C:evil` / `c:evil` 与穿越符同属非法 person_id；
6. 验收打回补齐：字符串时间戳的日期筛选/区间统计、Windows 非法文件名字符
   （`? * < > | "` 与控制字符）→ 400、训练目录 glob 按字面匹配、batch-delete
   兼容逗号分隔 indices。

全部 tmp_path 沙箱（`CLONE_DATA_DIR` / `TRAINING_DATA_DIR` 均 monkeypatch），
不写真实 `data/clone`。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from urllib.parse import quote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-clone-user-scope-32chars-ok")

import voice.clone_data_manager as cdm_mod
from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, get_db

_ADMIN_ID = 1
_ALICE_ID = 2
_BOB_ID = 3

# 上传体的最小可用形态：带 reply 侧（否则分析侧判「无被克隆者」422）
_UPLOAD_ROWS = [
    {"user": "你好呀", "reply": "在的在的", "timestamp": "1700000000"},
    {"user": "今天吃什么", "reply": "随便吧，你定", "timestamp": "1700000100"},
]

# 机器面 API Key（脚本/CI 通道）；测试内按需 configure_auth 注入
_MACHINE_KEY = "clone-hardening-machine-key-7f3c9a1e2b4d"

# 存量数据集形态（同 normalize_clone_conversations 输出）
_DATASET_ROWS = [
    {"user": "早", "reply": "早呀", "timestamp": 1700000000},
    {"user": "忙吗", "reply": "不忙，说吧", "timestamp": 1700000100},
]


# ═══════════════════════════════════════════════════════════
# App 构造（真实登录流；clone 数据目录全沙箱）
# ═══════════════════════════════════════════════════════════


def _build_app(tmp_path):
    db_path = str(tmp_path / "clone_scope.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

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
            for uid, email, username, role in (
                (_ADMIN_ID, "admin@clone.test", "admin", "admin"),
                (_ALICE_ID, "alice@clone.test", "alice", "viewer"),
                (_BOB_ID, "bob@clone.test", "bob", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=username,
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=username,
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            await session.commit()

    asyncio.run(_init())

    # 全局限速中间件与被测面无关，显式关闭（dev 默认即 false，防御性写死）
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    # 单用户数据集上限按用例覆盖（默认 20）
    os.environ.pop("CLONE_MAX_DATASETS_PER_USER", None)

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine


async def _login(client: AsyncClient, login: str) -> dict:
    resp = await client.post(
        "/api/auth/login", json={"login": login, "password": "Passw0rd!123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_of(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _seed_dataset(root, person_id: str, rows: list[dict] | None = None):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{person_id}_raw.json"
    path.write_text(json.dumps(rows or _DATASET_ROWS), encoding="utf-8")
    return path


async def _upload(client, headers: dict, target: str, *, persist: bool):
    payload = json.dumps(_UPLOAD_ROWS).encode("utf-8")
    return await client.post(
        "/api/clone/upload",
        params={"target": target, "persist": "true" if persist else "false"},
        files={"file": ("chat.json", payload, "application/json")},
        headers=headers,
    )


@pytest.fixture
def clone_env(tmp_path, monkeypatch):
    """三账号 app + `data/clone`、`data/training` 全沙箱。"""
    clone_dir = tmp_path / "clone"
    training_dir = tmp_path / "training"
    clone_dir.mkdir()
    training_dir.mkdir()
    monkeypatch.setattr(cdm_mod, "CLONE_DATA_DIR", clone_dir, raising=False)
    monkeypatch.setattr(cdm_mod, "TRAINING_DATA_DIR", training_dir, raising=False)

    app, client, engine = _build_app(tmp_path)
    yield client, clone_dir, training_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")
    os.environ.pop("CLONE_MAX_DATASETS_PER_USER", None)


# ═══════════════════════════════════════════════════════════
# ① 分区写入 + LIST 隔离
# ═══════════════════════════════════════════════════════════


async def test_persist_upload_creates_owner_partitioned_dataset(clone_env):
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    resp = await _upload(client, alice, "AliceFriend", persist=True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["persisted"] is True
    assert body["persona"]["name"] == "AliceFriend"
    # 落盘位置 = data/clone/<alice_id>/
    assert (clone_dir / str(_ALICE_ID) / "AliceFriend_raw.json").exists()
    assert not (clone_dir / "AliceFriend_raw.json").exists()

    listed = await client.get("/api/clone/datasets", headers=alice)
    assert listed.status_code == 200
    assert [d["person_id"] for d in listed.json()["datasets"]] == ["AliceFriend"]

    bob = _auth_of(await _login(client, "bob"))
    bob_listed = await client.get("/api/clone/datasets", headers=bob)
    assert bob_listed.json()["datasets"] == []


# ═══════════════════════════════════════════════════════════
# ② 越权删除 → 404 且文件仍在；他人对话不可见
# ═══════════════════════════════════════════════════════════


async def test_other_user_delete_returns_404_and_keeps_file(clone_env):
    client, clone_dir, _ = clone_env
    alice_file = _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")

    bob = _auth_of(await _login(client, "bob"))
    resp = await client.delete("/api/clone/datasets/AliceFriend", headers=bob)
    assert resp.status_code == 404, resp.text
    assert alice_file.exists(), "越权删除不得动他人文件"

    detail = await client.get("/api/clone/datasets/AliceFriend", headers=bob)
    assert detail.status_code == 404, detail.text


async def test_other_user_conversation_delete_returns_404(clone_env):
    client, clone_dir, _ = clone_env
    alice_file = _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")

    bob = _auth_of(await _login(client, "bob"))
    resp = await client.delete(
        "/api/clone/datasets/AliceFriend/conversation",
        params={"index": 0},
        headers=bob,
    )
    assert resp.status_code == 404, resp.text
    assert len(json.loads(alice_file.read_text(encoding="utf-8"))) == 2

    batch = await client.post(
        "/api/clone/datasets/AliceFriend/conversations/batch-delete",
        params={"indices": [0, 1]},
        headers=bob,
    )
    assert batch.status_code == 404, batch.text
    assert len(json.loads(alice_file.read_text(encoding="utf-8"))) == 2


# ═══════════════════════════════════════════════════════════
# ③ 存量扁平数据 = 无主 → 仅 admin / 机器面可见
# ═══════════════════════════════════════════════════════════


async def test_legacy_root_dataset_admin_only(clone_env):
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "LegacyPerson")

    admin = _auth_of(await _login(client, "admin"))
    admin_listed = await client.get("/api/clone/datasets", headers=admin)
    assert [d["person_id"] for d in admin_listed.json()["datasets"]] == ["LegacyPerson"]
    admin_detail = await client.get("/api/clone/datasets/LegacyPerson", headers=admin)
    assert admin_detail.status_code == 200
    assert admin_detail.json()["total"] == 2

    alice = _auth_of(await _login(client, "alice"))
    alice_listed = await client.get("/api/clone/datasets", headers=alice)
    assert alice_listed.json()["datasets"] == []
    alice_detail = await client.get("/api/clone/datasets/LegacyPerson", headers=alice)
    assert alice_detail.status_code == 404
    alice_delete = await client.delete("/api/clone/datasets/LegacyPerson", headers=alice)
    assert alice_delete.status_code == 404
    assert (clone_dir / "LegacyPerson_raw.json").exists()

    # 机器面（无 Bearer）：R3-1 之后必须显式携带有效 X-API-Key——
    # 裸匿名请求即便 API_KEY_ENABLED=false 也不得退化成根目录全量视图。
    anonymous = await client.get("/api/clone/datasets")
    assert anonymous.status_code == 401, anonymous.text

    configure_auth(False, _MACHINE_KEY)
    try:
        machine = await client.get("/api/clone/datasets", headers={"X-API-Key": _MACHINE_KEY})
        assert machine.status_code == 200
        assert [d["person_id"] for d in machine.json()["datasets"]] == ["LegacyPerson"]
        wrong_key = await client.get("/api/clone/datasets", headers={"X-API-Key": "wrong"})
        assert wrong_key.status_code == 401
    finally:
        configure_auth(False, "")


# ═══════════════════════════════════════════════════════════
# ④ persist=False（默认）零落盘 —— 现状回归
# ═══════════════════════════════════════════════════════════


async def test_persist_false_writes_nothing(clone_env):
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    resp = await _upload(client, alice, "Ghost", persist=False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["persisted"] is False
    assert list(clone_dir.rglob("*_raw.json")) == []
    listed = await client.get("/api/clone/datasets", headers=alice)
    assert listed.json()["datasets"] == []


# ═══════════════════════════════════════════════════════════
# ⑤ target 路径穿越 → 400（复用 _safe_person_id 既有契约）
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_target", ["../escape", "a/b", "a\\b", ".."])
async def test_upload_target_traversal_rejected(clone_env, bad_target):
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    resp = await _upload(client, alice, bad_target, persist=True)
    assert resp.status_code == 400, resp.text
    assert list(clone_dir.rglob("*_raw.json")) == []


# ═══════════════════════════════════════════════════════════
# ⑥ 单用户数据集数量上限 → 429（可配）
# ═══════════════════════════════════════════════════════════


async def test_owner_dataset_quota_429(clone_env, monkeypatch):
    monkeypatch.setenv("CLONE_MAX_DATASETS_PER_USER", "1")
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    first = await _upload(client, alice, "One", persist=True)
    assert first.status_code == 200, first.text

    second = await _upload(client, alice, "Two", persist=True)
    assert second.status_code == 429, second.text
    assert not (clone_dir / str(_ALICE_ID) / "Two_raw.json").exists()

    # 覆盖同名数据集不占新配额（幂等重传）
    again = await _upload(client, alice, "One", persist=True)
    assert again.status_code == 200, again.text


# ═══════════════════════════════════════════════════════════
# ⑦ contacts / stats 同样按归属过滤
# ═══════════════════════════════════════════════════════════


async def test_contacts_and_stats_scoped_per_owner(clone_env):
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "LegacyPerson")
    _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")

    alice = _auth_of(await _login(client, "alice"))
    contacts = await client.get("/api/clone/contacts", headers=alice)
    assert [c["username"] for c in contacts.json()["contacts"]] == ["AliceFriend"]
    stats = await client.get("/api/clone/stats", headers=alice)
    assert stats.json()["total_persons"] == 1

    # admin / 机器面的「全量视图」= 根目录（存量无主数据）；per-user 分区**不跨目录
    # 聚合**——跨目录拼 LIST 会让 DETAIL/DELETE（同目录内 glob）与 LIST 口径分裂，
    # 且等于「猜归属」，规格明令禁止（D15 显式声明）。
    admin = _auth_of(await _login(client, "admin"))
    admin_contacts = await client.get("/api/clone/contacts", headers=admin)
    assert [c["username"] for c in admin_contacts.json()["contacts"]] == ["LegacyPerson"]
    admin_stats = await client.get("/api/clone/stats", headers=admin)
    assert admin_stats.json()["total_persons"] == 1
    admin_listed = await client.get("/api/clone/datasets", headers=admin)
    assert [d["person_id"] for d in admin_listed.json()["datasets"]] == ["LegacyPerson"]


# ═══════════════════════════════════════════════════════════
# ⑧ R3-1 匿名面收紧：无主体必须显式持有效 X-API-Key
# ═══════════════════════════════════════════════════════════


async def test_anonymous_blocked_even_when_api_key_disabled(clone_env):
    """API_KEY_ENABLED=false 时匿名不得退化成「机器面 = 根目录全量视图」。

    缺陷 R3-1 原状：`verify_api_key_dep` 在开关关闭时放行一切无凭证请求，
    `_owner_scope(None)` 返回空串 → LIST/DETAIL/DELETE 全部存量私密数据集。
    """
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "LegacyPerson")  # 存量扁平（无主）数据集

    for path in (
        "/api/clone/datasets",
        "/api/clone/contacts",
        "/api/clone/stats",
        "/api/clone/datasets/LegacyPerson",
    ):
        resp = await client.get(path)
        assert resp.status_code == 401, f"{path} 匿名应 401，实得 {resp.status_code}"

    deleted = await client.delete("/api/clone/datasets/LegacyPerson")
    assert deleted.status_code == 401, deleted.text
    assert (clone_dir / "LegacyPerson_raw.json").exists(), "匿名删除不得动任何文件"


async def test_invalid_bearer_does_not_fall_back_to_machine_view(clone_env):
    """R3-3①：API_KEY_ENABLED=true 下匿名 401；无效/过期 Bearer 不得退化成机器面。

    机器面（脚本/CI）带有效 X-API-Key 仍可用——这是兼容性不变量。
    """
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "LegacyPerson")

    configure_auth(True, _MACHINE_KEY)
    try:
        anonymous = await client.get("/api/clone/datasets")
        assert anonymous.status_code == 401, anonymous.text

        bad_bearer = await client.get(
            "/api/clone/datasets",
            headers={"Authorization": "Bearer forged-or-expired-token"},
        )
        assert bad_bearer.status_code == 401, bad_bearer.text

        wrong_key = await client.get(
            "/api/clone/datasets", headers={"X-API-Key": "not-the-configured-key"}
        )
        assert wrong_key.status_code == 401, wrong_key.text

        machine = await client.get(
            "/api/clone/datasets", headers={"X-API-Key": _MACHINE_KEY}
        )
        assert machine.status_code == 200, machine.text
        assert [d["person_id"] for d in machine.json()["datasets"]] == ["LegacyPerson"]
    finally:
        configure_auth(False, "")


async def test_machine_key_works_even_when_auth_switch_disabled(clone_env):
    """兼容面：API_KEY_ENABLED=false 但配置了 API_KEY，脚本/CI 带 key 仍可用。"""
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "LegacyPerson")

    configure_auth(False, _MACHINE_KEY)
    try:
        machine = await client.get(
            "/api/clone/datasets", headers={"X-API-Key": _MACHINE_KEY}
        )
        assert machine.status_code == 200, machine.text
        assert [d["person_id"] for d in machine.json()["datasets"]] == ["LegacyPerson"]

        # 非 ASCII header（latin-1 传输）：compare_digest 不支持该输入 → 按不匹配
        # 401（不得 500）。此处走的是本域守卫的比对分支（全局认证已关闭）。
        non_ascii = await client.get(
            "/api/clone/datasets",
            headers=httpx.Headers({"X-API-Key": "é"}, encoding="latin-1"),
        )
        assert non_ascii.status_code == 401, non_ascii.text
    finally:
        configure_auth(False, "")


# ═══════════════════════════════════════════════════════════
# ⑨ R3-2 Windows 盘符相对路径（C:evil）→ 400
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_person", ["C:evil", "c:evil"])
async def test_windows_drive_relative_person_id_rejected(clone_env, bad_person):
    """`C:evil` / `c:evil` 拼路径会替换整个 base（Windows 驱动器相对语义）→ 必须 400。"""
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    upload = await _upload(client, alice, bad_person, persist=True)
    assert upload.status_code == 400, upload.text

    detail = await client.get(f"/api/clone/datasets/{bad_person}", headers=alice)
    assert detail.status_code == 400, detail.text

    deleted = await client.delete(f"/api/clone/datasets/{bad_person}", headers=alice)
    assert deleted.status_code == 400, deleted.text

    assert list(clone_dir.rglob("*_raw.json")) == []


# ═══════════════════════════════════════════════════════════
# ⑩ R3-3 补测：本人 detail 200 / 非 admin 不触达 TRAINING_DATA_DIR
# ═══════════════════════════════════════════════════════════


async def test_own_dataset_detail_returns_200(clone_env):
    """R3-3③：本人分区内数据集 detail 必须 200 且正文完整（此前只测了越权 404）。"""
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")

    alice = _auth_of(await _login(client, "alice"))
    detail = await client.get("/api/clone/datasets/AliceFriend", headers=alice)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["person_id"] == "AliceFriend"
    assert body["total"] == len(_DATASET_ROWS)
    assert len(body["conversations"]) == len(_DATASET_ROWS)


async def test_non_admin_delete_does_not_touch_training_data(clone_env):
    """R3-3②：普通用户删自己的数据集，不得触发 TRAINING_DATA_DIR 下的批量删除。

    `delete_dataset` 仅在 owner 为空（admin / 机器面）时才 glob 训练目录；本用例
    同时钉死「普通用户删除成功」与「训练件原样保留」两端。
    """
    client, clone_dir, training_dir = clone_env
    _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")
    training_file = training_dir / "AliceFriend_style.bin"
    training_file.write_text("model-artifact", encoding="utf-8")

    alice = _auth_of(await _login(client, "alice"))
    resp = await client.delete("/api/clone/datasets/AliceFriend", headers=alice)
    assert resp.status_code == 200, resp.text
    assert not (clone_dir / str(_ALICE_ID) / "AliceFriend_raw.json").exists()
    assert training_file.exists(), "非 admin 删除不得触达 TRAINING_DATA_DIR"


# ═══════════════════════════════════════════════════════════
# ⑪ 验收打回①：字符串时间戳的日期筛选 / date_range（persist 路径必现）
# ═══════════════════════════════════════════════════════════


async def test_uploaded_string_timestamps_date_filter_and_range(clone_env):
    """persist=True 落盘的 timestamp 是字符串（normalize str 化）——
    date_from/date_to 不得抛 TypeError，stats.date_range 必须有值。"""
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    up = await _upload(client, alice, "AliceFriend", persist=True)
    assert up.status_code == 200, up.text
    rows = json.loads(
        (clone_dir / str(_ALICE_ID) / "AliceFriend_raw.json").read_text(encoding="utf-8")
    )
    assert isinstance(rows[0]["timestamp"], str), "本用例前提：落盘时间戳是字符串"

    detail = await client.get(
        "/api/clone/datasets/AliceFriend",
        params={"date_from": "2023-11-01", "date_to": "2023-11-30"},
        headers=alice,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["total"] == 2
    assert body["stats"]["date_range"] == "2023-11-14 ~ 2023-11-14"


async def test_detail_tolerates_mixed_and_invalid_timestamp_types(clone_env):
    """混合 int / 数字串 / 非法串：不得 500；非法串不参与筛选与区间统计。"""
    client, clone_dir, _ = clone_env
    _seed_dataset(
        clone_dir / str(_ALICE_ID),
        "Mixed",
        rows=[
            {"user": "a", "reply": "b", "timestamp": 1700000000},
            {"user": "c", "reply": "d", "timestamp": "1700000100"},
            {"user": "e", "reply": "f", "timestamp": "not-a-date"},
        ],
    )
    alice = _auth_of(await _login(client, "alice"))

    detail = await client.get("/api/clone/datasets/Mixed", headers=alice)
    assert detail.status_code == 200, detail.text
    assert detail.json()["total"] == 3
    assert detail.json()["stats"]["date_range"] == "2023-11-14 ~ 2023-11-14"

    filtered = await client.get(
        "/api/clone/datasets/Mixed",
        params={"date_from": "2023-11-01", "date_to": "2023-11-30"},
        headers=alice,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 2, "非法时间戳不得进入日期筛选结果"


# ═══════════════════════════════════════════════════════════
# ⑫ 验收打回②：Windows 非法文件名字符 → 400（不得 500）
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_char", ["?", "*", "<", ">", "|", '"', "\x01"])
async def test_windows_invalid_filename_chars_rejected(clone_env, bad_char):
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))

    resp = await _upload(client, alice, f"Bad{bad_char}Name", persist=True)
    assert resp.status_code == 400, resp.text
    assert list(clone_dir.rglob("*_raw.json")) == []


# ═══════════════════════════════════════════════════════════
# ⑬ 验收打回③：训练目录 glob 元字符不得误删
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_person", ["?", "*"])
async def test_admin_delete_wildcard_person_rejected_and_training_intact(
    clone_env, bad_person
):
    client, clone_dir, training_dir = clone_env
    keep1 = training_dir / "keep1.bin"
    keep2 = training_dir / "keep2.bin"
    keep1.write_text("x", encoding="utf-8")
    keep2.write_text("y", encoding="utf-8")

    admin = _auth_of(await _login(client, "admin"))
    resp = await client.delete(
        "/api/clone/datasets/" + quote(bad_person, safe=""), headers=admin
    )
    assert resp.status_code == 400, resp.text
    assert sorted(p.name for p in training_dir.iterdir()) == ["keep1.bin", "keep2.bin"]


async def test_admin_delete_bracket_name_only_removes_exact_training_prefix(clone_env):
    """``[`` 是合法 Windows 文件名字符但为 glob 元字符——必须按字面匹配。"""
    client, clone_dir, training_dir = clone_env
    exact = training_dir / "A[1].bin"
    other = training_dir / "Other.bin"
    exact.write_text("x", encoding="utf-8")
    other.write_text("y", encoding="utf-8")

    admin = _auth_of(await _login(client, "admin"))
    resp = await client.delete(
        "/api/clone/datasets/" + quote("A[1]", safe=""), headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert not exact.exists(), "字面同名训练件应被删除"
    assert other.exists(), "glob 元字符不得扩展到其他训练件"


# ═══════════════════════════════════════════════════════════
# ⑭ 验收打回④：batch-delete 兼容逗号分隔 indices
# ═══════════════════════════════════════════════════════════


async def test_batch_delete_accepts_comma_and_repeated_indices(clone_env):
    client, clone_dir, _ = clone_env
    alice = _auth_of(await _login(client, "alice"))
    path = "/api/clone/datasets/AliceFriend/conversations/batch-delete"

    _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")
    comma = await client.post(path, params={"indices": "0,1"}, headers=alice)
    assert comma.status_code == 200, comma.text
    assert comma.json()["deleted_count"] == 2
    assert json.loads(
        (clone_dir / str(_ALICE_ID) / "AliceFriend_raw.json").read_text(encoding="utf-8")
    ) == []

    # 重复参数形态（FastAPI 原生契约）继续保持可用
    _seed_dataset(clone_dir / str(_ALICE_ID), "AliceFriend")
    repeated = await client.post(path, params=[("indices", "0")], headers=alice)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["deleted_count"] == 1

    # 非法索引 → 400（不是 422 解析错误）
    bad = await client.post(path, params={"indices": "0,x"}, headers=alice)
    assert bad.status_code == 400, bad.text


# ═══════════════════════════════════════════════════════════
# ⑮ R3-3 验收补洞：伪造/过期 Bearer + 有效 X-API-Key → 401
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("auth_enabled", [True, False])
async def test_forged_or_expired_bearer_with_valid_key_still_401(clone_env, auth_enabled):
    """无主体判定不能被「有效机器 key」绕过，也不能让坏 Bearer 退化成机器面。

    覆盖验收指出的测试缺口：此前的无效 Bearer 用例不带 key，先被
    `verify_api_key_dep` 401，根本没走到 `_require_clone_access`。本用例同时
    携带有效 X-API-Key，必须在开关开/关两种状态下都由主体解析拒绝。
    """
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "LegacyPerson")
    configure_auth(auth_enabled, _MACHINE_KEY)
    expired = create_access_token(
        {"sub": str(_ALICE_ID)}, expires_delta=timedelta(seconds=-60)
    )
    try:
        for bad_token in ("forged-not-a-jwt", expired):
            resp = await client.get(
                "/api/clone/datasets",
                headers={
                    "Authorization": f"Bearer {bad_token}",
                    "X-API-Key": _MACHINE_KEY,
                },
            )
            assert resp.status_code == 401, f"{bad_token}: {resp.text}"

        ok = await client.get(
            "/api/clone/datasets", headers={"X-API-Key": _MACHINE_KEY}
        )
        assert ok.status_code == 200, ok.text
        assert [d["person_id"] for d in ok.json()["datasets"]] == ["LegacyPerson"]
    finally:
        configure_auth(False, "")


async def test_detail_tolerates_nonstring_user_reply_fields(clone_env):
    """legacy/导入数据的 user/reply 可能是 int/None/list/dict 或整行非 dict：
    keyword 过滤不得 AttributeError → 500（R3 清扫批）。"""
    client, clone_dir, _ = clone_env
    _seed_dataset(
        clone_dir / str(_ALICE_ID),
        "LegacyMixed",
        rows=[
            {"user": 12345, "reply": None, "timestamp": 1700000000},
            {"user": ["a"], "reply": {"x": 1}, "timestamp": 1700000100},
            {"user": "alice", "reply": "hello", "timestamp": 1700000200},
            "not-a-dict-row",
            42,
        ],
    )
    alice = _auth_of(await _login(client, "alice"))

    resp = await client.get(
        "/api/clone/datasets/LegacyMixed",
        params={"keyword": "alice"},
        headers=alice,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1, "只有字符串字段的行能命中关键词"
    assert body["conversations"][0]["reply"] == "hello"

    # only_user / 统计路径同样不得因脏值崩
    resp2 = await client.get(
        "/api/clone/datasets/LegacyMixed",
        params={"only_user": "true"},
        headers=alice,
    )
    assert resp2.status_code == 200, resp2.text


@pytest.mark.parametrize(
    ("bad_person", "encoded"),
    [(".", "%2E"), ("..", "%2E%2E")],
)
async def test_dot_person_id_rejected(clone_env, bad_person, encoded):
    """`.` / `..` 不是合法人物标识：`.` 的 glob 前缀会命中隐藏文件（R3 清扫批）。

    注意：``urllib.parse.quote`` 不会编码 ``.``（RFC 3986 unreserved 字符），
    ``DELETE /api/clone/datasets/.`` 会被 HTTP 客户端/服务器规范化为父路径
    → 404（路由不匹配），根本到不了 handler。必须用 ``%2E`` 字面量让点作为
    路径段送达（验收员实证：修复前 ``DELETE %2E`` → 200 且删掉训练隐藏文件）。
    """
    client, clone_dir, _ = clone_env
    _seed_dataset(clone_dir, "Someone")
    admin = _auth_of(await _login(client, "admin"))

    resp = await client.delete(
        f"/api/clone/datasets/{encoded}", headers=admin
    )
    assert resp.status_code == 400, f"{bad_person}: {resp.text}"

