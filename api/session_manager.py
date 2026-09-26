from __future__ import annotations

import logging
import threading
import time
import uuid

logger = logging.getLogger("session_manager")


def resolve_owned_session(session_id: str, user_id: int) -> str:
    """浏览器会话边界：归属只取认证身份，已有本人的键保持原样。"""
    from utils.session_key import owner_of

    raw = str(session_id or "").strip()
    if len(raw) > 128 or any(ord(c) < 32 for c in raw):
        raise ValueError("Invalid session_id")
    owner = owner_of(raw)
    if owner is not None:
        if owner != user_id:
            raise PermissionError("Session does not belong to the authenticated user")
        return raw
    # 无 owner 的旧客户端标识只作为本人的局部名称，绝不直接查同名历史。
    result = f"{user_id}:web:{raw or uuid.uuid4().hex[:8]}"
    if len(result) > 128:
        raise ValueError("Invalid session_id")
    return result


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create_session(self, user_id: str = "default", channel: str = "web") -> str:
        session_id = f"{user_id}:{channel}:{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._sessions[session_id] = {
                "user_id": user_id,
                "channel": channel,
                "created_at": time.time(),
                "is_active": True,
            }
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            return self._sessions.get(session_id)

    def end_session(self, session_id: str):
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["is_active"] = False

    def get_active_sessions(self, user_id: str = "") -> list[str]:
        result = []
        with self._lock:
            for sid, info in self._sessions.items():
                if info.get("is_active") and (not user_id or info.get("user_id") == user_id):
                    result.append(sid)
        return result

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.get("is_active"))
