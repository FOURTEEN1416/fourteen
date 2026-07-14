"""
共享认证模块 - 统一的 API Key 验证

所有路由模块都应从此模块导入 verify_api_key_dep，而非自行定义。
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from typing import Any

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger("api.auth")

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


async def verify_api_key_dep(
    request: Request,
    api_key: str | None = Security(_api_key_header),
) -> bool:
    """统一的 API Key 验证依赖注入函数

    支持 header 或 URL query 参数传递 API Key（EventSource 等场景无法自定义 header）。
    所有 FastAPI 路由应使用此函数作为 Security 依赖。
    """
    with _auth_lock:
        enabled = _auth_config["enabled"]
        key = _auth_config["api_key"]

    if not enabled:
        # 认证未启用时放行，但记录警告（生产环境应通过配置启用）
        if os.getenv("ENVIRONMENT", "development") == "production":
            logger.warning("API 认证未启用，生产环境存在安全风险，请设置 AUTH_ENABLED=true")
        return True

    # 优先从 header 读取，其次从 query 参数（EventSource 场景）
    candidate = api_key or request.query_params.get("api_key") or ""
    if key and hmac.compare_digest(candidate, key):
        return True

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"X-Error-Code": "AUTH_ERROR"},
    )

__all__ = ["configure_auth", "update_auth_key", "verify_api_key_dep"]
