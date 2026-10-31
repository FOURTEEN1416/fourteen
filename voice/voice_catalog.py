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

# MiMo 预置音色（2026-10-30 对齐官方 v2.5 音色表；
# 旧三档 female-tianmei/female-qingxin/male-chenwen 在 provider 侧保留别名映射）
_PRESET_VOICES: list[dict[str, str]] = [
    {"name": "mimo_default", "description": "MiMo 默认音色"},
    {"name": "冰糖", "description": "清甜女声"},
    {"name": "茉莉", "description": "清新女声"},
    {"name": "苏打", "description": "元气女声"},
    {"name": "白桦", "description": "沉稳男声"},
    {"name": "Mia", "description": "英文女声 Mia"},
    {"name": "Chloe", "description": "英文女声 Chloe"},
    {"name": "Milo", "description": "英文男声 Milo"},
    {"name": "Dean", "description": "英文男声 Dean"},
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
        owner: str = "",
        sample_path: str = "",
    ) -> dict[str, Any]:
        """登记音色。

        ``owner`` 为调用者主体（user_id 字符串）；空串 = 平台共享（P0 安全批 F2）。
        存量 JSON 条目无 owner 键时同样按平台共享解释，向后兼容。
        """
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
        if owner:
            entry["owner"] = owner
        if sample_path:
            # voiceclone 参考音频（相对项目根）；合成时 provider 还原为 data URL
            entry["sample_path"] = sample_path
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


# ── 音色归属判定（唯一 owner）────────────────────────────────────────────
#
# 归属位就是这个 catalog 条目的 ``owner`` 字段（克隆/设计登记时写入，见
# ``api/routers/mimo_voice_routes.py`` 的 ``_register_catalog`` 调用）。
# 规则**只在这里定义一次**：合成入口（``/api/mimo/synthesize``）与角色绑定入口
# （``/api/characters/{id}/voice`` POST/PUT）必须调用本函数，不得各写一份内联判断
# —— 两处口径一旦漂移，就会重新出现「绑得进去、合成不了」或反向的漏洞。
#
# 语义：``owner`` 为空串 = 平台共享（存量条目无该键，向后兼容），不受限；
# 静态预设不是 catalog 条目，天然不受限；admin 例外；无主体（机器面）不干预。

#: 归属拒绝的统一文案（两处强制点同文案，测试按常量断言防漂移）
VOICE_OWNER_DENIED = "无权使用他人克隆音色"


def denied_voice_owner(
    voice_id: str,
    *,
    user_id: str,
    is_admin: bool = False,
    catalog: VoiceCatalog | None = None,
) -> bool:
    """该 ``voice_id`` 是否属于**他人**的克隆音色（是 → 调用方应拒绝）。

    Args:
        voice_id: 待判音色；空串直接放行（未指定音色 = 用引擎默认）。
        user_id: 服务端从认证主体派生的用户键（客户端自报值不得传入）。
        is_admin: admin 例外（可管理全量音色资产）。
        catalog: 注入点（测试用）；缺省走 ``get_voice_catalog()`` 单例。

    Returns:
        True 表示 owner 非空且不等于 ``user_id``（他人资产）。
    """
    if not voice_id or is_admin:
        return False
    cat = catalog if catalog is not None else get_voice_catalog()
    entry = cat.get(voice_id)
    owner = str((entry or {}).get("owner") or "")
    return bool(owner) and owner != str(user_id)

