"""
角色卡集成适配器 - 对接小暖现有PersonaEngine

将SillyTavern角色卡无缝接入小暖的人格系统:
  1. 角色卡 → PersonaEngine配置转换
  2. 角色卡加载器 (支持加载目录、单文件)
  3. 角色卡缓存
  4. health_check() 集成
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import CharacterCard
from .parser import CharacterCardParser
from .prompt_builder import PromptBuilder
from .validator import CharacterValidator

logger = logging.getLogger("character_card.integration")


class CharacterCardAdapter:
    """
    角色卡适配器 - 将角色卡系统挂接到小暖

    特性:
      - 懒加载：首次使用时才解析角色卡文件
      - 缓存：解析后的角色卡在内存中缓存
      - 自动检测：自动识别JSON/PNG格式
      - 验证：加载时自动验证完整性
      - 降级：加载失败时使用默认人格配置
    """

    def __init__(
        self,
        card_dir: Optional[str] = None,
        default_card_path: Optional[str] = None,
        enabled: bool = True,
        user_name: str = "用户",
    ):
        self._card_dir = Path(card_dir) if card_dir else None
        self._default_card_path = Path(default_card_path) if default_card_path else None
        self._enabled = enabled
        self._user_name = user_name

        self._card_cache: Optional[CharacterCard] = None
        self._card_name: Optional[str] = None
        self._lock = threading.Lock()
        self._load_time: Optional[float] = None
        self._last_error: Optional[str] = None

        # 目录列表缓存: {文件名: (mtime, 解析结果)}
        self._dir_cache: Dict[str, tuple] = {}
        self._dir_cache_mtime: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        if not value:
            self._clear_cache()

    def load_card(self, path: str) -> Tuple[bool, Optional[str]]:
        """
        从文件或路径加载角色卡

        Args:
            path: JSON/PNG文件路径

        Returns:
            (success, error_message)
        """
        try:
            p = Path(path)
            if not p.exists():
                return False, f"角色卡文件不存在: {path}"

            if p.suffix.lower() == '.png':
                card = CharacterCardParser.parse_png_file(str(p))
            else:
                card = CharacterCardParser.parse_json_file(str(p))

            valid, errors = CharacterValidator.validate(card)
            if not valid:
                logger.warning("角色卡验证有警告: %s", errors)
                # 不阻止加载，只是记录警告

            with self._lock:
                self._card_cache = card
                self._card_name = card.data.name or p.stem
                self._load_time = time.time()
                self._last_error = None

            logger.info("角色卡加载成功: %s (v%s)", self._card_name, card.spec_version)
            return True, None

        except Exception as e:
            error_msg = f"角色卡加载失败: {e}"
            logger.error(error_msg)
            with self._lock:
                self._last_error = error_msg
            return False, error_msg

    def load_default(self) -> bool:
        """加载默认角色卡"""
        if self._default_card_path and self._default_card_path.exists():
            return self.load_card(str(self._default_card_path))[0]
        return False

    def get_card(self) -> Optional[CharacterCard]:
        """获取当前缓存的角色卡"""
        with self._lock:
            return self._card_cache

    def get_card_name(self) -> str:
        """获取当前角色卡名称"""
        with self._lock:
            return self._card_name or "默认角色"

    def _invalidate_dir_cache_if_stale(self):
        """检查目录变更，失效过期缓存"""
        if not self._card_dir or not self._card_dir.exists():
            self._dir_cache = {}
            self._dir_cache_mtime = 0.0
            return

        # 获取目录本身的最新mtime
        try:
            current_mtime = self._card_dir.stat().st_mtime
        except OSError:
            current_mtime = 0.0

        if current_mtime != self._dir_cache_mtime:
            self._dir_cache = {}
            self._dir_cache_mtime = current_mtime

    def list_available_cards(self) -> List[Dict[str, Any]]:
        """
        列出角色卡目录中的可用角色卡

        使用目录修改时间做缓存失效:
          仅当目录mtime变化时才重新解析所有文件
        """
        if not self._card_dir or not self._card_dir.exists():
            return []

        self._invalidate_dir_cache_if_stale()

        # 如果缓存已满且未失效，直接返回缓存
        if self._dir_cache:
            return [v for _, v in sorted(
                [(k, v) for k, v in self._dir_cache.items()],
                key=lambda x: x[0],
            )]

        # 无缓存或已失效，重新扫描
        for f in sorted(self._card_dir.iterdir()):
            if f.suffix.lower() not in ('.json', '.png'):
                continue

            # 检查单个文件mtime
            try:
                file_mtime = f.stat().st_mtime
            except OSError:
                file_mtime = 0.0

            cache_key = f.name

            # 如果文件在缓存中且mtime未变，跳过解析
            if cache_key in self._dir_cache:
                cached_mtime, cached_entry = self._dir_cache[cache_key]
                if cached_mtime == file_mtime:
                    continue

            # 需要解析
            try:
                if f.suffix.lower() == '.png':
                    card = CharacterCardParser.parse_png_file(str(f))
                else:
                    card = CharacterCardParser.parse_json_file(str(f))
                valid, errors = CharacterValidator.validate(card)
                entry = {
                    "file": f.name,
                    "path": str(f),
                    "name": card.data.name or f.stem,
                    "description": card.data.description[:100] if card.data.description else "",
                    "version": card.spec_version,
                    "valid": valid,
                    "warnings": errors,
                }
            except Exception as e:
                entry = {
                    "file": f.name,
                    "path": str(f),
                    "name": f.stem,
                    "error": str(e),
                }

            self._dir_cache[cache_key] = (file_mtime, entry)

        return [v for _, v in sorted(
            [(k, v) for k, v in self._dir_cache.items()],
            key=lambda x: x[0],
        )]

    def to_persona_config(self) -> Dict[str, Any]:
        """
        转换为PersonaEngine兼容配置

        这是核心集成点，输出可被my_character/persona_engine.py消费
        """
        card = self.get_card()
        if not card or not self._enabled:
            return {}

        return card.to_persona_config()

    def build_system_prompt(self, existing_prompt: str = "", mode: str = "merge") -> str:
        """
        构建系统提示词

        Args:
            existing_prompt: PersonaEngine已有的提示词
            mode: 'merge' - 合并, 'replace' - 替换, 'append' - 追加

        Returns:
            最终的提示词
        """
        card = self.get_card()
        if not card or not self._enabled:
            return existing_prompt

        builder = PromptBuilder(
            char_name=card.data.name or "Character",
            user_name=self._user_name,
        )

        if existing_prompt:
            return builder.merge_with_persona_prompt(card, existing_prompt, mode)
        else:
            return builder.build_system_prompt(card)

    def health_check(self) -> Dict[str, Any]:
        """健康检查 - 符合小暖可观测性规范"""
        card = self.get_card()
        return {
            "enabled": self._enabled,
            "card_loaded": card is not None,
            "card_name": self._card_name or "",
            "load_time": self._load_time,
            "last_error": self._last_error,
            "available_cards": len(self.list_available_cards()),
        }

    def _clear_cache(self):
        with self._lock:
            self._card_cache = None
            self._card_name = None
            self._load_time = None

    def __repr__(self) -> str:
        status = "enabled" if self._enabled else "disabled"
        name = self._card_name or "no card"
        return f"<CharacterCardAdapter {status}: {name}>"
