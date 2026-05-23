"""内容安全检测。"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("aiyu.sticker.safety_check")

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"暴力|血腥|色情|毒品|赌博", re.IGNORECASE),
]


def check_sticker_safety(filename: str, metadata: dict | None = None) -> tuple[bool, str]:
    for pat in _PATTERNS:
        if pat.search(filename):
            return False, f"文件名含敏感内容: {pat.pattern[:20]}"
    return True, ""
