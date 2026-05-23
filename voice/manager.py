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
from typing import Any, Dict, List, Optional

from .base import TTSProviderBase

logger = logging.getLogger("voice.manager")


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
        self._providers: Dict[str, TTSProviderBase] = {}
        self._current_engine: Optional[str] = None
        self._enabled: bool = False
        self._last_error: Optional[str] = None
        self._synthesize_count: int = 0

    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        从fusion配置初始化TTS引擎

        Args:
            config: fusion配置中的 voice 节
                    格式: {"enabled": true, "engine": "edge-tts",
                          "edge-tts": {"speaker_name": "..."},
                          "gpt-sovits": {...}, "bert-vits2": {...}}
        """
        if not config:
            self._enabled = False
            logger.info("TTS未配置，已禁用")
            return True

        self._enabled = config.get("enabled", False)
        if not self._enabled:
            return True

        # 创建各引擎实例（懒加载，Health check时不实际连接）

        edge_cfg = config.get("edge-tts", {})
        if edge_cfg:
            from .edge_tts_provider import EdgeTTSProvider
            self._providers["edge-tts"] = EdgeTTSProvider(
                speaker_name=edge_cfg.get("speaker_name", "zh-CN-XiaoxiaoNeural"),
                rate=edge_cfg.get("rate", "+0%"),
                volume=edge_cfg.get("volume", "+0%"),
                timeout=edge_cfg.get("timeout", 30.0),
            )

        sovits_cfg = config.get("gpt-sovits", {})
        if sovits_cfg:
            from .sovits_provider import GPTSoVITSProvider
            self._providers["gpt-sovits"] = GPTSoVITSProvider(
                url=sovits_cfg.get("url", "http://localhost:9880"),
                timeout=sovits_cfg.get("timeout", 60.0),
                text_language=sovits_cfg.get("text_language", "auto"),
            )

        bert_cfg = config.get("bert-vits2", {})
        if bert_cfg:
            from .bert_vits2_provider import BertVITS2Provider
            self._providers["bert-vits2"] = BertVITS2Provider(
                url=bert_cfg.get("url", "http://localhost:5000"),
                speaker_name=bert_cfg.get("speaker_name", "珊瑚宫心海[中]"),
                timeout=bert_cfg.get("timeout", 60.0),
                sdp_ratio=bert_cfg.get("sdp_ratio", 0.2),
                noise=bert_cfg.get("noise", 0.2),
                noisew=bert_cfg.get("noisew", 0.9),
                speed=bert_cfg.get("speed", 1.0),
            )

        # 设置当前引擎
        engine = config.get("engine", "edge-tts")
        if engine in self._providers:
            self._current_engine = engine
        elif self._providers:
            self._current_engine = list(self._providers.keys())[0]
        else:
            logger.warning("TTS已启用但未配置任何引擎")
            self._enabled = False
            return False

        logger.info("TTS初始化完成: engine=%s, providers=%s",
                     self._current_engine, list(self._providers.keys()))
        return True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def current_engine(self) -> Optional[str]:
        return self._current_engine

    @property
    def available_engines(self) -> List[str]:
        return list(self._providers.keys())

    async def synthesize(self, text: str, **kwargs) -> Optional[bytes]:
        """
        合成语音 - 使用当前引擎

        自动降级: 如果当前引擎失败，依次尝试其他引擎

        Args:
            text: 要合成的文本
            **kwargs: 传递给引擎的参数

        Returns:
            音频字节数据，全部失败返回None
        """
        if not self._enabled or not text:
            return None

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

    def get_engine(self, name: str) -> Optional[TTSProviderBase]:
        """获取指定的引擎实例"""
        return self._providers.get(name)

    def health_check(self) -> Dict[str, Any]:
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
