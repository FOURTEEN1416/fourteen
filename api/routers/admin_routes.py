"""
管理员用户管理 API — 创建/查询/编辑/删除用户

所有端点需要 admin 角色访问权限。
挂载于 /api/admin
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import hash_password, require_role
from api.database import User, get_db

logger = logging.getLogger("admin_routes")

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════
# 请求 / 响应模型
# ═══════════════════════════════════════════════════════


class AdminCreateUserRequest(BaseModel):
    email: str = Field(..., max_length=255)
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field("", max_length=255)
    role: str = Field("viewer", pattern=r"^(admin|editor|viewer)$")


class AdminUpdateUserRequest(BaseModel):
    email: str | None = Field(None, max_length=255)
    username: str | None = Field(None, min_length=2, max_length=100)
    password: str | None = Field(None, min_length=6, max_length=128)
    display_name: str | None = Field(None, max_length=255)
    role: str | None = Field(None, pattern=r"^(admin|editor|viewer)$")
    is_active: bool | None = None


class UserListResponse(BaseModel):
    users: list[dict]
    total: int
    page: int
    page_size: int


# ═══════════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════════


@router.get("/users", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=100),
    role: str | None = Query(None, pattern=r"^(admin|editor|viewer)$"),
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """获取用户列表（分页、搜索、筛选）"""
    query = select(User)
    count_query = select(func.count(User.id))

    # 搜索
    if search:
        like_pattern = f"%{search}%"
        query = query.where(
            (User.email.ilike(like_pattern)) |
            (User.username.ilike(like_pattern)) |
            (User.display_name.ilike(like_pattern))
        )
        count_query = count_query.where(
            (User.email.ilike(like_pattern)) |
            (User.username.ilike(like_pattern)) |
            (User.display_name.ilike(like_pattern))
        )

    # 角色筛选
    if role:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)

    # 总数
    result = await db.execute(count_query)
    total = result.scalar() or 0

    # 分页
    offset = (page - 1) * page_size
    query = query.order_by(User.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(
        users=[u.to_dict() for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """获取单个用户详情"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.to_dict()


@router.post("/users")
async def create_user(
    req: AdminCreateUserRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """管理员创建用户"""
    # 检查邮箱
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # 检查用户名
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
        display_name=req.display_name or req.username,
        role=req.role,
        is_active=True,
        is_verified=True,  # 管理员创建的用户自动验证
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("管理员创建用户: %s (%s, role=%s)", user.email, user.username, user.role)
    return user.to_dict()


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: AdminUpdateUserRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """管理员编辑用户"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 检查邮箱唯一性
    if req.email and req.email != user.email:
        result = await db.execute(select(User).where(User.email == req.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already taken")
        user.email = req.email

    # 检查用户名唯一性
    if req.username and req.username != user.username:
        result = await db.execute(select(User).where(User.username == req.username))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken")
        user.username = req.username

    if req.password:
        user.hashed_password = hash_password(req.password)
    if req.display_name is not None:
        user.display_name = req.display_name
    if req.role is not None:
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active

    await db.commit()
    await db.refresh(user)

    logger.info("管理员更新用户: %s (id=%d)", user.email, user.id)
    return user.to_dict()


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """管理员删除用户（禁止删除自己）"""
    admin_user_id, _ = _admin

    if user_id == admin_user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()

    logger.info("管理员删除用户: %s (id=%d)", user.email, user_id)
    return {"detail": f"User {user.email} deleted"}
