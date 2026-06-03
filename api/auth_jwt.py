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
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import User, get_db

logger = logging.getLogger("auth_jwt")

# ═══════════════════════════════════════════════════════
# 配置（可被环境变量覆写）
# ═══════════════════════════════════════════════════════

# 修复 P0-2：旧代码用 dev-jwt-secret-change-in-production-32chars! 弱默认
# 生产忘配 JWT_SECRET → 任何人都能用此 secret 伪造 admin token

# 仅用于 dev/test 兜底——生产环境必须显式提供，且 >=32 字符
_DEV_ONLY_JWT_SECRET = "dev-only-DO-NOT-USE-IN-PRODUCTION-32chars-ok-ok!"
_MIN_SECRET_LEN = 32

JWT_SECRET = os.environ.get("JWT_SECRET", "")
_IS_PROD = os.environ.get("ENV", os.environ.get("APP_ENV", "")).lower() in ("prod", "production")

if _IS_PROD and (not JWT_SECRET or len(JWT_SECRET) < _MIN_SECRET_LEN):
    raise RuntimeError(
        f"JWT_SECRET must be set and >={_MIN_SECRET_LEN} chars in production. "
        f"Generate with: openssl rand -base64 48"
    )

if not JWT_SECRET:
    # dev/test 兜底：保留可启动能力，但日志高强度警告
    JWT_SECRET = _DEV_ONLY_JWT_SECRET
    logger.warning(
        "🔓 JWT_SECRET 未设置，使用 DEV-ONLY 默认值（不安全）。"
        "生产环境必须显式设置 JWT_SECRET>=32 字符，否则任何用户可伪造 token。"
    )
elif len(JWT_SECRET) < _MIN_SECRET_LEN:
    raise ValueError(
        f"JWT_SECRET too short ({len(JWT_SECRET)} chars); minimum {_MIN_SECRET_LEN} chars required"
    )

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_EXPIRE_DAYS", "7"))

# ═══════════════════════════════════════════════════════
# 密码工具
# ═══════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """将明文密码哈希为安全字符串

    绕过 passlib（1.7.4 与 bcrypt 5.0.0 不兼容），直接调 bcrypt。
    """
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码 vs 哈希值

    绕过 passlib，直接调 bcrypt.checkpw。
    """
    return _bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ═══════════════════════════════════════════════════════
# JWT 工具
# ═══════════════════════════════════════════════════════


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """创建短期 access token（默认 30 分钟）"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access", "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """创建长期 refresh token（默认 7 天）"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh", "jti": str(uuid.uuid4())})
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


def require_role(required_role: str):
    """Factory: 返回一个 FastAPI 依赖，校验当前用户是否拥有指定角色。

    用法:
        @router.get("/admin/users")
        async def list_users(
            _admin: tuple[int, User] = Depends(require_role("admin")),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """
    async def _role_checker(
        user_id: int = Security(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ) -> tuple[int, User]:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.role != required_role:
            raise HTTPException(
                status_code=403,
                detail=f"{required_role} access required",
            )
        return user_id, user
    return _role_checker
