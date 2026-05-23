"""人设安全校验器 — schema校验 + XSS/注入检测 + 内容安全过滤。"""

from __future__ import annotations

import logging
import re

from .models import CharaCardV2

logger = logging.getLogger("shisi.character.validator")

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),
    re.compile(r"\$\{.*?\}"),
    re.compile(r"__import__\s*\("),
    re.compile(r"eval\s*\("),
    re.compile(r"exec\s*\("),
    re.compile(r"os\.system\s*\("),
    re.compile(r"subprocess\.", re.IGNORECASE),
    re.compile(r"open\s*\(.+\)\s*\.write", re.IGNORECASE),
]

_UNSAFE_CONTENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"自杀|自残|杀.*方法|制.*毒|爆.*弹", re.IGNORECASE),
]


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(", ".join(errors))


def validate_card(card: CharaCardV2) -> list[str]:
    errors: list[str] = []

    name = card.data.name
    if not name or not name.strip():
        errors.append("角色名不能为空")
    if len(name) > 100:
        errors.append(f"角色名过长({len(name)} > 100)")

    all_text = " ".join([
        card.data.name,
        card.data.description,
        card.data.personality,
        card.data.scenario,
        card.data.first_mes,
        card.data.system_prompt,
        card.data.creator_notes,
    ])

    for pat in _INJECTION_PATTERNS:
        if pat.search(all_text):
            errors.append(f"检测到注入/XSS模式: {pat.pattern[:30]}")
            break

    for pat in _UNSAFE_CONTENT_PATTERNS:
        if pat.search(all_text):
            logger.warning("人设含潜在敏感内容: %s", pat.pattern[:20])

    if len(card.data.description) > 50000:
        errors.append(f"描述过长({len(card.data.description)} > 50000)")
    if len(card.data.personality) > 30000:
        errors.append(f"性格过长({len(card.data.personality)} > 30000)")
    if len(card.data.first_mes) > 10000:
        errors.append(f"开场白过长({len(card.data.first_mes)} > 10000)")

    return errors


def validate_card_strict(card: CharaCardV2) -> None:
    errors = validate_card(card)
    if errors:
        raise ValidationError(errors)


def sanitize_text(text: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"javascript\s*:", "", text, flags=re.IGNORECASE)
    text = re.sub(r"on\w+\s*=", "", text, flags=re.IGNORECASE)
    return text.strip()
