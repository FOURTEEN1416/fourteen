"""ConnectorRegistry — 按 (user_id, slot) 管理微信通道连接器实例。

彻底取代进程级全局 `_connector` 单例：
- 每个登录用户只看得见/操作得了自己的通道
- 一人最多 MAX_CHANNELS_PER_USER（2）条
- 全局并发上限 WECHAT_MAX_CHANNELS（默认 100）
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from wechat_direct import channel_paths

logger = logging.getLogger("wechat_direct.registry")

_registry: ConnectorRegistry | None = None
_registry_lock = threading.Lock()


class ChannelQuotaError(RuntimeError):
    """通道名额已满"""


class ChannelSlotError(RuntimeError):
    """该用户通道槽位已满或非法"""


class ConnectorRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connectors: dict[tuple[int, int], Any] = {}

    def _key(self, user_id: int, slot: int) -> tuple[int, int]:
        return (int(user_id), int(slot))

    def get(self, user_id: int, slot: int = 0):
        with self._lock:
            return self._connectors.get(self._key(user_id, slot))

    def list_for_user(self, user_id: int) -> list[Any]:
        uid = int(user_id)
        with self._lock:
            return [c for (u, _s), c in self._connectors.items() if u == uid]

    def all(self) -> list[tuple[int, int, Any]]:
        with self._lock:
            return [(u, s, c) for (u, s), c in self._connectors.items()]

    def online_count(self) -> int:
        with self._lock:
            return sum(1 for c in self._connectors.values() if getattr(c, "token", ""))

    def _pick_slot(self, user_id: int, prefer: int | None = None) -> int:
        uid = int(user_id)
        with self._lock:
            owned = {s for (u, s) in self._connectors if u == uid}
            if prefer is not None:
                if prefer in owned:
                    return prefer
                if prefer >= channel_paths.MAX_CHANNELS_PER_USER:
                    raise ChannelSlotError(
                        f"用户 {uid} 通道槽位非法 slot={prefer}（上限 {channel_paths.MAX_CHANNELS_PER_USER}）"
                    )
                return prefer
            for s in range(channel_paths.MAX_CHANNELS_PER_USER):
                if s not in owned:
                    return s
            raise ChannelSlotError(
                f"用户 {uid} 通道已满（一人最多 {channel_paths.MAX_CHANNELS_PER_USER} 条）"
            )

    def ensure(self, user_id: int, slot: int | None = None, user_manager=None, base_url: str | None = None):
        """懒创建连接器实例（不自动 login）。"""
        from wechat_direct.wechat_connector import WeChatConnector

        uid = int(user_id)
        with self._lock:
            if slot is None:
                # 已有实例则复用第一条，否则分配空槽
                existing = self.list_for_user(uid)
                if existing:
                    return existing[0]
            s = self._pick_slot(uid, slot)
            key = self._key(uid, s)
            conn = self._connectors.get(key)
            if conn is not None:
                return conn
            if self.online_count() >= channel_paths.max_channels():
                raise ChannelQuotaError(
                    f"在线微信通道已达上限 {channel_paths.max_channels()}，请联系管理员"
                )
            channel_paths.ensure_session_dir(uid, s)
            kwargs: dict[str, Any] = {
                "owner_user_id": uid,
                "slot": s,
                "session_dir": channel_paths.session_dir(uid, s),
                "credentials_path": str(channel_paths.credentials_path(uid, s)),
                "state_path": str(channel_paths.state_path(uid, s)),
                "qrcode_path": str(channel_paths.qrcode_path(uid, s)),
                "context_tokens_path": str(channel_paths.context_tokens_path(uid, s)),
            }
            if base_url:
                kwargs["base_url"] = base_url
            if user_manager is not None:
                conn = WeChatConnector(user_manager, **kwargs)
            else:
                conn = WeChatConnector(None, **kwargs)
            self._connectors[key] = conn
            logger.info("创建微信通道实例 user=%s slot=%s", uid, s)
            return conn

    def disconnect(self, user_id: int, slot: int = 0) -> bool:
        with self._lock:
            conn = self._connectors.get(self._key(user_id, slot))
        if conn is None:
            return False
        try:
            conn.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning("断开通道异常 user=%s slot=%s: %s", user_id, slot, e)
        with self._lock:
            self._connectors.pop(self._key(user_id, slot), None)
        return True

    def remove(self, user_id: int, slot: int = 0) -> None:
        with self._lock:
            self._connectors.pop(self._key(user_id, slot), None)

    def status_for_user(self, user_id: int) -> list[dict[str, Any]]:
        """返回该用户全部通道状态（永不回退到全局他人通道）。"""
        from wechat_direct import channel_paths as cp

        uid = int(user_id)
        out: list[dict[str, Any]] = []
        conns = {(getattr(c, "slot", 0)): c for c in self.list_for_user(uid)}
        slots = set(conns.keys()) | set(cp.list_user_slots_with_credentials(uid))
        if not slots:
            slots = {0}
        for slot in sorted(slots):
            conn = conns.get(slot)
            state = self._state_for(uid, slot, conn)
            out.append(state)
        return out

    def primary_status(self, user_id: int) -> dict[str, Any]:
        statuses = self.status_for_user(user_id)
        for st in statuses:
            if st.get("connected"):
                return st
        return statuses[0] if statuses else {
            "connected": False,
            "status": "idle",
            "bot_id": "",
            "owner_user_id": int(user_id),
            "slot": 0,
            "uptime_seconds": 0,
            "messages_today": 0,
            "reconnect_attempts": 0,
        }

    def _state_for(self, user_id: int, slot: int, conn: Any) -> dict[str, Any]:
        from wechat_direct.wechat_connector import load_session_state

        base = load_session_state(user_id, slot)
        if conn is not None and getattr(conn, "token", ""):
            try:
                live = conn.get_status()
                base.update(live)
            except Exception:  # noqa: BLE001
                pass
            base["connected"] = True
        else:
            base["connected"] = bool(base.get("connected")) and bool(base.get("bot_id"))
            # 无内存实例时，若状态文件写着 connected 但无 token，降为 disconnected
            # （避免“假在线”——历史全局 wechat_state.json 污染）。
            if conn is None:
                creds = channel_paths.credentials_path(user_id, slot)
                if not creds.exists():
                    base["connected"] = False
                    if base.get("status") == "connected":
                        base["status"] = "idle"
        base["owner_user_id"] = int(user_id)
        base["slot"] = int(slot)
        return base

    def start_login(self, user_id: int, slot: int | None = None, user_manager=None) -> dict[str, Any]:
        """为指定用户启动扫码登录（异步线程），返回初始状态。"""
        conn = self.ensure(user_id, slot=slot, user_manager=user_manager)

        def _run() -> None:
            try:
                conn.run()
            except Exception as e:  # noqa: BLE001
                logger.exception("用户 %s 通道登录失败: %s", user_id, e)

        t = threading.Thread(target=_run, daemon=True, name=f"wx-login-{user_id}")
        t.start()
        return {
            "status": "connecting",
            "owner_user_id": int(user_id),
            "slot": int(getattr(conn, "slot", 0)),
            "message": "已触发扫码，请使用你自己的微信扫码登录",
        }

    def restore_on_boot(self, user_manager=None) -> int:
        """仅恢复「磁盘上已有凭证」的通道；不读全局 ~/.weixin_cow_credentials.json。"""
        root = channel_paths.sessions_root()
        restored = 0
        if not root.exists():
            return 0
        for user_dir in root.iterdir():
            if not user_dir.is_dir() or not user_dir.name.isdigit():
                continue
            uid = int(user_dir.name)
            for slot in channel_paths.list_user_slots_with_credentials(uid):
                try:
                    conn = self.ensure(uid, slot=slot, user_manager=user_manager)
                    if getattr(conn, "token", ""):
                        continue
                    t = threading.Thread(
                        target=conn.run, daemon=True, name=f"wx-restore-{uid}-{slot}"
                    )
                    t.start()
                    restored += 1
                except ChannelQuotaError:
                    logger.warning("通道名额已满，跳过恢复 user=%s slot=%s", uid, slot)
                except Exception as e:  # noqa: BLE001
                    logger.warning("恢复通道失败 user=%s slot=%s: %s", uid, slot, e)
        if restored:
            logger.info("已恢复 %d 条用户微信通道", restored)
        return restored


def get_registry() -> ConnectorRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ConnectorRegistry()
    return _registry


def get_connector_for_user(user_id: int, slot: int = 0):
    return get_registry().get(user_id, slot)
