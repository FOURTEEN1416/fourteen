"""
用户认证 API — 注册 / 登录 / 刷新令牌 / 登出

所有端点挂载于 /api/auth，与现有 X-API-Key 认证系统并行共存。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import (
    create_access_token,
    create_refresh_token,
    get_current_user_id,
    hash_password,
    hash_refresh_token,
    require_role,
    verify_password,
    verify_token,
)
from api.consent import (
    CURRENT_AGREEMENT_VERSION,
    has_consented,
    latest_consent,
    record_consent,
)
from api.database import User, UserSession, get_db

logger = logging.getLogger("auth_routes")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ═══════════════════════════════════════════════════════
# 请求 / 响应模型
# ═══════════════════════════════════════════════════════


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=255, examples=["user@example.com"])
    username: str = Field(..., min_length=2, max_length=100, examples=["demo"])
    password: str = Field(..., min_length=8, max_length=128, examples=["password123"])
    display_name: str = Field("", max_length=255)


class LoginRequest(BaseModel):
    login: str = Field(..., examples=["user@example.com", "demo"])
    password: str = Field(..., examples=["password123"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict
    # 使用即同意（W2-CONSENT）：True 表示该用户尚未同意当前版本协议，前端需弹全屏同意窗
    needs_consent: bool = False
    agreement_version: str = CURRENT_AGREEMENT_VERSION


class RefreshRequest(BaseModel):
    refresh_token: str = ""  # 可选：优先从 body 读，无则退到 cookie


class LogoutRequest(BaseModel):
    refresh_token: str = ""  # 可选：优先从 body 读，无则退到 cookie


class ConsentRequest(BaseModel):
    agreement_version: str = Field(..., examples=["1.0.0"])


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128, examples=["oldPass123"])
    new_password: str = Field(..., min_length=8, max_length=128, examples=["newPass456"])


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128, examples=["resetPass789"])


# ═══════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════


def validate_password_strength(password: str) -> str | None:
    """验证密码强度，返回错误信息或 None"""
    if len(password) < 8:
        return "密码长度至少8位"
    if not re.search(r'[A-Za-z]', password):
        return "密码必须包含字母"
    if not re.search(r'\d', password):
        return "密码必须包含数字"
    return None


def _set_refresh_cookie(response: Response, refresh_token: str, request: Request) -> None:
    """设置 refresh_token httpOnly cookie（Option A：安全最佳实践）"""
    # 仅在 HTTPS 下启用 Secure 标志，本地开发 HTTP 也 OK
    secure = request.url.scheme == "https"
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 天（与 refresh token 有效期一致）
        path="/api/auth",
    )


def _get_refresh_token(request: Request, body_token: str) -> str:
    """从 body 或 cookie 获取 refresh_token"""
    if body_token:
        return body_token
    cookie_token = request.cookies.get("refresh_token", "")
    if cookie_token:
        return cookie_token
    return ""


# ═══════════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════════


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """注册新用户（邮箱、用户名、密码），完成后直接返回令牌"""
    # 检查邮箱是否已注册
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # 检查用户名是否已注册
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    # 验证密码强度
    pwd_error = validate_password_strength(req.password)
    if pwd_error:
        raise HTTPException(status_code=422, detail=pwd_error)

    # 创建用户
    user = User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
        display_name=req.display_name or req.username,
        role="viewer",     # 新注册用户默认 viewer
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # 生成令牌
    token_data = {"sub": str(user.id), "email": user.email, "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # 存储 refresh token 哈希
    _save_refresh_token(db, int(user.id), refresh_token)
    await db.commit()

    logger.info("新用户注册: %s (%s)", user.email, user.username)
    _set_refresh_cookie(response, refresh_token, request)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
        needs_consent=True,  # 新用户必然未同意过协议
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """使用邮箱或用户名 + 密码登录，返回令牌"""
    # 查找用户（支持邮箱或用户名）
    result = await db.execute(
        select(User).where(
            (User.email == req.login) | (User.username == req.login)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid login credentials")

    # 验证密码
    if not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid login credentials")

    # 检查账号是否激活
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    # 生成令牌
    token_data = {"sub": str(user.id), "email": user.email, "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # 存储 refresh token 哈希
    _save_refresh_token(db, int(user.id), refresh_token)

    # 更新最后登录时间
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("用户登录: %s", user.email)
    _set_refresh_cookie(response, refresh_token, request)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
        needs_consent=not await has_consented(db, int(user.id)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    req: RefreshRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """使用 refresh token 换取新的 access token + refresh token

    优先从 body 读 refresh_token，如果 body 为空则退到 httpOnly cookie。
    """
    raw_refresh = _get_refresh_token(request, req.refresh_token)
    if not raw_refresh:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    payload = verify_token(raw_refresh, expected_type="refresh")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 验证 refresh token 在数据库中是否存在
    token_hash = hash_refresh_token(raw_refresh)
    result = await db.execute(
        select(UserSession).where(
            UserSession.refresh_token_hash == token_hash,
            UserSession.user_id == int(user_id),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # 检查是否过期
    if session.is_expired():
        await db.delete(session)
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token has expired")

    # 删除旧 session，生成新令牌
    await db.delete(session)

    # 查找用户（result 变量重命名，避免与上方 UserSession Result 联合误判）
    user_result = await db.execute(select(User).where(User.id == int(user_id)))
    refresh_user = user_result.scalar_one_or_none()
    if not refresh_user or not refresh_user.is_active:
        await db.commit()
        raise HTTPException(status_code=401, detail="User not found or disabled")

    # 生成新令牌
    token_data = {"sub": str(refresh_user.id), "email": refresh_user.email, "role": refresh_user.role}
    new_access_token = create_access_token(token_data)
    new_refresh_token = create_refresh_token(token_data)

    _save_refresh_token(db, int(refresh_user.id), new_refresh_token)
    await db.commit()

    logger.info("Refresh token 成功刷新: user=%s", user_id)
    _set_refresh_cookie(response, new_refresh_token, request)
    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        user=refresh_user.to_dict(),
        needs_consent=not await has_consented(db, int(refresh_user.id)),
    )


@router.post("/logout")
async def logout(
    req: LogoutRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """登出 — 吊销 refresh token + 清除 httpOnly cookie"""
    raw_refresh = _get_refresh_token(request, req.refresh_token)
    if raw_refresh:
        token_hash = hash_refresh_token(raw_refresh)
        result = await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
        session = result.scalar_one_or_none()
        if session:
            await db.delete(session)
            await db.commit()

    # 清除 httpOnly cookie
    response.delete_cookie(
        key="refresh_token",
        path="/api/auth",
        httponly=True,
    )

    return {"detail": "Logged out successfully"}


@router.get("/me")
async def me(
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录的用户信息"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.to_dict()


@router.post("/consent")
async def consent(
    req: ConsentRequest,
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """记录用户对《用户协议与隐私声明》的同意（使用即同意，W2-CONSENT）

    - 版本必须等于当前协议版本，否则 422（防止旧版本号绕过重新同意）
    - 时间戳由服务端 UTC 生成
    - 幂等：重复同意同版本返回已有记录，不重复落库
    """
    if req.agreement_version != CURRENT_AGREEMENT_VERSION:
        raise HTTPException(
            status_code=422,
            detail=f"Agreement version mismatch: expected {CURRENT_AGREEMENT_VERSION}",
        )

    # 用户必须真实存在（get_current_user_id 只解 token 不查库）
    result = await db.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    # 幂等：已同意同版本则直接返回
    if await has_consented(db, user_id):
        existing = await latest_consent(db, user_id)
        if existing:
            return {
                "detail": "Consent already recorded",
                "agreement_version": existing.agreement_version,
                "agreed_at": existing.agreed_at.isoformat() if existing.agreed_at else None,
            }

    record = await record_consent(db, user_id, req.agreement_version)
    logger.info("用户 %s 同意协议 v%s", user_id, req.agreement_version)
    return {
        "detail": "Consent recorded",
        "agreement_version": record.agreement_version,
        "agreed_at": record.agreed_at.isoformat() if record.agreed_at else None,
    }



@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """当前用户修改自己的密码"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 验证当前密码
    if not verify_password(req.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # 新旧密码不能一样
    if req.current_password == req.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from current password")

    # 验证新密码强度
    pwd_error = validate_password_strength(req.new_password)
    if pwd_error:
        raise HTTPException(status_code=422, detail=pwd_error)

    # 更新密码
    user.hashed_password = hash_password(req.new_password)
    await db.commit()

    logger.info("用户 %s 修改了密码", user.email)
    return {"detail": "Password changed successfully"}


@router.post("/admin/reset-password/{target_user_id}")
async def admin_reset_password(
    target_user_id: int,
    req: AdminResetPasswordRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """管理员强制重置指定用户的密码"""
    result = await db.execute(select(User).where(User.id == target_user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 验证新密码强度
    pwd_error = validate_password_strength(req.new_password)
    if pwd_error:
        raise HTTPException(status_code=422, detail=pwd_error)

    # 更新密码为管理员指定的新密码
    user.hashed_password = hash_password(req.new_password)

    # 吊销该用户所有 refresh token（强制重新登录）
    result = await db.execute(
        select(UserSession).where(UserSession.user_id == target_user_id)
    )
    sessions = result.scalars().all()
    for session in sessions:
        await db.delete(session)

    await db.commit()

    logger.info("管理员重置了用户 %s 的密码并吊销了其会话", user.email)
    return {"detail": f"Password reset for user {target_user_id} successful. All sessions revoked."}


# ═══════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════


def _save_refresh_token(db: AsyncSession, user_id: int, refresh_token: str) -> None:
    """保存 refresh token 哈希到数据库（add 但不 commit）"""
    session = UserSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(session)
