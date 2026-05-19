from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from observability.logging_setup import get_logger, get_trace_id, set_trace_id, new_trace_id

logger = get_logger("tracing")

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
    metadata: Dict[str, Any] = field(default_factory=dict)

    def start(self):
        self.start_time = time.perf_counter()

    def end(self):
        self.duration_ms = (time.perf_counter() - self.start_time) * 1000


class Tracer:
    def __init__(self):
        self._active_traces: Dict[str, List[TraceSpan]] = {}

    def start_trace(self, trace_id: Optional[str] = None) -> str:
        tid = trace_id or str(uuid.uuid4())
        self._active_traces[tid] = []
        set_trace_id(tid)
        return tid

    def end_trace(self, trace_id: Optional[str] = None) -> Dict[str, Any]:
        tid = trace_id or get_trace_id()
        spans = self._active_traces.pop(tid, [])
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
    def span(self, node: str, metadata: Optional[Dict] = None):
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
            if trace_id in self._active_traces:
                self._active_traces[trace_id].append(span)

    def get_active_trace_id(self) -> str:
        return get_trace_id()


tracer = Tracer()
