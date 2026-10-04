"""角色音色绑定 API — 管理角色与 TTS 音色的绑定关系

2026-08-28 MiMo-only 收敛：引擎维度删除（唯一引擎 mimo-tts）。
2026-09-27 W7 契约：``mimo_model`` / ``voice_id`` / ``speed`` / ``pitch``(数值)
成为显式角色语音契约字段（校验白名单与 catalog）；试听按角色契约快照合成并
按实际格式返回 MIME；``/voice/speakers`` 合并静态预设与音色 catalog。
既有 POST 展平契约保留（extra_params 内容仍展开落盘）。"""


from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.responses import Response
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.deps import deps
from api.routers.character_routes import require_character_access
from voice.mimo_tts_provider import MiMoTTSProvider
from voice.voice_catalog import get_voice_catalog, presets

logger = logging.getLogger("api.voice_routes")

router = APIRouter(prefix="/api", tags=["voice"])

# ── 请求/响应模型 ──

# pitch 双形态：数值 = MiMo 音调比例（云端契约）；字符串 = SAPI 风格（仅展示存储）
PitchField = str | float


class VoiceBindRequest(BaseModel):
    engine: str = "mimo-tts"
    speaker_name: str = ""
    rate: str = "+0%"
    pitch: PitchField = "0Hz"
    volume: str = "+0%"
    mimo_model: str | None = None
    voice_id: str | None = None
    speed: float | None = None
    extra_params: dict = {}


class VoiceUpdateRequest(BaseModel):
    engine: str | None = None
    speaker_name: str | None = None
    rate: str | None = None
    pitch: PitchField | None = None
    volume: str | None = None
    mimo_model: str | None = None
    voice_id: str | None = None
    speed: float | None = None
    extra_params: dict | None = None


class VoiceTestRequest(BaseModel):
    text: str = "你好，我是你的专属语音助手"


# ── 契约校验（C：引擎名/模型名/voice_id 三者类型分明）──


def _validate_contract(
    engine: str,
    mimo_model: str | None,
    voice_id: str | None,
) -> None:
    if engine != "mimo-tts":
        raise HTTPException(
            status_code=400,
            detail=f"未知引擎 {engine!r}：唯一引擎为 mimo-tts；mimo_model 才是模型名字段"
            f"（可选值 {MiMoTTSProvider.SUPPORTED_MODELS}）",
        )
    if mimo_model and mimo_model not in MiMoTTSProvider.SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 MiMo 模型: {mimo_model}，支持的模型: {MiMoTTSProvider.SUPPORTED_MODELS}",
        )
    if voice_id and not get_voice_catalog().is_known(voice_id):
        raise HTTPException(
            status_code=400,
            detail=f"未知音色 voice_id: {voice_id}——请先在语音工作台克隆/设计音色，"
                "或使用预设音色名（见 /api/voice/speakers）",
        )


def _bind_fields(req: VoiceBindRequest) -> dict[str, Any]:
    """请求 → bind kwargs（显式契约字段 + 展平的 extra_params 并列落盘）。"""
    fields: dict[str, Any] = {
        "rate": req.rate,
        "volume": req.volume,
        **req.extra_params,
    }
    # pitch：数值按 MiMo 比例落盘；字符串按 SAPI 风格原样落盘
    fields["pitch"] = float(req.pitch) if isinstance(req.pitch, (int, float)) else req.pitch
    if req.mimo_model is not None:
        fields["mimo_model"] = req.mimo_model
    if req.voice_id is not None:
        fields["voice_id"] = req.voice_id
    if req.speed is not None:
        fields["speed"] = req.speed
    return fields


# ── MiMo 预设音色（唯一引擎；克隆/设计音色见 /api/mimo/* 与 catalog）──

_MIMO_VOICES = [
    {**preset, "kind": "preset"} for preset in presets()
]


# ── API 端点 ──


@router.get("/characters/{character_id}/voice")
async def get_character_voice(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """绑定角色音色"""
    _validate_contract(req.engine, req.mimo_model, req.voice_id)
    voice_mgr = deps.get_character_voice_manager()

    try:
        voice_mgr.bind_voice(
            character_id=character_id,
            engine=req.engine,
            speaker_name=req.speaker_name,
            **_bind_fields(req),
        )
        logger.info(
            "角色 %s 绑定音色: model=%s voice_id=%s speaker=%s",
            character_id, req.mimo_model, req.voice_id, req.speaker_name,
        )
        return {"status": "bound", "character_id": character_id, "engine": req.engine}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/characters/{character_id}/voice")
async def update_character_voice(
    character_id: str,
    req: VoiceUpdateRequest,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """更新角色音色配置（部分更新）"""
    _validate_contract(
        req.engine or "mimo-tts", req.mimo_model, req.voice_id,
    )
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
        updated["pitch"] = float(req.pitch) if isinstance(req.pitch, (int, float)) else req.pitch
    if req.volume is not None:
        updated["volume"] = req.volume
    if req.mimo_model is not None:
        updated["mimo_model"] = req.mimo_model
    if req.voice_id is not None:
        updated["voice_id"] = req.voice_id
    if req.speed is not None:
        updated["speed"] = req.speed
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
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    _principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    """MiMo 音色列表：静态预设 + 音色 catalog（克隆/设计产物，重启可找回）

    P0 F2 按主体过滤：普通用户只见平台音色（静态预设+无主存量克隆）与自己
    的克隆音色；admin 全量。机器面（无 Bearer）契约不收窄（W1 先例：机器面
    不干预），仍全量——直呼 handler 传入的 Depends 哨兵按机器面解释。
    """
    principal = _principal if isinstance(_principal, AuthPrincipal) else None
    is_admin = principal is None or principal.role == "admin"
    my_key = str(principal.user_id) if principal is not None else ""
    custom = [
        {
            "name": entry["voice_id"],
            "display_name": entry.get("name") or entry["voice_id"],
            "description": entry.get("description", ""),
            "kind": entry.get("kind", "custom"),
            "model": entry.get("model", ""),
            "voice_id": entry["voice_id"],
        }
        for entry in get_voice_catalog().list()
        # owner 为空 = 平台共享（存量旧 JSON 无 owner 键同此解释）
        if is_admin or not entry.get("owner") or entry.get("owner") == my_key
    ]
    speakers = [dict(v) for v in _MIMO_VOICES] + custom
    return {
        "engine": "mimo-tts",
        "speakers": speakers,
        "total": len(speakers),
    }


@router.post("/characters/{character_id}/voice/test")
async def test_character_voice(
    character_id: str,
    req: VoiceTestRequest,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """按角色音色契约试听（不可变快照合成；MIME 按实际格式返回）"""
    voice_mgr = deps.get_character_voice_manager()
    tts_mgr = deps.get_tts()
    if tts_mgr is None:
        raise HTTPException(status_code=503, detail="语音服务未初始化")

    voice_config = voice_mgr.get_voice_config(character_id)
    if voice_config is None:
        raise HTTPException(status_code=400, detail="角色未配置音色，请先绑定")

    # W7：按角色契约快照合成（不再需要也不允许切全局引擎/音色"模拟角色"）
    spec = voice_mgr.resolve_voice_spec(character_id)
    synth_kwargs = spec.synth_kwargs() if spec else {}

    audio = await tts_mgr.synthesize(req.text, **synth_kwargs)
    if audio is None or len(audio) == 0:
        last_error = getattr(tts_mgr, "_last_error", None) or "未知原因"
        raise HTTPException(
            status_code=502, detail=f"语音合成失败: {last_error}",
        )

    logger.info(
        "角色 %s 试听成功: %d bytes fmt=%s kwargs=%s",
        character_id, len(audio), audio.fmt, synth_kwargs,
    )
    return Response(content=audio.data, media_type=audio.mime)
