"""
SSE / WebSocket 连接生命周期测试

验证：客户端断开或异常时，生成器被正确关闭、后台任务被取消、资源被释放。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api import app_factory, auth
from api.websocket_server import HAS_WEBSOCKETS, WebSocketServer


class _TrackedAsyncGen:
    """可跟踪 aclose 是否被调用的异步生成器。"""

    def __init__(self, items=None, raise_on_send=None):
        self.items = items or []
        self.raise_on_send = raise_on_send
        self.closed = False
        self._iter = iter(self.items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        try:
            item = next(self._iter)
            if self.raise_on_send:
                raise self.raise_on_send
            return item
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        self.closed = True


def _make_app_with_mock_orch(gen):
    orch = MagicMock()
    orch.process_message_stream = MagicMock(return_value=gen)
    app = app_factory.create_api_app(orchestrator=orch)
    app.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    return app, orch


def test_sse_chat_stream_closes_generator_on_disconnect():
    """客户端提前断开时，process_message_stream 异步生成器必须被 aclose。"""
    gen = _TrackedAsyncGen(items=[{"type": "token", "content": "hello"}])
    app, _orch = _make_app_with_mock_orch(gen)

    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hi", "session_id": "s1"},
        headers={"X-API-Key": "dummy"},
    ) as response:
        # 读取首条数据后立即离开上下文，模拟客户端断开
        _ = next(response.iter_text())

    # TestClient 离开上下文后会触发生成器清理
    assert gen.closed, "async generator was not closed after client disconnect"


def test_sse_demo_stream_closes_generator_on_disconnect():
    """Demo SSE 客户端断开时，process_message_stream 异步生成器必须被 aclose。"""
    gen = _TrackedAsyncGen(items=[{"type": "token", "content": "demo"}])
    app, _orch = _make_app_with_mock_orch(gen)

    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/demo/chat/stream",
        json={"message": "hi", "session_id": "s2"},
    ) as response:
        _ = next(response.iter_text())

    assert gen.closed, "demo async generator was not closed after client disconnect"


@pytest.mark.asyncio
async def test_websocket_handler_closes_stream_generator_on_disconnect():
    """WebSocket 流式响应中途连接断开时，生成器必须被 aclose。"""
    if not HAS_WEBSOCKETS:
        pytest.skip("websockets not installed")

    stream_gen = _TrackedAsyncGen(items=[{"type": "token", "content": "tok1"}])
    orch = MagicMock()
    orch.process_message_stream = MagicMock(return_value=stream_gen)

    server = WebSocketServer(orchestrator=orch)

    # 模拟一个会在第二次 send 时断开的 websocket
    class FakeWebSocket:
        def __init__(self):
            self.closed = False
            self.close_code = None
            self.close_reason = None
            self.messages = []
            self._send_count = 0
            self.request = MagicMock()
            self.request.path = "/"

        async def recv(self):
            return json.dumps({"type": "chat", "message": "hi", "session_id": "ws1", "stream": True})

        async def send(self, msg):
            self._send_count += 1
            self.messages.append(msg)
            if self._send_count >= 2:
                import websockets.exceptions
                self.closed = True
                raise websockets.exceptions.ConnectionClosed(
                    rcvd=None, sent=None
                )

        async def close(self, code=1000, reason=""):
            self.closed = True
            self.close_code = code
            self.close_reason = reason

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(0)
            return json.dumps({"type": "chat", "message": "hi", "session_id": "ws1", "stream": True})

    ws = FakeWebSocket()
    await server._handler(ws)

    assert stream_gen.closed, "websocket stream generator was not closed on disconnect"
    assert ws.closed


@pytest.mark.asyncio
async def test_websocket_stop_cancels_client_tasks_and_closes_connections():
    """stop() 应取消所有客户端处理任务并关闭连接。"""
    if not HAS_WEBSOCKETS:
        pytest.skip("websockets not installed")

    server = WebSocketServer()

    class FakeWebSocket:
        def __init__(self):
            self.closed = False
            self.request = MagicMock()
            self.request.path = "/"

        async def recv(self):
            await asyncio.Event().wait()

        async def send(self, _msg):
            pass

        async def close(self, code=1000, reason=""):
            self.closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.recv()

    ws = FakeWebSocket()

    async def run_handler():
        await server._handler(ws)

    task = asyncio.create_task(run_handler())
    # 等待 handler 把客户端加入集合
    for _ in range(50):
        await asyncio.sleep(0)
        if server.client_count:
            break

    assert server.client_count == 1
    assert not task.done()

    await server.stop()

    assert task.done()
    assert ws.closed
    assert server.client_count == 0
