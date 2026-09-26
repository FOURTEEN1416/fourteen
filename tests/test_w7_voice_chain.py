"""W7 语音链根治红测 — 角色语音契约 / 音色 catalog / 统一音频对象 / 克隆方向

覆盖任务书六项任务的可测核心：
- A/E 角色 voice/model 进入对话合成（不可变快照，禁 provider 全局态模拟角色）
- B 克隆/设计 voice_id 落 catalog 持久化，可检索、可绑定
- C 引擎名/模型名/voice_id 类型分明（engine 名不进模型白名单）
- D 试听 MIME 按实际格式（云端 MP3 / SAPI WAV）
- F 克隆上传按 is_self 判方向，目标侧唯一进 reply
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, ".")

from shisi.voice.character_voice import CharacterVoiceManager, CharacterVoiceSpec  # noqa: E402
from voice import voice_catalog as vc_mod  # noqa: E402
from voice.audio_result import (  # noqa: E402
    SynthesizedAudio,
    coerce_voice_payload,
    voice_result_payload,
)
from voice.mimo_tts_provider import MiMoTTSProvider  # noqa: E402
from voice.tts_manager import TTSManager  # noqa: E402
from voice.tts_provider_base import TTSProviderBase  # noqa: E402
from voice.voice_catalog import VoiceCatalog  # noqa: E402

# ═══════════════════════════════════════════════════════════════
#  基础设施：伪造 aiohttp 会话（捕获真实 HTTP payload）
# ═══════════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"mp3-bytes", text: str = "{}"):
        self.status = status
        self._body = body
        self._text = text

    async def read(self) -> bytes:
        return self._body

    async def text(self) -> str:
        return self._text

    async def json(self):
        return json.loads(self._text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """捕获 post() 的 json payload，供断言「真实 HTTP 请求体」。"""

    payloads: list[dict] = []
    next_response: _FakeResponse | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        type(self).payloads.append({"url": url, **kwargs})
        resp = type(self).next_response or _FakeResponse()
        return resp


@pytest.fixture()
def fake_http(monkeypatch):
    """把 provider 的 aiohttp.ClientSession 换成捕获型伪造。"""
    import voice.mimo_tts_provider as mod

    _FakeSession.payloads = []
    _FakeSession.next_response = None
    monkeypatch.setattr(mod.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


# ═══════════════════════════════════════════════════════════════
#  1. 统一音频对象
# ═══════════════════════════════════════════════════════════════

def test_synthesized_audio_mime_by_format():
    assert SynthesizedAudio(data=b"x", fmt="mp3").mime == "audio/mpeg"
    assert SynthesizedAudio(data=b"RIFF", fmt="wav").mime == "audio/wav"
    assert SynthesizedAudio(data=b"?", fmt="weird").mime == "application/octet-stream"


def test_synthesized_audio_is_frozen():
    audio = SynthesizedAudio(data=b"x", fmt="mp3")
    with pytest.raises(dataclasses.FrozenInstanceError):
        audio.fmt = "wav"  # type: ignore[misc]


def test_voice_result_payload_roundtrip():
    audio = SynthesizedAudio(data=b"abc", fmt="mp3")
    payload = voice_result_payload(audio)
    assert payload == {"data": b"abc", "format": "mp3", "mime": "audio/mpeg"}
    assert coerce_voice_payload(payload) == (b"abc", "mp3")


def test_voice_result_payload_none_passthrough():
    assert voice_result_payload(None) is None


# ═══════════════════════════════════════════════════════════════
#  2. 角色语音契约（不可变快照）
# ═══════════════════════════════════════════════════════════════

def _bind(tmp_path: Path, character_id: str, **fields):
    mgr = CharacterVoiceManager(config_path=str(tmp_path / "cv.json"))
    mgr.bind_voice(character_id, "mimo-tts", **fields)
    return mgr


def test_spec_is_frozen():
    spec = CharacterVoiceSpec(mimo_model="mimo-v2.5-tts", voice_id="vc_a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.voice_id = "vc_b"  # type: ignore[misc]


def test_resolve_spec_reads_explicit_fields(tmp_path):
    mgr = _bind(
        tmp_path, "c1",
        mimo_model="mimo-v2.5-tts-voiceclone", voice_id="vc_a",
        speed=1.2, pitch=0.9, speaker_name="female-tianmei",
    )
    spec = mgr.resolve_voice_spec("c1")
    assert spec is not None
    assert spec.mimo_model == "mimo-v2.5-tts-voiceclone"
    assert spec.voice_id == "vc_a"
    assert spec.speed == 1.2
    assert spec.pitch == 0.9


def test_resolve_spec_reads_legacy_extra_params(tmp_path):
    """VoiceTab 旧版把 mimo_model 塞在 extra_params 里（展平契约的历史形态）。"""
    mgr = CharacterVoiceManager(config_path=str(tmp_path / "cv.json"))
    mgr.bind_voice("c1", "mimo-tts", extra_params={"mimo_model": "mimo-v2.5-tts-voicedesign"})
    spec = mgr.resolve_voice_spec("c1")
    assert spec is not None
    assert spec.mimo_model == "mimo-v2.5-tts-voicedesign"


def test_resolve_spec_sapi_string_pitch_is_not_cloud_ratio(tmp_path):
    """存量 SAPI 风格 pitch（"0Hz"）不是 MiMo 比例，不得进云端 payload。"""
    mgr = _bind(tmp_path, "c1", pitch="0Hz", speaker_name="female-tianmei")
    spec = mgr.resolve_voice_spec("c1")
    assert spec is not None
    assert spec.pitch is None


def test_resolve_spec_missing_character_returns_none(tmp_path):
    mgr = _bind(tmp_path, "c1")
    assert mgr.resolve_voice_spec("nobody") is None


def test_spec_synth_kwargs_snapshot(tmp_path):
    mgr = _bind(tmp_path, "c1", mimo_model="mimo-v2.5-tts-voiceclone", voice_id="vc_a", speed=1.2)
    spec = mgr.resolve_voice_spec("c1")
    kwargs = spec.synth_kwargs()
    assert kwargs == {"model": "mimo-v2.5-tts-voiceclone", "voice_id": "vc_a", "speed": 1.2}


def test_spec_synth_kwargs_preset_falls_back_to_speaker(tmp_path):
    mgr = _bind(tmp_path, "c1", speaker_name="female-qingxin")
    spec = mgr.resolve_voice_spec("c1")
    assert spec.synth_kwargs() == {"voice_id": "female-qingxin"}


# ═══════════════════════════════════════════════════════════════
#  3. 并发合成不串音（验收核心：payload 严格匹配所属角色）
# ═══════════════════════════════════════════════════════════════

def test_provider_concurrent_specs_no_crosstalk(fake_http):
    """两角色不同 model/voice 并发合成：每个请求 payload 严格用各自快照，
    provider 实例全局态不被改写。"""
    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts", voice_id="")
    spec_a = CharacterVoiceSpec(
        mimo_model="mimo-v2.5-tts-voiceclone", voice_id="vc_char_a", speed=1.3,
    )
    spec_b = CharacterVoiceSpec(mimo_model="mimo-v2.5-tts", voice_id="female-tianmei")

    async def _run():
        return await asyncio.gather(
            provider.synthesize("角色甲的话", **spec_a.synth_kwargs()),
            provider.synthesize("角色乙的话", **spec_b.synth_kwargs()),
        )

    results = asyncio.run(_run())
    assert all(r is not None and r.data == b"mp3-bytes" for r in results)

    payloads = [p["json"] for p in _FakeSession.payloads]
    by_text = {p["input"]: p for p in payloads}
    pa = by_text["角色甲的话"]
    pb = by_text["角色乙的话"]
    assert pa["model"] == "mimo-v2.5-tts-voiceclone"
    assert pa["voice"] == "vc_char_a"
    assert pa["voice_id"] == "vc_char_a"
    assert pa["speed"] == 1.3
    assert pb["model"] == "mimo-v2.5-tts"
    assert pb["voice"] == "female-tianmei"
    assert "voice_id" not in pb  # 非 voiceclone 模型不带 voice_id 字段

    # provider 全局态未被并发请求改写
    assert provider.health_check()["model"] == "mimo-v2.5-tts"
    assert provider.health_check()["voice_id"] == ""


def test_provider_emotion_reaches_payload(fake_http):
    """E：emotion 经 provider 的 EMOTION_MAPPING 落进真实 payload（速度/音高）。"""
    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts")
    result = asyncio.run(provider.synthesize("开心的话", emotion="开心"))
    assert result is not None
    payload = _FakeSession.payloads[-1]["json"]
    assert payload["speed"] == 1.1  # 「开心」映射 speed=1.1
    assert payload["pitch"] == 1.05  # 「开心」映射 pitch=1.05


def test_provider_sapi_fallback_is_wav_format(fake_http, monkeypatch):
    """云端失败降级 SAPI：返回的统一对象必须是 wav，不得再标 MP3。"""
    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts", fallback_local=True)
    _FakeSession.next_response = _FakeResponse(status=500, text='{"error":"boom"}')

    async def _fake_sapi(text, **kwargs):
        # 与真实 _local_synth 契约一致：返回统一对象（SAPI 产 WAV）
        return SynthesizedAudio(data=b"RIFF-wav-bytes", fmt="wav")

    monkeypatch.setattr(provider, "_local_synth", _fake_sapi)
    result = asyncio.run(provider.synthesize("降级的话"))
    assert result is not None
    assert result.fmt == "wav"
    assert result.mime == "audio/wav"


def test_provider_clone_does_not_hijack_global_voice_id(fake_http):
    """B：克隆成功返回 voice_id，但不得改写 provider 全局默认音色
    （多用户权威在 catalog，不在 provider 实例字段）。"""
    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts-voiceclone", voice_id="")
    _FakeSession.next_response = _FakeResponse(
        status=200, text=json.dumps({"voice_id": "vc_new"}),
    )
    result = asyncio.run(provider.clone_voice(audio_data=b"ref", voice_name="我的音色"))
    assert result["status"] == "success"
    assert result["voice_id"] == "vc_new"
    assert provider.health_check()["voice_id"] == ""


def test_provider_design_does_not_hijack_global_voice_id(fake_http):
    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts-voicedesign", voice_id="")
    _FakeSession.next_response = _FakeResponse(
        status=200, text=json.dumps({"voice_id": "vd_new"}),
    )
    result = asyncio.run(provider.design_voice(description="温柔女声", voice_name="设计音色"))
    assert result["status"] == "success"
    assert provider.health_check()["voice_id"] == ""


def test_provider_empty_audio_is_failure(fake_http):
    """供应商失败不得把空音频报成功。"""
    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts", fallback_local=False)
    _FakeSession.next_response = _FakeResponse(status=200, body=b"")
    assert asyncio.run(provider.synthesize("空响应")) is None


# ═══════════════════════════════════════════════════════════════
#  4. TTSManager：emotion 透传 + 空音频判失败
# ═══════════════════════════════════════════════════════════════

def _mgr_with_provider(return_value) -> tuple[TTSManager, MagicMock]:
    mgr = TTSManager()
    provider = MagicMock(spec=TTSProviderBase)
    provider.name = "mimo-tts"
    provider.health_check.return_value = {"available": True}
    provider.synthesize = AsyncMock(return_value=return_value)
    mgr._providers = {"mimo-tts": provider}
    mgr._current_engine = "mimo-tts"
    mgr._enabled = True
    return mgr, provider


def test_manager_forwards_emotion_to_provider():
    """E 红点：manager 签名收了 emotion 却从未转发给 provider。"""
    audio = SynthesizedAudio(data=b"a", fmt="mp3")
    mgr, provider = _mgr_with_provider(audio)
    result = asyncio.run(
        mgr.synthesize("文本", emotion="开心", model="mimo-v2.5-tts", voice_id="vc_x")
    )
    assert result is audio
    _, call_kwargs = provider.synthesize.call_args
    assert call_kwargs.get("emotion") == "开心"
    assert call_kwargs.get("model") == "mimo-v2.5-tts"
    assert call_kwargs.get("voice_id") == "vc_x"


def test_manager_treats_empty_audio_as_failure():
    mgr, _p = _mgr_with_provider(SynthesizedAudio(data=b"", fmt="mp3"))
    assert asyncio.run(mgr.synthesize("文本")) is None
    assert mgr._last_error


# ═══════════════════════════════════════════════════════════════
#  5. 音色 catalog（B：持久化可检索）
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "voice_catalog.json"
    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", path, raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)
    return VoiceCatalog(config_path=str(path))


def test_catalog_register_persist_and_reload(catalog, tmp_path):
    catalog.register(
        voice_id="vc_new", name="我的克隆", kind="clone",
        model="mimo-v2.5-tts-voiceclone",
    )
    reloaded = VoiceCatalog(config_path=str(tmp_path / "voice_catalog.json"))
    entry = reloaded.get("vc_new")
    assert entry is not None
    assert entry["name"] == "我的克隆"
    assert entry["kind"] == "clone"


def test_catalog_remove(catalog):
    catalog.register(voice_id="vc_x", name="n", kind="design", model="m")
    assert catalog.remove("vc_x") is True
    assert catalog.get("vc_x") is None
    assert catalog.remove("vc_x") is False


def test_catalog_list_preset_names():
    from voice.voice_catalog import preset_names

    names = set(preset_names())
    assert "female-tianmei" in names


# ═══════════════════════════════════════════════════════════════
#  6. 路由：绑定校验 / 试听 MIME / speakers 合并 catalog
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def voice_env(tmp_path, monkeypatch):
    import types

    import api.deps as deps_mod

    mgr = CharacterVoiceManager(config_path=str(tmp_path / "cv.json"))
    monkeypatch.setattr(deps_mod.deps, "_character_voice_mgr", mgr, raising=False)
    return types.SimpleNamespace(voice_routes=None, deps=deps_mod, mgr=mgr, tmp_path=tmp_path)


def test_bind_rejects_unknown_mimo_model(voice_env):
    from api.routers.voice_routes import VoiceBindRequest, bind_character_voice

    with pytest.raises(HTTPException) as ei:
        asyncio.run(bind_character_voice(
            "c1", VoiceBindRequest(mimo_model="bogus-model"),
        ))
    assert ei.value.status_code == 400


def test_bind_rejects_unknown_voice_id(voice_env, monkeypatch):
    from api.routers.voice_routes import VoiceBindRequest, bind_character_voice

    with pytest.raises(HTTPException) as ei:
        asyncio.run(bind_character_voice(
            "c1", VoiceBindRequest(voice_id="vc_never_registered"),
        ))
    assert ei.value.status_code == 400


def test_bind_accepts_catalog_voice_and_flatten_contract(voice_env):
    """保留既有 POST 展平契约：extra_params 仍展开落盘；新显式字段并列。"""
    from api.routers.voice_routes import VoiceBindRequest, bind_character_voice

    vc_mod.get_voice_catalog().register(
        voice_id="vc_ok", name="n", kind="clone", model="mimo-v2.5-tts-voiceclone",
    )

    resp = asyncio.run(bind_character_voice(
        "c1",
        VoiceBindRequest(
            speaker_name="female-tianmei", voice_id="vc_ok", speed=1.1, pitch=0.95,
            extra_params={"custom_keep": 1},
        ),
    ))
    assert resp["status"] == "bound"
    cfg = voice_env.mgr.get_voice_config("c1")
    assert cfg["voice_id"] == "vc_ok"
    assert cfg["speed"] == 1.1
    assert cfg["pitch"] == 0.95
    assert cfg["custom_keep"] == 1  # 展平契约保留


def _fake_tts(return_value):
    fake = MagicMock()
    fake.enabled = True
    fake.current_engine = "mimo-tts"
    fake.synthesize = AsyncMock(return_value=return_value)
    return fake


def test_voice_test_endpoint_uses_character_spec_and_mime(voice_env, monkeypatch):
    """试听按角色契约合成，云端 MP3 返回 audio/mpeg。"""
    import api.deps as deps_mod
    from api.routers.voice_routes import VoiceBindRequest, VoiceTestRequest, bind_character_voice, test_character_voice

    monkeypatch.setattr(deps_mod.deps, "_character_voice_mgr", voice_env.mgr, raising=False)
    asyncio.run(bind_character_voice(
        "c1",
        VoiceBindRequest(mimo_model="mimo-v2.5-tts-voiceclone", voice_id="female-tianmei", speed=1.2),
    ))

    fake_tts = _fake_tts(SynthesizedAudio(data=b"mp3", fmt="mp3"))
    monkeypatch.setattr(deps_mod.deps, "get_tts", lambda: fake_tts)

    response = asyncio.run(test_character_voice("c1", VoiceTestRequest(text="试听")))
    assert response.media_type == "audio/mpeg"
    _, kwargs = fake_tts.synthesize.call_args
    assert kwargs["model"] == "mimo-v2.5-tts-voiceclone"
    assert kwargs["voice_id"] == "female-tianmei"
    assert kwargs["speed"] == 1.2


def test_voice_test_endpoint_wav_mime(voice_env, monkeypatch):
    import api.deps as deps_mod
    from api.routers.voice_routes import VoiceBindRequest, VoiceTestRequest, bind_character_voice, test_character_voice

    monkeypatch.setattr(deps_mod.deps, "_character_voice_mgr", voice_env.mgr, raising=False)
    asyncio.run(bind_character_voice("c1", VoiceBindRequest(speaker_name="female-tianmei")))

    fake_tts = _fake_tts(SynthesizedAudio(data=b"RIFF", fmt="wav"))
    monkeypatch.setattr(deps_mod.deps, "get_tts", lambda: fake_tts)

    response = asyncio.run(test_character_voice("c1", VoiceTestRequest(text="试听")))
    assert response.media_type == "audio/wav"


def test_voice_test_endpoint_failure_visible(voice_env, monkeypatch):
    """合成失败必须可见（非 200 空体）。"""
    import api.deps as deps_mod
    from api.routers.voice_routes import VoiceBindRequest, VoiceTestRequest, bind_character_voice, test_character_voice

    monkeypatch.setattr(deps_mod.deps, "_character_voice_mgr", voice_env.mgr, raising=False)
    asyncio.run(bind_character_voice("c1", VoiceBindRequest()))
    fake_tts = _fake_tts(None)
    monkeypatch.setattr(deps_mod.deps, "get_tts", lambda: fake_tts)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(test_character_voice("c1", VoiceTestRequest(text="试听")))
    assert ei.value.status_code >= 500


def test_speakers_merge_catalog(voice_env, monkeypatch):
    from api.routers.voice_routes import list_speakers

    cat = vc_mod.get_voice_catalog()
    cat.register(voice_id="vc_merged", name="合并音色", kind="clone", model="mimo-v2.5-tts-voiceclone")
    data = asyncio.run(list_speakers())
    names = {s["name"] for s in data["speakers"]}
    assert "female-tianmei" in names
    assert "vc_merged" in names
    merged = next(s for s in data["speakers"] if s["name"] == "vc_merged")
    assert merged["kind"] == "clone"


# ═══════════════════════════════════════════════════════════════
#  7. MiMo 路由：clone 落 catalog / set-engine 类型分明 / 合成 MIME
# ═══════════════════════════════════════════════════════════════

def _clone_provider(fake_http) -> MiMoTTSProvider:
    _FakeSession.next_response = _FakeResponse(
        status=200, text=json.dumps({"voice_id": "vc_route_new"}),
    )
    return MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts-voiceclone", voice_id="")


class _FakeUpload:
    def __init__(self, data: bytes = b"ref-audio"):
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _fake_mgr(provider) -> MagicMock:
    mgr = MagicMock()
    mgr.get_engine.return_value = provider
    return mgr


def test_mimo_clone_registers_catalog(fake_http, monkeypatch, tmp_path):
    from api.routers import mimo_voice_routes

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)

    provider = _clone_provider(fake_http)
    result = asyncio.run(mimo_voice_routes.clone_voice(
        voice_name="路由克隆", description="", audio=_FakeUpload(),
        tts_manager=_fake_mgr(provider), _auth=True,
    ))
    assert result["voice_id"] == "vc_route_new"
    assert result["catalog_saved"] is True
    cat = vc_mod.get_voice_catalog()
    assert cat.get("vc_route_new")["name"] == "路由克隆"


def test_mimo_set_engine_rejects_engine_name(fake_http):
    """C：engine 名（mimo-tts）不是模型名，必须 400 而非静默。"""
    from api.routers import mimo_voice_routes

    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mimo_voice_routes.set_mimo_engine(
            model="mimo-tts", tts_manager=_fake_mgr(provider), _auth=True,
        ))
    assert ei.value.status_code == 400


def test_mimo_set_engine_accepts_model(fake_http):
    from api.routers import mimo_voice_routes

    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts")
    result = asyncio.run(mimo_voice_routes.set_mimo_engine(
        model="mimo-v2.5-tts-voicedesign", tts_manager=_fake_mgr(provider), _auth=True,
    ))
    assert result["status"] == "success"


def test_mimo_synthesize_returns_actual_mime(fake_http, monkeypatch, tmp_path):
    from api.routers import mimo_voice_routes

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)

    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts")
    response = asyncio.run(mimo_voice_routes.synthesize(
        text="直接合成", voice_id="", model="", emotion="",
        tts_manager=_fake_mgr(provider), _auth=True,
    ))
    assert response.media_type == "audio/mpeg"


def test_mimo_synthesize_rejects_unknown_model(fake_http, monkeypatch, tmp_path):
    from api.routers import mimo_voice_routes

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)

    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mimo_voice_routes.synthesize(
            text="x", voice_id="", model="mimo-tts", emotion="",
            tts_manager=_fake_mgr(provider), _auth=True,
        ))
    assert ei.value.status_code == 400


def test_mimo_switch_voice_validates(fake_http, monkeypatch, tmp_path):
    from api.routers import mimo_voice_routes

    monkeypatch.setattr(vc_mod, "_DEFAULT_PATH", tmp_path / "voice_catalog.json", raising=False)
    monkeypatch.setattr(vc_mod, "_singleton", None, raising=False)

    provider = MiMoTTSProvider(api_key="k", model="mimo-v2.5-tts", voice_id="")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mimo_voice_routes.switch_voice(
            voice_id="vc_ghost", tts_manager=_fake_mgr(provider), _auth=True,
        ))
    assert ei.value.status_code == 404

    ok = asyncio.run(mimo_voice_routes.switch_voice(
        voice_id="female-tianmei", tts_manager=_fake_mgr(provider), _auth=True,
    ))
    assert ok["status"] == "success"


def test_safety_voice_synthesize_actual_mime(monkeypatch):
    import api.deps as deps_mod
    from api.routers import safety_routes

    monkeypatch.setattr(deps_mod.deps, "get_tts", lambda: _fake_tts(SynthesizedAudio(data=b"m", fmt="mp3")))
    response = asyncio.run(safety_routes.voice_synthesize(text="x", engine=""))
    assert response.media_type == "audio/mpeg"


# ═══════════════════════════════════════════════════════════════
#  8. 克隆上传方向（F）
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def normalize():
    from api.routers.clone_routes import normalize_clone_conversations
    return normalize_clone_conversations


def test_native_self_text_not_duplicated_into_reply(normalize):
    """F 红点核心：is_self=true 仅 text 时，同一文本不得同时进 user 与 reply。"""
    conversations, _errors, _warnings, _stats = normalize(
        [{"is_self": True, "text": "我发送的话", "message_type": 1}],
    )
    assert len(conversations) == 1
    assert conversations[0]["user"] == "我发送的话"
    assert conversations[0]["reply"] == ""


def test_native_target_text_goes_to_reply_only(normalize):
    conversations, _e, _w, _s = normalize(
        [{"is_self": False, "text": "目标的话", "message_type": 1}],
    )
    assert conversations[0]["reply"] == "目标的话"
    assert conversations[0]["user"] == ""


def test_native_zero_target_side_stats(normalize):
    """全部是 is_self=true → stats.target==0，路由据此 422（见下一条路由层测试）。"""
    _conversations, _errors, _warnings, stats = normalize(
        [{"is_self": True, "text": "a", "message_type": 1}] * 3,
    )
    assert stats["target"] == 0
    assert stats["self"] == 3


def test_route_rejects_self_only_data():
    """路由层：只有自己一侧的消息 → 422 且提示明确（不得泛化为「无有效对话」）。"""
    from api.routers.clone_routes import upload_clone_data

    payload = json.dumps(
        [{"is_self": True, "text": "只有我", "message_type": 1}] * 2
    ).encode("utf-8")

    class _Upload:
        async def read(self) -> bytes:
            return payload

    with pytest.raises(HTTPException) as ei:
        asyncio.run(upload_clone_data(
            target="某人", file=_Upload(), _auth=True, _admin=(1, None),
        ))
    assert ei.value.status_code == 422
    assert "is_self" in str(ei.value.detail)


def test_mixed_missing_is_self_warns(normalize):
    """缺 is_self 的简单格式与原生格式混排 → 给明确提示，简单行仍按 user/reply。"""
    data = [
        {"is_self": False, "text": "目标", "message_type": 1},
        {"user": "用户", "reply": "回复"},
    ]
    conversations, _errors, warnings, stats = normalize(data)
    assert warnings, "混排必须提示"
    assert conversations[-1]["user"] == "用户"
    assert conversations[-1]["reply"] == "回复"
    assert stats["target"] == 2  # 原生目标侧 1 + 简单格式 reply 1
    assert stats["self"] == 1  # 简单格式 user 侧


def test_style_profile_counts_only_target_side(normalize):
    """验收：self=true 与 is_self=false 显著不同文本，StyleProfile 只统计目标侧。"""
    data = [
        {"is_self": True, "text": "尊敬的领导您好，请查收附件", "message_type": 1},
        {"is_self": False, "text": "哈哈绝绝子笑死我了", "message_type": 1},
        {"is_self": True, "text": "收到，我马上落实", "message_type": 1},
        {"is_self": False, "text": "绝绝子，真的会谢", "message_type": 1},
    ]
    conversations, _e, _w, stats = normalize(data)
    assert stats["self"] == 2
    assert stats["target"] == 2

    from clone_training.style_analyzer import StyleAnalyzer
    profile = StyleAnalyzer().analyze(conversations)
    assert profile.total_messages == 2  # 只有目标侧进入被克隆语料
    joined = json.dumps(profile.to_dict(), ensure_ascii=False)
    assert "绝绝子" in joined
    assert "尊敬的领导" not in joined
    assert "落实" not in joined


def test_non_text_message_type_skipped(normalize):
    conversations, _e, _w, _s = normalize([
        {"is_self": False, "text": "图片", "message_type": 3},
        {"is_self": False, "text": "正文", "message_type": 1},
    ])
    assert len(conversations) == 1
    assert conversations[0]["reply"] == "正文"
