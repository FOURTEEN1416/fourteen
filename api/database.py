"""
数据库引擎 + User/Role 模型 — 用户登录和管理系统

使用 SQLAlchemy async + aiosqlite，数据存储在 data/users.db。
后续可切换 PostgreSQL，只需改 DATABASE_URL 环境变量。

2026-08-31（T1 mypy 债清偿）：Column[] → Mapped[] 注解升级（SQLAlchemy 2.0 标准写法），
实例属性类型由 mypy 完整推断，消除全库 30+ 处 Column 联合类型错误。运行时行为不变。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

logger = logging.getLogger("database")

# ── 数据库 URL（默认 SQLite，可覆写为 PostgreSQL） ──
from api.runtime_config import get_database_url  # noqa: E402

DATABASE_URL = get_database_url()

# ── 异步引擎 ──
_engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


def _utcnow() -> datetime:
    """SQLite/aiosqlite 存 naive UTC；统一用带时区构造，读回再补 tz。"""
    return datetime.now(timezone.utc)


# ── 基类 ──


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════
# 模型
# ═══════════════════════════════════════════════════════


class User(Base):
    """平台用户 — 代表一个可登录的管理后台用户"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    avatar_url: Mapped[str] = mapped_column(String(512), default="")

    # 角色：admin / editor / viewer
    role: Mapped[str] = mapped_column(String(50), default="viewer", nullable=False)

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 用户级 LLM 配置（JSON）— 优先于全局默认，实现多用户 API Key 隔离
    llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 关联
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "role": self.role,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}', role='{self.role}')>"


class InviteCode(Base):
    """邀请码 — 内测注册控制"""

    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    used_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    def is_valid(self) -> bool:
        """检查邀请码是否仍可使用"""
        now = datetime.now(timezone.utc)
        if self.is_revoked:
            return False
        if self.used_by is not None:
            return False
        # SQLite 存的是 naive datetime
        exp: datetime = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return not (now > exp)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "used_by": self.used_by,
            "used_at": self.used_at.isoformat() if self.used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_revoked": self.is_revoked,
            "note": self.note,
        }

    def __repr__(self) -> str:
        return f"<InviteCode(code='{self.code}', revoked={self.is_revoked})>"


class UserSession(Base):
    """用户登录会话 — 记录 refresh token 和设备信息"""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    device_name: Mapped[str] = mapped_column(String(255), default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")

    # 过期时间
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    # 关联
    user = relationship("User", back_populates="sessions")

    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        # SQLite/aiosqlite 不保存时区信息，读回的是 naive datetime
        exp: datetime = self.expires_at
        if exp.tzinfo is None:
            return now > exp.replace(tzinfo=timezone.utc)
        return now > exp

    def __repr__(self) -> str:
        return f"<UserSession(id={self.id}, user_id={self.user_id})>"


class ConsentRecord(Base):
    """用户协议同意记录 — 使用即同意声明（W2-CONSENT）

    每次同意插入一条记录；是否"需同意"按用户最新同意版本与当前协议版本比较得出。
    独立成表而非 users 列：老库由 create_all 自动建新表，无需 ALTER。
    """

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agreement_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    agreed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "agreement_version": self.agreement_version,
            "agreed_at": self.agreed_at.isoformat() if self.agreed_at else None,
        }

    def __repr__(self) -> str:
        return f"<ConsentRecord(user_id={self.user_id}, v='{self.agreement_version}')>"


class WechatBinding(Base):
    """微信绑定 — 将微信账号关联到注册用户，并记录绑定的角色卡"""

    __tablename__ = "wechat_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wxid: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    nickname: Mapped[str] = mapped_column(String(255), default="")
    avatar: Mapped[str] = mapped_column(Text, default="")
    character_card_id: Mapped[str] = mapped_column(String(255), default="default")
    bound_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "wxid": self.wxid,
            "nickname": self.nickname,
            "avatar": self.avatar,
            "character_card_id": self.character_card_id,
            "bound_at": self.bound_at.isoformat() if self.bound_at else None,
        }

    def __repr__(self) -> str:
        return f"<WechatBinding(id={self.id}, wxid='{self.wxid}', user_id={self.user_id}, char='{self.character_card_id}')>"


# ═══════════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════════


async def init_db() -> None:
    """创建所有表（如不存在）"""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (if not existed)")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：获取数据库会话"""
    async with _async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_db() -> None:
    """关闭数据库引擎"""
    await _engine.dispose()
