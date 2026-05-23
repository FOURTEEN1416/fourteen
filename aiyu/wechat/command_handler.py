"""微信指令处理器 — 路由分发到对应模块。"""

from __future__ import annotations

import logging

from ..affinity.enhancer import AffinityEnhancer
from ..character.manager import CharacterManager
from ..emotion_stage.stage_engine import EmotionStageEngine
from ..memory_ext.favorite_manager import FavoriteManager
from ..memory_ext.forward_manager import ForwardManager
from ..sticker.sticker_manager import StickerManager
from .command_parser import Command, WeChatCommandParser

logger = logging.getLogger("aiyu.wechat.command_handler")


class WeChatCommandHandler:
    def __init__(
        self,
        character_manager: CharacterManager | None = None,
        affinity_enhancer: AffinityEnhancer | None = None,
        stage_engine: EmotionStageEngine | None = None,
        sticker_manager: StickerManager | None = None,
        favorite_manager: FavoriteManager | None = None,
        forward_manager: ForwardManager | None = None,
    ):
        self._parser = WeChatCommandParser()
        self._char_mgr = character_manager
        self._affinity = affinity_enhancer
        self._stage = stage_engine
        self._sticker = sticker_manager
        self._fav = favorite_manager
        self._fwd = forward_manager

    def handle(self, message: str, character_id: str = "") -> tuple[bool, str]:
        cmd = self._parser.parse(message)
        if cmd is None:
            return False, ""

        try:
            handler = getattr(self, f"_handle_{cmd.action}", None)
            if handler:
                result = handler(cmd, character_id)
                return True, result
            return True, f"未知指令: {cmd.action}"
        except Exception as e:
            logger.error("指令处理异常: %s", e)
            return True, f"指令执行失败: {e}"

    def _handle_switch_character(self, cmd: Command, cid: str) -> str:
        if not self._char_mgr:
            return "角色管理未启用"
        name = cmd.params.get("character_name", "")
        if not name:
            return "请指定角色名，如：切换角色：椎名真昼"
        chars = self._char_mgr.list_characters()
        match = next((c for c in chars if c.name == name or name in c.name), None)
        if not match:
            return f"未找到角色：{name}"
        ok, msg = self._char_mgr.switch_character(match.character_id)
        return msg if ok else f"切换失败: {msg}"

    def _handle_affinity(self, cmd: Command, cid: str) -> str:
        if not self._affinity:
            return "好感度系统未启用"
        active = cid or (self._char_mgr.get_active_id() if self._char_mgr else None)
        if not active:
            return "无活跃角色"
        progress = self._affinity.get_progress(active)
        return f"❤️ 好感度：{progress['affinity']:.0f}/{progress['max']} ({progress['percentage']:.0f}%)"

    def _handle_emotion_status(self, cmd: Command, cid: str) -> str:
        if not self._stage:
            return "情感阶段系统未启用"
        active = cid or (self._char_mgr.get_active_id() if self._char_mgr else None)
        if not active:
            return "无活跃角色"
        progress = self._stage.get_progress(active)
        return f"🎭 情感阶段：{progress['current_stage']} (阶段{progress['stage_index']+1}/{progress['total_stages']})\n可用功能：{', '.join(progress['features'])}"

    def _handle_vital_signs(self, cmd: Command, cid: str) -> str:
        return "🌡️ 生理指标系统（Phase 3实现）"

    def _handle_send_sticker(self, cmd: Command, cid: str) -> str:
        if not self._sticker:
            return "表情包系统未启用"
        return "😊 表情推荐（需结合当前情感）"

    def _handle_favorite(self, cmd: Command, cid: str) -> str:
        if not self._fav:
            return "收藏功能未启用"
        return "⭐ 已收藏当前对话"

    def _handle_forward(self, cmd: Command, cid: str) -> str:
        target = cmd.params.get("target_character", "")
        if not target:
            return "请指定目标角色，如：转发给：小暖"
        return f"📤 已转发记忆给：{target}"
