from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger("main_routes")

# \u2014\u2014 \u5171\u4eab\u5e38\u91cf \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014

SENSITIVE_FIELDS = {
    "api_key", "secret", "token", "password", "encryption_key",
    "access_token", "refresh_token", "client_secret",
}
SENSITIVE_SUFFIXES = ("_api_key", "_secret", "_token", "_password", "_key")
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
    session_id: str = ""
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
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23, description="免打扰开始小时")
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23, description="免打扰结束小时")
    # （对话内追问可调字段已于 2026-10-12 退役，见 docs/DELETION_LOG.md：
    #   下一轮对话何时进行由 LLM 决策，Owner=proactive 决策链）
    # ── 回复模式（web 控制端可切换，2026-09-19）──
    reply_mode: str | None = Field(
        default=None, pattern="^(immersive|novel)$",
        description="immersive=沉浸式真人聊天（不写动作神态）；novel=小说式（带动作神态）",
    )
    # ── LLM 主动决策（web 可调，2026-09-21 用户裁决）──
    llm_proactive_enabled: bool | None = Field(default=None, description="是否启用 LLM 主动决策")
    llm_proactive_style_hint: str | None = Field(default=None, max_length=200, description="主动消息风格提示")
    llm_proactive_intensity: str | None = Field(
        default=None, pattern="^(low|normal|high)$",
        description="控制台力度提示：low/normal/high（注入 LLM，非硬闸）",
    )
    llm_proactive_respect_quiet: bool | None = Field(default=None, description="是否将免打扰时段写入 prompt 供 LLM 遵守")
    llm_proactive_character_hint: str | None = Field(default=None, max_length=200, description="角色补充提示")
    llm_proactive_jitter_seconds: int | None = Field(
        default=None, ge=0, le=900,
        description="主动等待窗随机抖动上限（秒）；0=关闭（T-4 仿真人机制）",
    )

class ToolToggleRequest(BaseModel):
    enabled: bool

# \u2014\u2014 \u8f93\u52a9\u51fd\u6570 \u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014

def _is_sensitive_config_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in SENSITIVE_FIELDS or normalized.endswith(SENSITIVE_SUFFIXES)


def _sanitize_config(value):
    """Recursively mask credentials without hiding harmless fields such as max_tokens."""
    if isinstance(value, dict):
        return {
            key: "****" if _is_sensitive_config_key(key) else _sanitize_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_config(item) for item in value]
    return value
