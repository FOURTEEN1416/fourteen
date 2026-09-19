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

__all__ = [
    "is_production",
    "is_explicit_dev",
    "resolve_api_key_enabled",
    "get_database_url",
    "PLACEHOLDER_API_KEY",
    "UNSAFE_API_KEYS",
]

# ── API Key 认证开关的取值口径（唯一真源）─────────────────

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def resolve_api_key_enabled() -> bool:
    """API Key 认证是否启用 —— **唯一真源**。

    1/true/yes/on → True；0/false/no/off → False；未设或未知值 → 生产默认 True。

    ⚠️ 2026-09-19 审查（open-code-review）：该解析曾被复制到
    `api/app_factory.py` / `main.py` / `scripts/preflight_check.py` **三处**，
    且口径不一致（app_factory 只认 `"true"`，另两处还认 `1/yes/on`）。
    而生产入口是 `uvicorn api.run_api:app`，**`main.py` 的预检根本不执行**
    （systemd unit 实证）—— 运维若写 `API_KEY_ENABLED=yes`，
    main.py 会拒绝启动，app_factory 却会**静默关闭认证**。
    统一到本函数后，三处行为不可能再分叉。
    """
    raw = os.environ.get("API_KEY_ENABLED", "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return is_production()


# ── 生产环境标记值 ──────────────────────────────────────

_PROD_MARKERS = frozenset({"prod", "production"})
# 显式开发环境标记（仅这些值允许不安全默认值）
_DEV_MARKERS = frozenset({"dev", "development"})

# 公开模板/弱默认 API Key —— 生产或启用认证的非 dev 路径必须拒绝
# （.env.example 历史占位 + 现行占位 + 常见弱值）
PLACEHOLDER_API_KEY = "CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN"
UNSAFE_API_KEYS = frozenset({
    "",
    "changeme",
    "change_me",
    "change-me",
    "default",
    "test",
    "api_key",
    "your-api-key",
    "your-api-key-for-frontend",
    PLACEHOLDER_API_KEY,
    "REPLACE_ME_WITH_RANDOM_32_PLUS_CHAR_SECRET",
})


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


def is_explicit_dev() -> bool:
    """仅当 AI_GF_ENV/APP_ENV/ENV **显式** 为 dev/development 时返回 True。

    - 生产标记优先：任一变量为 prod/production → False（即使另一变量写 dev）
    - 变量缺失 / test / staging / 任意未知值 → False（fail-closed，不落入 dev 兜底）

    用途：JWT DEV 密钥、占位 API_KEY 等不安全默认值只允许出现在显式 dev。
    """
    if is_production():
        return False
    for var in ("AI_GF_ENV", "APP_ENV", "ENV"):
        val = os.environ.get(var, "").strip().lower()
        if val in _DEV_MARKERS:
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
