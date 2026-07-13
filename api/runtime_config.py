"""
统一运行时配置 — 生产模式检测 + 数据库 URL 解析

所有环境变量读取逻辑集中于此，避免散落在多个文件中。
"""
from __future__ import annotations

import os


def is_production() -> bool:
    """检测是否为生产环境。

    优先级：AI_GF_ENV > ENV > APP_ENV
    匹配值：prod / production
    """
    for var in ("AI_GF_ENV", "ENV", "APP_ENV"):
        val = os.environ.get(var, "").strip().lower()
        if val in ("prod", "production"):
            return True
    return False


# postgresql:// → postgresql+asyncpg:// 前缀映射
_SYNC_TO_ASYNC_PREFIXES = (
    ("mysql://", "mysql+aiomysql://"),
    ("mysql+mysqldb://", "mysql+aiomysql://"),
    ("postgresql://", "postgresql+asyncpg://"),
    ("postgres://", "postgresql+asyncpg://"),
)


def _to_async_url(url: str) -> str:
    """将同步驱动 URL 转换为对应的 async 驱动 URL。"""
    for sync_prefix, async_prefix in _SYNC_TO_ASYNC_PREFIXES:
        if url.startswith(sync_prefix):
            return async_prefix + url[len(sync_prefix):]
    return url


def get_database_url() -> str:
    """获取异步数据库 URL。

    优先级：DATABASE_URL > APP_DATABASE_URL > 默认 SQLite
    自动将同步驱动前缀转为 async 驱动前缀。
    """
    default = "sqlite+aiosqlite:///data/users.db"
    url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("APP_DATABASE_URL", "").strip()
    if not url:
        return default
    return _to_async_url(url)
