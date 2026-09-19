"""每人独立微信通道的磁盘路径 — 禁止再使用全局单例路径作为用户通道真源。"""

from __future__ import annotations

import os
from pathlib import Path

from utils.project_paths import PROJECT_ROOT

# 用户裁决 2026-09-19：通道并发上限默认 100
DEFAULT_MAX_CHANNELS = 100
MAX_CHANNELS_PER_USER = 2  # 用户裁决：一人两条


def sessions_root() -> Path:
    return PROJECT_ROOT / "data" / "wechat_sessions"


def session_dir(user_id: int, slot: int = 0) -> Path:
    return sessions_root() / str(int(user_id)) / f"slot{int(slot)}"


def credentials_path(user_id: int, slot: int = 0) -> Path:
    return session_dir(user_id, slot) / "credentials.json"


def state_path(user_id: int, slot: int = 0) -> Path:
    return session_dir(user_id, slot) / "state.json"


def qrcode_path(user_id: int, slot: int = 0) -> Path:
    return session_dir(user_id, slot) / "qrcode.json"


def context_tokens_path(user_id: int, slot: int = 0) -> Path:
    return session_dir(user_id, slot) / "context_tokens.json"


def lock_path(user_id: int, slot: int = 0) -> Path:
    return session_dir(user_id, slot) / "poll.lock"


def ensure_session_dir(user_id: int, slot: int = 0) -> Path:
    d = session_dir(user_id, slot)
    d.mkdir(parents=True, exist_ok=True)
    return d


def max_channels() -> int:
    raw = os.environ.get("WECHAT_MAX_CHANNELS", str(DEFAULT_MAX_CHANNELS)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_CHANNELS


def list_user_slots_with_credentials(user_id: int) -> list[int]:
    """列出该用户下已有凭证的 slot。"""
    root = sessions_root() / str(int(user_id))
    if not root.exists():
        return []
    slots: list[int] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith("slot"):
            try:
                slot = int(child.name.removeprefix("slot"))
            except ValueError:
                continue
            if (child / "credentials.json").exists():
                slots.append(slot)
    return slots


def count_sessions_with_credentials() -> int:
    root = sessions_root()
    if not root.exists():
        return 0
    n = 0
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        n += len(list_user_slots_with_credentials(int(user_dir.name)) if user_dir.name.isdigit() else [])
    return n
