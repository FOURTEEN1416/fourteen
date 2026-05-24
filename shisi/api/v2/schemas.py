"""API v2 请求/响应数据模型"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CreateCharacterRequest(BaseModel):
    name: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)


class UpdatePersonaRequest(BaseModel):
    warmth: Optional[float] = None
    playfulness: Optional[float] = None
    independence: Optional[float] = None
    jealousy: Optional[float] = None
    stubbornness: Optional[float] = None
    formality: Optional[float] = None
    emoji_frequency: Optional[float] = None
    sentence_length: Optional[float] = None
    emotional_expression: Optional[float] = None
    humor: Optional[float] = None
    core_anchors: Optional[List[str]] = None


class ProcessMessageRequest(BaseModel):
    message: str
    chat_history: str = ""


class CharacterDetail(BaseModel):
    id: str
    name: str
    description: str
    avatar_url: Optional[str]
    tags: List[str]
    persona: Dict[str, Any]
    emotional_state: Dict[str, Any]
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
    avatar_url: Optional[str]
    tags: List[str]
    updated_at: datetime


class ProcessMessageResponse(BaseModel):
    character: CharacterDetail
    system_prompt: str


class MigrationResponse(BaseModel):
    total_migrated: int
    total_failed: int
    errors: List[tuple]
    active_character: Optional[str]


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
