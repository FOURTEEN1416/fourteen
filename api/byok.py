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


async def load_user_llm_config(user_id: int, orchestrator: Any, db: Any) -> dict | None:
    """HTTP/WS 共用账号查验和 BYOK 策略；读取失败不偷偷降级为平台账号。"""
    from api.database import User

    try:
        user = await db.get(User, user_id)
    except Exception as exc:
        logger.warning("读取账号 %s 的模型配置失败: %s", user_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="用户配置暂不可用") from exc
    if user is None or getattr(user, "is_active", True) is False:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    component = (getattr(orchestrator, "components", None) or {}).get("config")
    cfg = getattr(getattr(component, "config", None), "llm", None)
    ensure_user_has_key(user, cfg)
    raw = getattr(user, "llm_config", None)
    return raw if isinstance(raw, dict) and raw else None


async def session_llm(session_key: str, orchestrator: Any) -> Any:
    """后台任务与请求使用相同账号策略；数据库失败/停用账号均拒绝借用平台。"""
    from api.database import _async_session
    from llm_provider import select_request_llm
    from utils.session_key import owner_of

    owner = owner_of(session_key)
    default = (getattr(orchestrator, "components", None) or {}).get("llm")
    if owner is None:
        cfg = (getattr(orchestrator, "components", None) or {}).get("config")
        ensure_user_has_key(None, getattr(getattr(cfg, "config", None), "llm", None))
        return default
    async with _async_session() as db:
        config = await load_user_llm_config(owner, orchestrator, db)
    return select_request_llm(default, owner, config)


def ensure_user_has_key(user: Any, llm_cfg: Any) -> None:
    """消费 LLM 的端点前置检查。user 为 ORM User（或 None）；llm_cfg 为 SystemConfig.llm。"""
    if not byok_required(llm_cfg):
        return
    role = getattr(user, "role", "") if user is not None else ""
    if role == "admin":
        return
    uc = getattr(user, "llm_config", None) if user is not None else None
    if isinstance(uc, dict):
        if (uc.get("api_key") or "").strip():
            return
        providers = uc.get("providers") or {}
        chain = uc.get("fallback_chain") or list(providers)
        if uc.get("provider") == "auto" and chain and all((providers.get(p) or {}).get("api_key") for p in chain):
            return
    raise HTTPException(
        status_code=403,
        detail="本平台由用户自带 API Key 运行：请先在「设置 → LLM 配置」中填入你自己的 API Key",
        headers={"X-Error-Code": ERROR_CODE},
    )
