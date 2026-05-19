from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("secret_manager")

HARDCODED_PATTERNS = ["sk-", "key=", "token=", "secret=", "password="]
DEFAULT_TTL = 3600


class SecretManager:
    def __init__(self, ttl: int = DEFAULT_TTL):
        self._secrets: dict = {}
        self._ttl = ttl

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self._secrets:
            value, timestamp = self._secrets[key]
            if time.time() - timestamp < self._ttl:
                return value
            del self._secrets[key]
        value = os.environ.get(key)
        if value:
            self._secrets[key] = (value, time.time())
            return value
        value = self._load_from_file(key)
        if value:
            self._secrets[key] = (value, time.time())
            return value
        return default

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
        return {"cached_keys": len(self._secrets), "ttl": self._ttl}
