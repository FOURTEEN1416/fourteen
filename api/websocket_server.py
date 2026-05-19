from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

try:
    import websockets
    from websockets.server import serve
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger("websocket_server")


class WebSocketServer:
    def __init__(self, orchestrator=None, host: str = "0.0.0.0", port: int = 8765):
        self._orch = orchestrator
        self.host = host
        self.port = port
        self._clients: Set = set()
        self._running = False

    async def start(self):
        if not HAS_WEBSOCKETS:
            logger.warning("websockets not installed, WebSocket server disabled")
            return
        self._running = True
        async with serve(self._handler, self.host, self.port, ping_interval=30, ping_timeout=10):
            logger.info("WebSocket server started on %s:%d", self.host, self.port)
            await asyncio.Future()

    async def stop(self):
        self._running = False
        for ws in self._clients:
            await ws.close()
        self._clients.clear()

    async def _handler(self, websocket):
        self._clients.add(websocket)
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "chat")
                    if msg_type == "chat":
                        user_msg = data.get("message", "")
                        session_id = data.get("session_id", "")
                        use_stream = data.get("stream", False)

                        if use_stream and self._orch and hasattr(self._orch, 'process_message_stream'):
                            await websocket.send(json.dumps({
                                "type": "stream_start",
                                "session_id": session_id,
                            }))
                            async for token in self._orch.process_message_stream(user_msg, session_id):
                                await websocket.send(json.dumps({
                                    "type": "stream_token",
                                    "content": token,
                                }, ensure_ascii=False))
                            await websocket.send(json.dumps({
                                "type": "stream_end",
                                "session_id": session_id,
                            }))
                        elif self._orch:
                            result = self._orch.process_message(user_msg, session_id)
                            await websocket.send(json.dumps({
                                "type": "reply",
                                "content": result.get("reply", ""),
                                "emotion": result.get("emotion"),
                                "trace_id": result.get("trace_id", ""),
                            }, ensure_ascii=False))
                    elif msg_type == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                except Exception as e:
                    logger.error("WebSocket handler error: %s", e)
                    await websocket.send(json.dumps({"type": "error", "message": str(e)}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def broadcast_proactive(self, content: str):
        msg = json.dumps({"type": "proactive", "content": content}, ensure_ascii=False)
        for ws in self._clients:
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)
