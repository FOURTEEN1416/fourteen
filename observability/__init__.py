"""可观测性模块 — 日志、追踪、指标、配置、健康检查、优雅关闭"""

from observability.logging_setup import setup_logging
from observability.logging_setup import get_logger
from observability.logging_setup import set_trace_id
from observability.logging_setup import get_trace_id
from observability.logging_setup import new_trace_id
from observability.logging_setup import set_session_id
from observability.logging_setup import get_session_id
from observability.logging_setup import RingBufferHandler
from observability.logging_setup import ring_buffer
from observability.config_models import SystemConfig
from observability.config_models import LLMConfig
from observability.config_models import EmotionConfig
from observability.config_models import MemoryConfig
from observability.config_models import ProactiveConfig
from observability.config_models import SafetyConfig
from observability.config_models import ToolsConfig
from observability.config_models import APIConfig
from observability.config_models import ObservabilityConfig
from observability.config_models import VoiceConfig
from observability.config_models import CharacterCardConfig
from observability.config_models import MemoryExtConfig
from observability.config_manager import ConfigManager
from observability.tracing import Tracer
from observability.tracing import TraceSpan
from observability.tracing import tracer
from observability.metrics import setup_metrics
from observability.metrics import record_chat_duration
from observability.metrics import record_token_usage
from observability.metrics import record_emotion_duration
from observability.metrics import record_memory_duration
from observability.metrics import record_tool_call
from observability.metrics import record_proactive_message
from observability.metrics import record_error
from observability.metrics import set_active_sessions
from observability.graceful_shutdown import GracefulShutdown
from observability.graceful_shutdown import graceful_shutdown
from observability.health import HealthChecker
from observability.health import health_checker

__all__ = [
    # Logging
    "setup_logging",
    "get_logger",
    "set_trace_id",
    "get_trace_id",
    "new_trace_id",
    "set_session_id",
    "get_session_id",
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
