from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

logger = logging.getLogger("training_state")


class TrainingStateManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "status": "idle",
            "progress": 0.0,
            "current_step": 0,
            "total_steps": 0,
            "loss": None,
            "extracted_turns": 0,
            "cleaned_turns": 0,
            "error": None,
            "start_time": None,
            "eta_seconds": None,
            "step_name": None,
        }
        self._executor: ThreadPoolExecutor | None = None
        self._current_future: Future | None = None
        self._stop_event = threading.Event()

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if k in self._state:
                    self._state[k] = v
        time.sleep(0)

    def reset(self) -> None:
        with self._lock:
            self._state = {
                "status": "idle",
                "progress": 0.0,
                "current_step": 0,
                "total_steps": 0,
                "loss": None,
                "extracted_turns": 0,
                "cleaned_turns": 0,
                "error": None,
                "start_time": None,
                "eta_seconds": None,
                "step_name": None,
            }

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    @property
    def is_stopping(self) -> bool:
        return self._stop_event.is_set()

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        self._stop_event.clear()
        if self._executor is None or self._executor._shutdown:
            self._executor = ThreadPoolExecutor(max_workers=1)

        def _wrapped():
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Background training task failed")
                with self._lock:
                    self._state["status"] = "error"
                    self._state["error"] = "internal_error"

        self._current_future = self._executor.submit(_wrapped)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._state["status"] = "stopped"
        if self._current_future and not self._current_future.done():
            self._current_future.cancel()
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    def __del__(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=False)
