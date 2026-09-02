"""
邀请码 API — 注册 / 管理 / 撤销

挂载:
  POST   /api/auth/register-invite  公开（用邀请码注册）
  POST   /api/admin/invites         admin（创建邀请码）
  GET    /api/admin/invites         admin（列出邀请码）
  DELETE /api/admin/invites/{code}  admin（撤销邀请码）
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    require_role,
)
from api.database import InviteCode, User, UserSession, get_db

logger = logging.getLogger("invite_routes")

router = APIRouter(tags=["invite"])


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _generate_code() -> str:
    """生成 8 字符 base32 随机邀请码"""
    raw = os.urandom(5)
    return base64.b32encode(raw).decode("utf-8").rstrip("=").lower()[:8]


# ═══════════════════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════════════════

class RegisterInviteRequest(BaseModel):
    invite_code: str = Field(..., min_length=1, max_length=16)
    email: str = Field(..., max_length=255)
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field("", max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict
    # 使用即同意（W2-CONSENT）：邀请码注册的新用户必然未同意协议
    needs_consent: bool = False


class CreateInvitesRequest(BaseModel):
    count: int = Field(1, ge=1, le=100)
    expires_days: int = Field(30, ge=1, le=365)
    note: str = Field("", max_length=255)


class CreateInvitesResponse(BaseModel):
    codes: list[str]
    total: int
    expires_at: str


class InviteCodeItem(BaseModel):
    code: str
    created_by: int | None
    created_at: str | None
    used_by: int | None
    used_at: str | None
    expires_at: str | None
    is_revoked: bool
    note: str


class InviteListResponse(BaseModel):
    items: list[InviteCodeItem]
    total: int
    page: int
    page_size: int
    valid_count: int
    used_count: int
    revoked_count: int
    expired_count: int


# ═══════════════════════════════════════════════════════
# 公开端点
# ═══════════════════════════════════════════════════════

@router.post("/api/auth/register-invite", response_model=TokenResponse)
async def register_with_invite(
    req: RegisterInviteRequest,
    db: AsyncSession = Depends(get_db),
):
    """使用邀请码注册新用户"""
    # ── 校验邀请码 ──
    result = await db.execute(
        select(InviteCode).where(InviteCode.code == req.invite_code.strip().lower())
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(
            status_code=400,
            detail="邀请码无效",
            headers={"X-Error-Code": "INVITE_INVALID"},
        )
    if not invite.is_valid():
        if invite.is_revoked:
            msg = "邀请码已被撤销"
        elif invite.used_by is not None:
            msg = "邀请码已被使用"
        else:
            msg = "邀请码已过期"
        raise HTTPException(
            status_code=400,
            detail=msg,
            headers={"X-Error-Code": "INVITE_INVALID"},
        )

    # ── 检查邮箱 ──
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    # ── 检查用户名 ──
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该用户名已被使用")

    # ── 创建用户 ──
    user = User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
        display_name=req.display_name or req.username,
        role="viewer",
        is_active=True,
        is_verified=True,  # 内测用户自动验证
    )
    db.add(user)
    await db.flush()

    # ── 标记邀请码已使用 ──
    invite.used_by = user.id
    invite.used_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    await db.flush()

    # ── 生成令牌 ──
    token_data = {"sub": str(user.id), "email": user.email, "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # ── 存储 refresh token ──
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(session)

    await db.commit()
    await db.refresh(user)

    logger.info("邀请码注册成功: %s (%s) | code=%s", user.email, user.username, invite.code)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
        needs_consent=True,
    )


# ═══════════════════════════════════════════════════════
# Admin 端点
# ═══════════════════════════════════════════════════════

@router.post("/api/admin/invites", response_model=CreateInvitesResponse)
async def create_invites(
    req: CreateInvitesRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """创建邀请码（admin only）"""
    admin_user_id = _admin[0]
    codes: list[str] = []
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=req.expires_days)

    for _ in range(req.count):
        # 避免重复
        for _attempt in range(10):
            candidate = _generate_code()
            result = await db.execute(
                select(InviteCode).where(InviteCode.code == candidate)
            )
            if not result.scalar_one_or_none():
                break
        else:
            raise HTTPException(status_code=500, detail="生成邀请码失败（重试耗尽）")

        invite = InviteCode(
            code=candidate,
            created_by=admin_user_id,
            created_at=now,
            expires_at=expires_at,
            note=req.note,
        )
        db.add(invite)
        codes.append(candidate)

    await db.commit()
    logger.info("管理员创建 %d 个邀请码 (过期 %d 天)", req.count, req.expires_days)
    return CreateInvitesResponse(
        codes=codes,
        total=len(codes),
        expires_at=expires_at.isoformat(),
    )


@router.get("/api/admin/invites", response_model=InviteListResponse)
async def list_invites(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, pattern=r"^(valid|used|revoked|expired)$"),
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """列出邀请码（admin only，分页，支持按状态筛选）"""
    query = select(InviteCode)
    count_query = select(func.count(InviteCode.code))

    now = datetime.now(timezone.utc)

    if status == "valid":
        query = query.where(
            ~InviteCode.is_revoked,
            InviteCode.used_by.is_(None),
            InviteCode.expires_at > now,
        )
        count_query = count_query.where(
            ~InviteCode.is_revoked,
            InviteCode.used_by.is_(None),
            InviteCode.expires_at > now,
        )
    elif status == "used":
        query = query.where(InviteCode.used_by.isnot(None))
        count_query = count_query.where(InviteCode.used_by.isnot(None))
    elif status == "revoked":
        query = query.where(InviteCode.is_revoked)
        count_query = count_query.where(InviteCode.is_revoked)
    elif status == "expired":
        query = query.where(
            ~InviteCode.is_revoked,
            InviteCode.used_by.is_(None),
            InviteCode.expires_at <= now,
        )
        count_query = count_query.where(
            ~InviteCode.is_revoked,
            InviteCode.used_by.is_(None),
            InviteCode.expires_at <= now,
        )

    # 总数
    result = await db.execute(count_query)
    total = result.scalar() or 0

    # 分页
    offset = (page - 1) * page_size
    query = query.order_by(InviteCode.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    invites = result.scalars().all()

    # 汇总
    all_invites = await db.execute(select(InviteCode))
    all_list = all_invites.scalars().all()
    valid_count = sum(1 for c in all_list if c.is_valid())
    used_count = sum(1 for c in all_list if c.used_by is not None)
    revoked_count = sum(1 for c in all_list if c.is_revoked)
    expired_count = sum(1 for c in all_list if not c.is_valid() and c.used_by is None and not c.is_revoked)

    return InviteListResponse(
        items=[InviteCodeItem(**c.to_dict()) for c in invites],  # type: ignore[attr-defined]
        total=total,
        page=page,
        page_size=page_size,
        valid_count=valid_count,
        used_count=used_count,
        revoked_count=revoked_count,
        expired_count=expired_count,
    )


@router.delete("/api/admin/invites/{code}")
async def revoke_invite(
    code: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """撤销邀请码（admin only）"""
    result = await db.execute(select(InviteCode).where(InviteCode.code == code))
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在")

    if invite.is_revoked:
        raise HTTPException(status_code=400, detail="邀请码已被撤销")

    if invite.used_by is not None:
        raise HTTPException(status_code=400, detail="邀请码已被使用，无法撤销")

    invite.is_revoked = True
    await db.commit()

    logger.info("管理员撤销邀请码: %s", code)
    return {"detail": f"邀请码 {code} 已撤销", "code": code}
