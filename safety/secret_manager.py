from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("secret_manager")

HARDCODED_PATTERNS = ["sk-", "key=", "token=", "secret=", "password="]
DEFAULT_TTL = 3600


class SecretManager:
    def __init__(self, ttl: int = DEFAULT_TTL, max_cache_size: int = 50):
        self._secrets: OrderedDict = OrderedDict()
        self._ttl = ttl
        self._max_cache_size = max_cache_size

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self._secrets:
            value, timestamp = self._secrets[key]
            if time.time() - timestamp < self._ttl:
                self._secrets.move_to_end(key)
                return value
            del self._secrets[key]
        value = os.environ.get(key)
        if value:
            self._add_to_cache(key, value)
            return value
        value = self._load_from_file(key)
        if value:
            self._add_to_cache(key, value)
            return value
        return default

    def _add_to_cache(self, key: str, value: str) -> None:
        if len(self._secrets) >= self._max_cache_size:
            self._secrets.popitem(last=False)
        self._secrets[key] = (value, time.time())

    def _load_from_file(self, key: str) -> Optional[str]:
        secret_file = Path(f"./secrets/{key}")
        if secret_file.exists():
            try:
                return secret_file.read_text().strip()
            except Exception as e:
                logger.warning("Failed to read secret file for %s: %s", key, e)
        return None

    @staticmethod
    def check_no_hardcoded_secrets(text: str) -> bool:
        lower = text.lower()
        for pattern in HARDCODED_PATTERNS:
            if pattern in lower:
                return False
        return True

    def health_check(self) -> dict:
        return {"cached_count": len(self._secrets), "ttl": self._ttl, "max_size": self._max_cache_size}
