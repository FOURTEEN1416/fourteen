from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger("session_manager")


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, Dict] = {}

    def create_session(self, user_id: str = "default", channel: str = "web") -> str:
        session_id = f"{user_id}:{channel}:{uuid.uuid4().hex[:8]}"
        self._sessions[session_id] = {
            "user_id": user_id,
            "channel": channel,
            "created_at": __import__("time").time(),
            "is_active": True,
        }
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict]:
        return self._sessions.get(session_id)

    def end_session(self, session_id: str):
        if session_id in self._sessions:
            self._sessions[session_id]["is_active"] = False

    def get_active_sessions(self, user_id: str = "") -> List[str]:
        result = []
        for sid, info in self._sessions.items():
            if info.get("is_active"):
                if not user_id or info.get("user_id") == user_id:
                    result.append(sid)
        return result

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.get("is_active"))
