"""
WeChat Heartbeat Monitor — 微信连接心跳监控 + 自动重连

Monitors CowAgent WeChat channel health, reports connection status,
and attempts automatic reconnection on failure.
"""

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("wechat.heartbeat")


class WeChatHeartbeat:
    """
    WeChat connection heartbeat monitor.

    Periodically checks if CowAgent's WeChat channel is alive,
    notifies listeners on state changes, and auto-reconnects.
    """

    def __init__(self, check_interval: int = 30, max_missed: int = 3):
        self._connected = False
        self._last_pong = time.time()
        self._missed = 0
        self._max_missed = max_missed
        self._check_interval = check_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._listeners: list[Callable[[bool], None]] = []
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5

    def on_status_change(self, callback: Callable[[bool], None]):
        """Register callback for connection status changes (connected=True/False)"""
        self._listeners.append(callback)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def uptime_seconds(self) -> float:
        if self._connected:
            return time.time() - self._last_pong
        return 0.0

    @property
    def reconnect_attempts(self) -> int:
        return self._reconnect_attempts

    def start(self, cowagent_instance: Any = None):
        """Start heartbeat monitoring in a daemon thread"""
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            args=(cowagent_instance,),
            daemon=True,
            name="wechat-heartbeat",
        )
        self._thread.start()
        logger.info("心跳监控已启动 (interval=%ds, max_missed=%d)",
                     self._check_interval, self._max_missed)

    def stop(self):
        """Stop heartbeat monitoring"""
        self._running = False
        logger.info("心跳监控已停止")

    def _loop(self, cowagent_instance: Any):
        """Main heartbeat loop"""
        while self._running:
            time.sleep(self._check_interval)
            try:
                alive = self._check_alive(cowagent_instance)
                if alive:
                    self._missed = 0
                    self._last_pong = time.time()
                    if not self._connected:
                        self._connected = True
                        self._reconnect_attempts = 0
                        self._notify(True)
                        logger.info("✅ 微信连接已恢复")
                else:
                    self._missed += 1
                    logger.warning("⚠️ 心跳丢失 (%d/%d)", self._missed, self._max_missed)
                    if self._missed >= self._max_missed:
                        if self._connected:
                            self._connected = False
                            self._notify(False)
                            logger.error("❌ 微信连接已断开")
                        self._try_reconnect(cowagent_instance)
            except Exception as e:
                logger.error("心跳检测异常: %s", e)

    def _check_alive(self, cowagent_instance: Any) -> bool:
        """Check if CowAgent WeChat channel is alive.
        If cowagent_instance is None, returns True (can't check).
        Otherwise checks the WeChat channel's thread/running status."""
        if cowagent_instance is None:
            return True  # Can't check
        # Try to check the WeChat channel's running status
        try:
            if hasattr(cowagent_instance, "channels"):
                channels = cowagent_instance.channels
                for ch in channels:
                    if "weixin" in str(type(ch).__name__).lower():
                        # Check if channel thread is alive
                        if hasattr(ch, "running") and ch.running:
                            return True
            # Fallback: check if the main app thread is alive
            if hasattr(cowagent_instance, "_thread") and cowagent_instance._thread:
                return cowagent_instance._thread.is_alive()
            return True  # Optimistic default
        except Exception:
            return True

    def _try_reconnect(self, cowagent_instance: Any):
        """Attempt to reconnect WeChat channel"""
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            logger.error("已达最大重连次数 (%d)，停止自动重连", self._max_reconnect_attempts)
            return

        self._reconnect_attempts += 1
        logger.info("正在重连微信... (第%d次)", self._reconnect_attempts)

        try:
            if cowagent_instance and hasattr(cowagent_instance, "run"):
                # Reinitialize the WeChat channel
                cowagent_instance.run()
                self._connected = True
                self._missed = 0
                self._notify(True)
                logger.info("✅ 微信重连成功")
        except Exception as e:
            logger.error("重连失败: %s", e)
            # Will retry on next cycle

    def _notify(self, connected: bool):
        """Notify all registered listeners"""
        for cb in self._listeners:
            try:
                cb(connected)
            except Exception as e:
                logger.warning("心跳通知回调异常: %s", e)

    def get_status(self) -> dict:
        """Get current heartbeat status as dict (for API endpoint)"""
        return {
            "connected": self._connected,
            "uptime_seconds": self.uptime_seconds,
            "last_pong": self._last_pong,
            "reconnect_attempts": self._reconnect_attempts,
            "missed_heartbeats": self._missed,
            "max_missed": self._max_missed,
        }
