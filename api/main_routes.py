from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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


class TopicStateConfigRequest(BaseModel):
    """话题状态机配置的 partial merge 入参（2026-10-27 实施计划 §9.1）。

    11 个配置键全部显式建模，**不存在**“管理端改不到、代码里却生效”的隐藏
    硬编码；其中 Q1/Q4/Q5/Q6 的裁决固化项用 ``ge=le`` 锁死（judge_every_turns=4、
    retention_days=7、daily_llm_budget=0、park_idle_hours=6），越界一律 422。
    ``switch_theta2 < switch_theta1`` 的跨字段校验在
    ``utils.topic_state.write_topic_config`` 合并后执行，返回 400（允许只传一个
    theta，按当前另一值比较）。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = Field(default=None, description="话题状态机总开关")
    llm_judge_enabled: bool | None = Field(default=None, description="稀疏 LLM 判定开关（每 4 轮一次）")
    judge_every_turns: int | None = Field(default=None, ge=4, le=4, description="Q1 固化：每 4 轮判定一次")
    daily_llm_budget: int | None = Field(default=None, ge=0, le=0, description="Q5 固化：0=不设每日 LLM 上限")
    active_cap: int | None = Field(default=None, ge=1, le=3, description="Q6：并行 active 上限 1..3")
    retention_days: int | None = Field(default=None, ge=7, le=7, description="Q4 固化：全部状态保留 7 天")
    recall_after_hours: int | None = Field(default=None, ge=6, le=168, description="Q3：parked 复提间隔下限 6 小时")
    recall_max: int | None = Field(default=None, ge=1, le=2, description="Q3：单次最多复提 2 条")
    park_idle_hours: int | None = Field(default=None, ge=6, le=6, description="状态机固化：6 小时无活动转 parked")
    switch_theta1: float | None = Field(default=None, ge=0.0, le=1.0, description="接续判定阈值 θ1")
    switch_theta2: float | None = Field(default=None, ge=0.0, le=1.0, description="复提判定阈值 θ2（不得小于 θ1）")

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
