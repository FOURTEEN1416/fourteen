"""
用户认证 API — 注册 / 登录 / 刷新令牌 / 登出

所有端点挂载于 /api/auth，与现有 X-API-Key 认证系统并行共存。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    auth_assert_account_unlocked,
    auth_rate_limit_ip,
    auth_record_failure,
    auth_record_success,
)
from api.auth_jwt import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    bump_token_version,
    create_access_token,
    create_refresh_token,
    get_current_user_id,
    hash_password,
    hash_refresh_token,
    require_role,
    token_claims,
    verify_password,
    verify_token,
)
from api.consent import (
    CURRENT_AGREEMENT_VERSION,
    consent_state_of,
    has_consented,
    latest_consent,
    record_consent,
    withdraw_consent,
)
from api.database import User, UserSession, get_db
from api.password_policy import PasswordStr, ensure_password_strength
from api.routers.character_template_routes import provision_initial_character

logger = logging.getLogger("auth_routes")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ═══════════════════════════════════════════════════════
# 凭证撤销语义（W1 唯一口径，改前必读）
# ═══════════════════════════════════════════════════════
#
# | 事件 | 旧 access token | 旧 refresh token |
# |------|-----------------|------------------|
# | 本人改密 | 立即失效（撤销版本自增） | 立即失效（撤销版本自增 + 全部会话行吊销） |
# | 管理员重置密码 | 立即失效（同上） | 立即失效（同上） |
# | 账号停用 | 立即失效（撤销版本自增；且每请求校验 is_active） | 立即失效（同上；重新启用后旧 refresh 亦不可用） |
# | 账号删除 | 立即失效（主体查不到） | 立即失效（主体查不到 + 会话行级联） |
# | 降权 / 升权 | **不失效**：角色每请求取库内现值，权限当场收窄/放开 | 不失效（refresh 换发时按新角色签） |
# | logout | 到期自然失效（不撤销版本：同账号其他设备不被误登出） | 当前这一枚立即吊销 |
#
# access 默认 30 分钟（JWT_ACCESS_EXPIRE_MINUTES），refresh 默认 7 天
# （JWT_REFRESH_EXPIRE_DAYS）——库内会话期限与后者**同源**，不得各写各的。


# ═══════════════════════════════════════════════════════
# 请求 / 响应模型
# ═══════════════════════════════════════════════════════


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=255, examples=["user@example.com"])
    username: str = Field(..., min_length=2, max_length=100, examples=["demo"])
    password: PasswordStr = Field(..., examples=["password123"])
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
    # W12 阶段2（注册分发）：新用户冷启动拿到的初始角色；未分发/分发失败为 null。
    # 仅注册端点填值（登录/刷新恒为 null）——additive 字段，既有契约零改动。
    initial_character: dict | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = ""  # 可选：优先从 body 读，无则退到 cookie


class LogoutRequest(BaseModel):
    refresh_token: str = ""  # 可选：优先从 body 读，无则退到 cookie


class ConsentRequest(BaseModel):
    agreement_version: str = Field(..., examples=["1.0.0"])


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128, examples=["oldPass123"])
    new_password: PasswordStr = Field(..., examples=["newPass456"])


class AdminResetPasswordRequest(BaseModel):
    new_password: PasswordStr = Field(..., examples=["resetPass789"])


# ═══════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════


# 密码策略已抽到 api/password_policy.py（唯一真源，2026-09-15）


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
        # 与 JWT_REFRESH_EXPIRE_DAYS 同源（旧实现硬编码 7 天，配置一改就对不上）
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
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

    # 验证密码强度（策略唯一真源：api/password_policy.py）
    ensure_password_strength(req.password)

    # 创建用户
    # P1-10（2026-09-21 审查修复）：bcrypt ~0.2-0.5s 纯 CPU，旧实现直接在
    # async 路由里同步跑——撞库时每个错误请求冻结整个事件循环一次。
    user = User(
        email=req.email,
        username=req.username,
        hashed_password=await asyncio.to_thread(hash_password, req.password),
        display_name=req.display_name or req.username,
        role="viewer",     # 新注册用户默认 viewer
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # 生成令牌
    token_data = token_claims(user)
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # 存储 refresh token 哈希
    _save_refresh_token(db, int(user.id), refresh_token)
    await db.commit()

    # W12 阶段2：注册分发初始角色（种子模板克隆 + 个人激活绑定）。
    # 在用户行已提交之后执行——分发环节任何失败都只降级为 initial_character=null。
    initial_character = await provision_initial_character(db, int(user.id))

    logger.info("新用户注册: %s (%s)", user.email, user.username)
    _set_refresh_cookie(response, refresh_token, request)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user.to_dict(),
        needs_consent=True,  # 新用户必然未同意过协议
        initial_character=initial_character,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """使用邮箱或用户名 + 密码登录，返回令牌"""
    # ── 防爆破（P0 修复批 F3）：IP 失败滑窗（5 失败/分/IP）+ 账号锁定检查 ──
    auth_rate_limit_ip(request)
    auth_assert_account_unlocked(req.login)

    # 查找用户（支持邮箱或用户名）
    result = await db.execute(
        select(User).where(
            (User.email == req.login) | (User.username == req.login)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        auth_record_failure(request, req.login)
        raise HTTPException(status_code=401, detail="Invalid login credentials")

    # 验证密码
    if not await asyncio.to_thread(verify_password, req.password, user.hashed_password):
        auth_record_failure(request, req.login)
        raise HTTPException(status_code=401, detail="Invalid login credentials")

    # 检查账号是否激活（密码正确不计失败；403 不动失败账）
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    # 密码验证通过：连续失败计数归零
    auth_record_success(req.login)

    # 生成令牌
    token_data = token_claims(user)
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

    # 撤销版本比对：改密 / 管理员重置 / 停用后，此前签发的 refresh 一律作废
    if int(payload.get("tv", 0) or 0) != int(refresh_user.token_version or 0):
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    # 生成新令牌
    token_data = token_claims(refresh_user)
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
    """登出 — 吊销 refresh token + 清除 httpOnly cookie

    语义（W1）：只吊销**当前这一枚** refresh 会话，**不**自增撤销版本——
    同一账号在其他设备上的会话不受影响；access token 到期自然失效。
    """
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
    if not await asyncio.to_thread(verify_password, req.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # 新旧密码不能一样
    if req.current_password == req.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from current password")

    # 验证新密码强度（策略唯一真源：api/password_policy.py）
    ensure_password_strength(req.new_password)

    # 更新密码
    user.hashed_password = await asyncio.to_thread(hash_password, req.new_password)

    # 改密即撤销：撤销版本自增（旧 access 立即失效）+ 全部 refresh 会话吊销
    bump_token_version(user)
    revoked = await _revoke_all_sessions(db, int(user.id))
    await db.commit()

    logger.info("用户 %s 修改了密码，已撤销 %d 个会话", user.email, revoked)
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

    # 验证新密码强度（策略唯一真源：api/password_policy.py）
    ensure_password_strength(req.new_password)

    # 更新密码为管理员指定的新密码
    user.hashed_password = await asyncio.to_thread(hash_password, req.new_password)

    # 撤销该用户全部凭证（强制重新登录）：撤销版本自增 + 会话行吊销。
    # 只有会话行吊销时，重新启用/旧 access 仍可能存活，故两者必须同时做。
    bump_token_version(user)
    revoked = await _revoke_all_sessions(db, target_user_id)
    await db.commit()

    logger.info("管理员重置了用户 %s 的密码并撤销了 %d 个会话", user.email, revoked)
    return {"detail": f"Password reset for user {target_user_id} successful. All sessions revoked."}


# ═══════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════


def _save_refresh_token(db: AsyncSession, user_id: int, refresh_token: str) -> None:
    """保存 refresh token 哈希到数据库（add 但不 commit）"""
    session = UserSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(session)


async def _revoke_all_sessions(db: AsyncSession, user_id: int) -> int:
    """吊销该用户全部 refresh 会话（改密 / 管理员重置 / 停用 / 删除用）。"""
    result = await db.execute(select(UserSession).where(UserSession.user_id == user_id))
    sessions = result.scalars().all()
    for session in sessions:
        await db.delete(session)
    return len(sessions)


# ═══════════════════════════════════════════════════════
# W9 自服务生命周期端点（D13）：撤回 / 状态 / 注销 / 导出
# 库层唯一真源：api/consent.py + api/lifecycle.py（本文件只做 HTTP 接线，
# 不复制任何判定逻辑；BOARD W9 条目声称的四端点自此真实在位）
# ═══════════════════════════════════════════════════════


@router.post("/consent/withdraw")
async def post_consent_withdraw(
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """撤回同意（D13）：落 WITHDRAWN 哨兵档，四通道外发即刻停发（fail-closed）。"""
    await withdraw_consent(db, user_id)
    state = await consent_state_of(db, user_id)
    return {"status": state}


@router.get("/consent/status")
async def get_consent_status(
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """当前同意状态：granted / missing / withdrawn / outdated。"""
    state = await consent_state_of(db, user_id)
    return {"status": state, "agreement_version": CURRENT_AGREEMENT_VERSION}


@router.post("/account/delete")
async def post_account_delete(
    user_id: int = Security(get_current_user_id),
):
    """自助注销（D13）：立即冻结（停用 + 撤会话 + 坟场 + 写入封禁），清除异步进行。

    响应只含作业受理（queued/purging），**不含任何「已删除」宣称**（W9 纪律）。
    """
    from api import lifecycle as _lifecycle

    return await _lifecycle.self_service_delete(int(user_id), background=True)


@router.get("/account/export")
async def get_account_export(
    user_id: int = Security(get_current_user_id),
):
    """账号全量导出清单（D13 §2.1）：类别计数 + 本人会话键清单。"""
    from api import lifecycle as _lifecycle

    return await _lifecycle.export_account_manifest(int(user_id))


@router.get("/account/export/chats")
async def get_account_export_chats(
    session_key: str,
    before_id: int = 0,
    limit: int = 500,
    user_id: int = Security(get_current_user_id),
):
    """分页导出本人某会话的聊天原文。

    归属门禁：session_key 必须属于本人（owner 前缀 / user_key 判据），
    他人会话一律 404（不泄露存在性，防越权枚举）。
    """
    from api import lifecycle as _lifecycle

    limit = max(1, min(1000, int(limit)))
    sm = _lifecycle._export_sm(None, None)
    owned = await asyncio.to_thread(
        _lifecycle._owned_session_keys_sync, sm, int(user_id)
    )
    if session_key not in set(owned):
        raise HTTPException(status_code=404, detail="session not found")
    return await asyncio.to_thread(
        _lifecycle.export_chats_page, sm, session_key, int(before_id), limit
    )
