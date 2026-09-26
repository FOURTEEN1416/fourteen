"""
MiMo TTS API路由扩展

提供语音克隆、音色设计等MiMo特有功能
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from api.auth import verify_api_key_dep
from api.deps import get_tts_manager
from voice.mimo_tts_provider import MiMoTTSProvider
from voice.tts_manager import TTSManager
from voice.voice_catalog import get_voice_catalog

logger = logging.getLogger("api.mimo_voice")

router = APIRouter(prefix="/api/mimo", tags=["mimo-tts"])


def _register_catalog(result: dict[str, Any], *, name: str, kind: str, model: str,
                      description: str = "", **extra: str) -> bool:
    """克隆/设计产物登记进音色 catalog（持久化 owner）。

    云端资源已创建、本地登记失败时如实返回 False（不谎报已持久化）。
    """
    voice_id = result.get("voice_id", "")
    if not voice_id:
        return False
    try:
        get_voice_catalog().register(
            voice_id=voice_id, name=name, kind=kind, model=model,
            description=description, **extra,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("音色 %s 登记catalog失败: %s", voice_id, e)
        return False


@router.post("/clone")
async def clone_voice(
    voice_name: str = Form(..., description="音色名称"),
    description: str = Form("", description="音色描述"),
    audio: UploadFile = File(..., description="参考音频文件(10-30秒)"),  # noqa: B008
    tts_manager: TTSManager | None = Depends(get_tts_manager),  # noqa: B008
    _auth: bool = Depends(verify_api_key_dep),  # noqa: B008
) -> dict[str, Any]:
    """
    克隆音色

    上传10-30秒的参考音频，创建自定义音色

    Args:
        voice_name: 音色名称（用于标识）
        description: 音色描述
        audio: 参考音频文件（MP3/WAV格式）

    Returns:
        {"voice_id": str, "status": str, "message": str}
    """
    # 获取MiMo TTS提供者
    if tts_manager is None:
        raise HTTPException(status_code=400, detail="TTS管理器未初始化")
    provider = tts_manager.get_engine("mimo-tts")
    if not isinstance(provider, MiMoTTSProvider):
        raise HTTPException(status_code=400, detail="MiMo TTS未配置")

    # 检查是否为voiceclone模型
    health = provider.health_check()
    if health.get("model") != "mimo-v2.5-tts-voiceclone":
        raise HTTPException(
            status_code=400,
            detail="当前MiMo TTS模型不支持语音克隆，请在配置中切换至mimo-v2.5-tts-voiceclone",
        )

    try:
        # 读取音频数据
        audio_data = await audio.read()

        # 调用克隆接口
        result = await provider.clone_voice(
            audio_data=audio_data,
            voice_name=voice_name,
            description=description or f"克隆音色: {voice_name}",
        )

        if result["status"] == "error":
            raise HTTPException(status_code=500, detail=result["message"])

        catalog_saved = _register_catalog(
            result, name=voice_name, kind="clone",
            model="mimo-v2.5-tts-voiceclone",
            description=description or f"克隆音色: {voice_name}",
        )
        result["catalog_saved"] = catalog_saved

        logger.info("语音克隆成功: %s -> %s (catalog_saved=%s)",
                    voice_name, result.get("voice_id"), catalog_saved)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("语音克隆失败: %s", e)
        raise HTTPException(status_code=500, detail="语音克隆失败") from e


@router.post("/design")
async def design_voice(
    voice_name: str = Form(..., description="音色名称"),
    description: str = Form(..., description="音色描述（如：温柔的女声，带有一点磁性）"),
    gender: str | None = Form(None, description="性别（male/female）"),
    age_group: str | None = Form(None, description="年龄段（young/adult/elder）"),
    tts_manager: TTSManager | None = Depends(get_tts_manager),  # noqa: B008
    _auth: bool = Depends(verify_api_key_dep),  # noqa: B008
) -> dict[str, Any]:
    """
    设计音色

    通过描述性文本设计新音色，无需参考音频

    Args:
        voice_name: 音色名称
        description: 音色描述
        gender: 性别（可选）
        age_group: 年龄段（可选）

    Returns:
        {"voice_id": str, "status": str, "message": str}
    """
    # 获取MiMo TTS提供者
    if tts_manager is None:
        raise HTTPException(status_code=400, detail="TTS管理器未初始化")
    provider = tts_manager.get_engine("mimo-tts")
    if not isinstance(provider, MiMoTTSProvider):
        raise HTTPException(status_code=400, detail="MiMo TTS未配置")

    # 检查是否为voicedesign模型
    health = provider.health_check()
    if health.get("model") != "mimo-v2.5-tts-voicedesign":
        raise HTTPException(
            status_code=400,
            detail="当前MiMo TTS模型不支持音色设计，请在配置中切换至mimo-v2.5-tts-voicedesign",
        )

    try:
        kwargs = {}
        if gender:
            kwargs["gender"] = gender
        if age_group:
            kwargs["age_group"] = age_group

        # 调用设计接口
        result = await provider.design_voice(
            description=description,
            voice_name=voice_name,
            **kwargs,
        )

        if result["status"] == "error":
            raise HTTPException(status_code=500, detail=result["message"])

        catalog_saved = _register_catalog(
            result, name=voice_name, kind="design",
            model="mimo-v2.5-tts-voicedesign",
            description=description, **kwargs,
        )
        result["catalog_saved"] = catalog_saved

        logger.info("音色设计成功: %s -> %s (catalog_saved=%s)",
                    voice_name, result.get("voice_id"), catalog_saved)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("音色设计失败: %s", e)
        raise HTTPException(status_code=500, detail="音色设计失败") from e


@router.post("/switch-voice")
async def switch_voice(
    voice_id: str = Form(..., description="音色ID"),
    tts_manager: TTSManager | None = Depends(get_tts_manager),  # noqa: B008
    _auth: bool = Depends(verify_api_key_dep),  # noqa: B008
) -> dict[str, Any]:
    """
    切换当前使用的音色

    Args:
        voice_id: 要切换到的音色ID

    Returns:
        {"status": str, "voice_id": str}
    """
    if tts_manager is None:
        raise HTTPException(status_code=400, detail="TTS管理器未初始化")
    provider = tts_manager.get_engine("mimo-tts")
    if not isinstance(provider, MiMoTTSProvider):
        raise HTTPException(status_code=400, detail="MiMo TTS未配置")

    if not get_voice_catalog().is_known(voice_id):
        raise HTTPException(
            status_code=404,
            detail=f"未知音色 voice_id: {voice_id}——请先克隆/设计音色或使用预设音色名",
        )

    try:
        provider.set_voice_id(voice_id)
        return {
            "status": "success",
            "voice_id": voice_id,
            "message": f"已切换到音色: {voice_id}",
        }
    except Exception as e:
        logger.error("切换音色失败: %s", e)
        raise HTTPException(status_code=500, detail="切换音色失败") from e


@router.get("/status")
async def mimo_status(
    tts_manager: TTSManager | None = Depends(get_tts_manager),  # noqa: B008
    _auth: bool = Depends(verify_api_key_dep),  # noqa: B008
) -> dict[str, Any]:
    """
    获取MiMo TTS状态

    Returns:
        MiMo TTS配置和健康状况
    """
    if tts_manager is None:
        return {
            "enabled": False,
            "message": "TTS管理器未初始化",
        }
    provider = tts_manager.get_engine("mimo-tts")
    if not isinstance(provider, MiMoTTSProvider):
        return {
            "enabled": False,
            "message": "MiMo TTS未配置",
        }

    health = provider.health_check()
    return {
        "enabled": True,
        "health": health,
        "current_engine": tts_manager.current_engine,
        "available_engines": tts_manager.available_engines,
    }


@router.post("/set-engine")
async def set_mimo_engine(
    model: str = Form(..., description="模型名称"),
    tts_manager: TTSManager | None = Depends(get_tts_manager),  # noqa: B008
    _auth: bool = Depends(verify_api_key_dep),  # noqa: B008
) -> dict[str, Any]:
    """
    切换MiMo TTS引擎模型

    Args:
        model: 模型名称（mimo-v2.5-tts / mimo-v2.5-tts-voiceclone / mimo-v2.5-tts-voicedesign / mimo-v2-tts）

    Returns:
        {"status": str, "model": str}
    """
    if tts_manager is None:
        raise HTTPException(status_code=400, detail="TTS管理器未初始化")
    from voice.mimo_tts_provider import MiMoTTSProvider

    provider = tts_manager.get_engine("mimo-tts")
    if not isinstance(provider, MiMoTTSProvider):
        raise HTTPException(status_code=400, detail="MiMo TTS未配置")

    if model not in MiMoTTSProvider.SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型: {model}，支持的模型: {MiMoTTSProvider.SUPPORTED_MODELS}",
        )

    # 重新初始化provider
    try:
        api_key = provider._api_key
        voice_id = provider._voice_id

        new_provider = MiMoTTSProvider(
            api_key=api_key,
            model=model,
            voice_id=voice_id,
            timeout=provider._timeout,
            fallback_local=provider._fallback_local,
            base_url=getattr(provider, "_api_base", MiMoTTSProvider.API_BASE),
        )

        # 更新providers字典
        tts_manager._providers["mimo-tts"] = new_provider

        logger.info("MiMo TTS引擎切换: %s", model)
        return {
            "status": "success",
            "model": model,
            "message": f"已切换到模型: {model}",
        }

    except Exception as e:
        logger.error("切换MiMo引擎失败: %s", e)
        raise HTTPException(status_code=500, detail="切换引擎失败") from e


@router.post("/synthesize")
async def synthesize(
    text: str = Form(..., description="合成文本"),
    voice_id: str = Form("", description="音色ID/预设名（可选，试听指定音色）"),
    model: str = Form("", description="MiMo 模型名（可选，须在白名单内）"),
    emotion: str = Form("", description="情感（可选，如 开心/伤心）"),
    tts_manager: TTSManager | None = Depends(get_tts_manager),  # noqa: B008
    _auth: bool = Depends(verify_api_key_dep),
):
    """MiMo TTS 直接合成（无需角色绑定；MIME 按实际格式返回）"""
    if tts_manager is None:
        raise HTTPException(status_code=400, detail="TTS管理器未初始化")
    provider = tts_manager.get_engine("mimo-tts")
    if not isinstance(provider, MiMoTTSProvider):
        raise HTTPException(status_code=400, detail="MiMo TTS未配置")

    if model and model not in MiMoTTSProvider.SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型: {model}，支持的模型: {MiMoTTSProvider.SUPPORTED_MODELS}",
        )
    if voice_id and not get_voice_catalog().is_known(voice_id):
        raise HTTPException(
            status_code=400,
            detail=f"未知音色 voice_id: {voice_id}——请先克隆/设计音色或使用预设音色名",
        )

    synth_kwargs: dict[str, Any] = {}
    if model:
        synth_kwargs["model"] = model
    if voice_id:
        synth_kwargs["voice_id"] = voice_id

    try:
        audio = await provider.synthesize(text, emotion=emotion, **synth_kwargs)
        if audio is None or len(audio) == 0:
            detail = getattr(provider, "_last_error", None) or "语音合成失败"
            raise HTTPException(status_code=502, detail=detail)
        return Response(content=audio.data, media_type=audio.mime)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("MiMo合成失败: %s", e)
        raise HTTPException(status_code=500, detail="语音合成失败") from e
