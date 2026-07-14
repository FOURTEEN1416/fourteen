from __future__ import annotations

import hashlib
import logging
import re

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

    def anonymize(self, text: str) -> tuple[str, list[dict]]:
        if not self.enabled:
            return text, []
        all_matches = []
        for pii_type, pattern in PII_PATTERNS:
            for match in pattern.finditer(text):
                original = match.group()
                if pii_type == "phone" and HOTLINE_WHITELIST.search(original):
                    continue
                all_matches.append((match.start(), match.end(), pii_type, original))

        all_matches.sort(key=lambda x: x[0])
        merged: list[tuple[int, int, str, str]] = []
        for m in all_matches:
            if merged and m[0] < merged[-1][1]:
                prev = merged[-1]
                merged[-1] = (prev[0], max(prev[1], m[1]), prev[2], prev[3])
            else:
                merged.append(m)
        merged.sort(key=lambda x: x[0], reverse=True)

        anonymized = text
        detected = []
        for start, end, pii_type, original in merged:
            replacement = self._mask(pii_type, original)
            anonymized = anonymized[:start] + replacement + anonymized[end:]
            detected.append({
                "type": pii_type,
                "placeholder": replacement,
                "original_hash": hashlib.sha256(original.encode()).hexdigest()[:16],
            })
        if detected:
            logger.info("PII detected and anonymized: %d items", len(detected))
        return anonymized, detected

    def deanonymize(self, text: str, pii_map: dict[str, str] | list[dict]) -> str:
        """反脱敏 — 支持 dict[str, str] 和 list[dict] 两种格式

        Args:
            text: 脱敏后的文本
            pii_map: dict[str, str] 格式 {placeholder: original}
                     或 list[dict] 格式 [{"type":..., "placeholder":..., "original"...}]

        Note:
            anonymize() 不再存储原始 PII（使用 original_hash 替代），
            因此从 list[dict] 反脱敏时仅恢复有 "original" 字段的项。
        """
        if isinstance(pii_map, list):
            # 从 list[dict] 转换为 dict[str, str]（仅恢复有 original 字段的旧格式项）
            pii_dict: dict[str, str] = {}
            for item in pii_map:
                if isinstance(item, dict) and "placeholder" in item and "original" in item:
                    pii_dict[str(item["placeholder"])] = str(item["original"])
            pii_map = pii_dict

        result = text
        for placeholder, original in pii_map.items():
            result = result.replace(placeholder, original)
        return result

    @staticmethod
    def pii_list_to_map(pii_list: list[dict]) -> dict[str, str]:
        """将 anonymize() 返回的 list[dict] 转换为 dict[str,str] 格式

        Note:
            anonymize() 不再存储原始 PII（使用 original_hash 替代），
            因此仅转换包含 "original" 字段的旧格式项。
        """
        return {str(item["placeholder"]): str(item["original"])
                for item in pii_list
                if isinstance(item, dict) and "placeholder" in item and "original" in item}

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
