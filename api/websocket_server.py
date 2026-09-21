from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

try:
    import websockets
    from websockets.asyncio.server import serve
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger("websocket_server")

MAX_CLIENTS = 1000


class WebSocketServer:
    def __init__(self, orchestrator=None, host: str = "127.0.0.1", port: int = 8765):
        self._orch = orchestrator
        self.host = host
        self.port = port
        self._clients: set = set()
        self._client_lock = asyncio.Lock()
        self._running = False
        self._server_done: asyncio.Future | None = None
        self._client_tasks: dict[Any, asyncio.Task] = {}
        # websocket → 认证身份 {"user_id": int|None, "method": "jwt"|"apikey"|"open"}
        self._client_identity: dict[Any, dict[str, Any]] = {}
        # 2026-09-22：websocket → 会话键归属（该连接最近一条 chat 消息的
        # session_id，已带 owner 前缀）。供提醒/主动消息**定向**投递——
        # 旧实现 web 侧只有 broadcast：A 的提醒广播给所有连接（跨用户可见），
        # 且零归属连接也能「代收」成送达。断开即清理。
        self._client_sessions: dict[Any, str] = {}
        # P0-5: 认证开关走唯一真源 resolve_api_key_enabled()（生产 fail-closed），
        # 不再自抄一份默认 "false" 的解析——否则 nginx 反代的 /ws/ 成匿名聊天入口。
        from api.runtime_config import resolve_api_key_enabled

        self._auth_required = resolve_api_key_enabled()
        self._api_key = os.environ.get("API_KEY", "")

    async def start(self):
        if not HAS_WEBSOCKETS:
            logger.warning("websockets not installed, WebSocket server disabled")
            return
        self._running = True
        self._server_done = asyncio.get_running_loop().create_future()
        try:
            async with serve(self._handler, self.host, self.port, ping_interval=30, ping_timeout=10):
                logger.info("WebSocket server started on %s:%d", self.host, self.port)
                try:
                    await self._server_done
                except asyncio.CancelledError:
                    logger.info("WebSocket server shutting down")
                    raise
        finally:
            self._running = False
            self._server_done = None

    async def stop(self):
        self._running = False

        # 触发 server 退出阻塞
        if self._server_done is not None and not self._server_done.done():
            self._server_done.set_result(None)

        # 取消所有客户端处理任务，避免任务泄漏
        tasks: list[asyncio.Task] = []
        async with self._client_lock:
            for task in list(self._client_tasks.values()):
                if not task.done():
                    task.cancel()
                    tasks.append(task)
            self._client_tasks.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # 关闭所有客户端连接
        close_tasks: list[asyncio.Task] = []
        async with self._client_lock:
            for ws in list(self._clients):
                close_tasks.append(asyncio.ensure_future(ws.close()))
            self._clients.clear()
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

    def _authenticate(self, api_token: str, jwt_token: str) -> dict[str, Any] | None:
        """认证并返回身份；None 表示拒绝。

        优先 JWT（web 用户，身份从 token 解出，客户端不可自报）；
        其次 API Key（机器/E2E）。未启用认证（显式 dev）时放行匿名。
        """
        if jwt_token:
            try:
                from api.auth_jwt import verify_token

                payload = verify_token(jwt_token, expected_type="access")
            except Exception:  # noqa: BLE001  verify_token 抛 HTTPException
                return None
            sub = payload.get("sub")
            try:
                user_id = int(sub) if sub is not None else None
            except (TypeError, ValueError):
                user_id = None
            if user_id is None:
                return None
            return {"user_id": user_id, "method": "jwt"}
        if not self._auth_required:
            return {"user_id": None, "method": "open"}
        import hmac

        if api_token and self._api_key and hmac.compare_digest(api_token, self._api_key):
            return {"user_id": None, "method": "apikey"}
        return None

    async def _handler(self, websocket):
        # P0-5: 凭证从 URL query（token=APIKey / jwt=Bearer）或首帧取，身份从 token 解出
        api_token = ""
        jwt_token = ""
        try:
            path = websocket.request.path if hasattr(websocket.request, 'path') else str(websocket.request)
            if '?' in path:
                query = path.split('?', 1)[1]
                params = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
                api_token = params.get('token', '') or ''
                jwt_token = params.get('jwt', '') or params.get('access_token', '') or ''
        except Exception as e:
            logger.debug("token parse failed, falling back: %s", e)

        identity = self._authenticate(api_token, jwt_token)
        # URL 无凭证且需要认证时，等待首帧认证
        if identity is None and self._auth_required and not jwt_token and not api_token:
            try:
                auth_message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                auth_data = json.loads(auth_message)
            except asyncio.TimeoutError:
                logger.warning("WebSocket 认证超时")
                await websocket.close(code=1008, reason="Authentication timeout")
                return
            except json.JSONDecodeError:
                logger.warning("WebSocket 认证消息格式错误")
                await websocket.close(code=1008, reason="Invalid authentication format")
                return
            api_token = str(auth_data.get("token", "") or "")
            jwt_token = str(auth_data.get("jwt", "") or auth_data.get("access_token", "") or "")
            identity = self._authenticate(api_token, jwt_token)

        if identity is None:
            logger.warning("WebSocket 认证失败：缺少有效 JWT/API Key")
            await websocket.close(code=1008, reason="Authentication required")
            return

        authed_user_id = identity["user_id"]

        async with self._client_lock:
            if len(self._clients) >= MAX_CLIENTS:
                await websocket.close(code=1013, reason="连接已满")
                return
            self._clients.add(websocket)
            self._client_tasks[websocket] = asyncio.current_task()
            self._client_identity[websocket] = identity

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "chat")
                    if msg_type == "chat":
                        user_msg = data.get("message", "")
                        session_id = data.get("session_id", "")
                        # P0-5: JWT 身份强制归属——服务端加用户前缀，客户端自报的
                        # session_id 无法伪装成他人（记忆/工具/LLM 配额按此隔离）。
                        if authed_user_id is not None:
                            session_id = f"{authed_user_id}:{session_id}"
                        # 登记连接的会话归属（定向投递映射，断开时清理）
                        if session_id:
                            async with self._client_lock:
                                self._client_sessions[websocket] = str(session_id)
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
                            stream_gen = self._orch.process_message_stream(user_msg, session_id)
                            ended = False
                            try:
                                async for event in stream_gen:
                                    # 兼容旧版返回字符串的生成器
                                    if isinstance(event, str):
                                        event = {"type": "token", "content": event}
                                    if event.get("type") == "token":
                                        await websocket.send(json.dumps({
                                            "type": "stream_token",
                                            "content": event.get("content", ""),
                                            "message_type": message_type,
                                        }, ensure_ascii=False))
                                    elif event.get("type") == "done":
                                        ended = True
                                        await websocket.send(json.dumps({
                                            "type": "stream_end",
                                            "session_id": session_id,
                                            "message_type": message_type,
                                            "reply": event.get("reply", ""),
                                            "emotion": event.get("emotion"),
                                        }, ensure_ascii=False))
                            finally:
                                with contextlib.suppress(Exception):
                                    await stream_gen.aclose()
                            if not ended:
                                # 生成器未产出 done（异常/提前结束）时的兜底收口帧；
                                # 正常完成路径不再补发第二帧（旧实现每轮双 stream_end，
                                # 前端按帧计数会闪断/重复收尾）。
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
                except websockets.exceptions.ConnectionClosed:
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("WebSocket handler error")
                    try:
                        await websocket.send(json.dumps({"type": "error", "message": "internal_error"}))
                    except websockets.exceptions.ConnectionClosed:
                        break
        except websockets.exceptions.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            logger.debug("WebSocket handler cancelled for %s", getattr(websocket, "id", ""))
            raise
        finally:
            async with self._client_lock:
                self._clients.discard(websocket)
                self._client_tasks.pop(websocket, None)
                self._client_identity.pop(websocket, None)
                self._client_sessions.pop(websocket, None)
            with contextlib.suppress(Exception):
                await websocket.close()

    async def send_proactive_to_session(self, session_key: str, content: str) -> int:
        """按会话键**定向**投递主动消息/提醒，返回实际送达连接数。

        2026-09-22：web 侧从「无归属广播」收口为定向——只有登记了该
        session_key（发过消息）的连接才算送达；0 送达由调用方判失败
        （提醒重试 3 次判死，与微信侧诚实度一致）。找不到归属连接也是 0。
        """
        msg = json.dumps({"type": "proactive", "content": content}, ensure_ascii=False)
        target = str(session_key or "")
        if not target:
            return 0
        async with self._client_lock:
            clients = [
                ws for ws, sid in self._client_sessions.items()
                if sid == target and ws in self._clients
            ]
        if not clients:
            return 0
        disconnected: set = set()
        delivered = 0

        async def _send(ws) -> bool:
            try:
                await ws.send(msg)
                return True
            except Exception:  # noqa: BLE001
                disconnected.add(ws)
                return False

        results = await asyncio.gather(*[_send(ws) for ws in clients])
        delivered = sum(1 for ok in results if ok)
        if disconnected:
            async with self._client_lock:
                self._clients -= disconnected
                for ws in disconnected:
                    self._client_sessions.pop(ws, None)
        return delivered

    async def broadcast_proactive(self, content: str):
        """主动消息投递 —— **真实送达语义**（2026-09-19 修复）。

        ⚠️ 旧实现零客户端也正常返回，于是 `_send_to_all()` 把「没有任何人收到」
        记成「主动消息已送达: websocket」并据此提交配额 —— 与微信通道
        `ret=-2 prepare failed` 被谎报是同一类问题（第六层）。
        现在：无客户端或全部客户端发送失败 → 抛异常，由调用方判定为未送达。

        其余 broadcast_*（事件通知）保持 fire-and-forget 语义，不受影响。
        """
        msg = json.dumps({"type": "proactive", "content": content}, ensure_ascii=False)
        delivered = await self._broadcast_count_delivered(msg)
        if delivered == 0:
            raise RuntimeError(
                f"websocket 主动消息未送达任何客户端（client_count={self.client_count}）"
            )

    async def _broadcast_count_delivered(self, message: str) -> int:
        """广播并返回**实际送达的客户端数**。

        与 `_parallel_broadcast` 的区别：本方法统计成功数而非吞掉结果，
        供「投递是否真实发生」的判定使用。
        """
        disconnected: set = set()

        async def send_to_client(ws) -> bool:
            try:
                await ws.send(message)
                return True
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(ws)
            except Exception:  # noqa: BLE001
                disconnected.add(ws)
            return False

        async with self._client_lock:
            clients = list(self._clients)
        if not clients:
            return 0
        results = await asyncio.gather(
            *[send_to_client(ws) for ws in clients], return_exceptions=True
        )
        delivered = sum(1 for r in results if r is True)
        if disconnected:
            async with self._client_lock:
                self._clients -= disconnected
        return delivered

    async def broadcast_shisi_event(self, event_type: str, data: dict[str, Any]):
        msg = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        await self._parallel_broadcast(msg)

    async def _parallel_broadcast(self, message: str):
        disconnected = set()

        async def send_to_client(ws):
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(ws)
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
