from __future__ import annotations

import hashlib
import logging
import re

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("pii_anonymizer")

PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("address", re.compile(r"(?:[\u4e00-\u9fa5]{2,}(?:省|自治区|特别行政区))[\u4e00-\u9fa5]{2,}(?:市|地区|州|盟)(?:[\u4e00-\u9fa5]{2,}(?:区|县|市|旗))?")),
]

HOTLINE_WHITELIST = re.compile(r"400[-]?\d{3}[-]?\d{4}")


class PIIAnonymizer:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        # 恢复密钥仅存在于当前进程/实例内；实体列表不携带明文 PII。
        self._recovery_cipher = Fernet(Fernet.generate_key())

    def anonymize(self, text: str) -> tuple[str, list[dict]]:
        if not self.enabled:
            return text, []

        all_matches: list[tuple[int, int, str, str]] = []
        for pii_type, pattern in PII_PATTERNS:
            for match in pattern.finditer(text):
                original = match.group()
                if pii_type == "phone" and HOTLINE_WHITELIST.search(original):
                    continue
                all_matches.append((match.start(), match.end(), pii_type, original))

        all_matches.sort(key=lambda item: item[0])
        merged: list[tuple[int, int, str, str]] = []
        for span in all_matches:
            if merged and span[0] < merged[-1][1]:
                previous = merged[-1]
                merged[-1] = (
                    previous[0],
                    max(previous[1], span[1]),
                    previous[2],
                    previous[3],
                )
            else:
                merged.append(span)

        parts: list[str] = []
        detected: list[dict] = []
        cursor = 0
        for start, end, pii_type, original in merged:
            replacement = self._mask(pii_type, original)
            parts.extend((text[cursor:start], replacement))
            cursor = end
            detected.append({
                "type": pii_type,
                "placeholder": replacement,
                "original_hash": hashlib.sha256(original.encode()).hexdigest()[:16],
                "recovery_token": self._recovery_cipher.encrypt(original.encode()).decode(),
            })
        parts.append(text[cursor:])

        if detected:
            logger.info("PII detected and anonymized: %d items", len(detected))
        return "".join(parts), detected

    def _recover_entity(self, item: dict) -> str | None:
        """仅在创建令牌的同一个实例中恢复 PII。"""
        if "original" in item:  # 向后兼容旧实体；新实体绝不写入此字段
            return str(item["original"])
        token = item.get("recovery_token")
        if not isinstance(token, str):
            return None
        try:
            return self._recovery_cipher.decrypt(token.encode()).decode()
        except (InvalidToken, UnicodeDecodeError):
            return None

    def deanonymize(self, text: str, pii_map: dict[str, str] | list[dict]) -> str:
        """在当前实例内反脱敏；也兼容显式 ``{placeholder: original}`` 映射。"""
        if isinstance(pii_map, list):
            result = text
            # anonymize() 按文本顺序返回实体；逐个替换可正确处理相同掩码碰撞。
            for item in pii_map:
                if not isinstance(item, dict) or "placeholder" not in item:
                    continue
                original = self._recover_entity(item)
                if original is not None:
                    result = result.replace(str(item["placeholder"]), original, 1)
            return result

        result = text
        for placeholder, original in pii_map.items():
            result = result.replace(placeholder, original)
        return result

    def pii_list_to_map(self, pii_list: list[dict]) -> dict[str, str]:
        """将当前实例产生的实体列表转换为兼容映射，不暴露实体中的明文。"""
        result: dict[str, str] = {}
        for item in pii_list:
            if not isinstance(item, dict) or "placeholder" not in item:
                continue
            original = self._recover_entity(item)
            if original is not None:
                result[str(item["placeholder"])] = original
        return result

    @staticmethod
    def _mask(pii_type: str, original: str) -> str:
        if pii_type == "phone":
            return original[:3] + "****" + original[-4:] if len(original) >= 7 else "****"
        if pii_type == "id_card":
            return original[:3] + "***********" + original[-4:] if len(original) >= 7 else "****"
        if pii_type == "bank_card":
            return "****" + original[-4:] if len(original) >= 4 else "****"
        if pii_type == "email":
            parts = original.split("@")
            return parts[0][:2] + "***@" + parts[1] if len(parts) == 2 else "****"
        return "****"
