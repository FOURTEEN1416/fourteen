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

# 安全修复（审查 F-crit-1）：
# 公开仓不得把可签名的 JWT 密钥作为生产/未知环境的静默回退。
# - 生产：JWT_SECRET 必须来自环境且 >=32 字符，否则拒绝启动
# - 仅当 AI_GF_ENV/APP_ENV/ENV 显式为 dev/development 时，才允许 DEV 兜底密钥
# - 其它任何环境标记（缺失 / test / staging…）一律 fail-closed，要求注入 JWT_SECRET

# 仅允许在显式 dev 下使用的兜底密钥——公开仓可读，绝不可用于任何真实环境
_DEV_ONLY_JWT_SECRET = "dev-only-DO-NOT-USE-IN-PRODUCTION-32chars-ok-ok!"
_MIN_SECRET_LEN = 32

JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
from api.runtime_config import is_explicit_dev as _is_explicit_dev  # noqa: E402, I001
from api.runtime_config import is_production as _is_production  # noqa: E402

_IS_PROD = _is_production()
_IS_EXPLICIT_DEV = _is_explicit_dev()

_JWT_SETUP_MSG = (
    "JWT_SECRET is required. Generate with: openssl rand -base64 48 "
    "and export JWT_SECRET before start. "
    "Outside explicit dev/development (AI_GF_ENV|APP_ENV|ENV=dev|development) "
    "the process refuses to fall back to the public DEV secret. "
    f"Minimum length: {_MIN_SECRET_LEN} characters."
)

if not JWT_SECRET:
    if _IS_EXPLICIT_DEV:
        # 显式开发环境：允许 DEV 兜底，但大声警告（stdout+logger）
        JWT_SECRET = _DEV_ONLY_JWT_SECRET
        _warn = (
            "🚨🚨🚨 SECURITY WARNING 🚨🚨🚨\n"
            "JWT_SECRET is NOT set. Using PUBLIC DEV-ONLY signing key.\n"
            "This key is readable in the public repository — anyone can forge\n"
            "access/refresh tokens (including role=admin).\n"
            "Allowed ONLY because AI_GF_ENV/APP_ENV/ENV is explicitly dev/development.\n"
            "Production/staging MUST set JWT_SECRET (>=32 chars). "
            "Generate: openssl rand -base64 48"
        )
        print(_warn, flush=True)
        logger.warning(_warn)
    else:
        # 生产、未标记、test/staging/未知：一律拒绝启动（fail-closed）
        raise RuntimeError(
            f"Refusing to start: JWT_SECRET missing and env is not explicit dev/development "
            f"(AI_GF_ENV/APP_ENV/ENV). production={_IS_PROD} explicit_dev={_IS_EXPLICIT_DEV}. "
            + _JWT_SETUP_MSG
        )
elif len(JWT_SECRET) < _MIN_SECRET_LEN:
    raise ValueError(
        f"JWT_SECRET too short ({len(JWT_SECRET)} chars); minimum {_MIN_SECRET_LEN} chars required. "
        + _JWT_SETUP_MSG
    )
elif _IS_PROD and JWT_SECRET == _DEV_ONLY_JWT_SECRET:
    # 防御：即便有人显式把 DEV 字面量写进生产 JWT_SECRET 也拒绝
    raise RuntimeError(
        "Refusing to start: JWT_SECRET equals the public DEV-ONLY constant in production. "
        + _JWT_SETUP_MSG
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


async def get_current_user(
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI Security 依赖：返回当前登录用户对象（含 role 等元数据）。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


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
