from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

try:
    import websockets
    from websockets.server import serve  # type: ignore[attr-defined]
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger("websocket_server")

MAX_CLIENTS = 1000


class WebSocketServer:
    def __init__(self, orchestrator=None, host: str = "0.0.0.0", port: int = 8765):
        self._orch = orchestrator
        self.host = host
        self.port = port
        self._clients: set = set()
        self._client_lock = asyncio.Lock()
        self._running = False
        # P0: API Key 认证配置
        self._api_key_enabled = os.environ.get("API_KEY_ENABLED", "false").lower() == "true"
        self._api_key = os.environ.get("API_KEY", "")

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

    def _verify_api_key(self, token: str) -> bool:
        """验证 API Key，参考 app_factory.py 的 _verify_api_key 实现"""
        if not self._api_key_enabled:
            return True
        import hmac
        return hmac.compare_digest(token or "", self._api_key)

    async def _handler(self, websocket):
        # P0: API Key 认证 - 检查 URL query 中的 token 参数
        token = None
        try:
            # 从 URL query 参数中获取 token
            path = websocket.request.path if hasattr(websocket.request, 'path') else str(websocket.request)
            if '?' in path:
                query = path.split('?', 1)[1]
                params = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
                token = params.get('token', '')
        except Exception:  # noqa: BLE001
            pass

        # 如果 URL 中没有 token，等待首条消息进行认证
        if not token and self._api_key_enabled:
            try:
                # 设置较短的超时时间等待认证消息
                auth_message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                auth_data = json.loads(auth_message)
                token = auth_data.get("token", "")
            except asyncio.TimeoutError:
                logger.warning("WebSocket 认证超时")
                await websocket.close(code=1008, reason="Authentication timeout")
                return
            except json.JSONDecodeError:
                logger.warning("WebSocket 认证消息格式错误")
                await websocket.close(code=1008, reason="Invalid authentication format")
                return

        # 验证 API Key
        if not self._verify_api_key(token):
            logger.warning("WebSocket 认证失败: 无效的 API Key")
            await websocket.close(code=1008, reason="Invalid or missing API key")
            return

        async with self._client_lock:
            if len(self._clients) >= MAX_CLIENTS:
                await websocket.close(code=1013, reason="连接已满")
                return
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
                        # P0: 支持 message_type 和 file_url 字段
                        message_type = data.get("message_type", "text")
                        file_url = data.get("file_url", "")

                        if use_stream and self._orch and hasattr(self._orch, 'process_message_stream'):
                            await websocket.send(json.dumps({
                                "type": "stream_start",
                                "session_id": session_id,
                                "message_type": message_type,
                            }))
                            async for token in self._orch.process_message_stream(user_msg, session_id):
                                await websocket.send(json.dumps({
                                    "type": "stream_token",
                                    "content": token,
                                    "message_type": message_type,
                                }, ensure_ascii=False))
                            await websocket.send(json.dumps({
                                "type": "stream_end",
                                "session_id": session_id,
                                "message_type": message_type,
                            }))
                        elif self._orch:
                            result = await self._orch.process_message(user_msg, session_id)
                            await websocket.send(json.dumps({
                                "type": "reply",
                                "content": result.get("reply", ""),
                                "emotion": result.get("emotion"),
                                "trace_id": result.get("trace_id", ""),
                                "message_type": message_type,
                                "file_url": file_url,
                            }, ensure_ascii=False))
                    elif msg_type == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                except Exception:
                    logger.exception("WebSocket handler error")
                    await websocket.send(json.dumps({"type": "error", "message": "internal_error"}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            async with self._client_lock:
                self._clients.discard(websocket)

    async def broadcast_proactive(self, content: str):
        msg = json.dumps({"type": "proactive", "content": content}, ensure_ascii=False)
        await self._parallel_broadcast(msg)

    async def broadcast_shisi_event(self, event_type: str, data: dict[str, Any]):
        msg = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        await self._parallel_broadcast(msg)

    async def _parallel_broadcast(self, message: str):
        disconnected = set()

        async def send_to_client(ws):
            try:
                await ws.send(message)
            except Exception:  # noqa: BLE001
                disconnected.add(ws)

        async with self._client_lock:
            clients = list(self._clients)

        if clients:
            await asyncio.gather(*[send_to_client(ws) for ws in clients], return_exceptions=True)

        if disconnected:
            async with self._client_lock:
                self._clients -= disconnected

    async def broadcast_character_switched(self, character_id: str, character_name: str):
        await self.broadcast_shisi_event("character_switched", {"character_id": character_id, "name": character_name})

    async def broadcast_emotion_stage_changed(self, character_id: str, old_stage: str, new_stage: str, affinity: float):
        await self.broadcast_shisi_event("emotion_stage_changed", {"character_id": character_id, "old_stage": old_stage, "new_stage": new_stage, "affinity": affinity})

    async def broadcast_affinity_changed(self, character_id: str, old_value: float, new_value: float):
        await self.broadcast_shisi_event("affinity_changed", {"character_id": character_id, "old_value": old_value, "new_value": new_value})

    async def broadcast_sticker_send(self, character_id: str, sticker_id: str, category: str):
        await self.broadcast_shisi_event("sticker_send", {"character_id": character_id, "sticker_id": sticker_id, "category": category})

    @property
    def client_count(self) -> int:
        return len(self._clients)
