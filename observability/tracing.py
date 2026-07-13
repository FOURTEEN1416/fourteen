from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from observability.logging_setup import get_logger, get_trace_id, set_trace_id

logger = get_logger("tracing")

_TRACE_TTL = 1800  # 30 minutes

TRACE_NODES = [
    "message_received",
    "multimodal_preprocess",
    "input_safety_check",
    "pii_anonymize",
    "emotion_analyze",
    "emotion_state_update",
    "memory_retrieve",
    "rag_retrieve",
    "prompt_assemble",
    "prompt_injection_check",
    "llm_inference",
    "tool_call",
    "output_safety_check",
    "reply_send",
    "memory_store",
    "emotion_memory_record",
    "persona_evolution_eval",
    "reflection",
]


@dataclass
class TraceSpan:
    trace_id: str
    node: str
    start_time: float = 0.0
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def start(self):
        self.start_time = time.perf_counter()

    def end(self):
        self.duration_ms = (time.perf_counter() - self.start_time) * 1000


class Tracer:
    def __init__(self):
        self._active_traces: dict[str, list[TraceSpan]] = {}
        self._trace_start_times: dict[str, float] = {}
        self._traces_lock = threading.Lock()

    def start_trace(self, trace_id: str | None = None) -> str:
        tid = trace_id or str(uuid.uuid4())
        with self._traces_lock:
            self._cleanup_expired_locked()
            self._active_traces[tid] = []
            self._trace_start_times[tid] = time.time()
        set_trace_id(tid)
        return tid

    def end_trace(self, trace_id: str | None = None) -> dict[str, Any]:
        tid = trace_id or get_trace_id()
        with self._traces_lock:
            spans = self._active_traces.pop(tid, [])
            self._trace_start_times.pop(tid, None)
        total_ms = sum(s.duration_ms for s in spans)
        result = {
            "trace_id": tid,
            "total_duration_ms": round(total_ms, 2),
            "spans": [
                {
                    "node": s.node,
                    "duration_ms": round(s.duration_ms, 2),
                    "metadata": s.metadata,
                }
                for s in spans
            ],
        }
        logger.info("trace_completed", trace_id=tid, total_ms=round(total_ms, 2))
        return result

    @contextmanager
    def span(self, node: str, metadata: dict | None = None):
        trace_id = get_trace_id()
        if not trace_id:
            yield
            return
        span = TraceSpan(trace_id=trace_id, node=node, metadata=metadata or {})
        span.start()
        try:
            yield span
        finally:
            span.end()
            with self._traces_lock:
                if trace_id in self._active_traces:
                    self._active_traces[trace_id].append(span)

    def get_active_trace_id(self) -> str:
        return get_trace_id()

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Get active trace info by trace_id, cleaning up expired traces."""
        with self._traces_lock:
            self._cleanup_expired_locked()
            if trace_id not in self._active_traces:
                return None
            spans = self._active_traces[trace_id]
            return {
                "trace_id": trace_id,
                "spans": [
                    {
                        "node": s.node,
                        "duration_ms": round(s.duration_ms, 2),
                        "metadata": s.metadata,
                    }
                    for s in spans
                ],
            }

    def _cleanup_expired_locked(self) -> int:
        """Remove traces older than _TRACE_TTL. Must be called with lock held."""
        now = time.time()
        expired = [
            tid for tid, start in self._trace_start_times.items()
            if now - start > _TRACE_TTL
        ]
        for tid in expired:
            self._active_traces.pop(tid, None)
            self._trace_start_times.pop(tid, None)
        if expired:
            logger.warning("Cleaned up %d expired traces (TTL=%ds)", len(expired), _TRACE_TTL)
        return len(expired)


tracer = Tracer()
