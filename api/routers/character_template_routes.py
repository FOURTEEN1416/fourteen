"""角色模板面（W12 阶段1）— 平台无主卡的只读暴露与服务端克隆。

任务包：`docs/board/TASK_PACKAGE_W12_角色模板面.md`（B 阶段1）。
上游归属真源：`api/routers/character_routes.py`（W1 owner 唯一制）。

契约边界：
- **只读模板面**：仅列「无主（`card_owner_key == ""`）且过策展清单」的卡摘要，
  不含 persona 正文（personality / mes_example / first_mes 等不出面）。
- **克隆 ≠ 认领**：克隆产出**新 id + 归属调用者**的独立副本，模板文件零字节改动；
  这与被 W1 修掉的旧 bug（共享同一张卡的全局 is_active 互相踩）语义相反。
- **不扩大机器面**：端点只认 Bearer 主体（无主体 401），机器 API Key 不放行。
- **防枚举**：不存在 / 非无主 / 未过策展 / 总开关关闭 → 统一 404。

路由前缀独立为 `/api/character-templates`——不得挂在 `/api/characters` 下，
否则会被先前注册的 `/api/characters/{character_id}` 抢匹配（把 `templates`
当 character_id 的静默语义错误）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Security

from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.routers.character_routes import (
    _list_all_characters,
    _load_character,
    _save_character,
    card_owner_key,
)
from utils.project_paths import project_path

logger = logging.getLogger("api.character_template_routes")

router = APIRouter(prefix="/api/character-templates", tags=["character-templates"])

_CONFIG_PATH: Path = project_path("config", "character_templates.yaml")

# 摘要描述截断口径与 GET /api/presets 一致（模板面只给选卡用的简介）
_DESCRIPTION_MAX_CHARS = 500


def _load_template_config() -> dict[str, Any]:
    """读策展清单；文件缺失/损坏按 fail-closed 处理（等同 enabled=false）。"""
    cfg: dict[str, Any] = {
        "enabled": False,
        "visible_ids": [],
        "hidden_ids": [],
        "seed_on_register": [],
    }
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = bool(raw.get("enabled", False))
    for key in ("visible_ids", "hidden_ids", "seed_on_register"):
        values = raw.get(key)
        cfg[key] = (
            [str(v) for v in values if str(v).strip()]
            if isinstance(values, list)
            else []
        )
    return cfg


def _card_tags(card: dict[str, Any]) -> list[str]:
    tags = card.get("tags")
    if not tags and isinstance(card.get("data"), dict):
        tags = card["data"].get("tags")  # SillyTavern V2 嵌套形态
    if not isinstance(tags, list):
        return []
    return [str(t) for t in tags if str(t).strip()]


def _is_curated_template(
    template_id: str, card: dict[str, Any], cfg: dict[str, Any]
) -> bool:
    """无主 + 过策展：白名单非空时须命中，黑名单永远优先。"""
    if not template_id or card_owner_key(card) != "":
        return False
    if template_id in cfg["hidden_ids"]:
        return False
    visible = cfg["visible_ids"]
    return not visible or template_id in visible


def _template_summary(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(card.get("id") or ""),
        "name": str(card.get("name") or ""),
        "description": str(card.get("description") or "")[:_DESCRIPTION_MAX_CHARS],
        "tags": _card_tags(card),
    }


@router.get("")
async def list_character_templates(
    principal: AuthPrincipal | None = Security(get_optional_principal),
) -> dict[str, Any]:
    """只读模板面：仅列无主且过策展的卡摘要（不含 persona 正文）。"""
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录主体")
    cfg = _load_template_config()
    if not cfg["enabled"]:
        return {"templates": [], "total": 0}
    templates: list[dict[str, Any]] = []
    for card in _list_all_characters():
        template_id = str(card.get("id") or "").strip()
        if _is_curated_template(template_id, card, cfg):
            templates.append(_template_summary(card))
    return {"templates": templates, "total": len(templates)}


@router.post("/{template_id}/clone", status_code=201)
async def clone_character_template(
    template_id: str,
    principal: AuthPrincipal | None = Security(get_optional_principal),
) -> dict[str, Any]:
    """克隆模板为调用者私有的独立副本（幂等不做约束：用户主动行为，允许多份）。

    保真克隆：整卡字段原样带走（mes_example / creator_notes 等不丢失），
    仅换身份三件事——新 id、归属调用者、个人未激活；不调度爬虫与知识索引
    （知识索引按 W5 ensure_index 冷加载在首次使用时自建）。
    """
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录主体")
    cfg = _load_template_config()
    # 不存在 / 非无主 / 未过策展 / 总开关关闭 → 统一 404（防状态码枚举）
    template = _load_character(template_id) if cfg["enabled"] else None
    resolved_id = str((template or {}).get("id") or "").strip()
    if template is None or not _is_curated_template(resolved_id, template, cfg):
        raise HTTPException(status_code=404, detail="模板不存在")

    new_id = str(uuid.uuid4())[:8]
    while _load_character(new_id) is not None:
        new_id = str(uuid.uuid4())[:8]

    now = datetime.now(tz=timezone.utc).isoformat()
    clone = dict(template)
    clone["id"] = new_id
    clone["user_id"] = str(principal.user_id)
    clone["is_active"] = False
    clone["created_at"] = now
    clone["updated_at"] = now
    clone["version"] = 1

    if not _save_character(new_id, clone):
        raise HTTPException(status_code=500, detail="克隆模板失败")
    logger.info(
        "模板已克隆: %s → %s (owner=%s)", resolved_id, new_id, clone["user_id"]
    )
    return {"id": new_id, "name": str(clone.get("name") or ""), "status": "created"}
