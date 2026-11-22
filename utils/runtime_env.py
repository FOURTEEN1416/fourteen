"""运行环境判定 —— 轻量唯一真源（2026-11-22 UTC 口径批 L8 修复）。

此前环境判定只存在于 ``api.runtime_config``；``utils.time_caliber`` 等
**底层工具**为拿 ``is_production()`` 不得不运行时导入 api 包，而
``import api`` 会经 ``api/__init__ → app_factory → auth_jwt`` 拉起重链——
裸环境（无 ``JWT_SECRET`` 且非显式 dev）在 import 阶段即抛 ``RuntimeError``
（本批前置探针实证），时间口径版本闸的检查根本执行不到（与 purge 脚本
``308ae3a`` 修掉的「脚本导入 api 包」同类缺陷）。

故把环境判定**下沉**到本模块：只依赖标准库、禁止导入 ``api/*``；
``api.runtime_config`` 改为从这里 re-export，对外 API 与语义不变。
"""

from __future__ import annotations

import os

__all__ = ["is_production", "is_explicit_dev"]

# ── 环境标记值 ──────────────────────────────────────────

#: 生产环境标记值
_PROD_MARKERS = frozenset({"prod", "production"})
#: 显式开发环境标记（仅这些值允许不安全默认值）
_DEV_MARKERS = frozenset({"dev", "development"})

#: 环境变量检测顺序（高 → 低）
_ENV_VARS = ("AI_GF_ENV", "APP_ENV", "ENV")


def is_production() -> bool:
    """判断当前运行环境是否为生产环境。

    检测顺序（任一命中即返回 True）：
    1. AI_GF_ENV  — 项目专用环境变量（最高优先级）
    2. APP_ENV    — 通用应用环境变量
    3. ENV        — 兼容旧版 / 通用 ENV 变量
    """
    for var in _ENV_VARS:
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
    for var in _ENV_VARS:
        val = os.environ.get(var, "").strip().lower()
        if val in _DEV_MARKERS:
            return True
    return False
