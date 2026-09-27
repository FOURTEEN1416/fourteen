"""
管理员用户管理 API — 创建/查询/编辑/删除用户

所有端点需要 admin 角色访问权限。
挂载于 /api/admin
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import bump_token_version, hash_password, require_role
from api.database import (
    User,
    get_db,
)
from api.password_policy import PasswordStr, ensure_password_strength

logger = logging.getLogger("admin_routes")

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════
# 请求 / 响应模型
# ═══════════════════════════════════════════════════════


class AdminCreateUserRequest(BaseModel):
    email: str = Field(..., max_length=255)
    username: str = Field(..., min_length=2, max_length=100)
    password: PasswordStr = Field(..., examples=["password123"])
    display_name: str = Field("", max_length=255)
    role: str = Field("viewer", pattern=r"^(admin|editor|viewer)$")


class AdminUpdateUserRequest(BaseModel):
    email: str | None = Field(None, max_length=255)
    username: str | None = Field(None, min_length=2, max_length=100)
    password: PasswordStr | None = Field(None, examples=["password123"])
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
    # 计数查询用独立变量，避免与 select(User) 的 query 联合类型互染（mypy）
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
    # query 经 count 联合后 mypy 推断退化；显式收窄回 User 行
    user_rows = [u for u in users if isinstance(u, User)]

    return UserListResponse(
        users=[u.to_dict() for u in user_rows],
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
    # 密码强度（策略唯一真源：api/password_policy.py）
    ensure_password_strength(req.password)

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
        hashed_password=await asyncio.to_thread(hash_password, req.password),
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
        # 仅在确实要改密码时校验强度（策略唯一真源：api/password_policy.py）
        ensure_password_strength(req.password)
        user.hashed_password = await asyncio.to_thread(hash_password, req.password)
    if req.display_name is not None:
        user.display_name = req.display_name
    if req.role is not None:
        # 降权 / 升权：**不**撤销版本——角色每请求取库内现值，权限当场收窄/放开，
        # 用户无需重新登录（撤销版本只留给改密 / 重置 / 停用 / 删除）。
        user.role = req.role
    if req.is_active is not None:
        was_active = bool(user.is_active)
        user.is_active = req.is_active
        if was_active and not req.is_active:
            # 停用即撤销：撤销版本自增 → 旧 access/refresh 立即失效，
            # 重新启用后旧 token 也不会「复活」。
            bump_token_version(user)

    await db.commit()
    await db.refresh(user)

    logger.info("管理员更新用户: %s (id=%d, role=%s, active=%s)",
                user.email, user.id, user.role, user.is_active)
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

    # W9：管理员删除 = 统一生命周期作业（协议 §2.5「删除即真正删除」）。
    # 冻结（停用+撤销版本+吊销会话+坟场+迟到写入封禁）→ 逐 owner 跨存储清除
    # （users.db 行 / sqlite.db 记忆 / chroma 派生 / agent_plane 账本 / ASE 状态 /
    #  好感度点存 / 微信通道磁盘与连接器 / 节流账本 / 私有角色实例）→ 验证。
    # 完成（且仅完成）才报 deleted；部分失败返回 failed + job_id 供查询续跑。
    email = user.email
    from api import lifecycle

    job = await lifecycle.delete_account_everywhere(user_id)
    if job.get("completed"):
        logger.info("管理员删除用户（跨存储清除完成）: %s (id=%d)", email, user_id)
        return {"detail": f"User {email} deleted", "job": job}
    logger.error("管理员删除用户未完成跨存储清除: %s (id=%d) job=%s", email, user_id, job.get("job_id"))
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"User {email} deletion incomplete; retry to resume",
            "job": job,
        },
    )
