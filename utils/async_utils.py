"""公共异步工具模块

提供跨模块复用的异步运行工具，避免各处重复实现
"有/无事件循环"的兼容逻辑。
"""

from __future__ import annotations

import asyncio
import logging
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
