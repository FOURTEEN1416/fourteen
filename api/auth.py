"""
共享认证模块 - 统一的 API Key 验证

所有路由模块都应从此模块导入 verify_api_key_dep，而非自行定义。
"""

from __future__ import annotations

import hmac
import threading
from typing import Any

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# 全局认证配置（可在运行时更新）
_auth_config: dict[str, Any] = {
    "enabled": False,
    "api_key": "",
}
_auth_lock = threading.Lock()


def configure_auth(enabled: bool, api_key: str) -> None:
    """在应用启动时设置认证参数"""
    with _auth_lock:
        _auth_config["enabled"] = enabled
        _auth_config["api_key"] = api_key


def update_auth_key(api_key: str) -> None:
    """运行时更新 API Key"""
    with _auth_lock:
        _auth_config["api_key"] = api_key


async def verify_api_key_dep(api_key: str | None = Security(_api_key_header)) -> bool:
    """统一的 API Key 验证依赖注入函数

    所有 FastAPI 路由应使用此函数作为 Security 依赖。
    """
    with _auth_lock:
        enabled = _auth_config["enabled"]
        key = _auth_config["api_key"]

    if not enabled:
        return True

    if hmac.compare_digest(api_key or "", key):
        return True

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"X-Error-Code": "AUTH_ERROR"},
    )
