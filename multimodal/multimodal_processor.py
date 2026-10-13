from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("multimodal")

# 图片描述 prompt（A 档 describe 链）：陪伴场景需要场景/人物/情绪粒度，
# 20 字以内承载不了图片语义——上限 150 字，供角色理解图片并自然接话。
IMAGE_DESCRIBE_PROMPT = (
    "请描述这张图片：画面场景、出现的人物或物品、整体情绪氛围，"
    "用不超过150字的中文口语描述；不要推测用户意图，只输出描述本身。"
)

# 图片无法归一化（损坏 base64 等）时的占位文本。必须保持「[收到一张图片」
# 前缀——connector._describe_image 按该前缀过滤为空串。
_IMAGE_UNAVAILABLE_PLACEHOLDER = "[收到一张图片，无法解析]"


class MultimodalProcessor:
    def __init__(self, llm_gateway=None, asr_config: dict[str, Any] | None = None):
        self._llm = llm_gateway
        self._vision = VisionHandler(llm_gateway)
        self._asr = ASRHandler(asr_config)
        self._emoji = EmojiResponder()

    async def process(self, message: Any, message_type: str = "text") -> dict[str, Any]:
        if message_type == "image":
            return await self._vision.process(message)  # type: ignore[no-any-return]
        if message_type == "voice":
            return await self._asr.process(message)  # type: ignore[no-any-return]
        return {"text": str(message), "modality": "text", "original": message}

    def should_reply_with_emoji(self, emotion: str = "", affinity: int = 0) -> bool:
        return self._emoji.should_send(emotion, affinity)  # type: ignore[no-any-return]

    def get_emoji_reply(self, emotion: str = "") -> str | None:
        return self._emoji.get_emoji(emotion)  # type: ignore[no-any-return]


class VisionHandler:
    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway

    async def process(self, image_data: Any) -> dict[str, Any]:
        if self._llm and hasattr(self._llm, 'chat'):
            try:
                # 归一化单一真源：裸 base64/data URL/公网 URL → data URL，与直传链
                # build_attachments 同源（multimodal.image_attachment.to_data_url）。
                # 旧实现把裸 base64 直塞 image_url.url，视觉模型换链即必失败。
                from multimodal.image_attachment import to_data_url

                url = to_data_url(image_data) if isinstance(image_data, str) else None
                if not url:
                    return {
                        "text": _IMAGE_UNAVAILABLE_PLACEHOLDER,
                        "modality": "image",
                        "original": None,
                    }
                messages = [
                    {"role": "user", "content": [
                        {"type": "text", "text": IMAGE_DESCRIBE_PROMPT},
                        {"type": "image_url", "image_url": {"url": url}},
                    ]},
                ]
                description = await self._llm.chat(messages=messages)
                return {"text": description, "modality": "image", "original": None}
            except Exception as e:  # noqa: BLE001
                logger.warning("Vision processing failed: %s", e)
                return {"text": "[收到一张图片，视觉理解失败]", "modality": "image", "original": None}
        # 视觉理解不可用：未传入支持 chat 的 LLM 网关
        return {"text": "[收到一张图片]", "modality": "image", "original": None}


class ASRHandler:
    """语音转文字（2026-08-28 补实，候选 A）。

    方案：OpenAI 兼容 /audio/transcriptions 端点（whisper-1 或任意兼容服务，
    如硅基流动 SenseVoice），配置驱动：config/system.yaml → voice.asr。
    未配置 enabled=true 时保持历史占位行为（不报错、零影响）。
    音频前置：ffmpeg 转 WAV 16k mono（AudioFormatConverter.to_wav）。
    """

    def __init__(self, asr_config: dict[str, Any] | None = None):
        cfg = asr_config or {}
        self._enabled = bool(cfg.get("enabled", False))
        self._api_base = (cfg.get("api_base") or "").rstrip("/")
        self._api_key = cfg.get("api_key") or ""
        self._model = cfg.get("model", "whisper-1")
        self._language = cfg.get("language", "zh")
        self._timeout = float(cfg.get("timeout", 30.0))

    async def process(self, audio_data: Any, source_format: str = "silk") -> dict[str, Any]:
        placeholder = {"text": "[语音消息，暂无法转文字]", "modality": "voice", "confidence": 0.0}
        if not self._enabled or not self._api_base:
            return placeholder
        raw = audio_data
        if isinstance(raw, str):
            import base64
            try:
                raw = base64.b64decode(raw)
            except Exception:
                return placeholder
        if not raw:
            return placeholder

        # ffmpeg 前置转 WAV（微信语音为 silk；若 ffmpeg 无 silk 解码则按原样尝试）
        import asyncio

        from voice.audio_converter import AudioFormatConverter

        def _convert() -> tuple[bytes, str]:
            conv = AudioFormatConverter()
            wav = conv.to_wav(raw, source_format=source_format)
            if wav:
                return wav, "wav"
            return raw, source_format

        try:
            payload, fmt = await asyncio.to_thread(_convert)
        except Exception as e:
            logger.warning("ASR 音频转换失败: %s", e)
            return placeholder

        try:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                files = {"file": (f"audio.{fmt}", payload, "audio/wav" if fmt == "wav" else "application/octet-stream")}
                data = {"model": self._model}
                if self._language:
                    data["language"] = self._language
                resp = await client.post(
                    f"{self._api_base}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {},
                    files=files,
                    data=data,
                )
            if resp.status_code == 200:
                text = (resp.json() or {}).get("text", "").strip()
                if text:
                    return {"text": text, "modality": "voice", "confidence": 0.9}
                return placeholder
            logger.warning("ASR API %s: %s", resp.status_code, resp.text[:150])
            return placeholder
        except Exception as e:
            logger.warning("ASR 转录异常: %s", e)
            return placeholder


EMOTION_EMOJI_MAP = {
    "LOVELY": ["❤️", "💕", "😘", "🥰"],
    "HAPPY": ["😊", "😄", "🎉", "✨"],
    "PLAYFUL": ["😏", "😜", "🤭", "😝"],
    "CARING": ["🤗", "💕", "🌸", "☀️"],
    "SULLEN": ["哼", "😤", "🙄", "😒"],
}

EMOTION_NAME_MAP = {
    "撒娇": "LOVELY", "开心": "HAPPY", "调皮": "PLAYFUL",
    "温柔": "CARING", "傲娇": "SULLEN",
}


class EmojiResponder:
    def __init__(self, max_ratio: float = 0.1, min_affinity: int = 6):
        self.max_ratio = max_ratio
        self.min_affinity = min_affinity
        self._reply_count = 0
        self._emoji_count = 0

    def should_send(self, emotion: str = "", affinity: int = 0) -> bool:
        if affinity < self.min_affinity:
            return False
        emotion_upper = EMOTION_NAME_MAP.get(emotion, emotion).upper()
        if emotion_upper not in EMOTION_EMOJI_MAP:
            return False
        return not (self._reply_count > 0 and self._emoji_count / self._reply_count >= self.max_ratio)

    def get_emoji(self, emotion: str = "") -> str | None:
        import random
        emotion_upper = EMOTION_NAME_MAP.get(emotion, emotion).upper()
        emojis = EMOTION_EMOJI_MAP.get(emotion_upper)
        if emojis:
            self._reply_count += 1
            self._emoji_count += 1
            return random.choice(emojis)
        return None
