from collections import deque
from typing import Any


class ToolHistoryManager:
    def __init__(self, maxlen: int = 1000):
        self._history: deque[Any] = deque(maxlen=maxlen)

    def append(self, entry: Any) -> None:
        self._history.append(entry)

    def get_recent(self, limit: int = 50) -> list[Any]:
        return list(self._history)[-limit:]

    def clear(self) -> None:
        self._history.clear()

    def __len__(self) -> int:
        return len(self._history)
