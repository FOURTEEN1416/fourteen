"""公共异步工具模块

提供跨模块复用的异步运行工具，避免各处重复实现
"有/无事件循环"的兼容逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


def run_async(coro) -> Any:
    """安全运行协程，支持有/无事件循环两种情况。

    设计:
      - 无运行中事件循环 -> asyncio.run()
      - 有运行中事件循环 -> 在新线程中新建事件循环运行（阻塞等待结果）

    Args:
        coro: 要运行的协程对象

    Returns:
        协程的返回值

    注意:
        如果需要在已有事件循环中以 fire-and-forget 方式调度协程，
        请直接使用 asyncio.ensure_future()，而非此函数。
    """
    try:
        asyncio.get_running_loop()
        # 有运行中事件循环，不能直接 asyncio.run
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        # 无运行中事件循环
        return asyncio.run(coro)


# ── 进程级共享常驻事件循环（P1-27，2026-09-21 审查修复）──────────
#
# 同步线程（微信消息 worker 等）反复 `asyncio.run` 会**每条消息一个新循环**，
# 而被调用方缓存的 asyncio 原语（如 orchestrator 的 per-session asyncio.Lock）
# 绑定在**首次使用时**的循环上——跨循环 waiter 唤醒失灵：轻则误判失败，
# 重则线程占死。此类调用方必须收敛到**同一条**常驻循环。

_shared_loop: asyncio.AbstractEventLoop | None = None
_shared_loop_lock = threading.Lock()


def get_shared_loop() -> asyncio.AbstractEventLoop:
    """返回进程级常驻事件循环（守护线程 run_forever，先确认线程就绪再发布）。"""
    global _shared_loop  # noqa: PLW0603
    if _shared_loop is not None and _shared_loop.is_running():
        return _shared_loop
    with _shared_loop_lock:
        if _shared_loop is None or not _shared_loop.is_running():
            loop = asyncio.new_event_loop()
            started = threading.Event()

            def _serve() -> None:
                loop.call_soon(started.set)
                loop.run_forever()

            threading.Thread(
                target=_serve, name="shared-async-loop", daemon=True,
            ).start()
            if not started.wait(timeout=5.0):
                logger.warning("shared-async-loop 线程 5s 未就绪（首调用可能延迟）")
            _shared_loop = loop
    return _shared_loop


def run_on_shared_loop(coro, timeout: float | None = None) -> Any:
    """把协程投递到共享常驻循环并阻塞取结果（同步线程专用）。

    timeout 到期抛 concurrent.futures.TimeoutError（任务仍在共享循环上跑完）。
    """
    loop = get_shared_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        coro.close()
        raise RuntimeError("不能在共享循环内部同步阻塞等待共享循环自身（必死锁）")
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)

