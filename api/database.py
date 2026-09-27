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
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
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

    # 撤销版本（W1 统一身份）：签发 token 时写入 `tv` 声明，每次请求与库内现值比对。
    # 自增即宣告该用户**先前签发的全部 access/refresh 失效**——改密、管理员重置、
    # 停用、删除走此路径；降权/升权**不**自增（角色每请求从库内现值读取，即时收窄）。
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

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


class UserActiveCharacter(Base):
    """用户当前激活角色（W1 · D2）— 「谁在用哪张卡」是按注册用户持有的**个人选择**。

    旧实现把激活写成角色卡文件里的全局 `is_active`：A 激活会把 B 的选择覆盖掉
    （两用户互相清空），而卡文件又同时承担「公共模板」职责。本表把两层语义分开：
    - 角色卡（`config/characters/*.json`）只描述卡片本身与它的归属（`user_id`）；
    - 本表记录**每个用户的**当前激活卡，互不覆盖。

    独立成表而非 users 列：老库由 create_all 自动建新表，无需 ALTER（与 consent_records 同法）。
    """

    __tablename__ = "user_active_characters"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    character_id: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "character_id": self.character_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<UserActiveCharacter(user_id={self.user_id}, character_id='{self.character_id}')>"


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


class WechatChannelSession(Base):
    """每人独立微信通道会话 — 凭证/状态/轮询按 user_id 隔离。

    一人最多两条（slot=0/1）；全局唯一 bot 通道模型已废弃。
    """

    __tablename__ = "wechat_channel_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bot_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="idle", nullable=False
    )  # idle|waiting_qr|scanned|connected|error
    nickname: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    last_error: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    messages_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "slot": self.slot,
            "bot_id": self.bot_id,
            "status": self.status,
            "nickname": self.nickname,
            "last_error": self.last_error,
            "messages_today": self.messages_today,
            "last_connected_at": (
                self.last_connected_at.isoformat() if self.last_connected_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<WechatChannelSession(user_id={self.user_id}, slot={self.slot}, "
            f"status='{self.status}', bot_id='{self.bot_id}')>"
        )


class WechatPeerPreference(Base):
    """通道内好友角色自选 — (通道所有者, 好友wxid) → 角色卡。

    与 wechat_bindings 区分：binding 表达「wxid↔注册用户」身份；
    本表表达「在 U 的通道里，F 选了哪张卡」。
    """

    __tablename__ = "wechat_peer_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    peer_wxid: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    character_card_id: Mapped[str] = mapped_column(String(255), nullable=False)
    chosen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "peer_wxid": self.peer_wxid,
            "character_card_id": self.character_card_id,
            "chosen_at": self.chosen_at.isoformat() if self.chosen_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<WechatPeerPreference(owner={self.owner_user_id}, "
            f"peer='{self.peer_wxid}', char='{self.character_card_id}')>"
        )


class CharacterAchievement(Base):
    """角色成就（ADR-0014）— 角色维度隔离，解锁时间以首次达标落库为准。

    进度由 achievement_engine 从既有事实源幂等重算，本表只持久化
    进度快照与 first-unlock 时间戳；重复重算不会重复解锁。
    """

    __tablename__ = "character_achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    achievement_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "achievement_id": self.achievement_id,
            "character_id": self.character_id,
            "progress": self.progress,
            "target": self.target,
            "unlocked_at": self.unlocked_at.isoformat() if self.unlocked_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<CharacterAchievement(char='{self.character_id}', "
            f"ach='{self.achievement_id}', {self.progress}/{self.target})>"
        )


# ═══════════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════════


async def _migrate_schema() -> None:
    """幂等列级迁移。

    `Base.metadata.create_all` 只建**缺失的表**，不会给既有表补列——因此新增
    用户列必须在这里显式补，否则老库（生产 `data/users.db`）一读该列就
    `no such column`。当前待补：`users.token_version`（W1 撤销版本）。
    """
    async with _engine.begin() as conn:
        def _user_columns(sync_conn) -> set[str]:
            from sqlalchemy import inspect as _inspect

            insp = _inspect(sync_conn)
            if not insp.has_table("users"):
                return set()
            return {c["name"] for c in insp.get_columns("users")}

        columns = await conn.run_sync(_user_columns)
        if columns and "token_version" not in columns:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
            )
            logger.info("老库迁移：users.token_version 列已补齐")


async def init_db() -> None:
    """创建所有表（如不存在）+ 补齐既有表缺失列 + users.id 防复用迁移"""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema()
    await _migrate_users_autoincrement()
    logger.info("Database tables created (if not existed)")


def enable_sqlite_fk(engine: Any) -> None:
    """给异步引擎的每条新连接打开外键强制（幂等，PostgreSQL 不受影响）。

    SQLite 默认 ``PRAGMA foreign_keys=OFF``——模型里声明的 ondelete
    CASCADE/SET NULL 全是摆设（P1-审查 item33 只能手抄显式 delete 兜底）。
    注意 PRAGMA 在事务内是 no-op，因此必须挂在**连接建立**事件上，
    不能放进 init_db 的迁移事务里。
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


enable_sqlite_fk(_engine)


# users.id 的完整列清单（W1 token_version 之后）。表重建迁移用；
# 与上方模型定义一一对应，改模型必须同步改这里。
_USERS_DDL = """
CREATE TABLE users_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(100) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    display_name VARCHAR(255) DEFAULT '',
    avatar_url VARCHAR(512) DEFAULT '',
    role VARCHAR(50) DEFAULT 'viewer' NOT NULL,
    is_active BOOLEAN DEFAULT 1 NOT NULL,
    is_verified BOOLEAN DEFAULT 0 NOT NULL,
    token_version INTEGER DEFAULT 0 NOT NULL,
    llm_config JSON,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_login_at DATETIME
)
"""
_USERS_COLS = [
    "id", "email", "username", "hashed_password", "display_name", "avatar_url",
    "role", "is_active", "is_verified", "token_version", "llm_config",
    "created_at", "updated_at", "last_login_at",
]


def ensure_users_autoincrement(conn: Any) -> bool:
    """users.id 改为真 AUTOINCREMENT（W9 缺陷 A 根治之二），幂等。

    入参是**原生 sqlite3 连接**（须处于 autocommit/可执行 PRAGMA 状态）：
    ``PRAGMA foreign_keys`` 在事务内是 no-op，表重建必须在迁移自己的连接上
    管理事务，不能经 SQLAlchemy ``engine.begin()``。

    SQLite 的 ``INTEGER PRIMARY KEY`` 默认取 ``max(rowid)+1``——删除最高 id
    账号后，新注册账号会**复用该 id**，而记忆/向量/账本里的会话键、画像键、
    好感度键都以 ``"{uid}:"`` 为 owner 段，复用即让新账号「继承」旧账号的
    owner 前缀存储。``AUTOINCREMENT`` 关键字使 rowid 单调不回退。
    返回是否执行了重建。
    """
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if cur is None:
        return False
    if "AUTOINCREMENT" in str(cur[0] or ""):
        return False
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    missing = [c for c in _USERS_COLS if c not in cols]
    if missing:
        raise RuntimeError(
            f"users 表缺少列 {missing}，请先跑列级迁移（init_db/_migrate_schema）"
        )
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS users_new")
        conn.execute(_USERS_DDL)
        collist = ",".join(_USERS_COLS)
        conn.execute(f"INSERT INTO users_new ({collist}) SELECT {collist} FROM users")
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    logger.info("users.id 已迁移为 AUTOINCREMENT（防删除后 UID 复用）")
    return True


async def _migrate_users_autoincrement() -> bool:
    import asyncio
    import sqlite3 as _sqlite3

    if not _engine.url.get_backend_name().startswith("sqlite"):
        return False
    db_file = _engine.url.database
    if not db_file:
        return False

    def _go() -> bool:
        conn = _sqlite3.connect(str(db_file))
        try:
            return ensure_users_autoincrement(conn)
        finally:
            conn.close()

    return await asyncio.to_thread(_go)


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
