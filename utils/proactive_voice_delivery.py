"""主动线语音投递（2026-11-08 用户裁决：「让她拥有并且会驱动语音功能」）。

唯一 owner：TTS 配置 / 角色音色契约 / 合成 / silk 转码 / 微信语音发送 /
凭据定位。**常驻主动线（scheduler）与广播脚本共用本模块**——禁止两处各写
一份（否则会出现「脚本能发、主动线发不出」这类漂移）。

关键约束（踩过的坑）：
- TTS 初始化与合成必须在**同一事件循环**（aiohttp client 绑定 loop）→ 由
  调用方经 ``asyncio.to_thread`` 进来，内部 ``asyncio.run`` 一次包完；
- iLink 平台**不渲染机器人出站的原生语音条**（2026-11-08 真机实证，接口仍
  返回 message_id）→ 默认投递形态是**音频文件卡片**（mp3 原始音频）；silk
  转码只在 ``WECHAT_NATIVE_VOICE_BUBBLE=1`` 的原生气泡路径发生；
- iLink 会话窗口 24h：凭据过期就只能文字，``find_wechat_credentials`` 会
  如实返回 age_hours，调用方自行决定是否尝试。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

SESSIONS_DIR = Path("data/wechat_sessions")
DEFAULT_MAX_AGE_HOURS = 24.0

_TTS_LOCK = threading.Lock()
_TTS_MGR: Any = None
_TTS_CFG: dict | None = None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _voice_cfg() -> dict:
    from observability.config_manager import ConfigManager

    cfg = ConfigManager(config_dir="config").config
    return cfg.voice.model_dump(by_alias=True)


def get_tts_manager() -> Any:
    """进程内单例 TTSManager（初始化失败/未启用返回 None）。"""
    global _TTS_MGR, _TTS_CFG
    with _TTS_LOCK:
        if _TTS_MGR is None:
            try:
                from voice import TTSManager

                mgr = TTSManager()
                if _TTS_CFG is None:
                    _TTS_CFG = _voice_cfg()
                asyncio.run(mgr.initialize(_TTS_CFG))
                _TTS_MGR = mgr if getattr(mgr, "enabled", False) else None
            except Exception:  # noqa: BLE001
                _TTS_MGR = None
        return _TTS_MGR


def _reset_tts_manager() -> None:
    """测试用：清空缓存。"""
    global _TTS_MGR, _TTS_CFG
    with _TTS_LOCK:
        _TTS_MGR = None
        _TTS_CFG = None


def synthesize_audio(
    text: str, character_id: str = "", user_id: int | str | None = None,
) -> tuple[bytes, str] | None:
    """文本 → (原始合成音频字节, fmt=mp3/wav)。失败返回 None（调用方回落文字）。

    2026-11-08 语音送达批：投递形态默认是**音频文件卡片**（原生语音条被
    iLink 平台静默丢弃），文件卡片直接用 TTS 原始音频，不需要 silk 转码。
    """
    mgr = get_tts_manager()
    if mgr is None:
        return None
    kwargs: dict = {}
    try:
        from shisi.voice.character_voice import CharacterVoiceManager

        spec = CharacterVoiceManager().resolve_voice_spec(str(character_id or ""), str(user_id or ""))
        if spec is not None:
            kwargs = spec.synth_kwargs()
    except Exception:  # noqa: BLE001
        kwargs = {}

    # 朗读稿与对话链同一 owner（voice.voice_script）：主动线历史上也是
    # ``text[:200]`` 原样送 TTS，会把 ``[主动]`` 标记和动作括号念出来。
    from voice.voice_script import to_spoken_script

    script = to_spoken_script(text, max_chars=200)
    if not script:
        return None

    async def _synth():
        return await mgr.synthesize(script, **kwargs)

    try:
        audio = asyncio.run(_synth())
    except Exception:  # noqa: BLE001
        return None
    if not audio or not getattr(audio, "data", None):
        return None
    return audio.data, str(getattr(audio, "fmt", "") or "mp3")


def synthesize_silk(
    text: str, character_id: str = "", user_id: int | str | None = None,
) -> tuple[bytes, str] | None:
    """文本 → (silk/amr 字节, fmt)。仅原生气泡路径需要；失败返回 None。"""
    audio = synthesize_audio(text, character_id, user_id)
    if audio is None:
        return None
    data, source_fmt = audio
    try:
        from voice.audio_converter import AudioFormatConverter

        conv = AudioFormatConverter()
        silk = conv.to_silk(data, source_fmt)
        if silk:
            return silk, "silk"
        amr = conv.to_amr(data, source_fmt)
        if amr:
            return amr, "amr"
    except Exception:  # noqa: BLE001
        return None
    return None


def find_wechat_credentials(
    owner_id: int | str, peer: str, *, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict | None:
    """定位 owner 下 peer 的发送凭据（token/base_url/context_token/age_hours）。

    命中多个 slot 时优先返回**窗口内**（``fresh=True``）的那份；都没有则返回
    最新一份（``fresh=False``，调用方自行决定是否仍尝试——平台会拒）。
    """
    try:
        owner_dir = SESSIONS_DIR / str(int(owner_id))
    except Exception:  # noqa: BLE001
        return None
    if not owner_dir.is_dir():
        return None
    best: dict | None = None
    now = time.time()
    for slot_dir in sorted(owner_dir.iterdir()):
        if not slot_dir.is_dir():
            continue
        cred = _read_json(slot_dir / "credentials.json")
        tokens = _read_json(slot_dir / "context_tokens.json")
        meta = tokens.get(str(peer))
        if not cred.get("token") or not isinstance(meta, dict):
            continue
        ts = float(meta.get("ts") or 0)
        age_h = (now - ts) / 3600.0 if ts else 9999.0
        item = {
            "token": str(cred.get("token") or ""),
            "base_url": str(cred.get("base_url") or ""),
            "context_token": str(meta.get("token") or ""),
            "age_hours": round(age_h, 1),
            "fresh": age_h < float(max_age_hours),
        }
        if item["fresh"]:
            return item
        if best is None or item["age_hours"] < best["age_hours"]:
            best = item
    return best


def send_proactive_voice(
    session_key: str,
    text: str,
    character_id: str = "",
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> tuple[bool, str]:
    """给微信会话发一条语音条。返回 ``(ok, reason)``；失败原因不含隐私。"""
    try:
        from utils import session_key as sk_mod

        sk = str(session_key or "")
        if not sk_mod.is_wechat_key(sk):
            return False, "not_wechat"
        parsed = sk_mod.parse(sk)
        owner, peer = parsed.owner, parsed.peer
        if owner is None or not peer:
            return False, "bad_session_key"
    except Exception:  # noqa: BLE001
        return False, "bad_session_key"
    creds = find_wechat_credentials(owner, peer, max_age_hours=max_age_hours)
    if creds is None:
        return False, "no_credentials"
    audio = synthesize_audio(text, character_id, owner)
    if audio is None:
        return False, "tts_unavailable"
    data, fmt = audio
    try:
        # 2026-11-08 语音送达批：形态决策（音频文件卡片 / 原生气泡）的唯一
        # owner 在 wechat_connector，主动线不再自己拼 voice item。
        from wechat_direct.wechat_connector import send_voice_clip_to

        ok, err = send_voice_clip_to(
            peer,
            data,
            fmt,
            context_token=creds.get("context_token") or "",
            token=creds.get("token") or "",
            base_url=creds.get("base_url") or "",
            duration_ms=min(60000, max(1500, len(str(text or "")) * 180)),
        )
        return bool(ok), ("" if ok else str(err or "send_rejected")[:120])
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"
