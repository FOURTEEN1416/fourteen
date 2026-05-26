"""
优雅关闭管理器

支持信号处理和活跃请求追踪，确保在关闭时等待正在处理的任务完成。
"""

from __future__ import annotations

import contextlib
import logging
import signal
import threading
from collections.abc import Callable

logger = logging.getLogger("graceful_shutdown")


class GracefulShutdown:
    """优雅关闭管理器

    用法:
        gs = GracefulShutdown()
        gs.setup_signal_handlers()
        gs.on_cleanup(my_cleanup_fn)

        在处理请求前: gs.increment_active()
        在处理完成后: gs.decrement_active()

        当收到 SIGTERM/SIGINT 时:
        - 设置 shutting_down 标志（新请求应拒绝）
        - 等待所有活跃任务完成或超时
        - 执行所有 cleanup 函数
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._shutting_down = False
        self._cleanup_fns: list[Callable] = []
        self._event = threading.Event()
        self._active_count = 0
        self._count_lock = threading.Lock()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def increment_active(self) -> None:
        """标记一个新活跃请求开始"""
        with self._count_lock:
            self._active_count += 1

    def decrement_active(self) -> None:
        """标记一个活跃请求完成"""
        with self._count_lock:
            self._active_count = max(0, self._active_count - 1)
            if self._active_count == 0 and self._shutting_down:
                self._event.set()

    @property
    def active_count(self) -> int:
        with self._count_lock:
            return self._active_count

    def on_cleanup(self, fn: Callable) -> None:
        self._cleanup_fns.append(fn)

    def setup_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, self._handle_signal)

    def _handle_signal(self, signum: int, _frame) -> None:
        if self._shutting_down:
            return
        logger.info("收到信号 %d，开始优雅关闭", signum)
        self._shutting_down = True
        threading.Thread(target=self._do_shutdown, daemon=False).start()

    def _do_shutdown(self) -> None:
        # 等待活跃请求完成（最多 timeout 秒）
        active = self.active_count
        if active > 0:
            logger.info("正在等待 %d 个活跃请求完成（超时 %.1fs）...", active, self.timeout)
            self._event.wait(timeout=self.timeout)

        logger.info("运行清理任务...")
        for fn in self._cleanup_fns:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                logger.warning("清理任务异常: %s", e)
        logger.info("优雅关闭完成")

    def wait_for_completion(self, timeout: float | None = None) -> None:
        self._event.wait(timeout=timeout or self.timeout)


graceful_shutdown = GracefulShutdown()
