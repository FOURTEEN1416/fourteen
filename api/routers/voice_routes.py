"""角色音色绑定 API — 管理角色与 TTS 音色的绑定关系

2026-08-28 MiMo-only 收敛：引擎维度删除（唯一引擎 mimo-tts），音色维度保留
speaker_name 等参数（MiMo voice_id/预设音色名）。"""


from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Security
from fastapi.responses import Response
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.deps import deps

logger = logging.getLogger("api.voice_routes")

router = APIRouter(prefix="/api", tags=["voice"])

# ── 请求/响应模型 ──


class VoiceBindRequest(BaseModel):
    engine: str = "mimo-tts"
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


# ── MiMo 预设音色（唯一引擎；克隆/设计音色见 /api/mimo/*）──

_MIMO_VOICES = [
    {"name": "female-tianmei", "description": "甜美女声"},
    {"name": "female-qingxin", "description": "清新女声"},
    {"name": "male-chenwen", "description": "沉稳男声"},
]


# ── API 端点 ──


@router.get("/characters/{character_id}/voice")
async def get_character_voice(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """获取角色音色配置"""
    voice_mgr = deps.get_character_voice_manager()
    config = voice_mgr.get_voice_config(character_id)
    if config is None:
        return {"configured": False, "voice": None}
    return {"configured": True, "voice": config}


@router.post("/characters/{character_id}/voice")
async def bind_character_voice(
    character_id: str,
    req: VoiceBindRequest,
    _auth: bool = Security(verify_api_key_dep),
):
    """绑定角色音色"""
    voice_mgr = deps.get_character_voice_manager()

    try:
        voice_mgr.bind_voice(
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
    _auth: bool = Security(verify_api_key_dep),
):
    """更新角色音色配置（部分更新）"""
    voice_mgr = deps.get_character_voice_manager()

    current = voice_mgr.get_voice_config(character_id)
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

    voice_mgr.bind_voice(
        character_id=character_id,
        engine=updated.get("engine", "mimo-tts"),
        speaker_name=updated.get("speaker_name", ""),
        **{k: v for k, v in updated.items() if k not in ("engine", "speaker_name")},
    )
    return {"status": "updated", "character_id": character_id}


@router.delete("/characters/{character_id}/voice")
async def unbind_character_voice(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """解绑角色音色"""
    voice_mgr = deps.get_character_voice_manager()

    ok = voice_mgr.unbind_voice(character_id)
    if not ok:
        raise HTTPException(status_code=404, detail="角色未配置音色")
    return {"status": "unbound", "character_id": character_id}


@router.get("/voice/speakers")
async def list_speakers(
    _auth: bool = Security(verify_api_key_dep),
):
    """获取 MiMo 预设音色列表（唯一引擎；克隆/设计音色走 /api/mimo/*）"""
    return {
        "engine": "mimo-tts",
        "speakers": _MIMO_VOICES,
        "total": len(_MIMO_VOICES),
    }


@router.post("/characters/{character_id}/voice/test")
async def test_character_voice(
    character_id: str,
    req: VoiceTestRequest,
    _auth: bool = Security(verify_api_key_dep),
):
    """测试角色音色合成"""
    voice_mgr = deps.get_character_voice_manager()
    tts_mgr = deps.get_tts()
    if tts_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")

    voice_config = voice_mgr.get_voice_config(character_id)
    if voice_config is None:
        raise HTTPException(status_code=400, detail="角色未配置音色，请先绑定")

    # 切换到角色配置的引擎
    # MiMo-only：不再切换引擎，直接用全局 TTSManager 合成

    # 提取合成参数
    tts_kwargs = {
        "speaker_name": voice_config.get("speaker_name", ""),
        "rate": voice_config.get("rate", "+0%"),
        "pitch": voice_config.get("pitch", "0Hz"),
        "volume": voice_config.get("volume", "+0%"),
    }
    # 过滤掉空值
    tts_kwargs = {k: v for k, v in tts_kwargs.items() if v}

    audio_data = await tts_mgr.synthesize(req.text, **tts_kwargs)
    if audio_data is None:
        raise HTTPException(status_code=500, detail="语音合成失败")

    return Response(content=audio_data, media_type="audio/wav")
