"""角色数据模型 — Pydantic V2模型，兼容chara_card_v2/V3与十四APP prompts格式。

@deprecated 此模块已弃用，请使用 shisi.core.models.character_aggregate.CharacterAggregate
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class CardFormat(str, Enum):
    CHARA_CARD_V2 = "chara_card_v2"
    CHARA_CARD_V3 = "chara_card_v3"
    AIYU_PROMPTS = "shisi_prompts"


class WorldInfoEntry(BaseModel):
    id: int = 0
    keys: list[str] = Field(default_factory=list)
    content: str = ""
    secondary_keys: list[str] = Field(default_factory=list)
    comment: str = ""
    constant: bool = False
    selective: bool = True
    insertion_order: int = 100
    enabled: bool = True
    position: str = "0"
    extensions: dict[str, Any] = Field(default_factory=dict)


class WorldInfoBook(BaseModel):
    name: str = ""
    entries: list[WorldInfoEntry] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class CharacterExtensions(BaseModel):
    talkativeness: float = 0.5
    fav: bool = False
    world: str = ""
    depth_prompt: Optional[dict[str, Any]] = None
    regex_scripts: list[dict[str, Any]] = Field(default_factory=list)


class CharacterData(BaseModel):
    name: str = ""
    description: str = ""
    character_version: str = "1.0"
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    post_history_instructions: str = ""
    creator_notes: str = ""
    tags: list[str] = Field(default_factory=list)
    creator: str = "unknown"
    character_book: Optional[WorldInfoBook] = None
    extensions: CharacterExtensions = Field(default_factory=lambda: CharacterExtensions())

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("角色名不能为空")
        return v.strip()


class CharaCardV2(BaseModel):
    spec: str = "chara_card_v2"
    spec_version: str = "2.0"
    data: CharacterData = Field(default_factory=lambda: CharacterData())

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, v: str) -> str:
        allowed = {"chara_card_v2", "chara_card_v3"}
        if v not in allowed:
            raise ValueError(f"不支持的spec: {v}，仅支持 {allowed}")
        return v


class CharacterState(BaseModel):
    character_id: str
    name: str
    format: CardFormat = CardFormat.CHARA_CARD_V2
    is_active: bool = False
    affinity: float = 0.0
    emotion_stage: str = "陌生"
    avatar_url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ImportResult(BaseModel):
    total: int = 0
    success: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    imported_ids: list[str] = Field(default_factory=list)
