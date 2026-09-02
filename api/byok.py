"""BYOK 强制策略（W1，2026-08-28）：用户自带 Key，平台不补贴 token。

开关：config system.yaml → llm.byok_required（经 LLMConfig.byok_required）。
开启后：非 admin 用户消费 LLM 前必须已配置自己的 api_key（users.llm_config）；
admin 的 key 视为平台运营 key 不受限。未开启时行为完全不变。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger("api.byok")

ERROR_CODE = "BYOK_REQUIRED"


def byok_required(cfg: Any) -> bool:
    # 仅认真实 bool 属性；Mock/任意对象默认关闭（防御测试与异常装配环境）
    val = getattr(cfg, "byok_required", False) if cfg is not None else False
    return val is True


def ensure_user_has_key(user: Any, llm_cfg: Any) -> None:
    """消费 LLM 的端点前置检查。user 为 ORM User（或 None）；llm_cfg 为 SystemConfig.llm。"""
    if not byok_required(llm_cfg):
        return
    role = getattr(user, "role", "") if user is not None else "admin"  # 未登录上下文视为平台侧
    if role == "admin":
        return
    uc = getattr(user, "llm_config", None) if user is not None else None
    if isinstance(uc, dict) and (uc.get("api_key") or "").strip():
        return
    raise HTTPException(
        status_code=403,
        detail="本平台由用户自带 API Key 运行：请先在「设置 → LLM 配置」中填入你自己的 API Key",
        headers={"X-Error-Code": ERROR_CODE},
    )
