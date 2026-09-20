"""同步↔异步桥接契约回归测试（2026-09-20 批次）。

背景：项目里存在多处「在同步上下文里跑协程」的桥接实现。其中
``shisi/memory/legacy/vector_memory._run_async`` 的「已处于事件循环中」分支用
``asyncio.run_coroutine_threadsafe(coro, loop).result()`` —— 而 ``loop`` 取自
``asyncio.get_running_loop()``（**当前线程正在运行的那个循环**），同线程阻塞等待
该循环推进结果 = **必然死锁**。现统一委托公共真源 ``utils.async_utils.run_async``。

本文件钉住：① 委托关系（静态防护，防回退）；② 两条分支都能真实返回结果；
③ 在事件循环内调用不会挂死（守护线程 + 超时判定）。
"""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHISI_VM = ROOT / "shisi" / "memory" / "legacy" / "vector_memory.py"


def _call_within_running_loop(fn, timeout: float = 10.0):
    """在工作线程内的**运行中事件循环**里调用 fn，返回 (是否完成, 结果/异常)。"""
    import asyncio

    box: dict = {"done": False}

    def _worker() -> None:
        async def _inner():
            return fn()

        try:
            box["value"] = asyncio.run(_inner())
        except BaseException as e:  # noqa: BLE001
            box["error"] = e
        finally:
            box["done"] = True

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    return (not t.is_alive()), box


def test_vector_memory_run_async_delegates_to_shared_helper() -> None:
    """静态防护：`_run_async` 必须委托公共真源，不得再自带同线程循环桥接。

    只检查**函数体代码**（用 AST 剥掉 docstring / 注释）——修复说明里会引用旧写法
    作为反面教材，按纯文本 grep 会误判。
    """
    import ast

    tree = ast.parse(SHISI_VM.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_run_async"
    )
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # 剥掉 docstring
    code = "\n".join(ast.unparse(stmt) for stmt in body)
    assert "run_coroutine_threadsafe" not in code, (
        "_run_async 不得再用 run_coroutine_threadsafe(...同一线程的 loop...).result()"
        "（同线程阻塞等待自身循环 = 死锁）"
    )
    assert "run_async" in code, "_run_async 应委托 utils.async_utils.run_async"


def test_run_async_from_sync_context_returns_value() -> None:
    from utils.async_utils import run_async

    async def _coro() -> str:
        return "ok"

    assert run_async(_coro()) == "ok"


def test_run_async_inside_running_loop_does_not_hang() -> None:
    from utils.async_utils import run_async

    async def _coro() -> str:
        return "inside"

    finished, box = _call_within_running_loop(lambda: run_async(_coro()))
    assert finished, "在运行中的事件循环里调用 run_async 挂死（未在超时内返回）"
    assert box.get("value") == "inside"


def test_vector_memory_run_async_inside_running_loop_does_not_hang() -> None:
    from shisi.memory.legacy.vector_memory import _run_async

    async def _coro() -> int:
        return 42

    finished, box = _call_within_running_loop(lambda: _run_async(_coro()))
    assert finished, "vector_memory._run_async 在运行中的事件循环里挂死"
    assert box.get("value") == 42


def test_run_async_is_a_plain_function() -> None:
    """`run_async` 本身是同步函数（供同步调用点使用）。"""
    from utils.async_utils import run_async

    assert not inspect.iscoroutinefunction(run_async)
