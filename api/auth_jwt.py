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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, Request, Security
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

# ═══════════════════════════════════════════════════════
# 唯一认证主体（W1，2026-09-27）
# ═══════════════════════════════════════════════════════
#
# 旧实现把「token 能验签」当成「账号可用」：任何一处只调 verify_token 的入口都会
# 放行**已停用 / 已改密 / 已删除**账号的旧 token，且 scope 直接读 token 里的 role
# 声明（降权后仍按旧 role 全量读）。此处收口为单一主体：
#
#   存在性 + is_active + 当前 role（DB 现值）+ 撤销版本（token `tv` ↔ users.token_version）
#
# 一次校验后交给路由；路由**不得**再独立解码 token 取 role。

_TV_CLAIM = "tv"


def token_claims(user: User) -> dict[str, Any]:
    """签发 token 的声明唯一 owner（不含 role：角色一律以库内现值为准）。"""
    return {
        "sub": str(user.id),
        "email": user.email,
        _TV_CLAIM: int(user.token_version or 0),
    }


def bump_token_version(user: User) -> int:
    """撤销该用户**已签发**的全部 access/refresh（自增撤销版本）。

    调用方负责 commit。语义（W1 明确）：
    - 改密 / 管理员重置 / 停用 / 删除 → 自增（旧 token 立即失效）
    - 降权 / 升权 → **不**自增（角色每请求读库内现值，即时收窄，无需重登）
    """
    user.token_version = int(user.token_version or 0) + 1
    return user.token_version


def _assert_token_version(payload: dict[str, Any], user: User) -> None:
    if int(payload.get(_TV_CLAIM, 0) or 0) != int(user.token_version or 0):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked (credentials changed after it was issued)",
        )


async def _load_enabled_user(db: AsyncSession, user_id: int) -> User:
    """账号可用性校验：不存在 → 404，已停用 → 401。

    `is_active` 条件写在 SQL 里（而非取回对象后再判），停用账号直接查不出行。
    """
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    exists = await db.execute(select(User.id).where(User.id == user_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")
    raise HTTPException(status_code=401, detail="Account is disabled")


@dataclass(frozen=True)
class AuthPrincipal:
    """唯一认证主体 — 路由只从这里取身份与角色。"""

    user_id: int
    role: str
    token_version: int
    user: User


async def _resolve_principal(
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
) -> AuthPrincipal | None:
    if credentials is None:
        return None
    payload = verify_token(credentials.credentials, expected_type="access")
    sub = payload.get("sub")
    if sub is None:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")
    user_id = int(sub)
    user = await _load_enabled_user(db, user_id)
    _assert_token_version(payload, user)
    return AuthPrincipal(
        user_id=user_id,
        role=user.role,
        token_version=int(user.token_version or 0),
        user=user,
    )


async def resolve_principal_from_request(
    request: Request,
    db: AsyncSession,
) -> AuthPrincipal | None:
    """从原始请求解析唯一主体（供共享依赖在非 DI 上下文复用，单一提取路径）。

    无 Bearer → None（机器 / 匿名面）；有 Bearer 则必须合法（否则 401）。
    """
    auth = str(request.headers.get("Authorization") or "")
    credentials: HTTPAuthorizationCredentials | None = None
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        if token:
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    return await _resolve_principal(credentials, db)


async def get_auth_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthPrincipal:
    """必备主体：无 Bearer / token 无效 / 账号停用 / 版本过期 → 401。"""
    principal = await resolve_principal_from_request(request, db)
    if principal is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header (Bearer token)")
    request.state.auth_principal = principal
    return principal


async def get_optional_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthPrincipal | None:
    """可选主体：无 Bearer → None（机器 / 匿名面，保持既有契约）。

    有 Bearer 则**必须**是合法主体（无效 / 停用 / 版本过期一律 401）——不允许
    无效 token 借「可选」通道退化成机器全权限。
    """
    principal = await resolve_principal_from_request(request, db)
    request.state.auth_principal = principal
    return principal


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> int:
    """FastAPI Security 依赖：从 access token 中提取当前用户 ID。

    统一校验：存在性（404）+ is_active（401）+ 撤销版本（401）。
    用法：
        @router.get("/me")
        async def me(user_id: int = Security(get_current_user_id)):
            ...
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header (Bearer token)")
    payload = verify_token(credentials.credentials, expected_type="access")
    sub = payload.get("sub")
    if sub is None:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")
    user_id = int(sub)
    _assert_token_version(payload, await _load_enabled_user(db, user_id))
    return user_id


async def get_current_user(
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI Security 依赖：返回当前登录用户对象（含 role 等元数据）。

    身份与撤销版本已在 `get_current_user_id` 一次校验（它是本依赖的 uid 来源）；
    此处只按 uid 取行并确认账号仍可用。角色以**库内现值**为准（token 内不再
    携带 role 声明，旧 token 里的 role 是陈旧副本）。
    """
    return await _load_enabled_user(db, user_id)


def require_role(required_role: str):
    """Factory: 返回一个 FastAPI 依赖，校验当前用户是否拥有指定角色。

    角色取自数据库当前值——降权后旧 token 立即失去管理端权限（无需等 token 过期）。
    uid 来源仍是 `get_current_user_id`（其内部完成存在性 / is_active / 撤销版本
    校验），因此本依赖**不**再独立解码 token。

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
        user = await _load_enabled_user(db, user_id)
        if user.role != required_role:
            raise HTTPException(
                status_code=403,
                detail=f"{required_role} access required",
            )
        return user_id, user
    return _role_checker
