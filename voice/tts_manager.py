"""
TTS管理器 - 统一管理多TTS引擎

与AI-YinMei的tts_core.py等效，但采用异步设计：
  - 不使用singleton装饰器
  - 不使用threading.Lock
  - 引擎选择在运行时动态切换
  - 失败时自动降级到可用引擎
  - 集成十四的fusion配置系统
"""

from __future__ import annotations

import logging
from typing import Any

from .tts_provider_base import TTSProviderBase

logger = logging.getLogger("voice.tts_manager")


class TTSManager:
    """
    TTS管理器

    职责:
      - 管理多个TTS引擎实例
      - 根据配置选择当前引擎
      - 运行时切换引擎
      - 健康检查
      - 失败自动降级
    """

    def __init__(self):
        self._providers: dict[str, TTSProviderBase] = {}
        self._current_engine: str | None = None
        self._enabled: bool = False
        self._last_error: str | None = None
        self._synthesize_count: int = 0

    async def initialize(self, config: dict[str, Any] | None = None) -> bool:
        """
        从fusion配置初始化TTS引擎

        Args:
            config: fusion配置中的 voice 节
                    格式: {"enabled": true, "engine": "mimo-tts",
                          "mimo-tts": {"api_key": "...", "fallback_local": true}}
        """
        if not config:
            self._enabled = False
            logger.info("TTS未配置，已禁用")
            return True

        self._enabled = config.get("enabled", False)
        if not self._enabled:
            return True

        # 唯一引擎: MiMo TTS（2026-08-28 用户裁决 A：全语音域 MiMo-only，
        # 其余四引擎 Edge/SoVITS/CosyVoice/Bert-VITS2 已删除；
        # 云 API 失败时由 provider 内部 fallback_local 本地降级兜底）
        mimo_cfg = config.get("mimo-tts", {})
        if mimo_cfg and mimo_cfg.get("api_key"):
            from .mimo_tts_provider import MiMoTTSProvider
            self._providers["mimo-tts"] = MiMoTTSProvider(
                api_key=mimo_cfg.get("api_key", ""),
                model=mimo_cfg.get("model", "mimo-v2.5-tts"),
                voice_id=mimo_cfg.get("voice_id", ""),
                timeout=mimo_cfg.get("timeout", 30.0),
                fallback_local=mimo_cfg.get("fallback_local", True),
                base_url=mimo_cfg.get("base_url"),
            )
            logger.info("MiMo TTS已配置: model=%s", mimo_cfg.get("model", "mimo-v2.5-tts"))

        # 设置当前引擎
        engine = config.get("engine", "mimo-tts")
        if engine in self._providers:
            self._current_engine = engine
        elif self._providers:
            self._current_engine = list(self._providers.keys())[0]
        else:
            logger.warning("TTS已启用但未配置 MiMo TTS（缺 api_key）")
            self._enabled = False
            return False

        logger.info("TTS初始化完成: engine=%s, providers=%s",
                     self._current_engine, list(self._providers.keys()))
        return True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def current_engine(self) -> str | None:
        return self._current_engine

    @property
    def available_engines(self) -> list[str]:
        return list(self._providers.keys())

    async def synthesize(self, text: str, emotion: str = "", **kwargs) -> bytes | None:
        """
        合成语音 - 使用当前引擎（MiMo-only；云 API 失败由 provider 内部 fallback_local 兜底）

        Args:
            text: 要合成的文本
            emotion: 情感状态(如"开心"、"伤心")，由 MiMoTTSProvider 内部映射
            **kwargs: 传递给引擎的参数

        Returns:
            音频字节数据，全部失败返回None
        """
        if not self._enabled or not text:
            return None

        # 情感映射由 MiMoTTSProvider 内部处理（8 情感→emotion/speed/pitch），
        # shisi EmotionMapping 注入路径已随多引擎时代结束移除。
        # 注意: 无全局锁，支持并发合成
        # _current_engine/_last_error 的竞态只影响统计日志，不影响正确性
        # 优先使用当前引擎
        if self._current_engine and self._current_engine in self._providers:
            provider = self._providers[self._current_engine]
            result = await provider.synthesize(text, **kwargs)
            if result is not None:
                self._synthesize_count += 1
                self._last_error = None
                return result
            self._last_error = f"{self._current_engine} 失败"

        # 降级: 尝试其他引擎
        for name, provider in self._providers.items():
            if name == self._current_engine:
                continue
            logger.info("TTS降级: %s → %s", self._current_engine, name)
            result = await provider.synthesize(text, **kwargs)
            if result is not None:
                self._current_engine = name  # 自动切换
                self._synthesize_count += 1
                self._last_error = None
                return result

        logger.error("所有TTS引擎均失败")
        self._last_error = "所有引擎不可用"
        return None

    async def switch_engine(self, engine_name: str) -> bool:
        """运行时切换TTS引擎"""
        if engine_name not in self._providers:
            logger.warning("未知引擎: %s，可用: %s", engine_name, list(self._providers.keys()))
            return False
        self._current_engine = engine_name
        logger.info("TTS引擎切换到: %s", engine_name)
        return True

    def get_engine(self, name: str) -> TTSProviderBase | None:
        """获取指定的引擎实例"""
        return self._providers.get(name)

    def health_check(self) -> dict[str, Any]:
        providers_health = {}
        for name, provider in self._providers.items():
            providers_health[name] = provider.health_check()

        return {
            "enabled": self._enabled,
            "current_engine": self._current_engine,
            "available_engines": list(self._providers.keys()),
            "providers": providers_health,
            "synthesize_count": self._synthesize_count,
            "last_error": self._last_error,
        }
