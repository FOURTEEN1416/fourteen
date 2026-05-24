"""API v2 请求/响应数据模型"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateCharacterRequest(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class UpdatePersonaRequest(BaseModel):
    warmth: float | None = None
    playfulness: float | None = None
    independence: float | None = None
    jealousy: float | None = None
    stubbornness: float | None = None
    formality: float | None = None
    emoji_frequency: float | None = None
    sentence_length: float | None = None
    emotional_expression: float | None = None
    humor: float | None = None
    core_anchors: list[str] | None = None


class ProcessMessageRequest(BaseModel):
    message: str
    chat_history: str = ""


class CharacterDetail(BaseModel):
    id: str
    name: str
    description: str
    avatar_url: str | None
    tags: list[str]
    persona: dict[str, Any]
    emotional_state: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CharacterSummary(BaseModel):
    id: str
    name: str
    description: str
    is_active: bool
    affinity_level: int
    affinity_name: str
    avatar_url: str | None
    tags: list[str]
    updated_at: datetime


class ProcessMessageResponse(BaseModel):
    character: CharacterDetail
    system_prompt: str


class MigrationResponse(BaseModel):
    total_migrated: int
    total_failed: int
    errors: list[tuple]
    active_character: str | None


class RollbackResponse(BaseModel):
    success: bool
    message: str


class MigrationStatusResponse(BaseModel):
    v2_table_exists: bool
    legacy_table_exists: bool
    v2_record_count: int
    legacy_record_count: int


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


class ApiResponse(BaseModel):
    code: int = 0
    data: Any = None
    message: str = ""
