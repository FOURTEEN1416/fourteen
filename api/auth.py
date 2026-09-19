"""
共享认证模块 - 统一的 API Key 验证

所有路由模块都应从此模块导入 verify_api_key_dep，而非自行定义。
"""

from __future__ import annotations

import hmac
import logging
import threading
from typing import Any

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from api.runtime_config import is_production

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
    """统一认证：优先放行**有效 JWT**（控制台用户路径，不把 API Key 打进前端）。

    2026-09-19 裁决：用户侧 API 仅 JWT；API Key 保留给机器/脚本/E2E。
    - 有效 Bearer access token → 通过
    - 否则若 API_KEY_ENABLED 且 X-API-Key 匹配 → 通过
    - API_KEY_ENABLED=false → 保持原放行（生产告警）
    """
    # 1) JWT 优先：控制台登录用户无需携带全局 API Key
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            from api.auth_jwt import verify_token

            verify_token(auth_header[7:].strip(), expected_type="access")
            return True
        except HTTPException:
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("JWT 校验失败，回落 API Key: %s", e)

    with _auth_lock:
        enabled = _auth_config["enabled"]
        key = _auth_config["api_key"]

    if not enabled:
        if is_production():
            logger.warning(
                "API 认证未启用，生产环境存在安全风险，"
                "请设置 API_KEY_ENABLED=true 并配置 API_KEY（见 .env.example）"
            )
        return True

    candidate = api_key or request.query_params.get("api_key") or ""
    if key and hmac.compare_digest(candidate, key):
        return True

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"X-Error-Code": "AUTH_ERROR"},
    )

__all__ = ["configure_auth", "update_auth_key", "verify_api_key_dep"]
