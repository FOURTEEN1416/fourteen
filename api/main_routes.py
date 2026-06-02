"""
主路由模块 — 拆分后仅保留 模型 / 常量 / 辅助函数

业务路由已按域拆分为 8 个子路由文件（共 71 端点），本文件仅供子路由引用：

    子路由文件                    端点数   范围
    ─────────────────────────────────────────────────
    api._misc_routes.py             10   health/stats/memory/logs/config/channels/routes
    api._chat_routes.py             10   chat/session + wechat channels
    api._personality_routes.py       9   emotion/persona/psych
    api._users_routes.py             7   users/*
    api._training_routes.py         11   training/* + proactive/*
    api._tools_routes.py             5   tools/* + plugins/*
    api._safety_routes.py           12   safety/rag/voice/files/cache
    api._clone_routes.py             7   clone/*

本文件导出：
- 共享常量：SENSITIVE_FIELDS / UPLOAD_DIR / MAX_UPLOAD_SIZE / MAX_RAG_UPLOAD_SIZE
- 共享 Helper：_sanitize_config
- 6 个 Pydantic 请求/响应模型
- 空 router（向后兼容，避免任何 `from api.main_routes import router` 调用崩溃）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("main_routes")

# ── 共享常量 ────────────────────────────────────────────

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

# ── 请求/响应模型 ─────────────────────────────────────


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


# ── 辅助函数 ──────────────────────────────────────────


def _sanitize_config(config_dict: dict) -> dict:
    """脱敏配置中的敏感字段"""
    sanitized = {}
    for k, v in config_dict.items():
        if isinstance(v, dict):
            sanitized[k] = _sanitize_config(v)
        elif k.lower() in SENSITIVE_FIELDS or any(s in k.lower() for s in SENSITIVE_FIELDS):
            sanitized[k] = "****"
        else:
            sanitized[k] = v
    return sanitized


# ── 向后兼容的占位 router（无任何端点） ───────────────────
# 旧的代码可能仍 `from api.main_routes import router as main_router`，
# 保留空 router 让 import 不崩。新 app_factory.py 已不再挂载它。

router = APIRouter(tags=["main"])
