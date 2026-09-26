from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("metrics")

# 多进程聚合目录默认值：uvicorn --workers N 下每个 worker 进程独立计数，
# PROMETHEUS_MULTIPROC_DIR 指向同一共享目录，由 /api/metrics 聚合读取，
# 不再每 worker 争 start_http_server 固定端口。
_DEFAULT_MULTIPROC_DIR = Path(__file__).parent.parent / "data" / "cache" / "prom_multiproc"

# 🔑 prometheus_client 的 ValueClass 在其**首次 import** 时按
# PROMETHEUS_MULTIPROC_DIR 定死，事后设置 env 无效。因此必须在 import
# prometheus_client 之前落定目录——本模块是全仓该库的唯一 import 点
# （llm_gateway 等经此模块间接 import），在此设默认值即覆盖所有入口。
# 显式配置（部署方自设 env）不被覆盖。
if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
    try:
        _DEFAULT_MULTIPROC_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(_DEFAULT_MULTIPROC_DIR)
    except OSError:
        pass  # 只读文件系统等：退化为进程内计数（仅当前进程可见，不聚合）

try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

_metrics: dict = {}
_lock = threading.Lock()

# 模块私有注册表：避免与进程内其他 import 实例在默认 REGISTRY 上重复注册
_REGISTRY = CollectorRegistry() if HAS_PROMETHEUS else None


def _gauge_kwargs() -> dict:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return {"multiprocess_mode": "livesum"}
    return {}


def _aggregated_registry():
    """多进程模式 → CollectorRegistry + MultiProcessCollector；否则私有注册表。"""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    assert _REGISTRY is not None
    return _REGISTRY


def _init_metrics():
    if not HAS_PROMETHEUS:
        return
    _metrics["chat_request_duration"] = Histogram(        "chat_request_duration_seconds",
        "Chat request total duration",
        ["model"],
        registry=_REGISTRY,
    )
    _metrics["chat_token_usage"] = Counter(        "chat_token_usage_total",
        "Total tokens used",
        ["model", "type"],
        registry=_REGISTRY,
    )
    _metrics["emotion_analysis_duration"] = Histogram(        "emotion_analysis_duration_seconds",
        "Emotion analysis duration",
        registry=_REGISTRY,
    )
    _metrics["memory_retrieval_duration"] = Histogram(        "memory_retrieval_duration_seconds",
        "Memory retrieval duration",
        ["memory_type"],
        registry=_REGISTRY,
    )
    _metrics["tool_call_duration"] = Histogram(        "tool_call_duration_seconds",
        "Tool call duration",
        ["tool_name"],
        registry=_REGISTRY,
    )
    _metrics["tool_call_total"] = Counter(        "tool_call_total",
        "Tool call count",
        ["tool_name", "status"],
        registry=_REGISTRY,
    )
    _metrics["proactive_message_sent"] = Counter(        "proactive_message_sent_total",
        "Proactive messages sent",
        ["trigger_type"],
        registry=_REGISTRY,
    )
    _metrics["error_total"] = Counter(        "error_total",
        "Total errors",
        ["module", "error_type"],
        registry=_REGISTRY,
    )
    _metrics["active_sessions"] = Gauge(        "active_sessions",
        "Currently active sessions",
        registry=_REGISTRY,
        **_gauge_kwargs(),
    )
    _metrics["llm_provider_status"] = Counter(
        "llm_provider_status_total",
        "LLM provider call outcomes in multi-provider gateway",
        ["provider", "status"],
        registry=_REGISTRY,
    )


def setup_metrics(port: int = 9090):
    """控制台入口（main.py）装配：聚合注册表 + 固定端口 HTTP 服务。

    多进程 ValueClass 已在模块 import 时按 env 生效，因此 HTTP 服务必须
    挂聚合注册表（MultiProcessCollector），否则抓到的是空注册表。
    """
    if not HAS_PROMETHEUS:
        logger.warning("prometheus_client not installed, metrics disabled")
        return
    with _lock:
        if not _metrics:
            _init_metrics()
    try:
        start_http_server(port, registry=_aggregated_registry())
        logger.info("Prometheus metrics server started on port %d", port)
    except OSError:
        logger.warning("Metrics port %d already in use", port)


def setup_metrics_for_api(multiproc_dir: str | None = None) -> str:
    """API 生产入口装配（uvicorn 多 worker，lifespan 内调用）：

    - 多进程模式目录在模块 import 时已落定（见文件头注释）；此处确保目录存在
      并完成指标对象创建（子进程 lazy）；不启动固定端口 HTTP 服务——聚合由
      /api/metrics 端点按需读取；
    - multiproc_dir 参数仅供在 prometheus_client 首次 import 前调用的场景
      覆盖目录；生产部署请直接设置 PROMETHEUS_MULTIPROC_DIR 环境变量；
    - 返回生效的多进程目录（未安装 prometheus_client 时返回空串）。
    """
    if not HAS_PROMETHEUS:
        logger.warning("prometheus_client not installed, metrics disabled")
        return ""
    d = Path(multiproc_dir or os.environ.get("PROMETHEUS_MULTIPROC_DIR") or _DEFAULT_MULTIPROC_DIR)
    d.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", str(d))
    with _lock:
        if not _metrics:
            _init_metrics()
    logger.info("Prometheus 多进程计量已装配（聚合目录 %s，经 /api/metrics 汇总）", d)
    return str(d)


def cleanup_multiproc_dir(multiproc_dir: str | None = None) -> int:
    """清理上次运行残留的 worker 计数文件（重启后死进程文件会让计数虚高）。

    最佳努力：多 worker 并发启动时用独占锁保证只有一个 worker 执行清理；
    平台无 fcntl 时跳过（单进程开发场景无残留问题）。
    """
    d = Path(multiproc_dir or os.environ.get("PROMETHEUS_MULTIPROC_DIR") or _DEFAULT_MULTIPROC_DIR)
    if not d.is_dir():
        return 0
    lock_fd: int | None = None
    try:
        import fcntl  # type: ignore[import-not-found,attr-defined]

        lock_path = d / ".cleanup.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined,union-attr]
        except OSError:
            os.close(lock_fd)
            return 0  # 其他 worker 正在清理
    except ImportError:
        lock_fd = None  # 无 fcntl（Windows 单进程开发态）：无多 worker 竞态，直接清理
    except Exception as e:  # noqa: BLE001 — 清理失败不阻断装配
        logger.warning("multiproc 目录清理失败（忽略）: %s", e)
        return 0
    try:
        removed = 0
        for f in d.iterdir():
            if f.is_file() and f.name != ".cleanup.lock":
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed
    finally:
        if lock_fd is not None:
            import fcntl  # type: ignore[import-not-found,attr-defined]

            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)  # type: ignore[attr-defined,union-attr]
            finally:
                os.close(lock_fd)


def collect_metrics_text() -> str:
    """聚合读取 Prometheus 文本格式指标。

    多进程模式（设置了 PROMETHEUS_MULTIPROC_DIR）时经 MultiProcessCollector
    汇总所有 worker；单进程模式读私有注册表。
    """
    if not HAS_PROMETHEUS:
        return ""
    from prometheus_client import generate_latest

    return generate_latest(_aggregated_registry()).decode("utf-8", errors="replace")


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


def record_provider_fallback(provider: str, status: str):
    """记录 multi-provider gateway 中每个 provider 的调用结果。

    status: "success" | "fallback" | "error"
    - success: 该 provider 成功响应
    - fallback: 该 provider 返回错误信息（以"（"开头），触发降级到下一个
    - error: 该 provider 抛出异常
    """
    if _metrics.get("llm_provider_status"):
        _metrics["llm_provider_status"].labels(provider=provider, status=status).inc()
