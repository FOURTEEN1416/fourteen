"""角色专属音色管理"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("shisi.voice_ext.character_voice")

_DEFAULT_VOICE_DIR = Path("data/voice_samples")


class CharacterVoiceManager:
    """
    管理角色与TTS音色的绑定关系

    功能:
    - 每个角色可独立配置TTS引擎+speaker+参数
    - 支持热更新（运行时修改无需重启）
    - 配置持久化到JSON文件
    - 自动降级: 角色配置引擎不可用时回退到默认
    """

    def __init__(self, config_path: str = "data/character_voices.json"):
        self._config_path = Path(config_path)
        self._bindings: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._bindings = json.load(f)
                logger.info("角色音色配置已加载: %d 个角色", len(self._bindings))
            except Exception as e:
                logger.error("加载角色音色配置失败: %s", e)
                self._bindings = {}

    def _save(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._bindings, f, ensure_ascii=False, indent=2)

    def bind_voice(self, character_id: str, engine: str, speaker_name: str = "", **kwargs: Any) -> None:
        """绑定角色音色"""
        self._bindings[character_id] = {
            "engine": engine,
            "speaker_name": speaker_name,
            **kwargs,
        }
        self._save()
        logger.info("角色音色绑定: %s → %s (%s)", character_id, engine, speaker_name)

    def unbind_voice(self, character_id: str) -> bool:
        """解除角色音色绑定"""
        if character_id in self._bindings:
            del self._bindings[character_id]
            self._save()
            logger.info("角色音色解绑: %s", character_id)
            return True
        return False

    def get_voice_config(self, character_id: str) -> Optional[dict[str, Any]]:
        """获取角色音色配置"""
        return self._bindings.get(character_id)

    def list_bindings(self) -> dict[str, dict[str, Any]]:
        """列出所有角色音色绑定"""
        return self._bindings.copy()

    async def setup_character_voice(self, character_id: str, tts_manager: Any) -> bool:
        """
        运行时设置角色音色到TTSManager

        Args:
            character_id: 角色ID
            tts_manager: TTSManager实例

        Returns:
            True if successful
        """
        config = self.get_voice_config(character_id)
        if not config:
            logger.debug("角色 %s 无专属音色配置，使用默认", character_id)
            return False

        engine = config.get("engine", "edge-tts")
        if engine not in (tts_manager.available_engines or []):
            logger.warning("角色 %s 配置引擎 %s 不可用，回退默认", character_id, engine)
            return False

        success = await tts_manager.switch_engine(engine)
        if success:
            logger.info("角色 %s 音色已切换到 %s", character_id, engine)
        return success
