from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("multimodal")


class MultimodalProcessor:
    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway
        self._vision = VisionHandler(llm_gateway)
        self._asr = ASRHandler()
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
                # 修复：原代码构建了消息列表但从未传给 LLM，直接返回假回复
                # 现在实际调用 LLM 网关进行视觉理解
                messages = [
                    {"role": "user", "content": [
                        {"type": "text", "text": "请描述这张图片的内容，简洁20字以内。"},
                        {"type": "image_url", "image_url": {"url": image_data} if isinstance(image_data, str) else {}},
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
    async def process(self, audio_data: Any) -> dict[str, Any]:
        # TODO: ASR 语音转文字未实现，当前 Whisper API 转录逻辑缺失
        # 需接入 openai.Audio.transcribe 或本地 Whisper 模型后替换下方占位返回
        try:
            import openai  # noqa: F401
            logger.info("ASR: openai 库已安装，但转录逻辑未实现")
        except ImportError:
            logger.debug("ASR: openai 库未安装")
        return {"text": "[语音消息，暂无法转文字]", "modality": "voice", "confidence": 0.0}


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
