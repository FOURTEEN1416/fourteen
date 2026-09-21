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
      - 有运行中事件循环 -> 投递到**进程级常驻循环**（见 :func:`run_on_shared_loop`）

    Args:
        coro: 要运行的协程对象

    Returns:
        协程的返回值

    注意（2026-09-21 审查修订）:
        1. 有循环分支此前是「临时线程 + ``asyncio.run``」——协程里缓存的
           ``asyncio`` 原语（Lock / Queue / httpx 连接池）会绑定在一个**用完即弃**
           的循环上，跨调用唤醒失灵、连接池反复重建。现统一投递到常驻循环。
           唯一例外：调用方**已在常驻循环内部**时无法再向它投递（必死锁），
           只能退回一次性循环。
        2. 该函数是**同步**入口，无论哪条分支都会阻塞调用线程。协程内的调用方
           必须直接 ``await``，不得经此函数（在事件循环线程上调用会冻结该循环）。
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中事件循环
        return asyncio.run(coro)

    if _shared_loop is not None and _shared_loop is running:
        # 已在常驻循环内部：向自身投递 = 死锁，退回一次性循环
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return run_on_shared_loop(coro)


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

