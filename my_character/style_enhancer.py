"""
风格增强器 — 16维度深度风格分析

扩展自原有12维度，新增4维度：
修辞手法、亲密表达方式、冲突处理风格、话题转换模式
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("style_enhancer")


@dataclass
class EnhancedStyle:
    config: Dict[str, Any] = field(default_factory=dict)
    examples: List[Dict[str, str]] = field(default_factory=list)
    dimensions: Dict[str, float] = field(default_factory=dict)


STYLE_DIMENSIONS = [
    "sentence_length",
    "punctuation_freq",
    "particle_freq",
    "emoji_freq",
    "catchphrases",
    "reply_delay",
    "emotion_vocab",
    "pronoun_pref",
    "sentence_type",
    "slang_freq",
    "uniqueness",
    "formality",
    "rhetorical_devices",
    "intimacy_expression",
    "conflict_handling",
    "topic_transition",
]


class StyleEnhancer:
    """风格增强器 — 16维度风格分析"""

    def __init__(self, chat_history: Optional[List[Dict]] = None):
        self._chat_history = chat_history or []

    def enhance_style(
        self,
        base_style: Dict[str, Any],
        chat_history: Optional[List[Dict]] = None,
        persona_style: Optional[Dict[str, Any]] = None,
    ) -> EnhancedStyle:
        """风格增强流程：提取历史风格 → 融合人设风格 → 生成增强配置"""
        history = chat_history or self._chat_history

        historical_style = self._extract_style_from_history(history)
        fused_style = self._fuse_styles(
            base_style,
            historical_style,
            persona_style or {},
        )
        style_examples = self._extract_style_examples(history, top_k=5)
        dimensions = self._calculate_all_dimensions(fused_style)

        return EnhancedStyle(
            config=fused_style,
            examples=style_examples,
            dimensions=dimensions,
        )

    def _extract_style_from_history(self, history: List[Dict]) -> Dict[str, Any]:
        if not history:
            return {}
        ai_messages = [m.get("response", "") for m in history if m.get("response")]
        if not ai_messages:
            return {}

        avg_len = sum(len(m) for m in ai_messages) / len(ai_messages)
        particles = self._count_particles(ai_messages)
        emoji_count = sum(1 for m in ai_messages for c in m if ord(c) > 0x1F000)
        emoji_freq = emoji_count / max(1, sum(len(m) for m in ai_messages))

        return {
            "sentence_length": "short" if avg_len < 20 else ("medium" if avg_len < 50 else "long"),
            "avg_length": avg_len,
            "particles": particles,
            "emoji_freq": min(1.0, emoji_freq * 10),
        }

    def _fuse_styles(
        self,
        base: Dict[str, Any],
        historical: Dict[str, Any],
        persona: Dict[str, Any],
        weights: Tuple[float, float, float] = (0.3, 0.3, 0.4),
    ) -> Dict[str, Any]:
        fused = {}
        all_keys = set(list(base.keys()) + list(historical.keys()) + list(persona.keys()))

        for key in all_keys:
            b = base.get(key, 0)
            h = historical.get(key, 0)
            p = persona.get(key, 0)

            if isinstance(b, (int, float)) or isinstance(h, (int, float)) or isinstance(p, (int, float)):
                b_val = float(b) if isinstance(b, (int, float)) else 0.0
                h_val = float(h) if isinstance(h, (int, float)) else 0.0
                p_val = float(p) if isinstance(p, (int, float)) else 0.0
                fused[key] = b_val * weights[0] + h_val * weights[1] + p_val * weights[2]
            else:
                fused[key] = p if p else (h if h else b)

        return fused

    def _extract_style_examples(
        self, history: List[Dict], top_k: int = 5,
    ) -> List[Dict[str, str]]:
        examples = []
        for msg in history[-top_k:]:
            user = msg.get("user", "")
            response = msg.get("response", "")
            if user and response:
                examples.append({"user": user, "response": response})
        return examples

    def _calculate_all_dimensions(self, style: Dict[str, Any]) -> Dict[str, float]:
        dims = {}

        avg_len = style.get("avg_length", 30)
        dims["sentence_length"] = min(1.0, avg_len / 100)
        dims["punctuation_freq"] = style.get("punctuation_freq", 0.3)
        dims["particle_freq"] = style.get("particle_freq", 0.4)
        dims["emoji_freq"] = style.get("emoji_freq", 0.5)
        dims["catchphrases"] = 0.5
        dims["reply_delay"] = 0.5
        dims["emotion_vocab"] = 0.5
        dims["pronoun_pref"] = 0.5
        dims["sentence_type"] = 0.5
        dims["slang_freq"] = 0.3
        dims["uniqueness"] = 0.6
        dims["formality"] = style.get("formality", 0.3)
        dims["rhetorical_devices"] = 0.4
        dims["intimacy_expression"] = style.get("intimacy", 0.4)
        dims["conflict_handling"] = 0.5
        dims["topic_transition"] = 0.5

        return dims

    def _count_particles(self, messages: List[str]) -> Dict[str, int]:
        particle_pattern = re.compile(r'[哼嘛呢呀哦吧哇唉嗯呵呜]')
        counts: Dict[str, int] = {}
        for msg in messages:
            for char in msg:
                if particle_pattern.match(char):
                    counts[char] = counts.get(char, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10])

    def generate_style_prompt_segment(self, enhanced: EnhancedStyle) -> str:
        """生成风格指导的提示词段"""
        parts = []
        dims = enhanced.dimensions

        if dims.get("sentence_length", 0.5) < 0.3:
            parts.append("回复简短")
        elif dims.get("sentence_length", 0.5) > 0.6:
            parts.append("回复可以稍长")

        if dims.get("emoji_freq", 0.5) > 0.5:
            parts.append("适当使用表情和语气词")

        if dims.get("formality", 0.3) < 0.3:
            parts.append("语气随意亲密")

        if dims.get("intimacy_expression", 0.4) > 0.5:
            parts.append("自然表达亲密感")

        if dims.get("rhetorical_devices", 0.4) > 0.4:
            parts.append("可以使用反问、暗示等修辞")

        return "；".join(parts) if parts else ""
