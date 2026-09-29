"""角色模板面（W12 阶段1+2）— 平台无主卡的只读暴露、服务端克隆与注册分发。

任务包：W12 角色模板面 · 阶段1 + 阶段2 注册分发钩子（任务包文档已随 2026-09-29 文档清理批删除，背景见 docs/history/ 归档交接书）。
上游归属真源：`api/routers/character_routes.py`（W1 owner 唯一制）。

契约边界：
- **只读模板面**：仅列「无主（`card_owner_key == ""`）且过策展清单」的卡摘要，
  不含 persona 正文（personality / mes_example / first_mes 等不出面）。
- **克隆 ≠ 认领**：克隆产出**新 id + 归属调用者**的独立副本，模板文件零字节改动；
  这与被 W1 修掉的旧 bug（共享同一张卡的全局 is_active 互相踩）语义相反。
- **不扩大机器面**：端点只认 Bearer 主体（无主体 401），机器 API Key 不放行。
- **防枚举**：不存在 / 非无主 / 未过策展 / 总开关关闭 → 统一 404。
- **阶段2（注册分发）**：`provision_initial_character` 与 clone 端点共用同一
  `clone_template_for_user` 核心（克隆语义唯一 owner），分发后写
  `user_active_characters` 个人激活绑定；**任何失败都不阻断注册**（诚实返回 None）。

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.database import UserActiveCharacter
from api.routers.character_routes import (
    _delete_character_file,
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


# ── 克隆核心（阶段1 端点与阶段2 注册分发共用，克隆语义唯一 owner）────────


class TemplateNotAvailableError(Exception):
    """模板不可用：总开关关闭 / 不在盘上 / 非无主 / 未过策展（端点侧统一 404 防枚举）。"""


class TemplateSaveError(Exception):
    """新卡写盘失败（磁盘 / 权限），调用方据此区分 500 与「分发降级」。"""


def _new_clone_id() -> str:
    candidate = str(uuid.uuid4())[:8]
    while _load_character(candidate) is not None:
        candidate = str(uuid.uuid4())[:8]
    return candidate


def clone_template_for_user(template_id: str, user_id: int) -> dict[str, str]:
    """把策展模板克隆为归属 `user_id` 的私有副本，返回 `{id, name}`。

    保真克隆：整卡字段原样带走（mes_example / creator_notes 等不丢失），
    仅换身份三件事——新 id、归属该用户、个人未激活；不调度爬虫与知识索引
    （知识索引按 W5 ensure_index 冷加载在首次使用时自建）。
    """
    cfg = _load_template_config()
    template = _load_character(template_id) if cfg["enabled"] else None
    resolved_id = str((template or {}).get("id") or "").strip()
    if template is None or not _is_curated_template(resolved_id, template, cfg):
        raise TemplateNotAvailableError(f"模板不可用: {template_id}")

    new_id = _new_clone_id()
    now = datetime.now(tz=timezone.utc).isoformat()
    clone = dict(template)
    clone["id"] = new_id
    clone["user_id"] = str(user_id)
    clone["is_active"] = False
    clone["created_at"] = now
    clone["updated_at"] = now
    clone["version"] = 1

    if not _save_character(new_id, clone):
        raise TemplateSaveError(f"模板 {resolved_id} 克隆写盘失败")
    logger.info("模板已克隆: %s → %s (owner=%s)", resolved_id, new_id, user_id)
    return {"id": new_id, "name": str(clone.get("name") or "")}


# ── 阶段2：注册分发（新用户冷启动即有初始角色）─────────────────


async def bind_active_character(
    db: AsyncSession, user_id: int, character_id: str
) -> None:
    """把角色设为该用户的**个人激活**角色（`user_active_characters` 一行 upsert）。

    与 `character_routes.activate_character` 的真人分支同形：只写表、不改卡文件的全局
    `is_active`（W1 · D2 口径：激活是个人选择，写进共享卡文件会互相覆盖）。
    """
    result = await db.execute(
        select(UserActiveCharacter).where(UserActiveCharacter.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        db.add(UserActiveCharacter(user_id=user_id, character_id=character_id))
    else:
        row.character_id = character_id
    await db.commit()


def _registration_candidates(cfg: dict[str, Any]) -> list[str]:
    """分发候选**顺序**：`seed_on_register` 逐项 → 策展面（`visible_ids` 声明序；
    白名单未填时按卡目录序）。

    本函数只排顺序，不做可用性判定——无主 / 过策展 / 在盘 三关由
    `clone_template_for_user` 逐项把关，不可用即顺延到下一张。
    """
    face = list(cfg["visible_ids"]) or [
        str(card.get("id") or "").strip() for card in _list_all_characters()
    ]
    ordered: list[str] = []
    for cid in [*cfg["seed_on_register"], *face]:
        if cid and cid not in ordered:
            ordered.append(cid)
    return ordered


async def provision_initial_character(
    db: AsyncSession, user_id: int
) -> dict[str, str] | None:
    """注册分发钩子：按策展种子给新用户克隆一张私有卡并绑为个人激活角色。

    契约（任务书 W12 阶段2）：
    - **additive**：只影响响应新增字段，注册结果本身零改动；
    - 总开关关闭 / 无可用模板 → 返回 None（调用方据此回 `initial_character: null`）；
    - **失败可见且不阻断注册**：每一步失败都落 WARNING 日志并返回 None，绝不谎报；
    - 绑定失败时补偿删除已克隆的卡，不在盘上留用户从未索取过的孤儿副本。
    """
    uid = int(user_id)
    try:
        cfg = _load_template_config()
        if not cfg["enabled"]:
            return None
        for candidate in _registration_candidates(cfg):
            try:
                created = clone_template_for_user(candidate, uid)
            except TemplateNotAvailableError:
                continue
            except TemplateSaveError as exc:
                logger.warning("注册分发写卡失败（user=%s，模板=%s）: %s", uid, candidate, exc)
                return None
            try:
                await bind_active_character(db, uid, created["id"])
            except Exception as exc:  # noqa: BLE001 —— 绑定失败要回收副本，不能让注册陪葬
                logger.warning(
                    "注册分发激活绑定失败（user=%s，卡=%s）: %s", uid, created["id"], exc
                )
                if not _delete_character_file(created["id"]):
                    logger.warning(
                        "回收克隆卡失败，孤儿副本留存: %s (user=%s)", created["id"], uid
                    )
                return None
            logger.info(
                "注册分发初始角色: user=%s 模板=%s → 卡=%s", uid, candidate, created["id"]
            )
            return created
        logger.warning(
            "注册分发无可用模板（user=%s，seed=%s）", uid, cfg["seed_on_register"]
        )
        return None
    except Exception as exc:  # noqa: BLE001 —— 硬承诺：分发环节任何异常都不得阻断注册
        logger.warning("注册分发异常降级（user=%s）: %s", uid, exc, exc_info=True)
        return None


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
    """克隆模板为调用者私有的独立副本（幂等不做约束：用户主动行为，允许多份）。"""
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录主体")
    try:
        created = clone_template_for_user(template_id, int(principal.user_id))
    except TemplateNotAvailableError as exc:
        # 不存在 / 非无主 / 未过策展 / 总开关关闭 → 统一 404（防状态码枚举）
        raise HTTPException(status_code=404, detail="模板不存在") from exc
    except TemplateSaveError as exc:
        raise HTTPException(status_code=500, detail="克隆模板失败") from exc
    return {**created, "status": "created"}
