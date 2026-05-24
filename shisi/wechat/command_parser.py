"""微信指令解析器 — 解析中文指令为结构化Command对象。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import get_config

logger = logging.getLogger("shisi.wechat.command_parser")


@dataclass
class Command:
    action: str
    params: dict[str, str] = field(default_factory=dict)
    raw: str = ""


class WeChatCommandParser:
    def __init__(self):
        self._prefix = get_config("wechat", "command_prefix", "")
        cmd_config = get_config("wechat", "commands", {})
        self._commands = cmd_config or {
            "switch_character": ["切换角色", "切换"],
            "affinity": ["好感度", "好感"],
            "emotion_status": ["情感状态", "情感"],
            "vital_signs": ["生理指标", "生理"],
            "send_sticker": ["发表情", "表情"],
            "favorite": ["收藏"],
            "forward": ["转发给", "转发"],
        }

    def parse(self, message: str) -> Command | None:
        text = message.strip()
        if self._prefix and text.startswith(self._prefix):
            text = text[len(self._prefix):].strip()

        if not text:
            return None

        for action, keywords in self._commands.items():
            for kw in keywords:
                if text.startswith(kw):
                    param = text[len(kw):].strip()
                    params = self._extract_params(action, param)
                    return Command(action=action, params=params, raw=message)

        return None

    def _extract_params(self, action: str, param: str) -> dict[str, str]:
        if action == "switch_character" and param:
            cleaned = param.lstrip("：: ").strip()
            return {"character_name": cleaned}
        if action == "forward" and param:
            cleaned = param.lstrip("：: ").strip()
            return {"target_character": cleaned}
        return {}
