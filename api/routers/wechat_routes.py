from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, Security

logger = logging.getLogger("rest_api.wechat")

router = APIRouter(prefix="/api", tags=["wechat"])

_orch = None
_gf = None
_verify_api_key = None


def set_dependencies(orch, gf, verify_api_key):
    global _orch, _gf, _verify_api_key
    _orch = orch
    _gf = gf
    _verify_api_key = verify_api_key


def _get_wechat_connector():
    from wechat_direct import get_connector
    return get_connector()


_wechat_lock = threading.Lock()


@router.post("/channels/wechat/connect")
async def manual_connect_wechat(_auth: bool = Security(_verify_api_key)):
    conn = _get_wechat_connector()
    if conn and conn.token:
        return {"status": "connected", "message": "微信已连接"}

    def _do_connect():
        try:
            from wechat_direct import WeChatConnector
            connector = WeChatConnector(_orch)
            connector.run()
        except Exception as e:
            logger.exception("微信连接失败: %s", e)

    thread = threading.Thread(target=_do_connect, daemon=True)
    thread.start()

    return {"status": "connecting", "message": "微信连接已触发，请看终端/页面二维码扫码登录"}


@router.post("/channels/wechat/disconnect")
async def manual_disconnect_wechat(_auth: bool = Security(_verify_api_key)):
    conn = _get_wechat_connector()
    if conn:
        conn.stop()
    return {"status": "disconnected", "message": "微信已断开"}


@router.get("/channels/wechat/connection-status")
async def get_wechat_connection_status(_auth: bool = Security(_verify_api_key)):
    conn = _get_wechat_connector()
    if conn and conn.token:
        return {"status": "connected", "message": "已连接", "started_at": conn.started_at}
    return {"status": "idle", "message": "未连接"}


@router.get("/channels/wechat/status")
async def get_wechat_status(_auth: bool = Security(_verify_api_key)):
    conn = _get_wechat_connector()
    if conn and conn.token:
        return {
            "connected": True,
            "uptime_seconds": time.time() - conn.started_at if conn.started_at else 0,
            "bot_id": conn.bot_id,
        }
    return {"connected": False, "uptime_seconds": 0}


@router.post("/channels/wechat/reconnect")
async def reconnect_wechat(_auth: bool = Security(_verify_api_key)):
    conn = _get_wechat_connector()
    if conn and conn.token:
        conn.stop()

    def _do_reconnect():
        import wechat_direct.connector as wc
        from wechat_direct import WeChatConnector
        time.sleep(1)
        wc._clear_credentials()
        new_conn = WeChatConnector(_gf)
        new_conn.run()

    thread = threading.Thread(target=_do_reconnect, daemon=True)
    thread.start()
    return {"status": "reconnecting"}
