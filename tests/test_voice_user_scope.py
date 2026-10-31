"""语音域 per-user 边界钉死 + 归属级对抗测试（本域不开放全局端点）。

═══════════════════════════════════════════════════════════════════════
取证结论（本文件即口径真源，防止后人把 switch-voice / set-engine「顺手开放」）
═══════════════════════════════════════════════════════════════════════
**这两个端点不是 per-user 功能，而是「改全局 TTS 单例」的真全局操作：**

- ``POST /api/mimo/switch-voice``（``api/routers/mimo_voice_routes.py::switch_voice``，:314 起）
  直接 ``provider.set_voice_id(voice_id)`` —— 改的是**进程内唯一的 MiMo provider 实例**；
- ``POST /api/mimo/set-engine``（同文件 ``::set_mimo_engine``，:385-441）
  直接替换 ``tts_manager._providers["mimo-tts"]``（:430）—— 改的是**全进程引擎配置**。

源码注释已自证「P0：改全局 TTS 单例，仅 admin」。若对普通用户开放，效果是
「A 改音色 → 全站所有人的所有角色跟着变」，属于跨用户行为篡改，不是 per-user 能力。

**per-user 能力的真实边界（本域只做取证 + 补测，不新增第二套实现，AGENTS §1.2）：**

1. 音色资产按 owner 隔离 —— 克隆/设计登记时写 ``owner=str(principal.user_id)``
   （``mimo_voice_routes.py:219`` / ``:297``）；合成时他人克隆音色 403、admin 例外、
   平台共享（无 owner）与预设不受限（同文件 ``:468-480``）。
2. 角色音色绑定：**per-user 能力只对「自有私有卡」成立**，公共卡不是 per-user 的。
   ``api/routers/voice_routes.py`` 六条子路由按读写分档：GET、``/voice/test`` 走
   ``require_character_access``（他人私有卡 404；**无主公共卡可读**，2026-10-27 公共池
   改造）；POST/PUT/DELETE 走 ``require_character_write``（他人私有卡**与无主公共卡**
   一律 404）。归属键由 ``card_owner_key`` 从卡内 ``user_id`` 判定，
   ``""/default/system`` = 平台公共卡。
   **注意**（本文件前三版口径均错，已更正）：公共卡不是「一律 404」——读/激活放行、
   写拒绝；写侧用 404（与 `require_character_write` 及全批「无权不区分不存在」口径
   一致，防状态码枚举），不用 403 特例。
3. 音色资产归属（``voice_catalog.entry.owner``）必须在**每个**能落到合成 voice_id 的
   入口强制，且必须按**有效** voice_id 判——四个入口都要覆盖：
   ① 声明字段 ``req.voice_id``；② ``extra_params["voice_id"]``（``_bind_fields`` 先铺
   ``**extra_params``、仅当声明字段非 None 才覆盖）；③ ``speaker_name``
   （``CharacterVoiceSpec.synth_kwargs`` 用 ``voice_id or speaker_name`` 下发）；
   ④ **嵌套** ``extra_params.extra_params.voice_id``（展平后 ``cfg["extra_params"]``
   就是内层 dict，``_pick`` 会读它——只查一层的守卫在此处读空串并放行）。
   PUT 还需按**合并后**的绑定判（只改 speaker_name / extra_params 即可绕过），
   且必须深拷贝后再合并（浅拷贝会就地污染共享的嵌套 dict，403 也救不回内存态）。
   强制点：``api/routers/voice_routes.py:_assert_voice_owner``（唯一），有效音色位由
   ``shisi/voice/character_voice.effective_voice_ids`` 单点定义（**保守上界**，不是
   读取面 ``_pick`` 的逐位镜像），与合成入口 ``mimo_voice_routes.py:468-480`` 同文案 403。只守合成入口是不够的——
   用户可把他人克隆 voice_id 绑到自己卡上，再走 ``/voice/test`` 或 chat 链合成，
   而那两条链路只校验 W14 亲和、不看 owner。
4. chat 链按 ``resolve_voice_spec(character_id, user_id)`` 解析并受 W14 亲和门禁
   （``orchestrator/optimized_orchestrator.py:2293-2296``、``shisi/voice/character_voice.py:220-255``）。
5. 前端已如实告知边界（``frontend/src/pages/SettingsVoice.tsx:504-507`` 注释 +
   ``:629-633``「音色全局切换为管理员能力，普通用户可在角色设置中绑定个人音色」）。

**⚠️ DEFERRED（登记，需主控/前端域接手，不在本批伪做）**

**(A) 公共卡的「个人音色」——**产品级能力回退，必须主控登记 + 前端适配**
``data/character_voices.json`` 是**全局 per-character** 存储（``shisi/voice/
character_voice.py:24`` ``_DEFAULT_CONFIG_PATH``、``:193-199`` ``_bindings[character_id]``，
无用户维度），所以「普通用户给公共卡绑定自己的音色」在现存储下无法表达——写进去
就是改写全站共用配置。本批因此把公共卡写侧收归 admin（否则任何 viewer 都能篡改
41 张公共卡的音色 = 跨用户行为篡改）。**后果（真实产品路径）**：
- 普通用户在 ``/roles/:roleId/settings`` 的「保存音色」对公共卡会得到 **404**，
  detail 为 ``角色不存在: <card_id>``（``require_character_write`` 的防枚举文案，
  ``api/routers/character_routes.py:119-133``）——而页面正显示该角色，文案自相矛盾。
- 前端控件没有任何归属门：``frontend/src/components/admin/RoleSettingsTabs.tsx:460-489``
  对任意卡直接 POST ``/characters/{id}/voice``；``frontend/src/pages/RoleSettings.tsx``
  对公共卡照常渲染整页。**41 张公共卡是产品主路径**，用户侧「角色音色绑定」对绝大多数卡
  实际失效。
- **待办（归属）**：① 主控在 LOG/BOARD 显式登记该能力回退；② **前端域**在公共卡上
  隐藏或禁用音色保存控件，或改为「音色由管理员统一管理」的只读提示；③ 本文件**不得**
  为此放宽任何后端门。
- 注意：这里**不**把 404 改成 403。写作侧统一 404 是已定契约
  （``tests/test_w1_identity_authorization.py:477``「PUT/DELETE → 404」），
  且 404 防枚举口径与全批一致；文案矛盾由前端适配解决（后端不区分「不存在/无权限」
  本身是安全要求）。
- 要真正恢复「公共卡个人音色」，必须给 :class:`CharacterVoiceManager` 加
  ``(character_id, user_id)`` 二级键 + ``resolve_voice_spec`` 按用户优先解析 + chat 链
  透传真实 user_id，属结构性改造（deferred）。

**(B) 存量绑定的运行时复核**：写入口校验无法回溯历史数据。``effective_voice_ids`` +
``audit_voice_owner_mismatches``（``shisi/voice/character_voice.py``）提供只读审计，
``scripts/audit_voice_bindings.py`` 为运维入口（退出码 1 = 有疑似越权绑定）；
本批**不做数据迁移**（避免误删合法配置），清理需人工确认后走管理端重绑/解绑。
残余风险如实说明：修复前已存在的「私有卡绑了他人克隆音色」仍会被 ``/voice/test``
与 chat 链（``resolve_voice_spec`` 只看 W14 亲和）使用，本批以审计清单而非静默迁移处理。

**不得**通过「给全局端点加 user 分支」或「放行公共卡写」来伪做 per-user。

═══════════════════════════════════════════════════════════════════════
测试设计（红测先行；spy 断言比状态码更强——只看 403 分不清「被门拦住」还是「失败但没副作用」）
═══════════════════════════════════════════════════════════════════════
按**函数名**列举（不编号，避免增删用例后编号漂移；总数须与
``grep -cE "^(async )?def test_"`` 恒等——本文件既有 async 用例也有同步用例，
只 grep ``^async def test_`` 会漏掉 ``test_audit_voice_owner_mismatches_flags_legacy_rows``）：
- ``test_viewer_switch_voice_403_and_global_singleton_untouched`` /
  ``test_viewer_set_engine_403_and_global_singleton_untouched``：
  viewer → 403 **且** 全局单例零副作用（set_voice_id 未被调用 / provider 未被替换）
- ``test_admin_switch_voice_200_and_singleton_really_switched`` /
  ``test_admin_set_engine_200_and_provider_really_rebuilt``：
  admin → 200 **且** 副作用真的发生（既有 SEC-P0 语义回归，防「门写宽了但功能坏了」）
- ``test_viewer_synthesize_own_clone_voice_200`` /
  ``test_viewer_synthesize_others_clone_voice_403_without_synthesis``：
  合成入口本人 200 / 他人 403 且合成器零调用
- ``test_admin_synthesize_may_use_others_clone``：admin 例外分支 → 200
- ``test_machine_face_synthesize_not_blocked_by_owner``：机器面（无 Bearer）不被归属门
  拦下（收敛判定函数时把 null principal 当 ``user_id=""`` 会全灭机器面）
- ``test_viewer_bind_voice_to_others_card_404_and_not_written``：
  他人私有卡绑定 404（防枚举）且未落盘
- ``test_viewer_cannot_write_public_card_voice`` / ``test_admin_can_write_public_card_voice``：
  无主公共卡 viewer POST/PUT/DELETE 均 404 **且 detail 都是写门文案**
  （``角色不存在: <id>``，不是 handler 自己的「角色未配置音色」——只断状态码不可判别）、
  未落盘，同卡 GET 200；admin 200 且落盘
- ``test_viewer_cannot_bind_others_clone_voice_to_own_card``：绕过路径①**声明字段**
  （POST 与 PUT 双 403，**且原绑定未被改写**）
- ``test_bind_rejects_others_voice_via_extra_params``：绕过路径②``extra_params.voice_id``
- ``test_bind_rejects_others_voice_via_nested_extra_params``：绕过路径②的**嵌套变体**
  （``extra_params.extra_params.voice_id``，展平后即 ``cfg["extra_params"]``）
- ``test_bind_rejects_others_voice_via_speaker_name``：绕过路径③``speaker_name``
- ``test_put_rejects_others_voice_in_merged_binding``：绕过路径④PUT **合并态**
  （先合法空 voice_id 绑定，再只改 speaker_name）
- ``test_put_rejection_does_not_pollute_shared_binding``：PUT 被拒后**内存绑定零污染**
  （浅拷贝别名缺陷：403 但 ``extra_params`` 已被就地改写）
- ``test_bind_rejects_reserved_extra_params_keys``：``extra_params`` 内保留字段名
  （engine/speaker_name/character_id）→ 400（否则展平后 TypeError → 500）
- ``test_denied_voice_owner_is_single_rule``：owner 判定唯一 owner
  （``voice.voice_catalog.denied_voice_owner``）的四种口径 + 两处 403 共用
  ``VOICE_OWNER_DENIED`` 文案（防合成/绑定两侧口径漂移）
- ``test_audit_voice_owner_mismatches_flags_legacy_rows``：存量绑定审计（问题 6）
- ``test_voice_synthesize_engine_switch_is_admin_only``：``/api/voice/synthesize`` 的
  ``engine`` 不得成为普通用户的全局引擎开关（潜伏面）
- ``test_viewer_can_bind_own_and_platform_voice`` / ``test_viewer_bind_voice_to_own_card_200``：
  正向对照（本人/平台共享/预设均 200；本人卡落盘）——防上面 403 用例是「链路整体坏掉」的假绿

全部 tmp_path 沙箱（DB / 卡库 / voice catalog / 角色音色配置），不写真实 data/。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-voice-user-scope-32chars-ok!!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, get_db
from api.deps import deps, get_tts_manager
from api.routers import mimo_voice_routes
from shisi.voice.character_voice import CharacterVoiceManager
from voice.audio_result import SynthesizedAudio
from voice.mimo_tts_provider import MiMoTTSProvider
from voice.voice_catalog import VOICE_OWNER_DENIED, get_voice_catalog

_ADMIN_ID = 1
_ALICE_ID = 2
_BOB_ID = 3

_ALICE_CARD_ID = "alice-card"
_BOB_CARD_ID = "bob-card"
# 无主公共卡（user_id 留空 / default）：对普通用户**可读、不可写**
PUBLIC_CARD_ID = "public-card"

# catalog 沙箱条目：alice / bob 各持一份克隆音色，另加一条无 owner 的平台共享
_ALICE_VOICE = "vc_alice"
_BOB_VOICE = "vc_bob"
_PLATFORM_VOICE = "vc_platform"


# ═══════════════════════════════════════════════════════════
# 伪造件：全局单例间谍（必须继承 MiMoTTSProvider）
# ═══════════════════════════════════════════════════════════


class _SpyProvider(MiMoTTSProvider):
    """全局 provider 间谍。

    必须继承 ``MiMoTTSProvider``：三个端点都有 ``isinstance(provider, MiMoTTSProvider)``
    门，假对象会在进入被测逻辑之前被 400 挡住（那样 spy 断言就毫无意义）。
    同时基类 ``__init__`` 必须真跑——``set-engine`` 会读 ``_api_key`` / ``_voice_id`` /
    ``_timeout`` / ``_fallback_local`` / ``_api_base`` 去重建 provider。
    """

    def __init__(self) -> None:
        super().__init__(api_key="spy-key", model="mimo-v2.5-tts", voice_id="")
        self.set_voice_id_calls: list[str] = []
        self.synthesize_calls: list[tuple[str, dict[str, Any]]] = []

    def set_voice_id(self, voice_id: str) -> None:
        """记录全局音色切换（这是「跨用户行为篡改」的那一下）。"""
        self.set_voice_id_calls.append(voice_id)
        super().set_voice_id(voice_id)

    def health_check(self) -> dict[str, Any]:
        return {"model": self._model, "voice_id": self._voice_id}

    async def synthesize(self, text: str, **kwargs: Any) -> SynthesizedAudio | None:
        """恒成功返回 MP3 字节——用来把「被归属门拦下」与「合成失败」区分开。"""
        self.synthesize_calls.append((text, dict(kwargs)))
        return SynthesizedAudio(data=b"mp3-bytes", fmt="mp3")


class _FakeTTSManager:
    """最小 TTSManager 替身：``set-engine`` 直接替换 ``_providers["mimo-tts"]``。"""

    def __init__(self, provider: _SpyProvider) -> None:
        self.current_engine = "mimo-tts"
        self.available_engines = ["mimo-tts"]
        self._providers: dict[str, Any] = {"mimo-tts": provider}

    def get_engine(self, name: str) -> Any:
        return self._providers.get(name)

    def provider_of(self, name: str = "mimo-tts") -> Any:
        return self._providers.get(name)


# ═══════════════════════════════════════════════════════════
# App 构造（真实登录流；卡库 / catalog / 角色音色配置全沙箱）
# ═══════════════════════════════════════════════════════════


def _write_card(chars_dir, card_id: str, user_id: int | str) -> None:
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / f"{card_id}.json").write_text(
        json.dumps(
            {
                "id": card_id,
                "name": card_id,
                "description": "语音域归属沙箱卡",
                "user_id": str(user_id),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _seed_catalog(entries: list[dict[str, Any]]) -> None:
    get_voice_catalog()._entries = {e["voice_id"]: dict(e) for e in entries}


def _reset_limiters() -> None:
    """限速桶是模块级内存状态，setup/teardown 双清零防跨用例、跨文件污染。"""
    for name in ("_SYNTH_LIMITER", "_CLONE_LIMITER", "_DESIGN_LIMITER"):
        limiter = getattr(mimo_voice_routes, name, None)
        if limiter is not None:
            limiter.reset()


@pytest.fixture
def voice_scope_app(tmp_path, monkeypatch):
    """三账号（admin/alice/bob）+ 双私有卡 + 三音色 catalog + 全局 provider 间谍。"""
    import api.routers.character_routes as character_routes
    import voice.voice_catalog as vc_mod

    # ── DB：真实登录流（角色取自数据库当前值，见 require_role docstring）──
    db_path = str(tmp_path / "voice_scope.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for uid, email, username, role in (
                (_ADMIN_ID, "admin@voice-scope.test", "admin", "admin"),
                (_ALICE_ID, "alice@voice-scope.test", "alice", "viewer"),
                (_BOB_ID, "bob@voice-scope.test", "bob", "viewer"),
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

    # ── 沙箱：卡库（require_character_access 归属门的数据源）──
    chars_dir = tmp_path / "chars"
    _write_card(chars_dir, _ALICE_CARD_ID, _ALICE_ID)
    _write_card(chars_dir, _BOB_CARD_ID, _BOB_ID)
    _write_card(chars_dir, PUBLIC_CARD_ID, "default")  # 公共池：无主
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    # ── 沙箱：voice catalog（owner 归属的数据源）──
    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)
    _seed_catalog(
        [
            {"voice_id": _ALICE_VOICE, "name": _ALICE_VOICE, "kind": "clone",
             "model": "mimo-v2.5-tts-voiceclone", "description": "", "owner": str(_ALICE_ID)},
            {"voice_id": _BOB_VOICE, "name": _BOB_VOICE, "kind": "clone",
             "model": "mimo-v2.5-tts-voiceclone", "description": "", "owner": str(_BOB_ID)},
            {"voice_id": _PLATFORM_VOICE, "name": _PLATFORM_VOICE, "kind": "clone",
             "model": "mimo-v2.5-tts-voiceclone", "description": ""},  # 无 owner = 平台共享
        ]
    )

    # ── 沙箱：角色音色配置（bind 落盘目标）──
    voice_mgr = CharacterVoiceManager(config_path=str(tmp_path / "character_voices.json"))
    monkeypatch.setattr(deps, "_character_voice_mgr", voice_mgr, raising=False)

    # ── 全局 provider 间谍 + 假 TTS 管理器 ──
    spy = _SpyProvider()
    manager = _FakeTTSManager(spy)

    # 关全局限速中间件，避免与被测的每用户桶互相干扰（dev 默认即 false，防御性显式关）。
    # 用 monkeypatch.setenv 而非 os.environ[]——后者不复原，会把 "false" 泄漏给同进程
    # 后续测试文件（生产语义环境下可能压掉别人期待的全局 429）。
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_tts_manager] = lambda: manager
    configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    _reset_limiters()
    yield client, spy, manager, voice_mgr
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


# ═══════════════════════════════════════════════════════════
# 全局控制面：viewer → 403 且零副作用
# ═══════════════════════════════════════════════════════════


async def test_viewer_switch_voice_403_and_global_singleton_untouched(voice_scope_app):
    """普通用户改全局音色：403，且 ``set_voice_id`` 一次都没被调用。

    只断言 403 不够——若门被写成「先改再拒」（抛 HTTPException 之前已生效），
    状态码照样是 403 但全站音色已经变了。spy 记的是那一次副作用本身。
    """
    client, spy, _, _ = voice_scope_app
    auth = _auth_of(await _login(client, "alice"))

    r = await client.post(
        "/api/mimo/switch-voice", data={"voice_id": _PLATFORM_VOICE}, headers=auth
    )

    assert r.status_code == 403, f"viewer 切全局音色应 403，实得 {r.status_code}: {r.text}"
    assert spy.set_voice_id_calls == [], (
        f"403 之外还要求全局音色零副作用，实得调用记录 {spy.set_voice_id_calls}"
    )


async def test_viewer_set_engine_403_and_global_singleton_untouched(voice_scope_app):
    """普通用户换全局引擎：403，且 ``tts_manager._providers`` 未被替换。"""
    client, spy, manager, _ = voice_scope_app
    auth = _auth_of(await _login(client, "alice"))

    r = await client.post(
        "/api/mimo/set-engine", data={"model": "mimo-v2.5-tts-voiceclone"}, headers=auth
    )

    assert r.status_code == 403, f"viewer 换全局引擎应 403，实得 {r.status_code}: {r.text}"
    assert manager.provider_of() is spy, (
        "403 之外还要求全局引擎单例未被替换，实得 provider 已换为 "
        f"{type(manager.provider_of()).__name__}"
    )


# ═══════════════════════════════════════════════════════════
# 全局控制面：admin → 200 且副作用真的发生（SEC-P0 语义回归）
# ═══════════════════════════════════════════════════════════


async def test_admin_switch_voice_200_and_singleton_really_switched(voice_scope_app):
    """admin 是这条全局链路的唯一合法主体：200 且 ``set_voice_id`` 真被调用一次。"""
    client, spy, _, _ = voice_scope_app
    auth = _auth_of(await _login(client, "admin"))

    r = await client.post(
        "/api/mimo/switch-voice", data={"voice_id": _PLATFORM_VOICE}, headers=auth
    )

    assert r.status_code == 200, f"admin 切全局音色应 200，实得 {r.status_code}: {r.text}"
    assert spy.set_voice_id_calls == [_PLATFORM_VOICE], (
        f"admin 必须真的改到全局单例，实得调用记录 {spy.set_voice_id_calls}"
    )
    assert r.json()["voice_id"] == _PLATFORM_VOICE


async def test_admin_set_engine_200_and_provider_really_rebuilt(voice_scope_app):
    """admin 换引擎：200 且 ``_providers["mimo-tts"]`` 真被换成新模型的 provider。"""
    client, spy, manager, _ = voice_scope_app
    auth = _auth_of(await _login(client, "admin"))

    r = await client.post(
        "/api/mimo/set-engine", data={"model": "mimo-v2.5-tts-voiceclone"}, headers=auth
    )

    assert r.status_code == 200, f"admin 换全局引擎应 200，实得 {r.status_code}: {r.text}"
    rebuilt = manager.provider_of()
    assert rebuilt is not spy, "admin 换引擎后全局单例必须被重建（旧 spy 不应还在位）"
    assert isinstance(rebuilt, MiMoTTSProvider), f"重建品必须是真 provider，实得 {type(rebuilt)}"
    assert rebuilt._model == "mimo-v2.5-tts-voiceclone", (
        f"重建品模型应为请求指定值，实得 {rebuilt._model}"
    )
    assert r.json()["model"] == "mimo-v2.5-tts-voiceclone"


# ═══════════════════════════════════════════════════════════
# 音色资产归属：合成入口（mimo_voice_routes.py:468-480 的 denied_voice_owner 块）
# ═══════════════════════════════════════════════════════════


async def test_viewer_synthesize_own_clone_voice_200(voice_scope_app):
    """本人克隆音色可用：过归属门 → 真合成 → 200（正向对照，防用例 6 假绿）。"""
    client, spy, _, _ = voice_scope_app
    auth = _auth_of(await _login(client, "alice"))

    r = await client.post(
        "/api/mimo/synthesize", data={"text": "你好", "voice_id": _ALICE_VOICE}, headers=auth
    )

    assert r.status_code == 200, f"本人克隆音色应 200，实得 {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("audio/"), r.headers.get("content-type")
    assert len(spy.synthesize_calls) == 1, f"合成器应被调用一次，实得 {spy.synthesize_calls}"
    text, kwargs = spy.synthesize_calls[0]
    assert text == "你好"
    assert kwargs.get("voice_id") == _ALICE_VOICE, f"合成器应收到本人 voice_id，实得 {kwargs}"


async def test_viewer_synthesize_others_clone_voice_403_without_synthesis(voice_scope_app):
    """他人克隆音色：403 且合成器**根本没被调用**（归属门在服务状态暴露前拦下）。

    这一条是本次「边界钉死」的核心资产级护栏：音色资产本身按 owner 隔离，
    所以不需要给用户开全局 switch-voice —— 用户要的「用自己的音色」已经可用。
    """
    client, spy, _, _ = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    r = await client.post(
        "/api/mimo/synthesize", data={"text": "你好", "voice_id": _ALICE_VOICE}, headers=auth
    )

    assert r.status_code == 403, f"他人克隆音色应 403，实得 {r.status_code}: {r.text}"
    assert r.json()["detail"] == VOICE_OWNER_DENIED, r.json()
    assert spy.synthesize_calls == [], (
        f"403 之外还要求合成器零调用，实得 {spy.synthesize_calls}"
    )


# ═══════════════════════════════════════════════════════════
# 角色音色绑定：卡片归属门（读 access / 写 write → 404）
# ═══════════════════════════════════════════════════════════


async def test_viewer_bind_voice_to_others_card_404_and_not_written(voice_scope_app):
    """对他人角色卡绑定音色：404（不区分不存在/无权限，防枚举）且未落盘。"""
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "alice"))

    r = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"engine": "mimo-tts", "mimo_model": "mimo-v2.5-tts"},
        headers=auth,
    )

    assert r.status_code == 404, f"他人角色卡绑定音色应 404，实得 {r.status_code}: {r.text}"
    # 必须是归属门自己的 404（防枚举文案），而不是路由不存在的裸 "Not Found"
    assert r.json()["detail"] == f"角色不存在: {_BOB_CARD_ID}", (
        f"404 必须来自 require_character_write 的防枚举文案，实得 {r.json()}"
    )
    assert voice_mgr.get_voice_config(_BOB_CARD_ID) is None, (
        "404 之外还要求未落盘，实得 bob 卡已被写入音色配置"
    )


async def test_viewer_bind_voice_to_own_card_200(voice_scope_app):
    """本人角色卡可以绑个人音色：200 且落盘——这是普通用户该走的 per-user 路径。"""
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "alice"))

    r = await client.post(
        f"/api/characters/{_ALICE_CARD_ID}/voice",
        json={"engine": "mimo-tts", "mimo_model": "mimo-v2.5-tts", "voice_id": _ALICE_VOICE},
        headers=auth,
    )

    assert r.status_code == 200, f"本人角色卡绑定音色应 200，实得 {r.status_code}: {r.text}"
    cfg = voice_mgr.get_voice_config(_ALICE_CARD_ID)
    assert cfg is not None, "本人绑定必须落盘"
    assert cfg.get("voice_id") == _ALICE_VOICE, f"落盘音色应为请求值，实得 {cfg}"


async def test_viewer_cannot_write_public_card_voice(voice_scope_app):
    """无主公共卡：POST/PUT/DELETE 三个写动词全部被**写门**拒绝且未落盘。

    ``character_voices.json`` 是**全局 per-character** 存储（``shisi/voice/
    character_voice.py:24`` + ``:107-115``，不分用户），若写侧只挂读级门，任何
    viewer 都能改写/解绑 41 张公共卡的音色 → 影响所有使用该卡的人，正是本域要防的
    「跨用户行为篡改」。写侧统一 404（``require_character_write`` 的防枚举口径，
    与全批「无权不区分不存在」一致），不做 403 特例。

    **判别性要求**：三个动词都必须断 ``detail``，不能只断状态码。公共卡上没有绑定
    时会另有两条 404 来源——PUT 的「角色未配置音色，请先绑定」
    （``voice_routes.py:298``）、DELETE 的「角色未配置音色」（同文件 ``:353``）。
    只断 404 的话，把 PUT/DELETE 的依赖退回读级 ``require_character_access`` 仍然全绿
    （handler 自己也会 404），等于没有回归网。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "alice"))
    expected_detail = f"角色不存在: {PUBLIC_CARD_ID}"

    r_post = await client.post(
        f"/api/characters/{PUBLIC_CARD_ID}/voice",
        json={"engine": "mimo-tts", "mimo_model": "mimo-v2.5-tts"},
        headers=auth,
    )
    assert r_post.status_code == 404, f"公共卡 POST 应 404，实得 {r_post.status_code}"
    assert r_post.json()["detail"] == expected_detail, (
        f"POST 的 404 必须来自写门（防枚举文案），实得 {r_post.json()}"
    )

    r_put = await client.put(
        f"/api/characters/{PUBLIC_CARD_ID}/voice",
        json={"voice_id": _ALICE_VOICE},
        headers=auth,
    )
    assert r_put.status_code == 404, f"公共卡 PUT 应 404，实得 {r_put.status_code}"
    assert r_put.json()["detail"] == expected_detail, (
        "PUT 的 404 必须来自写门；若为「角色未配置音色，请先绑定」说明写门已退回读级。"
        f"实得 {r_put.json()}"
    )

    r_del = await client.delete(f"/api/characters/{PUBLIC_CARD_ID}/voice", headers=auth)
    assert r_del.status_code == 404, f"公共卡 DELETE 应 404，实得 {r_del.status_code}"
    assert r_del.json()["detail"] == expected_detail, (
        "DELETE 的 404 必须来自写门；若为「角色未配置音色」说明写门已退回读级。"
        f"实得 {r_del.json()}"
    )

    assert voice_mgr.get_voice_config(PUBLIC_CARD_ID) is None, "公共卡不得被 viewer 写入音色"

    # 读侧口径对照：公共卡对普通用户**可读**（不是 404）——防止把「写 404」误推成「读 404」
    r_get = await client.get(f"/api/characters/{PUBLIC_CARD_ID}/voice", headers=auth)
    assert r_get.status_code == 200, f"公共卡读侧应 200（公共池口径），实得 {r_get.status_code}"


async def test_admin_can_write_public_card_voice(voice_scope_app):
    """公共卡是平台资产：admin 保留写权（否则 41 张公共卡音色无人可管）。"""
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "admin"))

    r = await client.post(
        f"/api/characters/{PUBLIC_CARD_ID}/voice",
        json={"engine": "mimo-tts", "mimo_model": "mimo-v2.5-tts", "voice_id": _PLATFORM_VOICE},
        headers=auth,
    )

    assert r.status_code == 200, f"admin 写公共卡音色应 200，实得 {r.status_code}: {r.text}"
    cfg = voice_mgr.get_voice_config(PUBLIC_CARD_ID)
    assert cfg is not None and cfg.get("voice_id") == _PLATFORM_VOICE, cfg


# ═══════════════════════════════════════════════════════════
# 音色资产归属的**旁路**——绑定入口必须和合成入口同样强制 owner
# ═══════════════════════════════════════════════════════════


async def test_viewer_cannot_bind_others_clone_voice_to_own_card(voice_scope_app):
    """把他人克隆 voice_id 绑到自己卡上：403 且未落盘。

    这是「只守 /api/mimo/synthesize」的漏洞：绑上之后走 ``/voice/test`` 或 chat 链
    合成时，``resolve_voice_spec`` 只校验 W14 亲和、不看 owner，403 永远不会触发。
    因此 owner 强制点必须同时存在于绑定入口：``voice_routes._assert_voice_owner``。
    **不在** ``_validate_contract`` 里做——那函数只查声明字段，会被下面五个旁路绕过；
    有效音色位由 ``shisi.voice.character_voice.effective_voice_ids`` 单点定义。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    r = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"engine": "mimo-tts", "voice_id": _ALICE_VOICE},  # alice 的克隆
        headers=auth,
    )

    assert r.status_code == 403, f"绑他人克隆音色应 403，实得 {r.status_code}: {r.text}"
    assert r.json()["detail"] == VOICE_OWNER_DENIED, r.json()
    assert voice_mgr.get_voice_config(_BOB_CARD_ID) is None, "403 之外还要求未落盘"

    # PUT 声明字段：先合法绑定（无绑定会先撞 404「请先绑定」），再 PUT 他人克隆 → 403
    r0 = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"engine": "mimo-tts", "voice_id": _PLATFORM_VOICE},
        headers=auth,
    )
    assert r0.status_code == 200, f"前置合法绑定应 200，实得 {r0.status_code}: {r0.text}"

    r_put = await client.put(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"voice_id": _ALICE_VOICE},
        headers=auth,
    )
    assert r_put.status_code == 403, f"PUT 他人克隆音色应 403，实得 {r_put.status_code}"
    assert (voice_mgr.get_voice_config(_BOB_CARD_ID) or {}).get("voice_id") == _PLATFORM_VOICE, (
        "403 之外还要求原绑定未被改写"
    )


async def test_viewer_can_bind_own_and_platform_voice(voice_scope_app):
    """正向对照：本人克隆 / 无主平台共享 / 静态预设都可绑——403 只针对他人资产。"""
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    for voice_id in (_BOB_VOICE, _PLATFORM_VOICE, "mimo_default"):
        r = await client.post(
            f"/api/characters/{_BOB_CARD_ID}/voice",
            json={"engine": "mimo-tts", "voice_id": voice_id},
            headers=auth,
        )
        assert r.status_code == 200, f"绑定 {voice_id} 应 200，实得 {r.status_code}: {r.text}"
        assert (voice_mgr.get_voice_config(_BOB_CARD_ID) or {}).get("voice_id") == voice_id


async def test_admin_synthesize_may_use_others_clone(voice_scope_app):
    """admin 例外分支：``mimo_voice_routes.py:468-480`` 的 ``denied_voice_owner(... is_admin=principal.role == "admin")``。"""
    client, spy, _, _ = voice_scope_app
    auth = _auth_of(await _login(client, "admin"))

    r = await client.post(
        "/api/mimo/synthesize", data={"text": "你好", "voice_id": _ALICE_VOICE}, headers=auth
    )

    assert r.status_code == 200, f"admin 用他人克隆音色应 200，实得 {r.status_code}: {r.text}"
    assert spy.synthesize_calls and spy.synthesize_calls[0][1].get("voice_id") == _ALICE_VOICE


async def test_machine_face_synthesize_not_blocked_by_owner(voice_scope_app):
    """机器面（无 Bearer）不参与音色归属拦截——W1 既有契约。

    这是把归属判定收敛到 ``denied_voice_owner`` 时最容易踩的坑：若把「无主体」
    解释成 ``user_id=""`` 交给函数，「他人音色」会恒判 True（owner "2" != ""），
    机器面全灭。实现必须在调用点显式跳过 principal is None，本用例钉住。
    """
    client, spy, _, _ = voice_scope_app  # configure_auth(False) ⇒ 无 Authorization 即机器面

    r = await client.post(
        "/api/mimo/synthesize", data={"text": "你好", "voice_id": _ALICE_VOICE}
    )

    assert r.status_code == 200, f"机器面合成不应被归属门拦下，实得 {r.status_code}: {r.text}"
    assert spy.synthesize_calls and spy.synthesize_calls[0][1].get("voice_id") == _ALICE_VOICE


# ═══════════════════════════════════════════════════════════
# 绑定侧 owner 强制：全部旁路字段与合并态（只看声明字段 = 假护栏）
# ═══════════════════════════════════════════════════════════


async def test_bind_rejects_others_voice_via_extra_params(voice_scope_app):
    """旁路②：``extra_params.voice_id``。

    ``_bind_fields`` 先铺 ``**req.extra_params``，只有 ``req.voice_id is not None``
    时才覆盖 → 声明字段留空 + extra_params 塞他人克隆 id，旧实现可原样落盘，
    随后 ``resolve_voice_spec`` 直接把它当合成 voice_id。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    r = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"engine": "mimo-tts", "extra_params": {"voice_id": _ALICE_VOICE}},
        headers=auth,
    )

    assert r.status_code == 403, f"extra_params 夹带他人音色应 403，实得 {r.status_code}: {r.text}"
    assert r.json()["detail"] == VOICE_OWNER_DENIED, r.json()
    assert voice_mgr.get_voice_config(_BOB_CARD_ID) is None, "403 之外还要求未落盘"


async def test_bind_rejects_others_voice_via_speaker_name(voice_scope_app):
    """旁路③：``speaker_name``。

    ``CharacterVoiceSpec.synth_kwargs``（shisi/voice/character_voice.py:138-145）用
    ``voice = self.voice_id or self.speaker_name`` 下发——voice_id 为空时
    speaker_name 就是音色 id，不校验即等于把他人克隆音色送进合成链。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    r = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"engine": "mimo-tts", "speaker_name": _ALICE_VOICE},
        headers=auth,
    )

    assert r.status_code == 403, f"speaker_name 夹带他人音色应 403，实得 {r.status_code}: {r.text}"
    assert r.json()["detail"] == VOICE_OWNER_DENIED, r.json()
    assert voice_mgr.get_voice_config(_BOB_CARD_ID) is None, "403 之外还要求未落盘"


async def test_put_rejects_others_voice_in_merged_binding(voice_scope_app):
    """旁路④：PUT 的**合并态**。

    先合法绑定（voice_id 为空、speaker_name 为预设），再 PUT 只改 speaker_name
    为他人克隆 id——单看 ``req.voice_id``（None）会直接放行。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    r0 = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"engine": "mimo-tts", "speaker_name": "mimo_default"},
        headers=auth,
    )
    assert r0.status_code == 200, f"前置合法绑定应 200，实得 {r0.status_code}: {r0.text}"
    # 必须深拷贝快照：get_voice_config 返回 _bindings 的原引用，直接存引用再比较
    # 会恒等（即使内存已被就地改写），是标准的假绿写法。
    before = copy.deepcopy(voice_mgr.get_voice_config(_BOB_CARD_ID))

    r = await client.put(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"speaker_name": _ALICE_VOICE},
        headers=auth,
    )

    assert r.status_code == 403, f"PUT 合并态夹带他人音色应 403，实得 {r.status_code}: {r.text}"
    assert voice_mgr.get_voice_config(_BOB_CARD_ID) == before, "403 之外还要求绑定未被改写"


async def test_bind_rejects_others_voice_via_nested_extra_params(voice_scope_app):
    """旁路⑤：``extra_params.extra_params.voice_id``（嵌套一层）。

    ``_bind_fields`` 用 ``{**req.extra_params}`` 展平：客户端把 ``extra_params`` 自己
    塞进 ``extra_params``，展平后 ``cfg["extra_params"]`` 就是内层 dict，
    ``CharacterVoiceSpec._pick`` 会读它的 ``voice_id``。只查一层的守卫在此读到空串
    并放行 → 单次 POST 即可盗用他人音色，无需先合法绑定（本批 HIGH 缺陷）。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    r = await client.post(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={
            "engine": "mimo-tts",
            "extra_params": {"extra_params": {"voice_id": _ALICE_VOICE}},
        },
        headers=auth,
    )

    assert r.status_code == 403, f"嵌套 extra_params 夹带他人音色应 403，实得 {r.status_code}: {r.text}"
    assert r.json()["detail"] == VOICE_OWNER_DENIED, r.json()
    assert voice_mgr.get_voice_config(_BOB_CARD_ID) is None, "403 之外还要求未落盘"


async def test_put_rejection_does_not_pollute_shared_binding(voice_scope_app):
    """旁路⑥：PUT 浅拷贝别名——403 之后内存绑定也必须零污染。

    ``get_voice_config`` 返回 ``_bindings[character_id]`` 的**原引用**，
    ``dict(current)`` 只浅拷贝；``extra = updated.get("extra_params")`` 与 current
    共享同一嵌套 dict，``extra.update(req.extra_params)`` 在校验前就改掉内存态。
    结果：接口回 403，但 ``resolve_voice_spec`` 立刻返回他人音色。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    # 直接播种「旧形态」绑定：cfg 里存在嵌套 extra_params 字典。这是历史数据真实形态
    # （`_bind_fields` 展平后只有客户端显式塞 extra_params 键才会留下这一层）。
    voice_mgr.bind_voice(
        _BOB_CARD_ID, "mimo-tts", extra_params={"custom": 1},
    )
    before = copy.deepcopy(voice_mgr.get_voice_config(_BOB_CARD_ID))
    assert isinstance(before.get("extra_params"), dict)

    r = await client.put(
        f"/api/characters/{_BOB_CARD_ID}/voice",
        json={"extra_params": {"voice_id": _ALICE_VOICE}},
        headers=auth,
    )

    assert r.status_code == 403, f"PUT 夹带他人音色应 403，实得 {r.status_code}: {r.text}"
    after = voice_mgr.get_voice_config(_BOB_CARD_ID)
    assert after == before, f"403 后内存绑定必须零污染，before={before} after={after}"
    spec = voice_mgr.resolve_voice_spec(_BOB_CARD_ID)
    assert spec is not None and spec.voice_id == "", (
        f"resolve_voice_spec 不得读到被污染的 voice_id，实得 {spec}"
    )


def test_denied_voice_owner_is_single_rule(tmp_path):
    """owner 判定只在一处（``voice.voice_catalog.denied_voice_owner``）。

    合成入口（``/api/mimo/synthesize``）与绑定入口（``/api/characters/{id}/voice``
    POST/PUT）必须共用它；两处 403 的文案也由 ``VOICE_OWNER_DENIED`` 单点提供
    （上面各 403 用例按常量断言，改文案/改口径不会一处漂移另一处照旧）。

    本用例钉死四种口径：他人资产拒、本人放行、平台共享（无 owner 键）放行、
    admin 例外；空 voice_id（= 用引擎默认）不参与判定。
    """
    from voice.voice_catalog import VoiceCatalog, denied_voice_owner

    cat = VoiceCatalog(str(tmp_path / "owner_rule_catalog.json"))
    cat.register(voice_id="vc_a", name="a", kind="clone", model="m", owner=str(_ALICE_ID))
    cat.register(voice_id="vc_shared", name="s", kind="clone", model="m", owner="")

    assert denied_voice_owner("vc_a", user_id=str(_BOB_ID), catalog=cat) is True
    assert denied_voice_owner("vc_a", user_id=str(_ALICE_ID), catalog=cat) is False
    assert denied_voice_owner("vc_a", user_id=str(_BOB_ID), is_admin=True, catalog=cat) is False
    assert denied_voice_owner("vc_shared", user_id=str(_BOB_ID), catalog=cat) is False
    assert denied_voice_owner("", user_id=str(_BOB_ID), catalog=cat) is False
    assert denied_voice_owner("mimo_default", user_id=str(_BOB_ID), catalog=cat) is False


def test_audit_voice_owner_mismatches_flags_legacy_rows(tmp_path, monkeypatch):
    """存量绑定审计：只报「音色 owner ≠ 卡片 owner」的私有卡行，平台卡/无主音色不报。

    写入口校验无法回溯历史数据（问题 6）——本用例钉住审计函数的口径，
    保证运维有可执行清单，而不是「修复后仍可合成他人音色」的盲区。
    """
    from shisi.voice.character_voice import audit_voice_owner_mismatches

    class _Catalog:
        _entries = {
            "vc_alice": {"voice_id": "vc_alice", "owner": "2"},
            "vc_bob": {"voice_id": "vc_bob", "owner": "3"},
            "vc_platform": {"voice_id": "vc_platform"},
        }

        def get(self, voice_id):
            return self._entries.get(voice_id)

    bindings = {
        "bad-card": {"engine": "mimo-tts", "voice_id": "vc_alice"},          # 卡属于 bob == 3
        "nested-bad": {"engine": "mimo-tts", "extra_params": {"voice_id": "vc_bob"}},  # 卡属于 alice == 2
        "good-card": {"engine": "mimo-tts", "voice_id": "vc_bob"},           # 卡属于 bob == 3
        "platform-voice": {"engine": "mimo-tts", "voice_id": "vc_platform"}, # 无 owner 不报
        "public-card": {"engine": "mimo-tts", "voice_id": "vc_alice"},       # 卡片无主不报
    }
    owners = {"bad-card": "3", "nested-bad": "2", "good-card": "3",
              "platform-voice": "3", "public-card": "default"}

    rows = audit_voice_owner_mismatches(bindings, lambda cid: owners.get(cid, ""), _Catalog())

    flagged = {(r["character_id"], r["voice_id"]) for r in rows}
    assert flagged == {("bad-card", "vc_alice"), ("nested-bad", "vc_bob")}, rows


async def test_voice_synthesize_engine_switch_is_admin_only(voice_scope_app, monkeypatch):
    """``/api/voice/synthesize`` 的 ``engine`` 不得成为普通用户的全局引擎开关。

    ``voice/tts_manager.py:157-164`` 的 ``switch_engine`` 直接改 ``_current_engine``
    （全站生效）。今天只注册 mimo-tts 一个 provider 所以不可利用，但一旦新增第二引擎，
    任何登录用户都能借合成端点切全局引擎——潜伏面必须在有第二个引擎之前钉死。
    """
    client, _, _, _ = voice_scope_app

    class _TwoEngineTTS:
        def __init__(self):
            self.enabled = True
            self._current_engine = "mimo-tts"
            self._providers = {"mimo-tts": object(), "sapi": object()}
            self.switch_calls: list[str] = []

        @property
        def current_engine(self):
            return self._current_engine

        @property
        def available_engines(self):
            return list(self._providers)

        async def switch_engine(self, name):
            self.switch_calls.append(name)
            self._current_engine = name
            return True

        async def synthesize(self, text):
            return SynthesizedAudio(data=b"mp3-bytes", fmt="mp3")

    fake = _TwoEngineTTS()
    monkeypatch.setattr(deps, "get_tts", lambda: fake)

    viewer = _auth_of(await _login(client, "bob"))
    r = await client.post(
        "/api/voice/synthesize", data={"text": "你好", "engine": "sapi"}, headers=viewer
    )
    assert r.status_code == 403, f"viewer 切全局引擎应 403，实得 {r.status_code}: {r.text}"
    assert fake.switch_calls == [], f"403 之外还要求未切引擎，实得 {fake.switch_calls}"
    assert fake.current_engine == "mimo-tts"

    admin = _auth_of(await _login(client, "admin"))
    r = await client.post(
        "/api/voice/synthesize", data={"text": "你好", "engine": "sapi"}, headers=admin
    )
    assert r.status_code == 200, f"admin 切引擎应 200，实得 {r.status_code}: {r.text}"
    assert fake.switch_calls == ["sapi"], fake.switch_calls


async def test_bind_rejects_reserved_extra_params_keys(voice_scope_app):
    """``extra_params`` 里出现保留字段名（engine/speaker_name/character_id）→ 400。

    ``_bind_fields`` 用 ``**`` 展平，同名键会与显式形参冲突 →
    ``TypeError: got multiple values for keyword argument`` → 500。这些键在契约里
    都有专用顶层字段，客户端没有理由塞进 ``extra_params``；一律 400 fail-closed。
    """
    client, _, _, voice_mgr = voice_scope_app
    auth = _auth_of(await _login(client, "bob"))

    for reserved in ("engine", "speaker_name", "character_id"):
        r = await client.post(
            f"/api/characters/{_BOB_CARD_ID}/voice",
            json={"engine": "mimo-tts", "extra_params": {reserved: "x"}},
            headers=auth,
        )
        assert r.status_code == 400, f"{reserved} 应 400，实得 {r.status_code}: {r.text}"
        assert reserved in r.json()["detail"], r.json()

    assert voice_mgr.get_voice_config(_BOB_CARD_ID) is None, "400 之外还要求未落盘"
