"""
运行时配置工具 — 环境检测 & 数据库 URL 解析

提供两个核心函数：
1. is_production() — 判断当前是否运行在生产环境
2. get_database_url() — 返回异步兼容的数据库连接字符串

检测优先级（高 → 低）：
  AI_GF_ENV  >  APP_ENV  >  ENV

生产标记值：prod / production
"""

from __future__ import annotations

import os
import re

__all__ = ["is_production", "get_database_url"]

# ── 生产环境标记值 ──────────────────────────────────────

_PROD_MARKERS = frozenset({"prod", "production"})


def is_production() -> bool:
    """判断当前运行环境是否为生产环境。

    检测顺序（任一命中即返回 True）：
    1. AI_GF_ENV  — 项目专用环境变量（最高优先级）
    2. APP_ENV    — 通用应用环境变量
    3. ENV        — 兼容旧版 / 通用 ENV 变量
    """
    for var in ("AI_GF_ENV", "APP_ENV", "ENV"):
        val = os.environ.get(var, "").strip().lower()
        if val in _PROD_MARKERS:
            return True
    return False


# ── 数据库 URL 解析 ──────────────────────────────────────

# 同步 → 异步驱动映射
_DRIVER_MAP = {
    "postgresql://": "postgresql+asyncpg://",
    "postgres://": "postgresql+asyncpg://",
    "mysql://": "mysql+aiomysql://",
    "sqlite:///": "sqlite+aiosqlite:///",
}

# 编译正则：匹配 scheme:// 或 scheme:///
_SCHEME_RE = re.compile(r"^([a-z]+)://")


def _convert_to_async(url: str) -> str:
    """将同步数据库 URL 转换为异步驱动 URL。

    - postgresql://  → postgresql+asyncpg://
    - postgres://    → postgresql+asyncpg://
    - mysql://       → mysql+aiomysql://
    - sqlite:///     → sqlite+aiosqlite:///
    - 已包含 + 驱动的 URL 原样返回
    """
    for sync_prefix, async_prefix in _DRIVER_MAP.items():
        if url.startswith(sync_prefix) and not url.startswith(async_prefix):
            return async_prefix + url[len(sync_prefix):]

    # 如果 scheme 已包含 + 号（如 postgresql+asyncpg://），原样返回
    m = _SCHEME_RE.match(url)
    if m and "+" in m.group(1):
        return url

    # 兜底：未知 scheme，原样返回
    return url


def get_database_url() -> str:
    """返回异步兼容的数据库连接字符串。

    优先级：
    1. APP_DATABASE_URL — 应用专用数据库 URL（覆盖 DATABASE_URL）
    2. DATABASE_URL     — 通用数据库 URL
    3. 默认 SQLite       — data/users.db

    自动转换同步驱动为异步驱动（postgresql:// → postgresql+asyncpg://）。
    """
    # 优先使用 APP_DATABASE_URL，其次 DATABASE_URL
    url = os.environ.get("APP_DATABASE_URL", "").strip()
    if not url:
        url = os.environ.get("DATABASE_URL", "").strip()

    if not url:
        # 默认 SQLite — 使用 aiosqlite 异步驱动
        return "sqlite+aiosqlite:///data/users.db"

    return _convert_to_async(url)
