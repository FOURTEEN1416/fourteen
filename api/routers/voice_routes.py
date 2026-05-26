"""角色音色绑定 API — 管理角色与 TTS 音色的绑定关系"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.responses import Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from shisi.voice_ext.character_voice import CharacterVoiceManager

logger = logging.getLogger("api.voice_routes")

router = APIRouter(prefix="/api", tags=["voice"])

_voice_mgr: CharacterVoiceManager | None = None
_tts_mgr: Any | None = None
_orch: Any | None = None

# ── API Key 认证 ──
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_verify_api_key_func = None


async def _verify_api_key(api_key: str | None = Security(_api_key_header)):
    if _verify_api_key_func is not None:
        return await _verify_api_key_func(api_key)
    return True


# ── 请求/响应模型 ──


class VoiceBindRequest(BaseModel):
    engine: str = "edge-tts"
    speaker_name: str = ""
    rate: str = "+0%"
    pitch: str = "0Hz"
    volume: str = "+0%"
    extra_params: dict = {}


class VoiceUpdateRequest(BaseModel):
    engine: str | None = None
    speaker_name: str | None = None
    rate: str | None = None
    pitch: str | None = None
    volume: str | None = None
    extra_params: dict | None = None


class VoiceTestRequest(BaseModel):
    text: str = "你好，我是你的专属语音助手"


# ── 依赖注入 ──


def set_dependencies(orch, verify_api_key):
    global _orch, _voice_mgr, _tts_mgr, _verify_api_key_func
    _orch = orch
    _voice_mgr = CharacterVoiceManager()
    _verify_api_key_func = verify_api_key
    # 从 orchestrator 获取 TTSManager
    if orch and hasattr(orch, "components"):
        _tts_mgr = orch.components.get("voice")
    logger.info("Voice routes dependencies injected, TTS: %s", _tts_mgr is not None)


# ── Edge-TTS 预定义发音人 ──

_EDGE_SPEAKERS = [
    {"name": "zh-CN-XiaoxiaoNeural", "gender": "female", "description": "晓晓（女，活泼）"},
    {"name": "zh-CN-XiaoyiNeural", "gender": "female", "description": "晓伊（女，温柔）"},
    {"name": "zh-CN-YunjianNeural", "gender": "male", "description": "云健（男，磁性）"},
    {"name": "zh-CN-YunxiNeural", "gender": "male", "description": "云希（男，阳光）"},
    {"name": "zh-CN-YunyangNeural", "gender": "male", "description": "云扬（男，沉稳）"},
    {"name": "zh-CN-XiaochenNeural", "gender": "female", "description": "晓辰（女，知性）"},
    {"name": "zh-CN-XiaohanNeural", "gender": "female", "description": "晓涵（女，甜美）"},
    {"name": "zh-CN-XiaomengNeural", "gender": "female", "description": "晓梦（女，可爱）"},
    {"name": "zh-CN-XiaomoNeural", "gender": "female", "description": "晓墨（女，文艺）"},
    {"name": "zh-CN-XiaoqiuNeural", "gender": "female", "description": "晓秋（女，感性）"},
    {"name": "zh-CN-XiaoruiNeural", "gender": "female", "description": "晓睿（女，冷静）"},
    {"name": "zh-CN-XiaoshuangNeural", "gender": "female", "description": "晓霜（女，高冷）"},
    {"name": "zh-CN-XiaoyanNeural", "gender": "female", "description": "晓颜（女，自然）"},
    {"name": "zh-CN-XiaozhenNeural", "gender": "female", "description": "晓珍（女，亲切）"},
    {"name": "zh-CN-YunjieNeural", "gender": "male", "description": "云杰（男，成熟）"},
    {"name": "zh-CN-YunhaoNeural", "gender": "male", "description": "云浩（男，厚重）"},
]

_GPT_SOVITS_SPEAKERS = [
    {"name": "default", "description": "默认模型"},
    {"name": "custom", "description": "自定义训练模型（需指定路径）"},
]

_BERT_VITS2_SPEAKERS = [
    {"name": "珊瑚宫心海[中]", "description": "珊瑚宫心海（中文）"},
    {"name": "珊瑚宫心海[日]", "description": "珊瑚宫心海（日文）"},
    {"name": "纳西妲[中]", "description": "纳西妲（中文）"},
    {"name": "雷电将军[中]", "description": "雷电将军（中文）"},
]

_ENGINE_SPEAKERS = {
    "edge-tts": _EDGE_SPEAKERS,
    "gpt-sovits": _GPT_SOVITS_SPEAKERS,
    "bert-vits2": _BERT_VITS2_SPEAKERS,
}


# ── API 端点 ──


@router.get("/characters/{character_id}/voice")
async def get_character_voice(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """获取角色音色配置"""
    if _voice_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")
    config = _voice_mgr.get_voice_config(character_id)
    if config is None:
        return {"configured": False, "voice": None}
    return {"configured": True, "voice": config}


@router.post("/characters/{character_id}/voice")
async def bind_character_voice(
    character_id: str,
    req: VoiceBindRequest,
    _auth: bool = Security(_verify_api_key),
):
    """绑定角色音色"""
    if _voice_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")

    try:
        _voice_mgr.bind_voice(
            character_id=character_id,
            engine=req.engine,
            speaker_name=req.speaker_name,
            rate=req.rate,
            pitch=req.pitch,
            volume=req.volume,
            **req.extra_params,
        )
        logger.info("角色 %s 绑定音色: %s / %s", character_id, req.engine, req.speaker_name)
        return {"status": "bound", "character_id": character_id, "engine": req.engine}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/characters/{character_id}/voice")
async def update_character_voice(
    character_id: str,
    req: VoiceUpdateRequest,
    _auth: bool = Security(_verify_api_key),
):
    """更新角色音色配置（部分更新）"""
    if _voice_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")

    current = _voice_mgr.get_voice_config(character_id)
    if current is None:
        raise HTTPException(status_code=404, detail="角色未配置音色，请先绑定")

    updated = dict(current)
    if req.engine is not None:
        updated["engine"] = req.engine
    if req.speaker_name is not None:
        updated["speaker_name"] = req.speaker_name
    if req.rate is not None:
        updated["rate"] = req.rate
    if req.pitch is not None:
        updated["pitch"] = req.pitch
    if req.volume is not None:
        updated["volume"] = req.volume
    if req.extra_params is not None:
        extra = updated.get("extra_params", {})
        extra.update(req.extra_params)
        updated["extra_params"] = extra

    _voice_mgr.bind_voice(
        character_id=character_id,
        engine=updated.get("engine", "edge-tts"),
        speaker_name=updated.get("speaker_name", ""),
        **{k: v for k, v in updated.items() if k not in ("engine", "speaker_name")},
    )
    return {"status": "updated", "character_id": character_id}


@router.delete("/characters/{character_id}/voice")
async def unbind_character_voice(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """解绑角色音色"""
    if _voice_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")

    ok = _voice_mgr.unbind_voice(character_id)
    if not ok:
        raise HTTPException(status_code=404, detail="角色未配置音色")
    return {"status": "unbound", "character_id": character_id}


@router.get("/voice/speakers")
async def list_speakers(
    engine: str = Query(default="edge-tts"),
    _auth: bool = Security(_verify_api_key),
):
    """获取指定引擎的可用发音人列表"""
    speakers = _ENGINE_SPEAKERS.get(engine, [])
    return {
        "engine": engine,
        "speakers": speakers,
        "total": len(speakers),
    }


@router.post("/characters/{character_id}/voice/test")
async def test_character_voice(
    character_id: str,
    req: VoiceTestRequest,
    _auth: bool = Security(_verify_api_key),
):
    """测试角色音色合成"""
    if _voice_mgr is None or _tts_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")

    voice_config = _voice_mgr.get_voice_config(character_id)
    if voice_config is None:
        raise HTTPException(status_code=400, detail="角色未配置音色，请先绑定")

    # 切换到角色配置的引擎
    engine = voice_config.get("engine", "edge-tts")
    await _tts_mgr.switch_engine(engine)

    # 提取合成参数
    tts_kwargs = {
        "speaker_name": voice_config.get("speaker_name", ""),
        "rate": voice_config.get("rate", "+0%"),
        "pitch": voice_config.get("pitch", "0Hz"),
        "volume": voice_config.get("volume", "+0%"),
    }
    # 过滤掉空值
    tts_kwargs = {k: v for k, v in tts_kwargs.items() if v}

    audio_data = await _tts_mgr.synthesize(req.text, **tts_kwargs)
    if audio_data is None:
        raise HTTPException(status_code=500, detail="语音合成失败")

    return Response(content=audio_data, media_type="audio/wav")
