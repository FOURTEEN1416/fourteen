from __future__ import annotations

import logging
import sys
import threading
import uuid
from contextvars import ContextVar

try:
    import structlog
    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_session_id: ContextVar[str] = ContextVar("session_id", default="")


def set_trace_id(trace_id: str) -> None:
    _trace_id.set(trace_id)


def get_trace_id() -> str:
    return _trace_id.get()


def new_trace_id() -> str:
    tid = str(uuid.uuid4())
    _trace_id.set(tid)
    return tid


def set_session_id(session_id: str) -> None:
    _session_id.set(session_id)


def get_session_id() -> str:
    return _session_id.get()


# In-memory log ring buffer for /api/logs endpoint
class RingBufferHandler(logging.Handler):
    """Keeps recent log records in memory (last 200)."""
    def __init__(self, capacity: int = 200):
        super().__init__()
        self.capacity = capacity
        self._records: list[dict] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "time": self.format(record),
            "level": record.levelname.lower(),
            "module": record.name,
            "msg": record.getMessage(),
        }
        with self._lock:
            self._records.append(entry)
            if len(self._records) > self.capacity:
                self._records.pop(0)

    def get_recent(self, limit: int = 100, level: str = "all", search: str = "") -> list[dict]:
        with self._lock:
            results = list(self._records)
        if level != "all":
            results = [r for r in results if r["level"] == level]
        if search:
            results = [r for r in results if search.lower() in r["msg"].lower()]
        return results[-limit:]


# Global log handler to be attached during setup
ring_buffer = RingBufferHandler()


def setup_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    if not HAS_STRUCTLOG:
        logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
        root_logger = logging.getLogger()
        root_logger.addHandler(ring_buffer)
        return

    shared_processors = [
        structlog.contextvars.merge_contextvars,  # type: ignore
        structlog.stdlib.add_log_level,  # type: ignore
        structlog.stdlib.add_logger_name,  # type: ignore
        _add_trace_info,
        structlog.processors.TimeStamper(fmt="iso"),  # type: ignore
        structlog.processors.StackInfoRenderer(),  # type: ignore
        structlog.processors.format_exc_info,  # type: ignore
    ]

    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()  # type: ignore
    else:
        renderer = structlog.dev.ConsoleRenderer()  # type: ignore

    structlog.configure(  # type: ignore
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,  # type: ignore
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),  # type: ignore
        wrapper_class=structlog.stdlib.BoundLogger,  # type: ignore
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(  # type: ignore
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,  # type: ignore
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.addHandler(ring_buffer)  # capture recent logs for API
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))


def _add_trace_info(logger, method, event_dict):
    trace_id = _trace_id.get("")
    session_id = _session_id.get("")
    if trace_id:
        event_dict["trace_id"] = trace_id
    if session_id:
        event_dict["session_id"] = session_id
    return event_dict


def get_logger(name: str):
    if HAS_STRUCTLOG:
        return structlog.get_logger(name)  # type: ignore
    return logging.getLogger(name)
