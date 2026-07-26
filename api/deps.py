"""
API 全局依赖中心

所有 main_routes 需要的共享状态存于此模块，避免路由定义在函数闭包内。
"""

from __future__ import annotations

import threading
from typing import Any

from api.state import SafetyLogManager, ToolHistoryManager, TrainingStateManager


class _APIDeps:
    """共享依赖容器 — 由 app_factory.py 在启动时注入"""

    def __init__(self):
        # 核心管理器
        self.orch: Any = None
        self.health: Any = None
        self.config: Any = None
        self.sessions: Any = None
        self.gf: Any = None

        # 状态管理器
        self.training_mgr = TrainingStateManager()
        self.tool_history_mgr = ToolHistoryManager(maxlen=1000)
        self.safety_log_mgr = SafetyLogManager(maxlen=2000)

        # 微信状态缓存
        self.wechat_status_cache: dict = {"data": None, "ts": 0.0}
        self.WECHAT_STATUS_TTL = 5.0

        # 延迟初始化的管理器
        self._clone_mgr = None
        self._clone_mgr_lock = threading.Lock()

        # shisi 注册表（由子路由挂载时使用）
        self.shisi_reg: Any = None

        # 路由挂载状态（readiness 使用，防止关键 API 静默 404）
        self.route_mounts: dict[str, bool] = {}

        # 延迟创建的角色音色管理器
        self._character_voice_mgr: Any = None
        self._character_voice_mgr_lock = threading.Lock()

    def set_deps(self, orch=None, health=None, config=None, sessions=None, gf=None):
        self.orch = orch
        self.health = health
        self.config = config
        self.sessions = sessions
        self.gf = gf

    def get_clone_mgr(self):
        if self._clone_mgr is None:
            with self._clone_mgr_lock:
                if self._clone_mgr is None:
                    from voice.clone_data_manager import CloneDataManager
                    self._clone_mgr = CloneDataManager()
        return self._clone_mgr

    def get_rag(self):
        if self.orch and hasattr(self.orch, 'components'):
            return self.orch.components.get("rag")
        return None

    def get_tts(self):
        if self.orch and hasattr(self.orch, 'components'):
            return self.orch.components.get("voice")
        return None

    def get_tts_manager(self):
        tts = self.get_tts()
        if tts and hasattr(tts, 'health_check'):
            return tts
        return None

    def get_character_voice_manager(self):
        if self._character_voice_mgr is None:
            with self._character_voice_mgr_lock:
                if self._character_voice_mgr is None:
                    from shisi.voice.character_voice import CharacterVoiceManager
                    self._character_voice_mgr = CharacterVoiceManager()
        return self._character_voice_mgr

    def get_safety(self):
        if self.orch:
            if hasattr(self.orch, 'components'):
                return self.orch.components.get("safety")
            return getattr(self.orch, '_safety', None)
        return None

    def get_wechat_connector(self):
        from wechat_direct import get_connector
        return get_connector()


# 模块级单例
deps = _APIDeps()


# 模块级快捷函数 — 用于 FastAPI Depends()
def get_tts_manager():
    """FastAPI 依赖项：获取 TTSManager 实例"""
    return deps.get_tts_manager()
