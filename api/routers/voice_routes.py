"""角色音色绑定 API — 管理角色与 TTS 音色的绑定关系

2026-08-28 MiMo-only 收敛：引擎维度删除（唯一引擎 mimo-tts）。
2026-09-27 W7 契约：``mimo_model`` / ``voice_id`` / ``speed`` / ``pitch``(数值)
成为显式角色语音契约字段（校验白名单与 catalog）；试听按角色契约快照合成并
按实际格式返回 MIME；``/voice/speakers`` 合并静态预设与音色 catalog。
既有 POST 展平契约保留（extra_params 内容仍展开落盘）。"""


from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.responses import Response
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.deps import deps
from api.routers.character_routes import (
    require_character_access,
    require_character_write,
)
from shisi.voice.character_voice import effective_voice_ids
from voice.mimo_tts_provider import MiMoTTSProvider
from voice.voice_catalog import (
    VOICE_OWNER_DENIED,
    denied_voice_owner,
    get_voice_catalog,
    presets,
)

logger = logging.getLogger("api.voice_routes")

router = APIRouter(prefix="/api", tags=["voice"])

# 全仓历遍安全批 ABUSE-3：角色试听与 /api/voice/synthesize、/api/mimo/synthesize
# 是同成本路径（MiMo 云按字符计费），同族收口——600 字上限 + 6 次/分/用户限速。
_MAX_SYNTH_TEXT_LEN = 600


class _RateLimiter:
    """每主体滑动窗口限速（内存计数；进程级，多 worker 各自独立）。

    仅本文件使用，勿抽公共模块（P0 安全批并行窗口纪律，与 safety_routes /
    mimo_voice_routes 各自内置）。
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


_VOICE_TEST_LIMITER = _RateLimiter(6)  # 6 次/分钟/用户


def _limit_or_429(limiter: _RateLimiter, principal: AuthPrincipal | None) -> None:
    key = f"user:{principal.user_id}" if principal is not None else "machine"
    if not limiter.check(key):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

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


def _as_principal(candidate: Any) -> AuthPrincipal | None:
    """归一主体：直呼 handler（既有测试/脚本只传位置参数）会拿到 ``Depends`` 哨兵
    而非真实主体——统一按「无主体（机器面）」解释；HTTP 层由 FastAPI 注入真值。
    """
    return candidate if isinstance(candidate, AuthPrincipal) else None


def _reject_conflicting_extra_keys(extra_params: dict[str, Any] | None) -> None:
    """``extra_params`` 会被 ``**`` 展平进绑定 kwargs，与显式形参同名的键会
    触发 ``TypeError: got multiple values for keyword argument`` → 500。

    这些键在契约里都有专用字段，客户端没有理由塞进 ``extra_params``；一律 400
    （fail-closed，也顺手杜绝「用展平覆盖 engine/speaker_name 逃避校验」的形态）。
    """
    if not isinstance(extra_params, dict):
        return
    for key in ("engine", "speaker_name", "character_id"):
        if key in extra_params:
            raise HTTPException(
                status_code=400,
                detail=f"extra_params 不允许包含保留字段名: {key}（请使用同名顶层字段）",
            )


def _assert_voice_owner(voice_ids: list[str], principal: AuthPrincipal | None) -> None:
    """音色资产归属的强制点（普通用户不得使用他人克隆音色）。

    归属位是 ``voice.catalog`` 条目的 ``owner``（克隆/设计登记时写入，见 ``api/routers/
    mimo_voice_routes.py:219/:297``）。判定规则**不在本文件实现**——统一调
    ``voice.voice_catalog.denied_voice_owner``（唯一 owner），与合成入口
    ``/api/mimo/synthesize`` 共用同一函数与同一文案 ``VOICE_OWNER_DENIED``，
    避免两处口径漂移（AGENTS §1.2 重复 owner 零容忍）。
    无主条目（平台共享克隆）与静态预设不受限；admin 例外；机器面（无 Bearer）不干预。
    """
    if principal is None:
        return
    for voice_id in voice_ids:
        if denied_voice_owner(
            voice_id,
            user_id=str(principal.user_id),
            is_admin=principal.role == "admin",
        ):
            raise HTTPException(status_code=403, detail=VOICE_OWNER_DENIED)


def _validate_contract(
    engine: str,
    mimo_model: str | None,
    voice_id: str | None,
) -> None:
    """三类字段校验（引擎名 / 模型名 / voice_id 白名单）。

    归属不在这里做——只用声明字段校验会被 ``extra_params`` 与 ``speaker_name``
    两个旁路字段绕过（有效音色位由 ``shisi.voice.character_voice.effective_voice_ids``
    单点定义，是**保守上界**而非 ``_pick`` 的逐位镜像）。owner 强制点统一由调用方在
    算出「有效 voice_id 集合」后调 ``_assert_voice_owner``。
    """
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
    # 读侧走 require_character_access：他人私有卡 404；**无主公共卡可读**
    # （2026-10-27 公共池改造，见 character_routes.card_access_allowed）；
    # 机器面（无 Bearer）不干预。
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
    _principal: AuthPrincipal | None = Depends(get_optional_principal),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # **写**侧走 require_character_write：他人私有卡 + 无主公共卡一律 404
    # （公共池可读不可写，见 card_write_allowed）；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_write),
):
    """绑定角色音色"""
    _validate_contract(req.engine, req.mimo_model, req.voice_id)
    _reject_conflicting_extra_keys(req.extra_params)
    fields = _bind_fields(req)
    # 归属按**将要落盘的真实绑定体**判（与 bind_voice 的构造同形），不按请求字段猜：
    # 展平后的 cfg["extra_params"] 可能是客户端塞进来的内层 dict（嵌套绕过）。
    binding = {"engine": req.engine, "speaker_name": req.speaker_name, **fields}
    _assert_voice_owner(effective_voice_ids(binding), _as_principal(_principal))
    voice_mgr = deps.get_character_voice_manager()

    try:
        voice_mgr.bind_voice(
            character_id=character_id,
            engine=req.engine,
            speaker_name=req.speaker_name,
            **fields,
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
    _principal: AuthPrincipal | None = Depends(get_optional_principal),
    # 写侧同 POST：他人私有卡 + 无主公共卡一律 404（require_character_write）。
    _owned: dict = Depends(require_character_write),
):
    """更新角色音色配置（部分更新）"""
    _validate_contract(req.engine or "mimo-tts", req.mimo_model, req.voice_id)
    _reject_conflicting_extra_keys(req.extra_params)
    voice_mgr = deps.get_character_voice_manager()

    current = voice_mgr.get_voice_config(character_id)
    if current is None:
        raise HTTPException(status_code=404, detail="角色未配置音色，请先绑定")

    # 深拷贝再合并：get_voice_config 返回 _bindings[character_id] 的**原引用**，
    # dict(current) 只是浅拷贝，`extra.update(...)` 会就地改掉共享的嵌套 dict——
    # 即使随后 403 拒绝，内存里的绑定也已被污染，resolve_voice_spec 立刻生效。
    updated = copy.deepcopy(current)
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

    # 归属按**合并后**的完整绑定体判（deepcopy 出来的副本，不污染原对象）：
    # PUT 可只改 speaker_name / extra_params，单看 req.voice_id 会漏。
    _assert_voice_owner(effective_voice_ids(updated), _as_principal(_principal))

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
    # 解绑同样是写操作：他人私有卡 + 无主公共卡一律 404（require_character_write）。
    # 旧实现用读级 require_character_access → 任何 viewer 都能解绑 41 张公共卡的
    # 音色，而 character_voices.json 是**全局 per-character** 存储（不分用户），
    # 一次解绑影响全部使用该卡的用户。
    _owned: dict = Depends(require_character_write),
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
    principal = _as_principal(_principal)
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
    _principal: Any = Depends(get_optional_principal),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 试听不改卡片也不改绑定，走读侧 require_character_access：他人私有卡 404；
    # **无主公共卡可读可试听**（公共池口径）；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """按角色音色契约试听（不可变快照合成；MIME 按实际格式返回）

    ABUSE-3 同族收口：text>600 字 400 + 每用户限速 6 次/分钟（MiMo 云按字符
    计费，与 /api/voice/synthesize、/api/mimo/synthesize 同成本路径）。
    """
    # 直呼 handler（既有测试/脚本只传位置参数）会拿到 Depends 哨兵——按机器面解释
    principal = _as_principal(_principal)

    if len(req.text) > _MAX_SYNTH_TEXT_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"合成文本过长（{len(req.text)} 字 > 上限 {_MAX_SYNTH_TEXT_LEN} 字）",
        )
    _limit_or_429(_VOICE_TEST_LIMITER, principal)

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
