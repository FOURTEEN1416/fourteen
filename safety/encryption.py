from __future__ import annotations

import logging
import os

logger = logging.getLogger("encryption")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


class DecryptionError(Exception):
    def __init__(self, message: str, ciphertext_preview: str = ""):
        super().__init__(message)
        self.message = message
        self.ciphertext_preview = ciphertext_preview


class EncryptionError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class EncryptionManager:
    def __init__(self, key_env: str = "AI_GF_ENCRYPTION_KEY", enabled: bool = False):
        self.enabled = enabled and HAS_CRYPTO
        self._key: bytes | None = None
        if self.enabled:
            key_hex = os.environ.get(key_env)
            if key_hex and len(key_hex) == 64:
                self._key = bytes.fromhex(key_hex)
            else:
                logger.warning("Encryption key not found or invalid, encryption disabled")
                self.enabled = False

    def encrypt(self, plaintext: str, associated_data: bytes | None = None) -> str | None:
        if not self.enabled:
            return None
        if not self._key:
            raise EncryptionError("Encryption enabled but key not available")
        try:
            aesgcm = AESGCM(self._key)  # type: ignore
            nonce = os.urandom(12)
            ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data)
            return (nonce + ct).hex()
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {e}")

    def decrypt(self, ciphertext_hex: str, associated_data: bytes | None = None) -> str | None:
        if not self.enabled:
            raise DecryptionError("Encryption is disabled, cannot decrypt")
        if not self._key:
            raise DecryptionError("Encryption enabled but key not available")
        try:
            data = bytes.fromhex(ciphertext_hex)
            nonce = data[:12]
            ct = data[12:]
            aesgcm = AESGCM(self._key)  # type: ignore
            plaintext = aesgcm.decrypt(nonce, ct, associated_data)
            return plaintext.decode("utf-8")
        except Exception as e:
            preview = ciphertext_hex[:16] + "..." if len(ciphertext_hex) > 16 else ciphertext_hex
            raise DecryptionError(f"Decryption failed: {e}", ciphertext_preview=preview)

    def is_available(self) -> bool:
        return self.enabled
