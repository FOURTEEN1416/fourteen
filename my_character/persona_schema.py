"""
统一人设模式 — PersonaSchema

标准化人设数据结构，统一 PersonaProfile / CharaCardV2 / persona.yaml 三种来源，
提供单一真值源（Single Source of Truth）和运行时验证。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("persona_schema")


@dataclass
class EvolutionConfig:
    enabled: bool = True
    max_delta: float = 0.05
    cooldown_rounds: int = 10
    protected_dimensions: tuple[str, ...] = ("warmth",)


@dataclass
class AnchorConfig:
    semantic_threshold: float = 0.3
    keyword_check: bool = True
    llm_check: bool = False
    reinforcement_interval: int = 5
    max_reinforcement_length: int = 200


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    anchor_conflicts: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PersonaSchema:
    """统一人设模式 — 单一真值源"""
    name: str
    version: str = "2.0"
    core_anchors: tuple[str, ...] = ()
    personality_traits: dict[str, float] = field(default_factory=dict)
    speaking_style: dict[str, float] = field(default_factory=dict)
    emotional_preference: dict[str, float] = field(default_factory=dict)
    value_tendency: dict[str, float] = field(default_factory=dict)
    interest_hobbies: tuple[str, ...] = ()
    communication_templates: dict[str, str] = field(default_factory=dict)
    evolution_config: EvolutionConfig = field(default_factory=EvolutionConfig)
    anchor_config: AnchorConfig = field(default_factory=AnchorConfig)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> ValidationResult:
        errors = []
        warnings = []
        anchor_conflicts = []  # type: ignore[var-annotated]

        if not self.name:
            errors.append("name is required")

        for dim, val in self.personality_traits.items():
            if not (0.0 <= val <= 1.0):
                errors.append(f"personality_traits['{dim}'] = {val} out of [0, 1]")

        for dim, val in self.speaking_style.items():
            if not (0.0 <= val <= 1.0):
                errors.append(f"speaking_style['{dim}'] = {val} out of [0, 1]")

        seen = set()
        for anchor in self.core_anchors:
            normalized = anchor.strip().lower()
            if normalized in seen:
                warnings.append(f"duplicate anchor: '{anchor}'")
            seen.add(normalized)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            anchor_conflicts=anchor_conflicts,
        )

    def merge(self, other: PersonaSchema, weights: dict[str, float] | None = None) -> PersonaSchema:
        w = weights or {"self": 0.6, "other": 0.4}
        ws = w.get("self", 0.6)
        wo = w.get("other", 0.4)

        merged_traits = {}
        all_dims = set(self.personality_traits) | set(other.personality_traits)
        for dim in all_dims:
            sv = self.personality_traits.get(dim, 0.5)
            ov = other.personality_traits.get(dim, 0.5)
            merged_traits[dim] = round(sv * ws + ov * wo, 4)

        merged_style = {}
        all_sdims = set(self.speaking_style) | set(other.speaking_style)
        for dim in all_sdims:
            sv = self.speaking_style.get(dim, 0.5)
            ov = other.speaking_style.get(dim, 0.5)
            merged_style[dim] = round(sv * ws + ov * wo, 4)

        return PersonaSchema(
            name=self.name,
            version=self.version,
            core_anchors=self.core_anchors,
            personality_traits=merged_traits,
            speaking_style=merged_style,
            emotional_preference=self.emotional_preference,
            value_tendency=self.value_tendency,
            interest_hobbies=self.interest_hobbies,
            communication_templates=self.communication_templates,
            evolution_config=self.evolution_config,
            anchor_config=self.anchor_config,
            metadata=self.metadata,
        )

    def to_persona_config(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "core_anchors": list(self.core_anchors),
            "personality_traits": dict(self.personality_traits),
            "speaking_style": dict(self.speaking_style),
            "emotional_preference": dict(self.emotional_preference),
            "value_tendency": dict(self.value_tendency),
            "interest_hobbies": list(self.interest_hobbies),
            "communication_templates": dict(self.communication_templates),
        }

    @classmethod
    def from_persona_config(cls, config: dict) -> PersonaSchema:
        anchors = config.get("core_anchors", config.get("anchors", []))
        if isinstance(anchors, list):
            anchors = tuple(anchors)

        hobbies = config.get("interest_hobbies", [])
        if isinstance(hobbies, list):
            hobbies = tuple(hobbies)

        evo_cfg = config.get("evolution_config", {})
        anc_cfg = config.get("anchor_config", {})

        return cls(
            name=config.get("name", "unknown"),
            version=config.get("version", "2.0"),
            core_anchors=anchors,
            personality_traits=config.get("personality_traits", {}),
            speaking_style=config.get("speaking_style", {}),
            emotional_preference=config.get("emotional_preference", {}),
            value_tendency=config.get("value_tendency", {}),
            interest_hobbies=hobbies,
            communication_templates=config.get("communication_templates", {}),
            evolution_config=EvolutionConfig(**evo_cfg) if evo_cfg else EvolutionConfig(),
            anchor_config=AnchorConfig(**anc_cfg) if anc_cfg else AnchorConfig(),
            metadata=config.get("metadata", {}),
        )

    @classmethod
    def from_persona_profile(cls, profile: Any, anchors: list[str], name: str) -> PersonaSchema:
        traits = {}
        if hasattr(profile, "core_character"):
            traits = dict(profile.core_character)

        hobbies = []
        if hasattr(profile, "interest_hobbies"):
            hobbies = list(profile.interest_hobbies)

        return cls(
            name=name,
            core_anchors=tuple(anchors),
            personality_traits=traits,
            interest_hobbies=tuple(hobbies),
        )

    @classmethod
    def from_chara_card(cls, card: Any) -> PersonaSchema:
        name = ""
        anchors = []
        traits = {}

        if hasattr(card, "data"):
            data = card.data
            name = getattr(data, "name", "unknown")
            if hasattr(data, "personality"):
                anchors = [data.personality] if isinstance(data.personality, str) else []
        elif isinstance(card, dict):
            name = card.get("name", "unknown")
            data = card.get("data", card)
            anchors = data.get("personality", [])
            if isinstance(anchors, str):
                anchors = [anchors]
            traits = data.get("personality_traits", {})

        return cls(
            name=name,
            core_anchors=tuple(anchors),
            personality_traits=traits,
        )
