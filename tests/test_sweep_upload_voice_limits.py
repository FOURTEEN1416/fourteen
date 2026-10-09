"""全仓历遍安全扫描·upload-voice 域 — 攻击面实证测试（红测先行）。

覆盖两条 findings：
- ABUSE-2：``POST /api/mimo/clone`` 上传体无大小上限，``await audio.read()``
  全量载入 worker 内存——同仓其余上传面（clone_routes 50MB / character_routes
  10MB / knowledge_routes 10MB）均有上限，唯此站点缺失（nginx 未设
  client_max_body_size 只是恰好部分掩盖，直连 8000 即裸奔）。
  修法：读前 Content-Length 快速通道（整包含 multipart 开销，放宽余量）+
  分块读累计上限（50MB，对齐 clone_routes 口径），超限即刻 413 并停止读取。
- ABUSE-3：``POST /api/characters/{id}/voice/test`` 无文本长度上限、无每用户
  限速——上批同族两个合成端点（``/api/voice/synthesize`` 与
  ``/api/mimo/synthesize``：600 字 + 6 次/分/用户）均已收口，唯「角色试听」
  这条同成本路径（MiMo 云按字符计费）漏网。修法：handler 内 600 字 400
  （同族口径，pydantic Field 校验失败是 422 与同族 400 冲突故不用）+
  每用户 6 次/分钟滑动窗口限速桶（同族 _RateLimiter 款式自建）。

测试全部 tmp_path 沙箱（卡库 / voice_catalog / 角色语音配置 / DB），不写真实 data/。
限速桶为模块级内存状态，fixture setup/teardown 双清零（防跨用例、跨文件污染
——尤其 test_w7_voice_chain 的直呼 machine 桶）。
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
os.environ.setdefault("JWT_SECRET", "test-secret-for-sweep-upload-voice-32chars-ok")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, get_db
from api.deps import deps, get_tts_manager
from api.routers import mimo_voice_routes
from api.routers import voice_routes as voice_routes_mod
from shisi.voice.character_voice import CharacterVoiceManager
from voice.audio_result import SynthesizedAudio
from voice.mimo_tts_provider import MiMoTTSProvider

_ADMIN_ID = 1
_ALICE_ID = 2
_BOB_ID = 3

# clone_routes.py:182 同款口径：50MB
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# 同族合成文本上限（safety_routes/mimo_voice_routes 均 600）
_MAX_SYNTH_TEXT = 600

_ALICE_CARD_ID = "alice-card"
_BOB_CARD_ID = "bob-card"


# ═══════════════════════════════════════════════════════════
# 伪造件：clone 链（provider 捕获收到的字节数）/ 试听合成链
# ═══════════════════════════════════════════════════════════


class _SpyCloneProvider(MiMoTTSProvider):
    """voiceclone 间谍 provider：必须继承 MiMoTTSProvider（handler 有 isinstance 门，
    假对象会在 read() 之前被 400 挡住），覆写 clone_voice 记录收到的字节量。"""

    def __init__(self) -> None:
        super().__init__(api_key="spy-key", model="mimo-v2.5-tts-voiceclone")
        self.received_sizes: list[int] = []

    def health_check(self) -> dict:
        return {"model": "mimo-v2.5-tts-voiceclone"}

    async def clone_voice(self, audio_data: bytes, voice_name: str, description: str) -> dict:
        self.received_sizes.append(len(audio_data))
        return {"status": "success", "voice_id": "vc_sweep_new", "message": "ok"}


class _FakeTTSManager:
    def __init__(self, provider: _SpyCloneProvider) -> None:
        self._provider = provider

    def get_engine(self, name: str) -> _SpyCloneProvider:
        return self._provider


class _FakeSynthTTS:
    """试听合成假 TTS：记录每次调用（text, kwargs），恒返回云端 MP3。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def synthesize(self, text: str, **kwargs) -> SynthesizedAudio:
        self.calls.append((text, kwargs))
        return SynthesizedAudio(data=b"mp3-bytes", fmt="mp3")


# ═══════════════════════════════════════════════════════════
# App 构造（真实登录流；卡库/catalog/角色语音配置全沙箱）
# ═══════════════════════════════════════════════════════════


def _write_card(chars_dir, card_id: str, user_id: int) -> None:
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / f"{card_id}.json").write_text(
        json.dumps({"id": card_id, "name": card_id, "description": "扫描沙箱卡",
                    "user_id": str(user_id)},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def _reset_limiters() -> None:
    """限速桶为模块级内存状态，清零防跨用例/跨文件污染（teardown 亦清）。"""
    for mod, names in (
        (mimo_voice_routes, ("_CLONE_LIMITER", "_SYNTH_LIMITER", "_DESIGN_LIMITER")),
        (voice_routes_mod, ("_VOICE_TEST_LIMITER",)),
    ):
        for name in names:
            limiter = getattr(mod, name, None)
            if limiter is not None:
                limiter.reset()


@pytest.fixture
def sweep_app(tmp_path, monkeypatch):
    """双账号（alice/bob 各持己卡）+ clone 假 provider + 试听假 TTS + 桶清零。"""
    import api.routers.character_routes as character_routes
    import voice.voice_catalog as vc_mod

    # ── DB：真实登录流 ──
    db_path = str(tmp_path / "sweep.db")
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
                (_ADMIN_ID, "admin@sweep.test", "admin", "admin"),
                (_ALICE_ID, "alice@sweep.test", "alice", "viewer"),
                (_BOB_ID, "bob@sweep.test", "bob", "viewer"),
            ):
                session.add(
                    User(id=uid, email=email, username=username,
                         hashed_password=hash_password("Passw0rd!123"),
                         display_name=username, role=role,
                         is_active=True, is_verified=True)
                )
            await session.commit()

    asyncio.run(_init())

    # ── 沙箱：卡库（require_character_access 归属门）──
    chars_dir = tmp_path / "chars"
    _write_card(chars_dir, _ALICE_CARD_ID, _ALICE_ID)
    _write_card(chars_dir, _BOB_CARD_ID, _BOB_ID)
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    # ── 沙箱：voice catalog（clone 产物登记目标）──
    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)

    # ── 沙箱：角色语音配置（voice/test 的 voice_config 来源），双卡各绑一份 ──
    voice_mgr = CharacterVoiceManager(config_path=str(tmp_path / "cv.json"))
    voice_mgr.bind_voice(_ALICE_CARD_ID, "mimo-tts", mimo_model="mimo-v2.5-tts")
    voice_mgr.bind_voice(_BOB_CARD_ID, "mimo-tts", mimo_model="mimo-v2.5-tts")
    monkeypatch.setattr(deps, "_character_voice_mgr", voice_mgr, raising=False)

    # ── 关全局限速中间件（防与被测每用户桶互相干扰）──
    os.environ["RATE_LIMIT_ENABLED"] = "false"

    # ── 伪造件 ──
    fake_provider = _SpyCloneProvider()
    fake_synth = _FakeSynthTTS()

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_tts_manager] = lambda: _FakeTTSManager(fake_provider)
    monkeypatch.setattr(deps, "get_tts", lambda: fake_synth)

    configure_auth(False, "")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    _reset_limiters()
    yield client, fake_provider, fake_synth
    _reset_limiters()

    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    app.dependency_overrides.clear()
    configure_auth(False, "")


async def _login(client: AsyncClient, login: str) -> dict:
    resp = await client.post(
        "/api/auth/login", json={"login": login, "password": "Passw0rd!123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_of(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _alice(sweep_app) -> tuple[AsyncClient, dict]:
    client, *_ = sweep_app
    return client, _auth_of(asyncio.run(_login(client, "alice")))


# ── 手工 multipart（chunked 流式上传用：httpx 对流式 content 不发 Content-Length，
#    恰好绕过 app_factory 的 request_size_limiter——真实攻击路径）──
_BOUNDARY = "----sweepUploadVoiceLimits"


def _clone_multipart_head() -> bytes:
    """voice_name/description 两个 Form 字段 + audio 文件 part 的头部。"""
    return (
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="voice_name"\r\n\r\n'
        f"chunked-clone\r\n"
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="description"\r\n\r\n'
        f"\r\n"
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="ref.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode()


_CLONE_MULTIPART_TAIL = f"\r\n--{_BOUNDARY}--\r\n".encode()


async def _chunked_audio_body(total: int):
    """无 Content-Length 的流式 multipart 体（audio 字段 total 字节）。"""
    yield _clone_multipart_head()
    remaining = total
    block = b"a" * (1024 * 1024)
    while remaining > 0:
        piece = block if remaining >= len(block) else b"a" * remaining
        yield piece
        remaining -= len(piece)
    yield _CLONE_MULTIPART_TAIL


# ═══════════════════════════════════════════════════════════
# ABUSE-2：/api/mimo/clone 上传体大小上限
# ═══════════════════════════════════════════════════════════


async def test_mimo_clone_upload_over_limit_rejected_413(sweep_app):
    """红测核心：chunked（无 Content-Length）上传 50MB+1 参考音频必须 413。

    app_factory 的 request_size_limiter 只看 Content-Length 头（``if content_length:``
    对 chunked 请求放行），故此路径在修复前直达 ``await audio.read()`` 全量载入
    worker 内存——假 provider 应收到 50MB+1 且返回 200（红）。
    """
    client, fake_provider, _ = sweep_app
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        "/api/mimo/clone",
        content=_chunked_audio_body(_MAX_UPLOAD_BYTES + 1),
        headers={
            "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
            **auth,
        },
    )
    assert r.status_code == 413, f"chunked 超限上传应 413，实得 {r.status_code}: {r.text[:200]}"
    assert fake_provider.received_sizes == [], \
        f"超限请求不得送达克隆接口，实得 provider 收到 {fake_provider.received_sizes}"


async def test_mimo_clone_honest_content_length_over_global_limit_413(sweep_app):
    """行为锚定（纵深现状记录）：诚实 Content-Length > 全局 10MB 由
    app_factory.request_size_limiter 先行 413——本域修复不改变该层。"""
    client, fake_provider, _ = sweep_app
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        "/api/mimo/clone",
        data={"voice_name": "n", "description": ""},
        files={"audio": ("ref.wav", b"a" * (11 * 1024 * 1024), "audio/wav")},
        headers=auth,
    )
    assert r.status_code == 413, f"诚实 CL 超全局 10MB 应 413，实得 {r.status_code}: {r.text[:200]}"


async def test_mimo_clone_upload_limit_is_chunk_accumulated(sweep_app, monkeypatch):
    """分块累计判据：上限调小 + 读块调小，跨多块累计同样 413（防只看单块）。"""
    client, fake_provider, _ = sweep_app
    monkeypatch.setattr(mimo_voice_routes, "_MAX_AUDIO_UPLOAD_BYTES", 2048)
    monkeypatch.setattr(mimo_voice_routes, "_READ_CHUNK_SIZE", 256)
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        "/api/mimo/clone",
        data={"voice_name": "n", "description": ""},
        files={"audio": ("ref.wav", b"a" * 4096, "audio/wav")},
        headers=auth,
    )
    assert r.status_code == 413, f"跨块累计超限应 413，实得 {r.status_code}: {r.text[:200]}"
    assert fake_provider.received_sizes == []


async def test_mimo_clone_upload_boundary_accepted(sweep_app, monkeypatch):
    """边界守卫：恰好等于上限的音频不受影响（正常克隆路径不回归）。"""
    client, fake_provider, _ = sweep_app
    monkeypatch.setattr(mimo_voice_routes, "_MAX_AUDIO_UPLOAD_BYTES", 2048)
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        "/api/mimo/clone",
        data={"voice_name": "边界克隆", "description": ""},
        files={"audio": ("ref.wav", b"a" * 2048, "audio/wav")},
        headers=auth,
    )
    assert r.status_code == 200, f"恰好上限应放行，实得 {r.status_code}: {r.text[:200]}"
    assert fake_provider.received_sizes == [2048]


async def test_mimo_clone_content_length_fast_reject(sweep_app, monkeypatch):
    """读前快速通道：整包 Content-Length 超阈值即 413（不触碰分块判据）。"""
    client, fake_provider, _ = sweep_app
    # CL 阈值压到 200（真实请求整包 CL≈350+）；分块上限调巨大以隔离判据来源
    monkeypatch.setattr(mimo_voice_routes, "_CLONE_BODY_FAST_LIMIT", 200)
    monkeypatch.setattr(mimo_voice_routes, "_MAX_AUDIO_UPLOAD_BYTES", 1 << 30)
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        "/api/mimo/clone",
        data={"voice_name": "n", "description": "x" * 100},
        files={"audio": ("ref.wav", b"a" * 64, "audio/wav")},
        headers=auth,
    )
    assert r.status_code == 413, f"CL 快速通道应 413，实得 {r.status_code}: {r.text[:200]}"
    assert fake_provider.received_sizes == []


# ═══════════════════════════════════════════════════════════
# ABUSE-3：/api/characters/{id}/voice/test 文本上限 + 每用户限速
# ═══════════════════════════════════════════════════════════


async def test_voice_test_rejects_overlong_text(sweep_app):
    """红测核心：601 字必须 400 且不触发任何合成（MiMo 云按字符计费直通面）。"""
    client, _, fake_synth = sweep_app
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        f"/api/characters/{_ALICE_CARD_ID}/voice/test",
        json={"text": "啊" * (_MAX_SYNTH_TEXT + 1)},
        headers=auth,
    )
    assert r.status_code == 400, f"超长文本应 400，实得 {r.status_code}: {r.text[:200]}"
    assert "600" in r.json()["detail"], f"400 详情应说明长度上限，实得 {r.json()['detail']!r}"
    assert fake_synth.calls == [], f"超长文本不得触发合成，实得 {len(fake_synth.calls)} 次调用"


async def test_voice_test_accepts_boundary_text(sweep_app):
    """边界守卫：恰好 600 字放行（走真实合成路径，不回归）。"""
    client, _, fake_synth = sweep_app
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        f"/api/characters/{_ALICE_CARD_ID}/voice/test",
        json={"text": "好" * _MAX_SYNTH_TEXT},
        headers=auth,
    )
    assert r.status_code == 200, f"恰好 600 字应放行，实得 {r.status_code}: {r.text[:200]}"
    assert len(fake_synth.calls) == 1


async def test_voice_test_rate_limit_6_per_minute(sweep_app):
    """红测核心：每用户 6 次/分钟——前 6 次放行，第 7 次 429。"""
    client, _, _ = sweep_app
    auth = _auth_of(await _login(client, "alice"))
    statuses = [
        (await client.post(
            f"/api/characters/{_ALICE_CARD_ID}/voice/test",
            json={"text": "你好"},
            headers=auth,
        )).status_code
        for _ in range(7)
    ]
    assert statuses[:6] == [200] * 6, f"前 6 次应放行，实得 {statuses}"
    assert statuses[6] == 429, f"第 7 次应 429，实得 {statuses}"


async def test_voice_test_rate_limit_isolated_per_user(sweep_app):
    """alice 打满配额不连坐 bob（每主体独立计数，viewer 互不牵连）。"""
    client, _, _ = sweep_app
    alice = _auth_of(await _login(client, "alice"))
    for _ in range(6):
        await client.post(
            f"/api/characters/{_ALICE_CARD_ID}/voice/test",
            json={"text": "你好"}, headers=alice,
        )
    bob = _auth_of(await _login(client, "bob"))
    r = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice/test",
        json={"text": "你好"}, headers=bob,
    )
    assert r.status_code != 429, f"bob 不应被 alice 的配额连坐，实得 {r.status_code}: {r.text[:200]}"


async def test_voice_test_normal_text_no_regression(sweep_app):
    """守卫：正常短文本按角色契约合成 200，kwargs 透传不变（与 w7 直呼契约一致）。"""
    client, _, fake_synth = sweep_app
    auth = _auth_of(await _login(client, "alice"))
    r = await client.post(
        f"/api/characters/{_ALICE_CARD_ID}/voice/test",
        json={"text": "试听一句话"},
        headers=auth,
    )
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"] == "audio/mpeg"
    text, kwargs = fake_synth.calls[0]
    assert text == "试听一句话"
    assert kwargs["model"] == "mimo-v2.5-tts"
