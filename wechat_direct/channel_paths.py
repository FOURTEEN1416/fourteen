"""每人独立微信通道的磁盘路径 — 禁止再使用全局单例路径作为用户通道真源。"""

from __future__ import annotations

import os
from pathlib import Path

from utils.project_paths import PROJECT_ROOT

DEFAULT_MAX_CHANNELS = 100
# 2026-10-12 D16 补裁（用户原话「每一个用户只有一个BOT」）：一人一 bot 一条
# 通道，控制台自由切换当前聊天角色。取代 09-19 旧裁决「一人两条」。
MAX_CHANNELS_PER_USER = 1


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


def scanner_ids_for_user(user_id: int) -> list[str]:
    """该用户各通道的扫码人微信 id（凭证 ilink_user_id / user_id 字段）。

    D16（2026-10-12）：谁扫码谁聊——扫码人即该注册账号在微信侧的唯一化身。
    网页激活角色时据此把角色同步到扫码人绑定（wechat_bindings + 内存缓存），
    使「控制台选角色 → 微信聊天生效」不再依赖用户手动建立旧式绑定。
    """
    import json

    ids: list[str] = []
    for slot in list_user_slots_with_credentials(int(user_id)):
        try:
            data = json.loads(credentials_path(int(user_id), slot).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        uid = str(data.get("user_id") or "").strip()
        if uid and uid not in ids:
            ids.append(uid)
    return ids


def remove_user_sessions(user_id: int) -> bool:
    """账号生命周期（W9）：删除该账号全部 slot 的磁盘通道目录（凭证/状态/二维码）。

    调用前必须先经 connector_registry.purge_user 停掉在跑连接器，
    否则进程内对象可能按旧路径重建文件。
    """
    import shutil

    user_root = sessions_root() / str(int(user_id))
    if not user_root.exists():
        return False
    shutil.rmtree(user_root, ignore_errors=True)
    return True


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
