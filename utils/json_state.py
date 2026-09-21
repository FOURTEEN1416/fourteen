"""JSON 状态文件持久化 —— 唯一真源。

为什么存在：项目里 `data/*.json` 这类**运行时状态文件**（亲密度、重要日期、
回复模式、ASE 索引、调度配置 …）曾被 5 处各自实现为
「读 → 改 → 整体覆写」，且**原子性与失败语义各不相同**：

  - `utils/reply_mode.py`   ：tmp + replace（原子）
  - `proactive/ase_hub.py`  ：tmp + replace，但读改写**不在同一把锁内**（丢更新）
  - `utils/affinity_state.py`：直接 `write_text`（写一半崩 → 全量数据损坏）
  - `utils/important_dates.py`：直接 `write_text`（同上）
  - `proactive/scheduler.py`：tmp + replace

本模块提供三个原语，新增状态文件一律走这里，不再各自造：

  - :func:`read_json`        —— 容错读（缺失/损坏返回默认值）
  - :func:`atomic_write_json` —— 同目录临时文件 + ``os.replace``（读者永不看到半写）
  - :func:`update_json`       —— **锁内**读改写（进程内锁 + POSIX 跨进程 flock）

并发口径：生产为 uvicorn 多 worker（每 worker 独立进程），故「读改写」必须
跨进程互斥；Windows 开发机无 ``fcntl``，退化为进程内锁（与
``wechat_direct/connector_registry`` 的既有约定一致）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("utils.json_state")

_process_locks: dict[str, threading.Lock] = {}
_process_locks_guard = threading.Lock()


def _process_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _process_locks_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _process_locks[key] = lock
        return lock


@contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """跨进程互斥（POSIX ``flock``）；无 flock 平台退化为进程内锁。"""
    lock_path = target.with_name(target.name + ".lock")
    fd: int | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:  # noqa: BLE001
        logger.warning("打开状态文件锁失败 %s: %s", lock_path, e)
        fd = None
    if fd is None:
        yield
        return
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:
            pass  # Windows 开发机：单进程场景，进程内锁已足够
        yield
    finally:
        with contextlib.suppress(ImportError, OSError):
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


def read_json(path: str | Path, default: Any = None) -> Any:
    """容错读。文件缺失/损坏/类型不符时返回 ``default``（不抛异常）。"""
    p = Path(path)
    try:
        if not p.exists():
            return default
        raw = json.loads(p.read_text(encoding="utf-8"))
        if default is not None and isinstance(default, dict) and not isinstance(raw, dict):
            return default
        return raw
    except Exception as e:  # noqa: BLE001
        logger.warning("读取状态文件失败 %s: %s", p, e)
        return default


def atomic_write_json(path: str | Path, data: Any) -> None:
    """原子写：同目录 ``.tmp`` + ``os.replace``。

    写失败时抛异常（由调用方决定是「吞掉降级」还是「上抛」）。同目录是硬要求 ——
    跨文件系统 ``replace`` 退化为拷贝，原子性即丢失。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def update_json(
    path: str | Path,
    mutate: Callable[[dict[str, Any]], dict[str, Any] | None],
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """锁内「读 → 改 → 原子写」。

    ``mutate`` 原地修改传入的 dict 即可；返回非 None 时以其返回值作为新内容。
    读改写全程持锁 —— 旧实现把「读」放在锁外，多 worker 并发时后写者会
    用陈旧快照覆盖先写者（丢更新）。
    """
    p = Path(path)
    with _process_lock(p), _file_lock(p):
        data = read_json(p, default=dict(default or {}))
        if not isinstance(data, dict):
            data = dict(default or {})
        result = mutate(data)
        if result is not None:
            data = result
        atomic_write_json(p, data)
        return data
