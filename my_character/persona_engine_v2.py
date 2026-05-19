from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("persona_engine_v2")

ANCHOR_FROZEN_HASHES: Dict[str, str] = {}


class PersonaProfile:
    def __init__(self):
        self.core_character: Dict[str, float] = {
            "warmth": 0.8, "playfulness": 0.6, "independence": 0.7,
            "jealousy": 0.5, "stubbornness": 0.6,
        }
        self.speaking_style: Dict[str, float] = {
            "formality": 0.3, "emoji_freq": 0.6, "sentence_length": 0.5,
            "emotional_expression": 0.7, "humor": 0.5,
        }
        self.emotional_preference: Dict[str, float] = {
            "expressiveness": 0.7, "empathy": 0.8, "jealousy_tendency": 0.5,
        }
        self.interest_hobbies: List[str] = []
        self.value_tendency: Dict[str, float] = {
            "relationship": 0.9, "freedom": 0.6, "stability": 0.7,
        }

    def to_prompt_segments(self) -> str:
        lines = []
        lines.append("【性格维度】" + ", ".join(f"{k}={v:.1f}" for k, v in self.core_character.items()))
        lines.append("【说话风格】" + ", ".join(f"{k}={v:.1f}" for k, v in self.speaking_style.items()))
        lines.append("【情感偏好】" + ", ".join(f"{k}={v:.1f}" for k, v in self.emotional_preference.items()))
        if self.interest_hobbies:
            lines.append("【兴趣爱好】" + ", ".join(self.interest_hobbies))
        return "\n".join(lines)


class PersonaEngineV2:
    def __init__(self, config_loader=None, llm_gateway=None):
        self._config = config_loader
        self._llm = llm_gateway
        self.profile = PersonaProfile()
        self._original_anchors: List[str] = []
        self._evolution_log: List[Dict] = []
        self._load_config()

    def _load_config(self):
        if not self._config:
            return
        persona = self._config.load_persona()
        traits = persona.get("personality_traits", {})
        for k, v in traits.items():
            if k in self.profile.core_character:
                self.profile.core_character[k] = float(v)
        anchors = persona.get("core_anchors", [])
        self._original_anchors = anchors
        for anchor in anchors:
            ANCHOR_FROZEN_HASHES[anchor] = hashlib.sha256(anchor.encode()).hexdigest()

    def build_system_prompt(self, emotion_state: str = "", memory_context: str = "",
                            style_reference: str = "", rag_context: str = "") -> str:
        persona_data = self._config.load_persona() if self._config else {}
        name = persona_data.get("name", "小暖")
        anchors = persona_data.get("core_anchors", self._original_anchors)
        parts = [
            f"你是{name}，一个有个性的AI女友。",
            f"\n【核心锚点】\n" + "\n".join(f"- {a}" for a in anchors),
            f"\n{self.profile.to_prompt_segments()}",
        ]
        if emotion_state:
            parts.append(f"\n【当前情感状态】\n{emotion_state}")
        if memory_context:
            parts.append(f"\n【记忆上下文】\n{memory_context}")
        if style_reference:
            parts.append(f"\n【风格参考】\n{style_reference}")
        if rag_context:
            parts.append(f"\n【检索知识】\n{rag_context}")
        parts.append("\n请严格按照以上人设和情感状态回复，保持语气一致。")
        return "\n".join(parts)

    def evolve(self, dimension: str, delta: float, trigger: str = "",
               llm_reasoning: str = "") -> bool:
        delta = max(-0.05, min(0.05, delta))
        all_dims = {}
        all_dims.update(self.profile.core_character)
        all_dims.update(self.profile.speaking_style)
        all_dims.update(self.profile.emotional_preference)
        if dimension not in all_dims:
            return False
        before = all_dims[dimension]
        after = max(0.0, min(1.0, before + delta))
        all_dims[dimension] = after
        if dimension in self.profile.core_character:
            self.profile.core_character[dimension] = after
        elif dimension in self.profile.speaking_style:
            self.profile.speaking_style[dimension] = after
        elif dimension in self.profile.emotional_preference:
            self.profile.emotional_preference[dimension] = after
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "dimension": dimension,
            "before": before,
            "after": after,
            "delta": delta,
            "trigger": trigger,
            "llm_reasoning": llm_reasoning,
        }
        self._evolution_log.append(log_entry)
        logger.info("Persona evolved: %s %.3f -> %.3f (delta=%.4f)", dimension, before, after, delta)
        return True

    def verify_anchors(self) -> bool:
        for anchor, expected_hash in ANCHOR_FROZEN_HASHES.items():
            current_hash = hashlib.sha256(anchor.encode()).hexdigest()
            if current_hash != expected_hash:
                logger.error("Anchor integrity violation detected!")
                return False
        return True

    def get_evolution_log(self, limit: int = 50) -> List[Dict]:
        return self._evolution_log[-limit:]

    def health_check(self) -> dict:
        return {
            "anchor_integrity": self.verify_anchors(),
            "evolution_count": len(self._evolution_log),
        }
