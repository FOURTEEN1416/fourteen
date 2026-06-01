"""
数据库引擎 + User/Role 模型 — 用户登录和管理系统

使用 SQLAlchemy async + aiosqlite，数据存储在 data/users.db。
后续可切换 PostgreSQL，只需改 DATABASE_URL 环境变量。
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

logger = logging.getLogger("database")

# ── 数据库 URL（默认 SQLite，可覆写为 PostgreSQL） ──
_DEFAULT_DB_URL = "sqlite+aiosqlite:///data/users.db"
DATABASE_URL = os.environ.get("APP_DATABASE_URL", _DEFAULT_DB_URL)

# ── 异步引擎 ──
_engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


# ── 基类 ──


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════
# 模型
# ═══════════════════════════════════════════════════════


class User(Base):
    """平台用户 — 代表一个可登录的管理后台用户"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(255), default="")
    avatar_url = Column(String(512), default="")

    # 角色：admin / editor / viewer
    role = Column(String(50), default="viewer", nullable=False)

    # 状态
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # 时间
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_login_at = Column(DateTime, nullable=True)

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


class UserSession(Base):
    """用户登录会话 — 记录 refresh token 和设备信息"""

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token_hash = Column(String(255), nullable=False, index=True)
    device_name = Column(String(255), default="")
    ip_address = Column(String(45), default="")
    user_agent = Column(String(512), default="")

    # 过期时间
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # 关联
    user = relationship("User", back_populates="sessions")

    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        # SQLite/aiosqlite 不保存时区信息，读回的是 naive datetime
        if self.expires_at.tzinfo is None:
            return now > self.expires_at.replace(tzinfo=timezone.utc)
        return now > self.expires_at

    def __repr__(self) -> str:
        return f"<UserSession(id={self.id}, user_id={self.user_id})>"


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
