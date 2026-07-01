"""可观测性模块 — 日志、追踪、指标、配置、健康检查、优雅关闭"""

from observability.config_manager import ConfigManager
from observability.config_models import (
    APIConfig,
    CharacterCardConfig,
    EmotionConfig,
    LLMConfig,
    MemoryConfig,
    MemoryExtConfig,
    ObservabilityConfig,
    ProactiveConfig,
    SafetyConfig,
    SystemConfig,
    ToolsConfig,
    VoiceConfig,
)
from observability.graceful_shutdown import GracefulShutdown, graceful_shutdown
from observability.health import HealthChecker, health_checker
from observability.logging_setup import (
    RingBufferHandler,
    get_logger,
    get_session_id,
    get_trace_id,
    get_user_id,
    new_trace_id,
    ring_buffer,
    set_session_id,
    set_trace_id,
    set_user_id,
    setup_logging,
)
from observability.metrics import (
    record_chat_duration,
    record_emotion_duration,
    record_error,
    record_memory_duration,
    record_proactive_message,
    record_token_usage,
    record_tool_call,
    set_active_sessions,
    setup_metrics,
)
from observability.tracing import Tracer, TraceSpan, tracer

__all__ = [
    # Logging
    "setup_logging",
    "get_logger",
    "set_trace_id",
    "get_trace_id",
    "new_trace_id",
    "set_session_id",
    "get_session_id",
    "set_user_id",
    "get_user_id",
    "RingBufferHandler",
    "ring_buffer",
    # Config models
    "SystemConfig",
    "LLMConfig",
    "EmotionConfig",
    "MemoryConfig",
    "ProactiveConfig",
    "SafetyConfig",
    "ToolsConfig",
    "APIConfig",
    "ObservabilityConfig",
    "VoiceConfig",
    "CharacterCardConfig",
    "MemoryExtConfig",
    # Config manager
    "ConfigManager",
    # Tracing
    "Tracer",
    "TraceSpan",
    "tracer",
    # Metrics
    "setup_metrics",
    "record_chat_duration",
    "record_token_usage",
    "record_emotion_duration",
    "record_memory_duration",
    "record_tool_call",
    "record_proactive_message",
    "record_error",
    "set_active_sessions",
    # Graceful shutdown
    "GracefulShutdown",
    "graceful_shutdown",
    # Health check
    "HealthChecker",
    "health_checker",
]
