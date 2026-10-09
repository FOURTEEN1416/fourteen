"""
邀请码 API — 注册 / 管理 / 撤销

挂载:
  POST   /api/auth/register-invite  公开（用邀请码注册）
  POST   /api/admin/invites         admin（创建邀请码）
  GET    /api/admin/invites         admin（列出邀请码）
  DELETE /api/admin/invites/{code}  admin（撤销邀请码）
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    auth_assert_account_unlocked,
    auth_rate_limit_ip,
    auth_rate_limit_register,
    auth_record_failure,
    auth_record_success,
)
from api.auth_jwt import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    require_role,
    token_claims,
)
from api.database import InviteCode, User, UserSession, get_db
from api.password_policy import PasswordStr, ensure_password_strength
from api.routers.character_template_routes import provision_initial_character

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
    password: PasswordStr = Field(..., examples=["password123"])
    display_name: str = Field("", max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict
    # 使用即同意（W2-CONSENT）：邀请码注册的新用户必然未同意协议
    needs_consent: bool = False
    # W12 阶段2（与 auth_routes.TokenResponse 同契约）：注册分发的初始角色（克隆新 id），
    # 无可用模板/分发失败时为 null——additive，不影响既有字段与协议门流程。
    initial_character: dict | None = None


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
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """使用邀请码注册新用户"""
    # ── 防爆破（P0 修复批 F3 + EXT-3 修复）：IP 失败滑窗 + 账号锁定 + 注册面尝试桶 ──
    auth_rate_limit_ip(request)
    auth_rate_limit_register(request)
    auth_assert_account_unlocked(
        req.email, req.username, detail="注册请求暂时无法处理，请稍后再试"
    )

    # ── 密码强度（策略唯一真源：api/password_policy.py）──
    # 置于邀请码校验之前：输入不合规即刻失败，不必先查库
    ensure_password_strength(req.password)

    # ── 校验邀请码（快速失败；真正的消费在下方原子 CAS）──
    code_norm = req.invite_code.strip().lower()
    result = await db.execute(
        select(InviteCode).where(InviteCode.code == code_norm)
    )
    invite = result.scalar_one_or_none()
    # P0 修复批 F4：存在性 oracle 消除——不存在/已撤销/已使用/已过期
    # 一律同一文案「邀请码无效」，不向持码者泄露码的具体失效原因。
    if invite is None or not invite.is_valid():
        auth_record_failure(request, req.email, req.username)
        raise HTTPException(
            status_code=400,
            detail="邀请码无效",
            headers={"X-Error-Code": "INVITE_INVALID"},
        )

    # ── 检查邮箱/用户名（EXT-3 oracle 消除，2026-10-09）──
    # 409 资源冲突不是凭证类失败，不计入防爆破窗口（P0 修复批 F3 计数口径；
    # 注册面尝试桶 auth_rate_limit_register 已在入口按请求计数兜住枚举探测）。
    # 邮箱/用户名命中一律同一文案 + 同一机器码，不泄露命中字段
    # （与 /register 的 REGISTRATION_CONFLICT 同契约）。
    _conflict = {
        "detail": "邮箱或用户名已被使用",
        "headers": {"X-Error-Code": "REGISTRATION_CONFLICT"},
    }
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, **_conflict)

    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, **_conflict)

    # ── 创建用户 ──
    user = User(
        email=req.email,
        username=req.username,
        hashed_password=await asyncio.to_thread(hash_password, req.password),
        display_name=req.display_name or req.username,
        role="viewer",
        is_active=True,
        is_verified=True,  # 内测用户自动验证
    )
    db.add(user)
    await db.flush()

    # ── 原子消费邀请码（compare-and-swap）──
    # 旧实现是「先读校验、后写标记」：并发窗口内两个注册都通过 is_valid()
    # → 同一邀请码注册两个用户。改为条件 UPDATE，rowcount≠1 即回滚整笔注册。
    # naive-UTC：与列存储格式一致（DateTime 无 tz、_utcnow=utcnow()），
    # 且会话中已加载的 invite 会触发 ORM 的 Python 端 WHERE 求值，
    # aware/naive 混比直接 TypeError。
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    claim = await db.execute(
        update(InviteCode)
        .where(
            InviteCode.code == code_norm,
            InviteCode.used_by.is_(None),
            InviteCode.is_revoked.is_(False),
            InviteCode.expires_at > now,
        )
        .values(used_by=user.id, used_at=now)
    )
    if claim.rowcount != 1:
        await db.rollback()
        auth_record_failure(request, req.email, req.username)
        raise HTTPException(
            status_code=400,
            detail="邀请码无效",
            headers={"X-Error-Code": "INVITE_INVALID"},
        )

    # ── 生成令牌 ──
    # P0 修复批 F5：手工 token dict 改 token_claims(user) 唯一 owner——
    # 旧 dict 带 role 陈旧声明、缺 tv 撤销版本，refresh 换发链与 W1 口径脱节。
    token_data = token_claims(user)
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

    # 注册成功：清空该 email/username 的连续失败账（P0 修复批 F3）
    auth_record_success(req.email, req.username)

    logger.info("邀请码注册成功: %s (%s) | code=%s", user.email, user.username, code_norm)
    # W12 阶段2：与 /register 同一注册分发契约（用户行已提交之后执行；
    # 分发内部失败自降级为 None——绝不阻断注册、绝不 500）。
    initial_character = await provision_initial_character(db, int(user.id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
        needs_consent=True,
        initial_character=initial_character,
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
