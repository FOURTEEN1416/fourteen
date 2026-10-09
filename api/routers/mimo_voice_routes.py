"""
MiMo TTS API路由扩展

提供语音克隆、音色设计等MiMo特有功能
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from starlette.datastructures import UploadFile as _StarletteUploadFile

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal, require_role
from api.database import User
from api.deps import get_tts_manager
from voice.mimo_tts_provider import MiMoTTSProvider
from voice.tts_manager import TTSManager
from voice.voice_catalog import get_voice_catalog

logger = logging.getLogger("api.mimo")

router = APIRouter(prefix="/api/mimo", tags=["mimo-tts"])

# P0 安全批 F1：合成文本上限（字符数）——云端 TTS 按字符计费，超长文本既是滥用面也是慢请求
_MAX_SYNTH_TEXT_LEN = 600

# 全仓历遍安全批 ABUSE-2：clone 上传体上限（对齐 clone_routes.py 的 50MB 口径）。
# 旧实现 ``await audio.read()`` 把整个 spool 文件一次性读进 worker 内存——app_factory
# 的 request_size_limiter 只看 Content-Length 头，chunked 上传（无此头）绕过后直达
# 本端点，数 GB body 即内存耗尽。
_MAX_AUDIO_UPLOAD_BYTES = 50 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024
# 读前快速通道：multipart 整包 Content-Length 含表单字段与 boundary 开销，
# 故在音频上限上放宽 5MB 余量；CL 可缺失（chunked）/伪造，真正判据是分块累计。
_CLONE_BODY_FAST_LIMIT = _MAX_AUDIO_UPLOAD_BYTES + 5 * 1024 * 1024


class _RateLimiter:
    """每主体滑动窗口限速（内存计数；进程级，多 worker 各自独立——本批按任务书不做跨进程）。

    仅本文件使用，勿抽公共模块（P0 安全批并行窗口纪律）。
    """

    def __init__(self, max_events: int, window_seconds: float = 60.0):
        self._max = max_events
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """窗口内还有配额则记账并放行，否则拒绝（含失败请求，防绕过试错）。"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < self._window]
            if len(hits) >= self._max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self) -> None:
        """清空记账（测试隔离用）。"""
        with self._lock:
            self._hits.clear()


# synthesize 6 次/分钟；clone 与 design 各 2 次/分钟（按端点独立计桶）
_SYNTH_LIMITER = _RateLimiter(6)
_CLONE_LIMITER = _RateLimiter(2)
_DESIGN_LIMITER = _RateLimiter(2)


def _as_principal(candidate: Any) -> AuthPrincipal | None:
    """归一主体：直呼 handler（既有测试/脚本只传 _auth=True）会拿到 Depends 哨兵而非
    真实主体——统一按「无主体（机器面）」解释；HTTP 层由 FastAPI 注入真实 AuthPrincipal。
    """
    return candidate if isinstance(candidate, AuthPrincipal) else None


def _limit_or_429(limiter: _RateLimiter, principal: AuthPrincipal | None) -> None:
    key = f"user:{principal.user_id}" if principal is not None else "machine"
    if not limiter.check(key):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


async def _read_upload_capped(audio: UploadFile) -> bytes:
    """分块读取上传音频并强制大小上限（ABUSE-2：超限即刻 413 并停止读取）。

    starlette UploadFile.read(size) 支持分块累计；直呼 handler 的伪造上传
    （既有测试/脚本契约，仅实现无参 ``read()``）一次性读入——进程内调用
    没有传输体积攻击面。

    注意：FastAPI 注入的运行时实例是 starlette 的 UploadFile（fastapi.UploadFile
    是其子类），isinstance 必须按父类判定，按子类判定会恒 False 落入直呼分支。
    """
    if not isinstance(audio, _StarletteUploadFile):
        return await audio.read()
    buf = bytearray()
    while True:
        chunk = await audio.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > _MAX_AUDIO_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"参考音频过大（上限 {_MAX_AUDIO_UPLOAD_BYTES // (1024 * 1024)}MB）",
            )
    return bytes(buf)


def _reject_oversized_clone_body(request: Request) -> None:
    """ABUSE-2 读前快速通道（依赖实现）：整包 Content-Length 超阈值直接 413。

    multipart 整包 CL 含表单字段与 boundary 开销，故阈值在音频上限上放宽余量；
    chunked 上传无此头、CL 也可伪造，仅作快速通道，真正判据是分块累计。
    直呼 handler（既有测试/脚本契约，依赖为哨兵不执行）自然跳过本通道。
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _CLONE_BODY_FAST_LIMIT:
        raise HTTPException(
            status_code=413,
            detail=f"参考音频过大（整包超过 {_CLONE_BODY_FAST_LIMIT // (1024 * 1024)}MB 上限）",
        )


def _register_catalog(result: dict[str, Any], *, name: str, kind: str, model: str,
                      description: str = "", owner: str = "", **extra: str) -> bool:
    """克隆/设计产物登记进音色 catalog（持久化 owner）。

    云端资源已创建、本地登记失败时如实返回 False（不谎报已持久化）。
    ``owner`` 为调用者主体（user_id 字符串），空串 = 平台共享。
    """
    voice_id = result.get("voice_id", "")
    if not voice_id:
        return False
    try:
        get_voice_catalog().register(
            voice_id=voice_id, name=name, kind=kind, model=model,
            description=description, owner=owner, **extra,
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
    _principal: Any = Depends(get_optional_principal),  # noqa: B008
    _body_guard: Any = Depends(_reject_oversized_clone_body),  # noqa: B008
) -> dict[str, Any]:
    """
    克隆音色

    上传10-30秒的参考音频，创建自定义音色（P0：每用户限速 2 次/分钟；
    全仓历遍安全批 ABUSE-2：上传体 50MB 上限，读前 Content-Length 快速通道
    + 分块读累计，超限 413 并停止读取；产物归属登记给调用者主体）

    Args:
        voice_name: 音色名称（用于标识）
        description: 音色描述
        audio: 参考音频文件（MP3/WAV格式）

    Returns:
        {"voice_id": str, "status": str, "message": str}
    """
    principal = _as_principal(_principal)
    _limit_or_429(_CLONE_LIMITER, principal)

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
        # 读取音频数据（分块读带上限，超限 413 并停止读取）
        audio_data = await _read_upload_capped(audio)

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
            owner=str(principal.user_id) if principal is not None else "",
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
    _principal: Any = Depends(get_optional_principal),  # noqa: B008
) -> dict[str, Any]:
    """
    设计音色

    通过描述性文本设计新音色，无需参考音频（P0：每用户限速 2 次/分钟；
    产物归属登记给调用者主体）

    Args:
        voice_name: 音色名称
        description: 音色描述
        gender: 性别（可选）
        age_group: 年龄段（可选）

    Returns:
        {"voice_id": str, "status": str, "message": str}
    """
    principal = _as_principal(_principal)
    _limit_or_429(_DESIGN_LIMITER, principal)

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
            description=description, owner=str(principal.user_id) if principal is not None else "",
            **kwargs,
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
    _admin: tuple[int, User] = Depends(require_role("admin")),  # noqa: B008
) -> dict[str, Any]:
    """
    切换当前使用的音色（P0：改全局 TTS 单例，仅 admin）

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
    _admin: tuple[int, User] = Depends(require_role("admin")),  # noqa: B008
) -> dict[str, Any]:
    """
    切换MiMo TTS引擎模型（P0：改全局 TTS 单例，仅 admin）

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
    _principal: Any = Depends(get_optional_principal),
):
    """MiMo TTS 直接合成（无需角色绑定；MIME 按实际格式返回）

    P0 安全批：text>600 字 400；每用户限速 6 次/分钟；voice_id 归属校验
    （他人克隆音色 403，admin 例外，平台共享/预设不受限）。
    """
    principal = _as_principal(_principal)

    if len(text) > _MAX_SYNTH_TEXT_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"合成文本过长（{len(text)} 字 > 上限 {_MAX_SYNTH_TEXT_LEN} 字）",
        )
    _limit_or_429(_SYNTH_LIMITER, principal)

    # P0 F2 归属校验先于一切服务状态暴露：catalog 内他人克隆音色 403
    # （admin 例外；无主平台音色/预设不受限；未知 voice_id 交给下方 is_known 400）
    if voice_id and principal is not None and principal.role != "admin":
        entry = get_voice_catalog().get(voice_id)
        owner = str(entry.get("owner") or "") if entry else ""
        if owner and owner != str(principal.user_id):
            raise HTTPException(status_code=403, detail="无权使用他人克隆音色")

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
