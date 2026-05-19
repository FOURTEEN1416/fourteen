from __future__ import annotations

import asyncio
import logging
import signal
import threading
from typing import Callable, List

logger = logging.getLogger("graceful_shutdown")


class GracefulShutdown:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._shutting_down = False
        self._cleanup_fns: List[Callable] = []
        self._event = threading.Event()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def on_cleanup(self, fn: Callable):
        self._cleanup_fns.append(fn)

    def setup_signal_handlers(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle_signal)
            except OSError:
                pass

    def _handle_signal(self, signum, frame):
        if self._shutting_down:
            return
        logger.info("Received signal %d, initiating graceful shutdown", signum)
        self._shutting_down = True
        threading.Thread(target=self._do_shutdown, daemon=True).start()

    def _do_shutdown(self):
        logger.info("Rejecting new messages, completing current processing...")
        self._event.wait(timeout=self.timeout)
        logger.info("Running cleanup tasks...")
        for fn in self._cleanup_fns:
            try:
                fn()
            except Exception as e:
                logger.warning("Cleanup error: %s", e)
        logger.info("Graceful shutdown complete")

    def allow_current_processing(self):
        self._event.set()

    def wait_for_completion(self, timeout: Optional[float] = None):
        self._event.wait(timeout=timeout or self.timeout)


graceful_shutdown = GracefulShutdown()
