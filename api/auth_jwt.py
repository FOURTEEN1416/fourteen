"""
JWT 认证模块 — 用户登录 token + 密码安全

提供两个独立的面：
1. JWT 工具：create/verify access token 和 refresh token
2. 密码工具：hash 密码和验证密码

使用 python-jose (pyjose) + passlib bcrypt
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger("auth_jwt")

# ═══════════════════════════════════════════════════════
# 配置（可被环境变量覆写）
# ═══════════════════════════════════════════════════════

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-secret-change-in-production-32chars!")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_EXPIRE_DAYS", "7"))

# ═══════════════════════════════════════════════════════
# 密码工具
# ═══════════════════════════════════════════════════════

_pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    """将明文密码哈希为安全字符串"""
    return _pwd_ctx.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码 vs 哈希值"""
    return _pwd_ctx.verify(plain_password, hashed_password)


# ═══════════════════════════════════════════════════════
# JWT 工具
# ═══════════════════════════════════════════════════════


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """创建短期 access token（默认 30 分钟）"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """创建长期 refresh token（默认 7 天）"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """验证并解码 JWT token，失败抛 HTTPException"""
    try:
        payload: dict[str, Any] = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        token_type = payload.get("type")
        if token_type != expected_type:
            raise HTTPException(status_code=401, detail=f"Invalid token type (expected {expected_type})")
        return payload
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired") from None
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None


def hash_refresh_token(token: str) -> str:
    """对 refresh token 做 SHA-256 哈希，用于数据库存储比较"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════
# FastAPI Security 依赖
# ═══════════════════════════════════════════════════════

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> int:
    """FastAPI Security 依赖：从 access token 中提取当前用户 ID

    用法：
        @router.get("/me")
        async def me(user_id: int = Security(get_current_user_id)):
            ...
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header (Bearer token)")
    payload = verify_token(credentials.credentials, expected_type="access")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")
    return int(user_id)
