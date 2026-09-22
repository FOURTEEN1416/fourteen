"""启动期通道会话同步的异步桥契约（2026-09-23 部署后日志实证根治）。

旧缺陷：`sync_disk_sessions_to_db` 是 async 包装却同步调用
`_sync_disk_sessions_to_db_sync()`（内部 `asyncio.run`），FastAPI lifespan
在运行中的事件循环里 await → 恒抛
`RuntimeError: asyncio.run() cannot be called from a running event loop`，
生产 app.log 每次 worker 启动刷一条（09-21 起累计 116 条）——
「磁盘通道会话 → DB」的启动同步**从未执行成功**。

突变验红：把异步入口改回同步调用 → 本文件用例转红。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest


def test_startup_sync_works_inside_running_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api.database as db
    from scripts import migrate_legacy_wechat_channel as mig
    from wechat_direct import channel_paths

    async def _init_db_noop() -> None:
        return None

    class _FakeSession:
        def add(self, obj: object) -> None:
            raise AssertionError("sessions_root 缺失时不应触达 DB 写入")

        async def commit(self) -> None:
            raise AssertionError("sessions_root 缺失时不应触达 DB 提交")

    monkeypatch.setattr(db, "init_db", _init_db_noop)
    monkeypatch.setattr(
        db, "_async_session", lambda: contextlib.nullcontext(_FakeSession())
    )
    monkeypatch.setattr(channel_paths, "sessions_root", lambda: tmp_path / "missing")

    count = asyncio.run(mig.sync_disk_sessions_to_db())
    assert count == 0
