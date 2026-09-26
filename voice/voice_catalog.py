"""音色 catalog — 克隆/设计 voice_id 的持久化 owner（W7，2026-09-27）

旧链缺陷：clone/design 获得的 voice_id 只写 provider 实例字段（进程内），
浏览器刷新/换 worker/重启即找不到；provider 全局 ``_voice_id`` 又被当成
多用户权威。根治：voice_id 落 ``data/voice_catalog.json``，可检索、可绑定、
可删除；provider 全局态仅保留「显式 switch-voice」一个入口作引擎默认音色。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from utils.project_paths import project_path, resolve_project_path

logger = logging.getLogger("voice.voice_catalog")

_DEFAULT_PATH = project_path("data", "voice_catalog.json")

# MiMo 预设音色（静态，与 api/routers/voice_routes._MIMO_VOICES 同源语义）
_PRESET_VOICES: list[dict[str, str]] = [
    {"name": "female-tianmei", "description": "甜美女声"},
    {"name": "female-qingxin", "description": "清新女声"},
    {"name": "male-chenwen", "description": "沉稳男声"},
]

_singleton: VoiceCatalog | None  # noqa: E305 — 前向声明（定义见下）
_singleton = None
_singleton_lock = threading.Lock()


def preset_names() -> list[str]:
    return [v["name"] for v in _PRESET_VOICES]


def presets() -> list[dict[str, str]]:
    return [dict(v) for v in _PRESET_VOICES]


class VoiceCatalog:
    """自定义音色登记簿（clone/design 产物的唯一持久化 owner）。"""

    def __init__(self, config_path: str | None = None):
        # 锚定项目根：从非仓库根 CWD 启动时相对路径会读写到错误位置
        self._config_path = resolve_project_path(config_path) if config_path else _DEFAULT_PATH
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    # ── 持久化 ──

    def _load(self) -> None:
        if not self._config_path.exists():
            return
        try:
            import json

            with open(self._config_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._entries = data
                logger.info("音色 catalog 已加载: %d 个音色", len(self._entries))
        except Exception as e:  # noqa: BLE001
            logger.error("加载音色 catalog 失败: %s", e)
            self._entries = {}

    def _save(self) -> None:
        import json

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)

    # ── CRUD ──

    def register(
        self,
        voice_id: str,
        name: str,
        kind: str,
        model: str,
        description: str = "",
        gender: str = "",
        age_group: str = "",
    ) -> dict[str, Any]:
        if not voice_id:
            raise ValueError("voice_id 不能为空")
        entry: dict[str, Any] = {
            "voice_id": voice_id,
            "name": name,
            "kind": kind,  # clone | design
            "model": model,
            "description": description,
        }
        if gender:
            entry["gender"] = gender
        if age_group:
            entry["age_group"] = age_group
        with self._lock:
            self._entries[voice_id] = entry
            self._save()
        logger.info("音色已登记: %s (%s, %s)", voice_id, name, kind)
        return dict(entry)

    def get(self, voice_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(voice_id)
        return dict(entry) if entry else None

    def remove(self, voice_id: str) -> bool:
        with self._lock:
            if voice_id not in self._entries:
                return False
            del self._entries[voice_id]
            self._save()
        return True

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._entries.values()]

    def is_known(self, voice_id: str) -> bool:
        """catalog 或静态预设中存在该音色。"""
        return bool(voice_id) and (
            voice_id in preset_names() or self.get(voice_id) is not None
        )


def get_voice_catalog() -> VoiceCatalog:
    """进程级单例（多 worker 各自持有一份，写时整文件落盘，键少无竞争热点）。"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = VoiceCatalog()
    return _singleton
