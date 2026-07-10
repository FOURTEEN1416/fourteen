"""单元测试: 会话锁管理"""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.session_locks import SessionLockManager


class TestSessionLockManager:
    """测试 SessionLockManager 核心功能"""

    @pytest.mark.asyncio
    async def test_get_lock_creates_new_lock(self) -> None:
        manager = SessionLockManager()
        lock = manager.get_lock("session_1")
        assert lock is not None
        assert isinstance(lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_same_session_returns_same_lock(self) -> None:
        manager = SessionLockManager()
        lock1 = manager.get_lock("session_1")
        lock2 = manager.get_lock("session_1")
        assert lock1 is lock2

    @pytest.mark.asyncio
    async def test_different_sessions_get_different_locks(self) -> None:
        manager = SessionLockManager()
        lock_a = manager.get_lock("session_a")
        lock_b = manager.get_lock("session_b")
        assert lock_a is not lock_b

    @pytest.mark.asyncio
    async def test_lock_acquire_and_release(self) -> None:
        manager = SessionLockManager()
        lock = manager.get_lock("session_1")
        async with lock:
            assert lock.locked()
        assert not lock.locked()

    @pytest.mark.asyncio
    async def test_concurrent_sessions_dont_block_each_other(self) -> None:
        """不同 session 的锁应互不干扰"""
        manager = SessionLockManager()
        results: list[str] = []

        async def task(session_id: str, delay: float) -> None:
            lock = manager.get_lock(session_id)
            async with lock:
                results.append(f"{session_id}_start")
                await asyncio.sleep(delay)
                results.append(f"{session_id}_end")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(task("s1", 0.05))
            tg.create_task(task("s2", 0.01))

        # s2 应该先完成，因为不同 session 的锁不互斥
        assert results[0] == "s1_start"
        assert results[1] == "s2_start"
        assert "s2_end" in results[:3]

    @pytest.mark.asyncio
    async def test_same_session_serializes(self) -> None:
        """同一 session 的锁应保证串行访问"""
        manager = SessionLockManager()
        in_flight = 0
        max_in_flight = 0

        async def task() -> None:
            nonlocal in_flight, max_in_flight
            lock = manager.get_lock("same_session")
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await asyncio.sleep(0.02)
                in_flight -= 1

        async with asyncio.TaskGroup() as tg:
            tg.create_task(task())
            tg.create_task(task())
            tg.create_task(task())

        # 同一 session 最多只有一个任务在锁内
        assert max_in_flight == 1

    @pytest.mark.asyncio
    async def test_active_lock_count(self) -> None:
        manager = SessionLockManager()
        assert manager.active_lock_count == 0
        manager.get_lock("session_1")
        assert manager.active_lock_count == 1
        manager.get_lock("session_2")
        assert manager.active_lock_count == 2
        manager.get_lock("session_1")
        assert manager.active_lock_count == 2  # 重复获取不增加计数

    def test_cleanup_does_not_crash_empty(self) -> None:
        """空 manager 调用清理不应崩溃"""
        manager = SessionLockManager()
        manager._cleanup_expired_locks(0.0)
        assert manager.active_lock_count == 0

    @pytest.mark.asyncio
    async def test_lock_not_bound_until_get(self) -> None:
        """锁在首次 get 时创建，构造函数不绑定事件循环"""
        manager = SessionLockManager()
        # 无需在 async 上下文中构造 manager
        assert manager.active_lock_count == 0
        lock = manager.get_lock("test")
        assert isinstance(lock, asyncio.Lock)
