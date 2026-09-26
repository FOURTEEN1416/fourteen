"""会话锁管理 — per-session 异步锁系统，支持 TTL 过期清理"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

from utils.project_paths import project_path

logger = logging.getLogger("session_locks")

# Session锁缓存配置：最大缓存数、锁过期时间（秒）
_MAX_SESSION_LOCKS = 1000
_SESSION_LOCK_TTL_SECONDS = 3600  # 1小时无使用后清理
_LOCK_ROOT = Path(project_path("data", "session_locks"))


class ProcessSessionLock(asyncio.Lock):
    """同主机worker共用OS锁；异步非阻塞重试，取消/崩溃自动释放文件描述符。"""

    def __init__(self, session_id: str, namespace: str = "turn"):
        super().__init__()
        self._path = _LOCK_ROOT / (namespace + "-" + hashlib.sha256(session_id.encode()).hexdigest() + ".lock")
        self._fd: int | None = None
        self._thread_gate = threading.Lock()
        self._queued = 0

    async def acquire(self):
        deadline = time.monotonic() + 60
        self._queued += 1
        try:
            while not self._thread_gate.acquire(blocking=False):
                if time.monotonic() >= deadline:
                    raise TimeoutError("同会话排队超时")
                await asyncio.sleep(0.02)
        finally:
            self._queued -= 1
        self._locked = True
        fd = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            deadline = time.monotonic() + 60
            while True:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("同会话跨worker排队超时") from None
                    await asyncio.sleep(0.05)
        except BaseException:
            if fd is not None:
                os.close(fd)
            super().release()
            self._thread_gate.release()
            raise

    def release(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        super().release()
        self._thread_gate.release()


class SessionLockManager:
    """per-session 异步锁管理器

    设计原则：
    1. 每个 session 一个 asyncio.Lock，不同 session 可完全并行处理
    2. 同一 session 的消息串行处理，保证情感引擎状态一致性
    3. 带TTL的锁缓存，防止session过多导致内存无限增长

    线程安全：使用 threading.Lock 保护内部字典
    """

    def __init__(self) -> None:
        self._session_locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._session_lock_access_time: dict[str, float] = {}
        self._locks_mutex = threading.Lock()
        self._lock_cleanup_counter: int = 0

    def get_lock(self, session_id: str) -> asyncio.Lock:
        """获取 per-session 异步锁，确保不同 session 可并行处理。

        注意: asyncio.Lock 必须在 async 上下文中创建以绑定正确的事件循环。
        采用延迟创建策略，首次在 async 上下文中调用时才实例化 Lock。
        """
        current_time = time.time()

        with self._locks_mutex:
            # 清理过期锁（每100次访问触发一次清理，避免频繁清理）
            self._lock_cleanup_counter = (self._lock_cleanup_counter + 1) % 100
            if len(self._session_locks) >= _MAX_SESSION_LOCKS or \
               (len(self._session_locks) > 0 and self._lock_cleanup_counter == 0):
                self._cleanup_expired_locks(current_time)

            # 检查是否已存在该session的锁
            if session_id in self._session_locks:
                lock, _ = self._session_locks[session_id]
                self._session_lock_access_time[session_id] = current_time
                return lock

            # 延迟创建：确保 Lock 绑定到当前运行的事件循环
            new_lock = ProcessSessionLock(session_id)

            self._session_locks[session_id] = (new_lock, current_time)
            self._session_lock_access_time[session_id] = current_time
            return new_lock

    def _cleanup_expired_locks(self, current_time: float) -> None:
        """清理过期的session锁，防止内存无限增长"""
        expired_sessions = []
        for sid, (lock, created_time) in self._session_locks.items():
            if lock.locked() or getattr(lock, "_waiters", None) or getattr(lock, "_queued", 0):
                continue
            last_access = self._session_lock_access_time.get(sid, created_time)
            if current_time - last_access > _SESSION_LOCK_TTL_SECONDS:
                expired_sessions.append(sid)

        for sid in expired_sessions:
            del self._session_locks[sid]
            if sid in self._session_lock_access_time:
                del self._session_lock_access_time[sid]

        # 如果仍然超过最大限制，清理最久未访问的
        if len(self._session_locks) >= _MAX_SESSION_LOCKS:
            sorted_sessions = sorted(
                self._session_lock_access_time.items(),
                key=lambda x: x[1]
            )
            sessions_to_remove = len(self._session_locks) - _MAX_SESSION_LOCKS + 100
            for sid, _ in sorted_sessions[:sessions_to_remove]:
                if sid in self._session_locks:
                    lock = self._session_locks[sid][0]
                    if lock.locked() or getattr(lock, "_waiters", None) or getattr(lock, "_queued", 0):
                        continue
                    del self._session_locks[sid]
                del self._session_lock_access_time[sid]

        if expired_sessions:
            logger.debug("清理 %d 个过期session锁，当前总数: %d", len(expired_sessions), len(self._session_locks))

    @property
    def active_lock_count(self) -> int:
        return len(self._session_locks)
