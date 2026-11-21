from __future__ import annotations

import logging
import os
import sys
import threading
import uuid
from collections import deque
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

try:
    import structlog
    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_session_id: ContextVar[str] = ContextVar("session_id", default="")
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)


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


def set_user_id(user_id: int | None) -> None:
    _user_id.set(user_id)


def get_user_id() -> int | None:
    return _user_id.get()


# In-memory log ring buffer for /api/logs endpoint
class RingBufferHandler(logging.Handler):
    """Keeps recent log records in memory (last 200)."""
    def __init__(self, capacity: int = 200):
        super().__init__()
        self.capacity = capacity
        self._records: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "time": self.format(record),
            "level": record.levelname.lower(),
            "module": record.name,
            "msg": record.getMessage(),
            "user_id": getattr(record, "user_id", None),
        }
        with self._lock:
            self._records.append(entry)

    def get_recent(
        self,
        limit: int = 100,
        level: str = "all",
        search: str = "",
        user_id: int | None = None,
    ) -> list[dict]:
        with self._lock:
            results = list(self._records)
        if level != "all":
            results = [r for r in results if r["level"] == level]
        if search:
            results = [r for r in results if search.lower() in r["msg"].lower()]
        if user_id is not None:
            results = [r for r in results if r.get("user_id") == user_id]
        return results[-limit:]


class UserContextFilter(logging.Filter):
    """将当前请求上下文中的 user_id 注入到 LogRecord，用于后续按用户隔离日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.user_id = get_user_id()
        return True


# Global log handler to be attached during setup
ring_buffer = RingBufferHandler()
# 上下文过滤器必须挂在 **handler** 上：logging.Filter 挂在 logger 时只对
# 直接在该 logger 上产生的记录生效，子 logger propagate 上来的记录不经过
# 父 logger 的 filter → record.user_id 永不注入，/api/logs 按用户过滤恒为空。
ring_buffer.addFilter(UserContextFilter())


# 2026-11-21 可观测性批：第三方库高频 INFO 噪声压制。
# APScheduler 的 ``apscheduler.executors.default`` 每个 job 起止各打一条
# INFO；生产 app.log.1 55272 行中 53629 行（97.0%）来自它，其中 5 秒一次的
# 「控制命令消费」占 22443 行。job 级起止已由 proactive.scheduler 的
# ``_safe_job_wrapper`` 降为 DEBUG（仅慢 job 打 WARNING），这里再把库自身
# logger 压到 WARNING；ERROR/WARNING 不受影响。
_NOISY_LOGGERS: tuple[tuple[str, int], ...] = (
    ("apscheduler", logging.WARNING),
    ("apscheduler.scheduler", logging.WARNING),
    ("apscheduler.executors.default", logging.WARNING),
)


def _configure_noisy_loggers() -> None:
    """把高频第三方 logger 固定到 WARNING（ERROR/WARNING 仍完整保留）。"""
    for logger_name, level in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)


def _add_rotating_file_handler(root_logger: logging.Logger, log_level: int) -> None:
    """添加 10MB 轮转文件日志到 ./data/app.log"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)


def setup_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    level = getattr(logging, log_level, logging.INFO)
    # 先压第三方噪声：无论是否安装 structlog 都生效。
    _configure_noisy_loggers()

    if not HAS_STRUCTLOG:
        logging.basicConfig(level=level)
        root_logger = logging.getLogger()
        root_logger.addHandler(ring_buffer)
        _add_rotating_file_handler(root_logger, level)
        return

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_trace_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # 2026-11-21 可观测性批：structlog 的 BoundLogger 不会自动做
        # stdlib 风格的 ``event % args`` —— 旧实现把
        # ``logger.warning("Model %s ...", name, secs)`` 渲染成
        # ``{"event": "Model %s ...", "positional_args": [...]}``。放在
        # renderer 之前统一格式化，同型未渲染 dict 一并修复。
        structlog.stdlib.PositionalArgumentsFormatter(),
    ]

    renderer = (  # noqa: SIM108
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,  # type: ignore[list-item]
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,  # type: ignore[arg-type]
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.addHandler(ring_buffer)  # capture recent logs for API
    _add_rotating_file_handler(root_logger, level)
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
        return structlog.get_logger(name)
    return logging.getLogger(name)
