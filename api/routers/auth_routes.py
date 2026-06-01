"""
用户认证 API — 注册 / 登录 / 刷新令牌 / 登出

所有端点挂载于 /api/auth，与现有 X-API-Key 认证系统并行共存。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import (
    create_access_token,
    create_refresh_token,
    get_current_user_id,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_token,
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
    password: str = Field(..., min_length=6, max_length=128, examples=["password123"])
    display_name: str = Field("", max_length=255)


class LoginRequest(BaseModel):
    login: str = Field(..., examples=["user@example.com", "demo"])
    password: str = Field(..., examples=["password123"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


# ═══════════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════════


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """注册新用户（邮箱、用户名、密码），完成后直接返回令牌"""
    # 检查邮箱是否已注册
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # 检查用户名是否已注册
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

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
    _save_refresh_token(db, user.id, refresh_token)

    logger.info("新用户注册: %s (%s)", user.email, user.username)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
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
    _save_refresh_token(db, user.id, refresh_token)

    # 更新最后登录时间
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("用户登录: %s", user.email)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """使用 refresh token 换取新的 access token + refresh token"""
    payload = verify_token(req.refresh_token, expected_type="refresh")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 验证 refresh token 在数据库中是否存在
    token_hash = hash_refresh_token(req.refresh_token)
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

    # 查找用户
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        await db.commit()
        raise HTTPException(status_code=401, detail="User not found or disabled")

    # 生成新令牌
    token_data = {"sub": str(user.id), "email": user.email, "role": user.role}
    new_access_token = create_access_token(token_data)
    new_refresh_token = create_refresh_token(token_data)

    _save_refresh_token(db, user.id, new_refresh_token)
    await db.commit()

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        user=user.to_dict(),
    )


@router.post("/logout")
async def logout(req: LogoutRequest, db: AsyncSession = Depends(get_db)):
    """登出 — 吊销 refresh token"""
    token_hash = hash_refresh_token(req.refresh_token)
    result = await db.execute(
        select(UserSession).where(UserSession.refresh_token_hash == token_hash)
    )
    session = result.scalar_one_or_none()
    if session:
        await db.delete(session)
        await db.commit()

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
