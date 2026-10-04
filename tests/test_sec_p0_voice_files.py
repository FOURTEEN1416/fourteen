"""P0 安全修复批·语音与文件归属域 — 行为契约测试（红测先行）。

覆盖任务书 F1–F5：
- F1：/api/mimo/switch-voice、/api/mimo/set-engine 加 require_role(admin)（改全局 TTS 单例
      属控制面，viewer JWT / 机器 API Key 均不得触达）；/api/mimo/synthesize text>600 字 400
      + 每用户限速（6 次/分钟）；clone/design 每用户限速（2 次/分钟）。
- F2：voice_catalog 条目 owner 字段（register 记录调用者主体；旧 JSON 无 owner = 平台共享，
      向后兼容）；/api/voice/speakers 按主体过滤（普通用户只见自己的克隆音色+平台音色，
      admin 全量）；mimo synthesize 的 voice_id 归属校验（他人克隆音色 403，admin 例外）。
- F3：POST /api/rag/documents 加 require_role(admin)（共享库无归属写入）。
- F4：/api/files 归属化——上传落 data/uploads/<user_id>/ 子目录 + 文件名随机段；
      GET 按主体校验归属（本人/admin；存量根目录文件仅 admin 可读）；路径穿越防护保留。
- F5：POST /api/voice/synthesize text>600 字 400 + 每用户限速（6 次/分钟）。

测试全部 tmp_path 沙箱（UPLOAD_DIR / voice_catalog 均重定向），不写真实 data/。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-sec-p0-voice-files-32chars-ok")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, get_db
from api.routers import mimo_voice_routes, safety_routes
from voice.voice_catalog import VoiceCatalog, get_voice_catalog

_ADMIN_ID = 1
_ALICE_ID = 2
_BOB_ID = 3

_MACHINE_KEY = "sec-p0-machine-api-key-32chars-minimum-ok!"

_MAX_SYNTH_TEXT = 600


# ═══════════════════════════════════════════════════════════
# App 构造（真实登录流；UPLOAD_DIR / catalog 全沙箱）
# ═══════════════════════════════════════════════════════════


def _build_app(tmp_path, *, machine_auth: bool = False):
    db_path = str(tmp_path / "sec_p0.db")
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
                (_ADMIN_ID, "admin@sec.test", "admin", "admin"),
                (_ALICE_ID, "alice@sec.test", "alice", "viewer"),
                (_BOB_ID, "bob@sec.test", "bob", "viewer"),
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

    # 关闭全局限速中间件，避免与被测的每用户限速互相干扰（dev 默认即 false，防御性显式关）
    os.environ["RATE_LIMIT_ENABLED"] = "false"

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    if machine_auth:
        configure_auth(True, _MACHINE_KEY)
    else:
        configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine, session_factory


async def _login(client: AsyncClient, login: str) -> dict:
    resp = await client.post(
        "/api/auth/login", json={"login": login, "password": "Passw0rd!123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_of(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _seed_catalog(entries: list[dict]) -> None:
    """直接注入 catalog 条目（绕过 register，便于红测阶段/多 owner 场景构造）。"""
    cat = get_voice_catalog()
    cat._entries = {e["voice_id"]: dict(e) for e in entries}


def _reset_limiters() -> None:
    """限速器为模块级内存状态，逐用例清零防跨用例污染。"""
    for mod, names in (
        (mimo_voice_routes, ("_SYNTH_LIMITER", "_CLONE_LIMITER", "_DESIGN_LIMITER")),
        (safety_routes, ("_VOICE_SYNTH_LIMITER",)),
    ):
        for name in names:
            limiter = getattr(mod, name, None)
            if limiter is not None:
                limiter.reset()


@pytest.fixture
def sec_app(tmp_path, monkeypatch):
    """标准三账号 app + uploads/catalog 沙箱 + 限速器清零。"""
    import api.main_routes

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    # safety_routes 从 api.main_routes import 了 UPLOAD_DIR 到自己命名空间，两处都要换
    monkeypatch.setattr(api.main_routes, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(safety_routes, "UPLOAD_DIR", uploads_dir)

    # voice catalog 沙箱（同 test_w7 手法：换默认路径 + 重置单例）
    import voice.voice_catalog as vc_mod

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)

    _seed_catalog(
        [
            {"voice_id": "vc_alice", "name": "爱丽丝克隆", "kind": "clone",
             "model": "mimo-v2.5-tts-voiceclone", "owner": str(_ALICE_ID)},
            {"voice_id": "vc_bob", "name": "鲍勃克隆", "kind": "clone",
             "model": "mimo-v2.5-tts-voiceclone", "owner": str(_BOB_ID)},
            {"voice_id": "vc_platform", "name": "平台共享克隆", "kind": "clone",
             "model": "mimo-v2.5-tts-voiceclone"},  # 无 owner 键 = 存量平台共享
        ]
    )

    app, client, engine, session_factory = _build_app(tmp_path)
    _reset_limiters()
    yield client, engine, session_factory, uploads_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


@pytest.fixture
def machine_app(tmp_path, monkeypatch):
    """启用机器 API Key 的 app（控制面机器契约观察用）。"""
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    import api.main_routes

    monkeypatch.setattr(api.main_routes, "UPLOAD_DIR", uploads_dir)
    monkeypatch.setattr(safety_routes, "UPLOAD_DIR", uploads_dir)
    import voice.voice_catalog as vc_mod

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)
    _seed_catalog([])

    app, client, engine, session_factory = _build_app(tmp_path, machine_auth=True)
    _reset_limiters()
    yield client, engine, session_factory, uploads_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


# ═══════════════════════════════════════════════════════════
# F1：mimo 控制面 admin 门
# ═══════════════════════════════════════════════════════════


async def test_switch_voice_rejects_viewer(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/mimo/switch-voice",
        data={"voice_id": "female-tianmei"},
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 403, f"viewer 切全局音色应 403，实得 {r.status_code}: {r.text}"


async def test_set_engine_rejects_viewer(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/mimo/set-engine",
        data={"model": "mimo-v2.5-tts"},
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 403, f"viewer 切全局引擎应 403，实得 {r.status_code}: {r.text}"


async def test_control_plane_rejects_machine_key(machine_app):
    """机器 API Key 不是 admin 主体：改全局 TTS 单例的控制面一律 401。"""
    client, *_ = machine_app
    r = await client.post(
        "/api/mimo/switch-voice", data={"voice_id": "female-tianmei"},
        headers={"X-API-Key": _MACHINE_KEY},
    )
    assert r.status_code == 401, f"机器 key 切音色应 401，实得 {r.status_code}"
    r = await client.post(
        "/api/mimo/set-engine", data={"model": "mimo-v2.5-tts"},
        headers={"X-API-Key": _MACHINE_KEY},
    )
    assert r.status_code == 401, f"机器 key 切引擎应 401，实得 {r.status_code}"


async def test_admin_passes_control_plane_gate(sec_app):
    """admin 过门后进 handler（测试环境 TTS 未初始化 → 400，而非 401/403）。"""
    client, *_ = sec_app
    auth = _auth_of(await _login(client, "admin"))
    r = await client.post("/api/mimo/switch-voice", data={"voice_id": "female-tianmei"}, headers=auth)
    assert r.status_code == 400, r.text
    assert "TTS" in r.json()["detail"]
    r = await client.post("/api/mimo/set-engine", data={"model": "mimo-v2.5-tts"}, headers=auth)
    assert r.status_code == 400, r.text


# ═══════════════════════════════════════════════════════════
# F1：mimo synthesize 文本上限 + 每用户限速；clone/design 限速
# ═══════════════════════════════════════════════════════════


async def test_mimo_synthesize_rejects_overlong_text(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/mimo/synthesize",
        data={"text": "啊" * (_MAX_SYNTH_TEXT + 1)},
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 400, f"超长文本应 400，实得 {r.status_code}: {r.text}"
    assert "600" in r.json()["detail"], f"400 详情应说明长度上限，实得 {r.json()['detail']!r}"


async def test_mimo_synthesize_accepts_boundary_text(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/mimo/synthesize",
        data={"text": "好" * _MAX_SYNTH_TEXT},
        headers=_auth_of(await _login(client, "alice")),
    )
    # 600 字恰好不超限：过文本关，到 TTS 未初始化的 400（而非长度 400）
    assert r.status_code == 400, r.text
    assert "600" not in r.json()["detail"]


async def test_mimo_synthesize_rate_limit_per_user(sec_app):
    client, *_ = sec_app
    auth = _auth_of(await _login(client, "alice"))
    statuses = [
        (await client.post("/api/mimo/synthesize", data={"text": "你好"}, headers=auth)).status_code
        for _ in range(7)
    ]
    assert statuses[:6] == [400] * 6, f"前 6 次应为 TTS 未初始化的 400，实得 {statuses}"
    assert statuses[6] == 429, f"第 7 次应 429，实得 {statuses}"


async def test_mimo_synthesize_rate_limit_isolated_per_user(sec_app):
    """alice 打满配额不连坐 bob（每用户独立计数）。"""
    client, *_ = sec_app
    alice = _auth_of(await _login(client, "alice"))
    for _ in range(6):
        await client.post("/api/mimo/synthesize", data={"text": "你好"}, headers=alice)
    bob = _auth_of(await _login(client, "bob"))
    r = await client.post("/api/mimo/synthesize", data={"text": "你好"}, headers=bob)
    assert r.status_code != 429, "bob 不应被 alice 的配额连坐"


async def test_mimo_clone_rate_limit(sec_app):
    client, *_ = sec_app
    auth = _auth_of(await _login(client, "alice"))
    files = {"audio": ("ref.wav", b"fake-audio", "audio/wav")}
    statuses = []
    for _ in range(3):
        r = await client.post(
            "/api/mimo/clone",
            data={"voice_name": "n", "description": ""},
            files=files,
            headers=auth,
        )
        statuses.append(r.status_code)
    assert statuses[:2] == [400] * 2, f"前 2 次应为 TTS 未配置 400，实得 {statuses}"
    assert statuses[2] == 429, f"第 3 次克隆应 429，实得 {statuses}"


async def test_mimo_design_rate_limit(sec_app):
    client, *_ = sec_app
    auth = _auth_of(await _login(client, "alice"))
    statuses = []
    for _ in range(3):
        r = await client.post(
            "/api/mimo/design",
            data={"voice_name": "n", "description": "温柔女声"},
            headers=auth,
        )
        statuses.append(r.status_code)
    assert statuses[:2] == [400] * 2, f"前 2 次应为 TTS 未配置 400，实得 {statuses}"
    assert statuses[2] == 429, f"第 3 次设计应 429，实得 {statuses}"


# ═══════════════════════════════════════════════════════════
# F2：voice_catalog owner 字段 + speakers 主体过滤 + 归属校验
# ═══════════════════════════════════════════════════════════


def test_catalog_register_records_owner(tmp_path, monkeypatch):
    """F2：register 记录调用者主体（owner）。"""
    import voice.voice_catalog as vc_mod

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "vc.json", raising=False)
    cat = VoiceCatalog(str(tmp_path / "vc.json"))
    entry = cat.register(
        voice_id="vc_o", name="n", kind="clone",
        model="mimo-v2.5-tts-voiceclone", owner=str(_ALICE_ID),
    )
    assert entry["owner"] == str(_ALICE_ID), f"register 应记录 owner，实得 {entry}"
    assert cat.get("vc_o")["owner"] == str(_ALICE_ID)


def test_catalog_reads_legacy_json_without_owner(tmp_path):
    """F2：向后兼容——旧 JSON 条目无 owner 键，读入不报错且视为平台共享。"""
    path = tmp_path / "legacy_vc.json"
    path.write_text(
        json.dumps({"vc_old": {"voice_id": "vc_old", "name": "旧条目", "kind": "clone",
                                "model": "mimo-v2.5-tts-voiceclone", "description": ""}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    cat = VoiceCatalog(str(path))
    entry = cat.get("vc_old")
    assert entry is not None
    assert entry.get("owner", "") == "", "存量无 owner 条目应视为平台共享"


async def test_speakers_filtered_for_normal_user(sec_app):
    """普通用户只见：平台音色（预设+无主克隆）+ 自己的克隆；不见他人克隆。"""
    client, *_ = sec_app
    r = await client.get("/api/voice/speakers", headers=_auth_of(await _login(client, "alice")))
    assert r.status_code == 200, r.text
    names = {s["name"] for s in r.json()["speakers"]}
    assert "vc_alice" in names, "自己的克隆音色必须可见"
    assert "vc_platform" in names, "平台共享音色必须可见"
    assert "female-tianmei" in names, "静态预设必须可见"
    assert "vc_bob" not in names, f"不得看到他人克隆音色，实得 {names}"


async def test_speakers_admin_sees_all(sec_app):
    client, *_ = sec_app
    r = await client.get("/api/voice/speakers", headers=_auth_of(await _login(client, "admin")))
    names = {s["name"] for s in r.json()["speakers"]}
    assert {"vc_alice", "vc_bob", "vc_platform"} <= names


async def test_speakers_machine_face_sees_all(machine_app):
    """机器面（无 Bearer）契约不因本批收窄（W1 先例：机器面不干预）。"""
    client, *_ = machine_app
    get_voice_catalog()._entries = {
        "vc_bob": {"voice_id": "vc_bob", "name": "b", "kind": "clone", "model": "m",
                   "owner": "3"},
    }
    r = await client.get("/api/voice/speakers", headers={"X-API-Key": _MACHINE_KEY})
    names = {s["name"] for s in r.json()["speakers"]}
    assert "vc_bob" in names


async def test_mimo_synthesize_rejects_others_clone(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/mimo/synthesize",
        data={"text": "你好", "voice_id": "vc_alice"},
        headers=_auth_of(await _login(client, "bob")),
    )
    assert r.status_code == 403, f"他人克隆音色应 403，实得 {r.status_code}: {r.text}"


async def test_mimo_synthesize_allows_own_and_platform_and_admin(sec_app):
    client, *_ = sec_app
    bob = _auth_of(await _login(client, "bob"))
    admin = _auth_of(await _login(client, "admin"))
    # 自己的克隆 → 过归属关（TTS 未初始化 400）
    r = await client.post("/api/mimo/synthesize", data={"text": "你好", "voice_id": "vc_bob"}, headers=bob)
    assert r.status_code == 400 and "403" not in r.text
    # 无主平台克隆 → 人人可用
    r = await client.post("/api/mimo/synthesize", data={"text": "你好", "voice_id": "vc_platform"}, headers=bob)
    assert r.status_code == 400
    # admin 例外：可用任何人的克隆
    r = await client.post("/api/mimo/synthesize", data={"text": "你好", "voice_id": "vc_alice"}, headers=admin)
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════
# F3：/api/rag/documents admin 门
# ═══════════════════════════════════════════════════════════


async def test_rag_documents_rejects_viewer(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/rag/documents",
        files={"file": ("doc.txt", b"hello", "text/plain")},
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 403, f"viewer 上传共享知识库应 403，实得 {r.status_code}: {r.text}"


async def test_rag_documents_rejects_machine_key(machine_app):
    client, *_ = machine_app
    r = await client.post(
        "/api/rag/documents",
        files={"file": ("doc.txt", b"hello", "text/plain")},
        headers={"X-API-Key": _MACHINE_KEY},
    )
    assert r.status_code == 401, f"机器 key 上传共享知识库应 401，实得 {r.status_code}"


async def test_rag_documents_admin_reaches_handler(sec_app):
    """admin 过门后进 handler（RAG 未初始化 → 503，而非 401/403）。"""
    client, *_ = sec_app
    r = await client.post(
        "/api/rag/documents",
        files={"file": ("doc.txt", b"hello", "text/plain")},
        headers=_auth_of(await _login(client, "admin")),
    )
    assert r.status_code == 503, r.text


# ═══════════════════════════════════════════════════════════
# F4：/api/files 归属化
# ═══════════════════════════════════════════════════════════


async def test_upload_lands_in_user_subdir_with_random_segment(sec_app):
    client, _, _, uploads = sec_app
    r = await client.post(
        "/api/files/upload",
        files={"file": ("照片.png", b"img-bytes", "image/png")},
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    import re as _re

    assert _re.match(rf"^/api/files/{_ALICE_ID}/\d+_[0-9a-f]{{8}}_照片\.png$", body["url"]), \
        f"url 应为 /api/files/<uid>/<ts>_<rand8>_<name>，实得 {body['url']!r}"
    stored = uploads / str(_ALICE_ID)
    files_on_disk = list(stored.iterdir())
    assert len(files_on_disk) == 1, f"应落 {stored} 子目录，实得 {files_on_disk}"
    assert files_on_disk[0].read_bytes() == b"img-bytes"


async def test_upload_requires_principal(sec_app):
    """无主体（dev 放行面/机器面）不得再写不可归属的文件。"""
    client, *_ = sec_app
    r = await client.post(
        "/api/files/upload",
        files={"file": ("x.txt", b"data", "text/plain")},
    )
    assert r.status_code == 401, f"无主体上传应 401，实得 {r.status_code}"


async def test_owner_reads_own_file(sec_app):
    client, _, _, uploads = sec_app
    user_dir = uploads / str(_ALICE_ID)
    user_dir.mkdir(parents=True)
    (user_dir / "1234567890_abcd1234_mine.txt").write_bytes(b"mine")
    r = await client.get(
        f"/api/files/{_ALICE_ID}/1234567890_abcd1234_mine.txt",
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 200, f"本人文件应可读，实得 {r.status_code}"


async def test_other_user_file_forbidden(sec_app):
    client, _, _, uploads = sec_app
    user_dir = uploads / str(_ALICE_ID)
    user_dir.mkdir(parents=True)
    (user_dir / "1234567890_abcd1234_mine.txt").write_bytes(b"mine")
    r = await client.get(
        f"/api/files/{_ALICE_ID}/1234567890_abcd1234_mine.txt",
        headers=_auth_of(await _login(client, "bob")),
    )
    assert r.status_code == 403, f"他人文件应 403，实得 {r.status_code}"


async def test_admin_reads_user_files(sec_app):
    client, _, _, uploads = sec_app
    user_dir = uploads / str(_ALICE_ID)
    user_dir.mkdir(parents=True)
    (user_dir / "f.txt").write_bytes(b"mine")
    r = await client.get(
        f"/api/files/{_ALICE_ID}/f.txt",
        headers=_auth_of(await _login(client, "admin")),
    )
    assert r.status_code == 200, f"admin 全量可读，实得 {r.status_code}"


async def test_legacy_root_file_admin_only(sec_app):
    """存量根目录文件（无归属）：仅 admin 可读；普通用户/无主体一律 403。"""
    client, _, _, uploads = sec_app
    (uploads / "legacy.txt").write_bytes(b"legacy")
    r = await client.get("/api/files/legacy.txt", headers=_auth_of(await _login(client, "alice")))
    assert r.status_code == 403, f"普通用户读无归属存量文件应 403，实得 {r.status_code}"
    r = await client.get("/api/files/legacy.txt")
    assert r.status_code == 403, f"无主体读无归属存量文件应 403，实得 {r.status_code}"
    r = await client.get("/api/files/legacy.txt", headers=_auth_of(await _login(client, "admin")))
    assert r.status_code == 200, f"admin 读存量文件应 200，实得 {r.status_code}"


async def test_path_traversal_still_blocked(sec_app):
    """路径穿越既有防护保留：.. 段、越出 uploads 均拒绝。"""
    from fastapi import HTTPException

    from api.routers import safety_routes as sr

    with pytest.raises(HTTPException) as ei:
        await sr.serve_file(filename="2/../2/x.txt", _auth=True, _principal=None)
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        await sr.serve_file(filename="../../.env", _auth=True, _principal=None)
    assert ei.value.status_code == 403


# ═══════════════════════════════════════════════════════════
# F5：/api/voice/synthesize 文本上限 + 每用户限速
# ═══════════════════════════════════════════════════════════


async def test_safety_synthesize_rejects_overlong_text(sec_app):
    client, *_ = sec_app
    r = await client.post(
        "/api/voice/synthesize",
        data={"text": "嗯" * (_MAX_SYNTH_TEXT + 1)},
        headers=_auth_of(await _login(client, "alice")),
    )
    assert r.status_code == 400, f"超长文本应 400，实得 {r.status_code}: {r.text}"
    assert "600" in r.json()["detail"], f"400 详情应说明长度上限，实得 {r.json()['detail']!r}"


async def test_safety_synthesize_rate_limit(sec_app):
    client, *_ = sec_app
    auth = _auth_of(await _login(client, "alice"))
    statuses = [
        (await client.post("/api/voice/synthesize", data={"text": "你好"}, headers=auth)).status_code
        for _ in range(7)
    ]
    assert statuses[:6] == [503] * 6, f"前 6 次应为 TTS 未启用 503，实得 {statuses}"
    assert statuses[6] == 429, f"第 7 次应 429，实得 {statuses}"
