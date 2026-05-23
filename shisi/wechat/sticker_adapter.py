"""微信表情包消息适配 — AI回复自动附带表情+指令触发。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..sticker.sticker_manager import StickerManager

logger = logging.getLogger("shisi.wechat.sticker_adapter")


class WeChatStickerAdapter:
    def __init__(self, sticker_manager: StickerManager | None = None):
        self._sticker_mgr = sticker_manager

    def get_sticker_for_reply(self, emotion: str, character_id: str = "") -> Optional[dict[str, Any]]:
        if not self._sticker_mgr:
            return None
        emotion_map = {
            "开心": ["开心", "可爱", "撒娇"],
            "伤心": ["伤心", "委屈"],
            "生气": ["生气", "傲娇"],
            "撒娇": ["撒娇", "可爱", "开心"],
            "害怕": ["害怕", "紧张"],
            "害羞": ["害羞", "可爱"],
            "默认": ["可爱"],
        }
        tags = emotion_map.get(emotion, emotion_map["默认"])
        stickers = self._sticker_mgr.recommend(tags, limit=1)
        return stickers[0] if stickers else None

    def format_sticker_message(self, text: str, sticker: dict[str, Any] | None) -> dict[str, Any]:
        msg: dict[str, Any] = {"type": "text", "content": text}
        if sticker:
            msg["sticker"] = {
                "id": sticker.get("sticker_id", ""),
                "path": sticker.get("file_path", ""),
                "category": sticker.get("category", ""),
            }
        return msg
