from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger("main_routes")

# \u2014\u2014 \u5171\u4eab\u5e38\u91cf \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014

SENSITIVE_FIELDS = {"api_key", "secret", "token", "password", "encryption_key", "api_base"}
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
MAX_UPLOAD_SIZE = min(
    int(os.environ.get("MAX_UPLOAD_SIZE", str(50 * 1024 * 1024))),
    100 * 1024 * 1024,
)
MAX_RAG_UPLOAD_SIZE = min(
    int(os.environ.get("MAX_RAG_UPLOAD_SIZE", str(10 * 1024 * 1024))),
    50 * 1024 * 1024,
)

# \u2014\u2014 \u8bf7\u6c42/\u54cd\u5e94\u6a21\u578b \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014

class ChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    session_id: str = Field(default="", max_length=128)
    message_type: str = Field(default="text", pattern=r"^(text|image|voice|file)$")
    character_id: str = Field(default="default", max_length=128)

class ChatResponse(BaseModel):
    reply: str
    trace_id: str = ""
    emotion: dict | None = None

class EmotionStateResponse(BaseModel):
    current_emotion: str = ""
    intensity: float = 0.0
    energy: float = 0.0
    affinity: float = 0.0

class CreateSessionRequest(BaseModel):
    user_id: str = "default"
    channel: str = "web"

class ConfigUpdateRequest(BaseModel):
    config: dict = Field(default_factory=dict, description="Partial config updates to merge")

class ProactiveConfigRequest(BaseModel):
    threshold: float | None = None
    max_daily: int | None = None
    min_interval_minutes: int | None = None
    cooldown_after_reply_minutes: int | None = None

class ToolToggleRequest(BaseModel):
    enabled: bool

# \u2014\u2014 \u8f93\u52a9\u51fd\u6570 \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014

def _sanitize_config(config_dict: dict) -> dict:
    """\u8165\u654f\u914d\u7f6e\u4e2d\u7684\u654f\u611f\u5b57\u6bb5"""
    sanitized = {}
    for k, v in config_dict.items():
        if isinstance(v, dict):
            sanitized[k] = _sanitize_config(v)
        elif k.lower() in SENSITIVE_FIELDS or any(s in k.lower() for s in SENSITIVE_FIELDS):
            sanitized[k] = "****"
        else:
            sanitized[k] = v
    return sanitized
