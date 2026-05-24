from __future__ import annotations

import logging
import threading

logger = logging.getLogger("metrics")

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

_metrics: dict = {}
_lock = threading.Lock()


def _init_metrics():
    if not HAS_PROMETHEUS:
        return
    _metrics["chat_request_duration"] = Histogram(  # type: ignore
        "chat_request_duration_seconds",
        "Chat request total duration",
        ["model"],
    )
    _metrics["chat_token_usage"] = Counter(  # type: ignore
        "chat_token_usage_total",
        "Total tokens used",
        ["model", "type"],
    )
    _metrics["emotion_analysis_duration"] = Histogram(  # type: ignore
        "emotion_analysis_duration_seconds",
        "Emotion analysis duration",
    )
    _metrics["memory_retrieval_duration"] = Histogram(  # type: ignore
        "memory_retrieval_duration_seconds",
        "Memory retrieval duration",
        ["memory_type"],
    )
    _metrics["tool_call_duration"] = Histogram(  # type: ignore
        "tool_call_duration_seconds",
        "Tool call duration",
        ["tool_name"],
    )
    _metrics["tool_call_total"] = Counter(  # type: ignore
        "tool_call_total",
        "Tool call count",
        ["tool_name", "status"],
    )
    _metrics["proactive_message_sent"] = Counter(  # type: ignore
        "proactive_message_sent_total",
        "Proactive messages sent",
        ["trigger_type"],
    )
    _metrics["error_total"] = Counter(  # type: ignore
        "error_total",
        "Total errors",
        ["module", "error_type"],
    )
    _metrics["active_sessions"] = Gauge(  # type: ignore
        "active_sessions",
        "Currently active sessions",
    )


def setup_metrics(port: int = 9090):
    if not HAS_PROMETHEUS:
        logger.warning("prometheus_client not installed, metrics disabled")
        return
    with _lock:
        if not _metrics:
            _init_metrics()
    try:
        start_http_server(port)  # type: ignore
        logger.info("Prometheus metrics server started on port %d", port)
    except OSError:
        logger.warning("Metrics port %d already in use", port)


def record_chat_duration(model: str, duration: float):
    if _metrics.get("chat_request_duration"):
        _metrics["chat_request_duration"].labels(model=model).observe(duration)


def record_token_usage(model: str, token_type: str, count: int):
    if _metrics.get("chat_token_usage"):
        _metrics["chat_token_usage"].labels(model=model, type=token_type).inc(count)


def record_emotion_duration(duration: float):
    if _metrics.get("emotion_analysis_duration"):
        _metrics["emotion_analysis_duration"].observe(duration)


def record_memory_duration(memory_type: str, duration: float):
    if _metrics.get("memory_retrieval_duration"):
        _metrics["memory_retrieval_duration"].labels(memory_type=memory_type).observe(duration)


def record_tool_call(tool_name: str, duration: float, success: bool = True):
    if _metrics.get("tool_call_duration"):
        _metrics["tool_call_duration"].labels(tool_name=tool_name).observe(duration)
    if _metrics.get("tool_call_total"):
        status = "success" if success else "error"
        _metrics["tool_call_total"].labels(tool_name=tool_name, status=status).inc()


def record_proactive_message(trigger_type: str):
    if _metrics.get("proactive_message_sent"):
        _metrics["proactive_message_sent"].labels(trigger_type=trigger_type).inc()


def record_error(module: str, error_type: str):
    if _metrics.get("error_total"):
        _metrics["error_total"].labels(module=module, error_type=error_type).inc()


def set_active_sessions(count: int):
    if _metrics.get("active_sessions"):
        _metrics["active_sessions"].set(count)
