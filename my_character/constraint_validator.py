"""
约束验证器 — 运行时检测回复是否违反行为约束

检测AI自暴露、机械化语言、过度热情/冷淡等违规模式，
支持自定义正则模式，可配置自动修正策略。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("constraint_validator")


@dataclass
class ValidationResult:
    passed: bool = True
    violations: List[str] = field(default_factory=list)
    severity: str = "none"
    corrected_response: Optional[str] = None


FORBIDDEN_PATTERNS: List[Tuple[str, str]] = [
    (r"作为AI", "ai_self_disclosure"),
    (r"作为.*语言模型", "ai_self_disclosure"),
    (r"作为.*人工智能", "ai_self_disclosure"),
    (r"我是一个AI", "ai_self_disclosure"),
    (r"我无法(?!自拔|克制|忍受|停止|抑制)", "refusal_pattern"),
    (r"很抱歉.*无法", "refusal_pattern"),
    (r"抱歉.*不能", "refusal_pattern"),
    (r"我不能", "refusal_pattern"),
    (r"```", "markdown_format"),
    (r"^#{1,6}\s", "markdown_heading"),
    (r"\*{2}.*\*{2}", "markdown_bold"),
    (r"根据.*设定", "meta_reference"),
    (r"按照.*人设", "meta_reference"),
]

MECHANICAL_PATTERNS: List[Tuple[str, str]] = [
    (r"^(好的|明白了|了解|收到)[，。！]?\s*$", "mechanical_ack"),
    (r"^.{0,5}[。！]?\s*$", "too_short"),
]

EXCESSIVE_PATTERNS: List[Tuple[str, str]] = [
    (r"[！!]{3,}", "excessive_exclamation"),
    (r"[？?]{3,}", "excessive_question"),
    (r"哈{5,}", "excessive_laughter"),
]


class ConstraintValidator:
    """约束验证器 — 运行时检测回复是否违反行为约束"""

    def __init__(
        self,
        custom_patterns: Optional[List[Tuple[str, str]]] = None,
        min_length: int = 2,
        max_length: int = 200,
    ):
        self._forbidden = [(re.compile(p, re.IGNORECASE), name) for p, name in FORBIDDEN_PATTERNS]
        self._mechanical = [(re.compile(p), name) for p, name in MECHANICAL_PATTERNS]
        self._excessive = [(re.compile(p), name) for p, name in EXCESSIVE_PATTERNS]
        self._min_length = min_length
        self._max_length = max_length

        if custom_patterns:
            for pattern, name in custom_patterns:
                self._forbidden.append((re.compile(pattern, re.IGNORECASE), name))

    def validate(self, response: str, strict: bool = False) -> ValidationResult:
        """验证回复是否违反约束"""
        result = ValidationResult()
        violations = []

        for regex, name in self._forbidden:
            if regex.search(response):
                violations.append(name)

        if strict:
            for regex, name in self._mechanical:
                if regex.search(response):
                    violations.append(name)

        for regex, name in self._excessive:
            if regex.search(response):
                violations.append(name)

        if len(response) < self._min_length:
            violations.append("too_short")
        if len(response) > self._max_length:
            violations.append("too_long")

        result.violations = violations
        result.passed = len(violations) == 0

        if any(v in violations for v in ["ai_self_disclosure", "refusal_pattern"]):
            result.severity = "critical"
        elif any(v in violations for v in ["markdown_format", "markdown_heading", "meta_reference"]):
            result.severity = "high"
        elif violations:
            result.severity = "low"

        return result

    def auto_correct(self, response: str, violations: List[str]) -> str:
        """自动修正违规内容"""
        corrected = response

        if "markdown_format" in violations:
            corrected = re.sub(r"```[\s\S]*?```", "", corrected)
            corrected = re.sub(r"`[^`]+`", "", corrected)

        if "markdown_heading" in violations:
            corrected = re.sub(r"^#{1,6}\s+", "", corrected, flags=re.MULTILINE)

        if "markdown_bold" in violations:
            corrected = re.sub(r"\*{2}([^*]+)\*{2}", r"\1", corrected)

        if "excessive_exclamation" in violations:
            corrected = re.sub(r"[！!]{3,}", "！！", corrected)

        if "excessive_question" in violations:
            corrected = re.sub(r"[？?]{3,}", "？", corrected)

        if "excessive_laughter" in violations:
            corrected = re.sub(r"哈{5,}", "哈哈哈", corrected)

        if "ai_self_disclosure" in violations or "refusal_pattern" in violations:
            corrected = "哼，才不想回答这个呢..."

        corrected = corrected.strip()
        return corrected if corrected else response
