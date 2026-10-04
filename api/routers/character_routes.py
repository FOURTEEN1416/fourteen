"""统一角色管理API — 桥接新旧两套角色体系"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal, verify_token
from api.database import UserActiveCharacter, WechatBinding, get_db
from api.deps import deps
from api.path_security import sanitize_id
from my_character.persona_card import PersonaCardV3
from shisi.knowledge.character_knowledge_service import get_knowledge_service
from shisi.knowledge.crawler_adapter import get_crawler_adapter
from shisi.voice.character_voice import CharacterVoiceManager
from utils.character_helpers import normalize_character_card, sanitize_character_name
from utils.project_paths import project_path

PRESETS_DIR = project_path("data", "presets")

logger = logging.getLogger("api.character_routes")

router = APIRouter(prefix="/api", tags=["character"])

CHARACTERS_DIR = project_path("config", "characters")


# ── 资源归属（W1 唯一 owner）─────────────────────────────
#
# 角色卡（`config/characters/*.json`）同时承担两种语义，必须分清：
# 1. **私人实例**：`user_id` 是某个注册用户 id → 只有本人与管理员可见/可改/可删。
# 2. **公共模板 / 无法判归属的存量卡**：`user_id` 为空、`default`、`system` 或任何
#    不是注册用户 id 的值 → 归平台（管理员）管理；**不得**在首次访问时被自动
#    认领给访问者（旧实现把全局 `is_active` 当个人选择，首个访问者即「继承」）。
#
# 机器侧纯 API Key（无 Bearer，部署脚本 / E2E / 测试）是独立的服务面契约：
# 不做归属收窄，行为与改造前一致——归属校验只在存在 Bearer 主体时生效。

_UNOWNED_MARKERS = {"", "default", "system"}


def card_owner_key(card: dict[str, Any]) -> str:
    """卡片的归属键；无主（公共模板 / 存量卡）返回空串。"""
    owner = str(card.get("user_id") or "").strip()
    return "" if owner in _UNOWNED_MARKERS else owner


def card_access_allowed(card: dict[str, Any], principal: AuthPrincipal | None) -> bool:
    """主体是否有权读写该卡片。principal 为 None = 机器面（不限制）。"""
    if principal is None:
        return True
    if principal.role == "admin":
        return True
    owner = card_owner_key(card)
    return bool(owner) and owner == str(principal.user_id)


async def require_character_access(
    character_id: str,
    principal: AuthPrincipal | None = Security(get_optional_principal),
) -> dict[str, Any]:
    """FastAPI 依赖：角色资源归属校验（所有角色子资源路由共用）。

    - 机器面（无 Bearer）：不干预，交由各路由维持既有行为。
    - 有主体但无权：一律 404（不区分「不存在」与「无权限」，防状态码枚举他人卡）。
    """
    if principal is None:
        return {}
    data = _load_character(character_id)
    if data is None or not card_access_allowed(data, principal):
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return data


def _owner_for_write(principal: AuthPrincipal | None, declared: str | None) -> str:
    """写侧归属：普通用户的卡片归属**只能**由认证主体导出，客户端自封无效。"""
    if principal is not None and principal.role != "admin":
        return str(principal.user_id)
    declared = str(declared or "").strip()
    return declared or "default"


async def _personal_active_character_id(db: AsyncSession, user_id: int) -> str | None:
    """该用户当前激活的角色卡 id（D2：个人选择按用户持有）。"""
    result = await db.execute(
        select(UserActiveCharacter).where(UserActiveCharacter.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    return row.character_id if row else None


def _overlay_personal_activation(
    characters: list[dict[str, Any]], active_id: str | None
) -> list[dict[str, Any]]:
    """把「个人激活态」叠加到卡片视角上（不写回卡文件）。

    卡文件里的 `is_active` 不是某人的选择，故对真实用户一律以本表为准：
    没有个人记录就是「都没有激活」，由前端按首张卡兜底，避免多张卡同时点亮。
    """
    for card in characters:
        card["is_active"] = bool(active_id) and str(card.get("id")) == active_id
    return characters


# ── 请求/响应模型 ────────────────────────────────────────


class UnifiedCharacterCreate(BaseModel):
    name: str
    description: str = ""
    personality: dict = {}
    speaking_style: dict = {}
    catchphrases: list[str] = []
    core_anchors: list[str] = []
    user_id: str = "default"


class UnifiedCharacterUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    personality: dict | None = None
    speaking_style: dict | None = None
    catchphrases: list[str] | None = None
    core_anchors: list[str] | None = None


class PersonaUpdate(BaseModel):
    personality: dict | None = None
    speaking_style: dict | None = None
    catchphrases: list[str] | None = None
    core_anchors: list[str] | None = None


class CharacterGenerateRequest(BaseModel):
    description: str
    archetype: str = "温柔"
    user_id: str = "default"


class CharacterPreviewResponse(BaseModel):
    reply: str
    persona: dict[str, Any]


_ACTIVE_ID_FP: str = ""
_ACTIVE_ID_VALUE: str = ""


def get_active_character_id() -> str:
    """返回控制端当前激活角色；未配置时回退 default。

    P1-10（2026-09-21 审查修复）：本函数在 web 聊天**每条消息**路径上被调
    （chat_routes._resolve_character_id），旧实现对目录内全部 JSON 逐张
    open+json.load（现役 41 张）。现在先算目录轻量指纹（文件名+mtime+size），
    未变化直接复用上次的 active id；任何卡变更（含 is_active 切换、增删卡）
    都会改指纹，不会读到陈旧值。

    🔴 2026-09-22 块E 缓存污染修复：`_list_all_characters` 可能被**替换**
    （测试替身 / 未来热插拔角色源），此时目录指纹不变、缓存判定依然成立，
    于是继续返回与当前数据源无关的陈旧值 —— 缓存击穿的不是存储层而是
    **数据源身份**。缓存键补齐「当前数据源 callable 的 id」：一旦调用方
    把 `character_routes._list_all_characters` 换掉，缓存立即失效。
    """
    global _ACTIVE_ID_FP, _ACTIVE_ID_VALUE
    chars_dir = _get_characters_dir()
    try:
        parts: list[str] = []
        for f in sorted(chars_dir.glob("*.json")):
            st = f.stat()
            parts.append(f"{f.name}:{st.st_mtime_ns}:{st.st_size}")
        fp = "|".join(parts)
    except OSError:
        fp = ""
    # 数据源身份参与缓存键：换掉 _list_all_characters 即失效
    source_id = id(_list_all_characters)
    cache_key = f"{fp}#{source_id}"
    if cache_key == _ACTIVE_ID_FP and _ACTIVE_ID_VALUE:
        return _ACTIVE_ID_VALUE
    active = "default"
    for character in _list_all_characters(normalize=False):
        if character.get("is_active"):
            active = str(character.get("id") or "default")
            break
    _ACTIVE_ID_FP, _ACTIVE_ID_VALUE = cache_key, active
    return active


class MemoryFactCreate(BaseModel):
    content: str
    category: str = "general"
    tags: list[str] = []


# ── 数据持久化工具 ───────────────────────────────────────


def _get_characters_dir() -> Path:
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    return CHARACTERS_DIR


def _character_path(character_id: str) -> Path:
    safe_id = sanitize_id(character_id)
    if not safe_id:
        raise HTTPException(status_code=400, detail="Invalid character ID")
    return _get_characters_dir() / f"{safe_id}.json"


def _load_character(character_id: str) -> dict[str, Any] | None:
    """加载角色数据。

    查找顺序：
    1. 按 {character_id}.json 文件名直接查
    2. 遍历所有角色文件，匹配 JSON 内部 id 字段
    3. 都找不到返回 None
    """
    # 1. 直接按文件名查
    path = _character_path(character_id)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("加载角色 %s 失败: %s", character_id, e)
            return None

    # 2. 遍历匹配 JSON 内部 id 字段（兼容 id 与文件名不一致的情况）
    try:
        for f in _get_characters_dir().glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("id") == character_id:
                    return data
            except (json.JSONDecodeError, OSError):
                continue
    except OSError as e:
        logger.error("遍历角色目录失败: %s", e)
    return None


def _find_character_file_by_id(character_id: str) -> Path | None:
    """根据 JSON 内部 id 字段查找对应的文件路径。"""
    try:
        for f in _get_characters_dir().glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("id") == character_id:
                    return f
            except (json.JSONDecodeError, OSError):
                continue
    except OSError:
        return None
    return None


def _save_character(character_id: str, data: dict[str, Any]) -> bool:
    # 优先用直接文件名；若不存在但能按 id 找到原文件，则写回原文件路径
    path = _character_path(character_id)
    if not path.exists():
        found = _find_character_file_by_id(character_id)
        if found is not None:
            path = found
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        logger.error("保存角色 %s 失败: %s", character_id, e)
        return False


def _delete_character_file(character_id: str) -> bool:
    path = _character_path(character_id)
    if not path.exists():
        found = _find_character_file_by_id(character_id)
        if found is not None:
            path = found
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.error("删除角色 %s 失败: %s", character_id, e)
            return False
    return False


def _list_all_characters(normalize: bool = True) -> list[dict[str, Any]]:
    chars_dir = _get_characters_dir()
    characters: list[dict[str, Any]] = []
    for f in sorted(chars_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            characters.append(normalize_character_card(data) if normalize else data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("读取角色文件 %s 失败: %s", f.name, e)
    return characters


def _schedule_character_crawl(character_id: str, name: str, card: dict[str, Any]) -> None:
    """在后台触发角色卡索引与爬虫补全，不阻塞 API 响应。"""
    async def _run() -> None:
        try:
            await asyncio.to_thread(get_crawler_adapter().crawl_and_index, character_id, name, card)
        except Exception:  # noqa: BLE001
            logger.warning("角色 %s 后台爬虫/索引失败（非阻塞）", character_id, exc_info=True)

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        # 无运行事件循环时降级到同步线程
        import threading
        threading.Thread(target=get_crawler_adapter().crawl_and_index,
                         args=(character_id, name, card), daemon=True).start()


def _build_character_data(
    name: str,
    description: str = "",
    personality: dict[str, Any] | None = None,
    speaking_style: dict[str, Any] | None = None,
    catchphrases: list[str] | None = None,
    core_anchors: list[str] | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc).isoformat()
    character_id = str(uuid.uuid4())[:8]
    style = dict(speaking_style or {})
    if catchphrases:
        style["catchphrases"] = catchphrases
    return {
        "id": character_id,
        "name": sanitize_character_name(name),
        "description": description,
        "schema_version": 1,
        "personality": personality or {},
        "speaking_style": style,
        "core_anchors": core_anchors or [],
        "user_id": user_id,
        "is_active": False,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }


# ── 角色卡安全校验（P0 修复批 2026-10-04，F1）────────────
#
# validate_card（shisi/character/validator.py）此前没有任何 API 写路径调用它：
# 三个主链写端点（create / update / import）不校验即落盘，注入/XSS 与不安全
# 内容直进角色卡真源。此处收口为写路径唯一 owner，统一 400 出口，不让
# ValidationError 落全局 500 handler。
# （PUT /{id}/persona-card 经 char_mgr.update_character 已有同款校验：
# persona_card_routes 调 manager.update_character → manager 内 validate_card。）


def _to_validatable_card(char_data: dict[str, Any]) -> Any:
    """把统一扁平卡（或含 V2/V3 嵌套块的导入卡）转成 validator 可消费的 CharaCardV2。

    - 顶层扁平字段优先，缺失回退嵌套 data 块（V2/V3 导入卡才携带 system_prompt 等）；
    - personality 数值维度 dict 摊平为文本（不走 CharaCardV2Parser，避免
      _from_standard 把同步进嵌套块的 dict personality 硬塞进 str 字段误报 400）；
    - 空 name 以占位符过 CharacterData 非空校验：空名在 create/import 已有前置
      400，更新路径不得因存量空名卡被本闸误伤。
    """
    from shisi.character.models import CharaCardV2, CharacterData

    nested = char_data.get("data") if isinstance(char_data.get("data"), dict) else {}

    def _field(key: str) -> str:
        value = char_data.get(key)
        if value in (None, "", {}, []):
            value = nested.get(key)
        if isinstance(value, dict):
            return "，".join(f"{k}：{v}" for k, v in value.items() if str(v).strip())
        return str(value or "")

    return CharaCardV2(
        spec="chara_card_v2",
        data=CharacterData(
            name=_field("name") or "未命名",
            description=_field("description"),
            personality=_field("personality"),
            scenario=_field("scenario"),
            first_mes=_field("first_mes"),
            mes_example=_field("mes_example"),
            system_prompt=_field("system_prompt"),
            creator_notes=_field("creator_notes"),
        ),
    )


def _validate_card_or_400(char_data: dict[str, Any]) -> None:
    """F1 写路径唯一安全校验 owner：validate_card 不过 → 400（捕获 ValidationError）。"""
    from shisi.character.validator import ValidationError, validate_card_strict

    try:
        validate_card_strict(_to_validatable_card(char_data))
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail="角色卡未通过安全校验: " + "；".join(e.errors),
        ) from e


def _enforce_byok_for_generation(principal: AuthPrincipal | None) -> None:
    """F2（P0 修复批 2026-10-04）：AI 生成角色卡消费 LLM 前的 BYOK 闸门。

    与 chat 链同语义（api/byok.ensure_user_has_key）：byok_required=true 时，
    无自带 key 的非 admin 用户 403（BYOK_REQUIRED）；false 或平台未装配配置
    组件时放行。机器面（无 Bearer）没有可挂靠的用户 key，同样受闸——平台
    不补贴匿名生成。
    """
    from api.byok import ensure_user_has_key

    orch = deps.orch
    component = (
        (getattr(orch, "components", None) or {}).get("config") if orch is not None else None
    )
    llm_cfg = getattr(getattr(component, "config", None), "llm", None)
    # isinstance 收窄：FastAPI 注入为 AuthPrincipal | None；直调路由函数（既有
    # 单测形态）拿到的是未解析的 Security 哨兵，按机器面（无用户 key）处理。
    user = principal.user if isinstance(principal, AuthPrincipal) else None
    ensure_user_has_key(user, llm_cfg)


# ── API 端点 ────────────────────────────────────────────


@router.get("/characters")
async def list_characters(
    user_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    db: AsyncSession = Depends(get_db),
):
    """列出角色：私人卡只对本人（与管理员）可见，无主存量卡只对平台面可见。

    客户端传 `user_id` 只能**收窄**已授权的范围，不能借此越权读取他人卡片。
    """
    characters = [
        c for c in _list_all_characters() if card_access_allowed(c, principal)
    ]
    if user_id:
        characters = [c for c in characters if c.get("user_id") == user_id]
    if search:
        search_lower = search.lower()
        characters = [
            c
            for c in characters
            if search_lower in c.get("name", "").lower()
            or search_lower in c.get("description", "").lower()
        ]
    if principal is not None:
        active_id = await _personal_active_character_id(db, principal.user_id)
        characters = _overlay_personal_activation(characters, active_id)
    return {"characters": characters, "total": len(characters)}


@router.post("/characters", status_code=201)
async def create_character(
    req: UnifiedCharacterCreate,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
):
    """创建新角色（归属由认证主体导出，客户端 user_id 不能自封）"""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="角色名称不能为空")

    data = _build_character_data(
        name=req.name,
        description=req.description,
        personality=req.personality,
        speaking_style=req.speaking_style,
        catchphrases=req.catchphrases,
        core_anchors=req.core_anchors,
        user_id=_owner_for_write(principal, req.user_id),
    )
    _validate_card_or_400(data)  # F1：写路径唯一安全校验 owner（失败 400，不落盘）
    if not _save_character(data["id"], data):
        raise HTTPException(status_code=500, detail="保存角色失败")
    _schedule_character_crawl(data["id"], data["name"], data)
    logger.info("角色已创建: %s (%s) owner=%s", data["name"], data["id"], data["user_id"])
    return {"id": data["id"], "name": data["name"], "status": "created"}


@router.get("/characters/{character_id}")
async def get_character(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    _owned: dict[str, Any] = Depends(require_character_access),
    db: AsyncSession = Depends(get_db),
):
    """获取角色详情（含音色配置）"""
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    # 自动升级 schema
    if data.get("schema_version", 0) < 1:
        data["schema_version"] = 1
        if "personality" not in data:
            data["personality"] = {}
        if "speaking_style" not in data:
            data["speaking_style"] = {}
        if "core_anchors" not in data:
            data["core_anchors"] = []
        _save_character(character_id, data)

    # 个人激活态（不读卡文件的全局 is_active）
    if principal is not None:
        active_id = await _personal_active_character_id(db, principal.user_id)
        _overlay_personal_activation([data], active_id)

    # 附加音色配置（如果存在）
    try:
        voice_mgr = CharacterVoiceManager()
        voice_config = voice_mgr.get_voice_config(character_id)
        if voice_config:
            data["voice_config"] = voice_config
    except Exception as e:
        logger.debug("获取角色音色配置失败: %s", e)

    return data


def _invalidate_knowledge_index(character_id: str) -> None:
    """角色内容变化后刷新知识的 card 来源并派生重建（外部来源保留）。

    旧索引一旦落盘，ensure_index 优先磁盘加载且永不重建——卡更新/换绑后
    知识库停留在旧内容（2026-09-17 排查发现绑定卡索引仅含建卡初期琐碎块，
    卡内丰富的描述/锚点从未进入检索）。

    W5 语义变更：旧实现 ``svc.clear + unlink 整份索引`` —— 上传文档与
    抓取/增强内容只存在于索引中，任何改卡/激活都会把它们一并销毁。现改为
    ``refresh_card_source``：card 来源整体替换，上传与抓取来源原样保留，
    索引在源存储锁内派生重建；其他 worker 下次 ensure 按版本自动跟进。
    卡文件已不存在（角色删除中）则连源存储一并清理。
    """
    try:
        svc = get_knowledge_service()
        raw = _load_character(character_id)
        if raw is None:
            svc.forget(character_id)
            logger.info("角色卡已不存在，知识索引与源存储已清理: %s", character_id)
            return
        count, changed = svc.refresh_card_source(character_id, raw=raw)
        logger.info("知识 card 来源已刷新（%d 块, changed=%s），外部来源保留: %s",
                    count, changed, character_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识索引刷新失败（非阻塞）: %s", e)


@router.put("/characters/{character_id}")
async def update_character(
    character_id: str,
    req: UnifiedCharacterUpdate,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """更新角色（合并更新，只传要改的字段）"""
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    # 合并更新
    if req.name is not None:
        data["name"] = req.name
    if req.description is not None:
        data["description"] = req.description
    if req.personality is not None:
        existing = data.get("personality", {})
        existing.update(req.personality)
        data["personality"] = existing
    if req.speaking_style is not None:
        existing = data.get("speaking_style", {})
        existing.update(req.speaking_style)
        data["speaking_style"] = existing
    if req.catchphrases is not None:
        style = data.get("speaking_style", {})
        style["catchphrases"] = req.catchphrases
        data["speaking_style"] = style
    if req.core_anchors is not None:
        data["core_anchors"] = req.core_anchors

    data["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    data["version"] = data.get("version", 1) + 1

    _validate_card_or_400(data)  # F1：合并结果同样过闸（失败 400，不半写真源）

    if not _save_character(character_id, data):
        raise HTTPException(status_code=500, detail="保存角色失败")

    # 清除人设缓存，确保下次对话使用最新角色卡
    if deps.orch and hasattr(deps.orch, "invalidate_character_persona_cache"):
        deps.orch.invalidate_character_persona_cache(character_id)

    # 卡内容已变 → 知识索引同步失效（防陈旧检索）
    _invalidate_knowledge_index(character_id)

    return {"status": "updated", "character_id": character_id}


@router.delete("/characters/{character_id}")
async def delete_character(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
    db: AsyncSession = Depends(get_db),
):
    """删除角色 —— 承诺「删除即全清」，删除范围按 owner 粒度收口（W9 缺陷 B）。

    清除面：卡文件、知识索引与源存储（W5）、人设缓存、成就行、
    绑定/好友偏好/个人激活的引用重置（不孤儿悬挂）、向量集合中
    character_id 派生、音色绑定解绑。
    """
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    if not _delete_character_file(character_id):
        raise HTTPException(status_code=500, detail="删除角色失败")

    # W5：知识索引与源存储随角色一并清理（旧实现遗留孤儿索引文件）
    _invalidate_knowledge_index(character_id)

    # W9：引用与派生面全清——成就、绑定/偏好/个人激活引用、向量派生、音色绑定
    from sqlalchemy import delete as _delete
    from sqlalchemy import update as _update

    from api.database import (
        CharacterAchievement,
        UserActiveCharacter,
        WechatBinding,
        WechatPeerPreference,
    )

    receipt: dict[str, int] = {}
    receipt["achievements"] = int((await db.execute(
        _delete(CharacterAchievement).where(
            CharacterAchievement.character_id == character_id
        )
    )).rowcount or 0)
    receipt["active_rows_reset"] = int((await db.execute(
        _update(UserActiveCharacter)
        .where(UserActiveCharacter.character_id == character_id)
        .values(character_id="default")
    )).rowcount or 0)
    receipt["binding_refs_reset"] = int((await db.execute(
        _update(WechatBinding)
        .where(WechatBinding.character_card_id == character_id)
        .values(character_card_id="default")
    )).rowcount or 0)
    receipt["peer_pref_refs_reset"] = int((await db.execute(
        _update(WechatPeerPreference)
        .where(WechatPeerPreference.character_card_id == character_id)
        .values(character_card_id="default")
    )).rowcount or 0)
    await db.commit()

    try:
        from shisi.memory.legacy.vector_memory import VectorMemory

        vm = VectorMemory()
        removed = vm.purge_owner_data([], [], character_ids=[character_id])
        receipt["vector_docs"] = int(sum(removed.values()))
    except Exception as e:  # noqa: BLE001
        logger.warning("向量 character_id 派生清理失败（非阻塞）: %s", e)

    try:
        if CharacterVoiceManager().unbind_voice(character_id):
            receipt["voice_unbound"] = 1
    except Exception as e:  # noqa: BLE001
        logger.warning("音色绑定解绑失败（非阻塞）: %s", e)

    # 清除人设缓存
    if deps.orch and hasattr(deps.orch, "invalidate_character_persona_cache"):
        deps.orch.invalidate_character_persona_cache(character_id)

    logger.info("角色已删除: %s (%s) receipt=%s", data.get("name", ""), character_id, receipt)
    return {"status": "deleted", "character_id": character_id, "receipt": receipt}


async def _optional_user_id(request: Request) -> int | None:
    """从请求头尽力提取 JWT 用户身份；无/无效 token 返回 None（不抛错）。

    供既支持 API-Key 又想联动用户级数据（如微信绑定）的端点使用。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = verify_token(auth[len("Bearer "):], expected_type="access")
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except Exception:  # noqa: BLE001
        return None


@router.post("/characters/{character_id}/activate")
async def activate_character(
    character_id: str,
    request: Request,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    _owned: dict[str, Any] = Depends(require_character_access),
    db: AsyncSession = Depends(get_db),
):
    """激活角色（设为**当前用户**的当前角色）。

    D2：激活是**个人选择**——写入 `user_active_characters`，不改卡片文件里的全局
    `is_active`。旧实现把个人选择写进共享卡文件：A 激活会把 B 的选择清掉（两用户
    互相覆盖），而卡文件同时还要承担「公共模板」语义，两套语义互相污染。

    机器面（无 Bearer，部署脚本 / 测试 / 无登录控制台）没有用户身份可挂靠，
    沿用卡片级系统默认标记。
    """
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    if principal is not None:
        result = await db.execute(
            select(UserActiveCharacter).where(
                UserActiveCharacter.user_id == principal.user_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            db.add(
                UserActiveCharacter(
                    user_id=principal.user_id, character_id=character_id
                )
            )
        else:
            row.character_id = character_id
        await db.commit()
        active_owner_id = str(principal.user_id)
        logger.info(
            "用户 %s 激活角色 %s（个人选择，未改动卡片全局 is_active）",
            principal.user_id, character_id,
        )
    else:
        # ⚠️ P1-审查 item37：必须用 normalize=False 的**原始卡**做写回——
        # _list_all_characters() 默认逐张跑 normalize_character_card（含文本清洗），
        # 把派生结果存回磁盘会用有损版本覆盖真源（历次英文卡「by 子串被抠」事故根因）。
        for c in _list_all_characters(normalize=False):
            if c.get("id") != character_id and c.get("is_active"):
                c["is_active"] = False
                _save_character(c["id"], c)

        data["is_active"] = True
        data["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
        if not _save_character(character_id, data):
            raise HTTPException(status_code=500, detail="激活角色失败")
        active_owner_id = str(data.get("user_id") or "default")

    # 同步到女友管理器
    if deps.gf and hasattr(deps.gf, "set_user_character"):
        deps.gf.set_user_character(active_owner_id, character_id)
        logger.info("角色激活已同步到女友管理器: %s → %s", active_owner_id, character_id)

    # 换绑后知识索引失效重建（旧索引可能停留在该卡早期版本的贫乏内容）
    _invalidate_knowledge_index(character_id)

    # ── 同步当前登录用户的微信绑定（2026-09-17：web 切角色 → 微信实时生效）──
    # 微信回复人设的真源是 wechat_bindings.character_card_id（UserManager 读取），
    # 旧实现只改卡文件 is_active，与微信链路断裂。此处带 JWT 时同步绑定并
    # 通过 upsert_binding 刷新运行中进程的内存缓存（无需重启）。
    # W1：用户身份只从认证主体取（不再独立解 token）；机器面保持原有容忍语义。
    web_user_id = principal.user_id if principal is not None else await _optional_user_id(request)
    if web_user_id is not None:
        result = await db.execute(
            select(WechatBinding).where(WechatBinding.user_id == web_user_id)
        )
        bindings = result.scalars().all()
        if bindings:
            for b in bindings:
                b.character_card_id = character_id
            await db.commit()
            gf = deps.gf
            if gf:
                for b in bindings:
                    await gf.upsert_binding(b.wxid, {
                        "wxid": b.wxid,
                        "character_card_id": character_id,
                    })
            logger.info(
                "角色激活已同步 %d 条微信绑定（user=%s → 角色 %s）",
                len(bindings), web_user_id, character_id,
            )

    return {"status": "activated", "character_id": character_id}


@router.get("/characters/{character_id}/persona")
async def get_character_persona(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """获取角色的人设详情"""
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    # 尝试转成 PersonaCardV3 格式返回
    try:
        card = PersonaCardV3.from_dict(data)
        persona_data = {
            "name": card.name,
            "personality": card.personality.to_dict(),
            "speaking_style": card.speaking_style.to_dict(),
            "core_anchors": card.get_default_anchors(),
        }
    except Exception:
        # 降级到原始数据
        persona_data = {
            "name": data.get("name", ""),
            "personality": data.get("personality", {}),
            "speaking_style": data.get("speaking_style", {}),
            "core_anchors": data.get("core_anchors", []),
        }
    return persona_data


@router.put("/characters/{character_id}/persona")
async def update_character_persona(
    character_id: str,
    req: PersonaUpdate,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """更新角色人设（部分更新 personality / speaking_style / catchphrases / core_anchors）"""
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    if req.personality is not None:
        existing = data.get("personality", {})
        existing.update(req.personality)
        data["personality"] = existing
    if req.speaking_style is not None:
        existing = data.get("speaking_style", {})
        existing.update(req.speaking_style)
        data["speaking_style"] = existing
    if req.catchphrases is not None:
        style = data.get("speaking_style", {})
        style["catchphrases"] = req.catchphrases
        data["speaking_style"] = style
    if req.core_anchors is not None:
        data["core_anchors"] = req.core_anchors

    data["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    if not _save_character(character_id, data):
        raise HTTPException(status_code=500, detail="保存人设失败")

    # P1-审查 item34：人设 PUT 与 PUT /characters 改的是同一张卡，此前却不做
    # 任何失效—— persona 缓存与知识索引停留在旧人设。补齐同款失效。
    if deps.orch and hasattr(deps.orch, "invalidate_character_persona_cache"):
        deps.orch.invalidate_character_persona_cache(character_id)
    _invalidate_knowledge_index(character_id)

    return {"status": "updated", "character_id": character_id}


@router.post("/characters/import", status_code=201)
async def import_character(
    file: UploadFile = File(...),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
):
    """导入角色卡（JSON 文件 或 SillyTavern PNG 角色卡）

    支持格式:
      - .json: chara_card_v2/V3 JSON
      - .png: SillyTavern 标准 PNG 角色卡（chara tEXt chunk，base64 编码 JSON）
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    suffix = file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
    if suffix not in ("json", "png"):
        raise HTTPException(status_code=400, detail="仅支持 .json 或 .png 文件")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    # PNG 文件：从 tEXt chunk 提取 JSON
    if suffix == "png":
        try:
            from shisi.character.png_codec import PNGCodecError, extract_card_from_png
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Pillow 未安装: {e}") from e
        try:
            data = extract_card_from_png(content)
        except PNGCodecError as e:
            raise HTTPException(status_code=400, detail=f"PNG 角色卡解析失败: {e}") from e
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="无效的 JSON 文件") from None

    # 标准 CharaCard V2/V3 的核心字段位于 data 内；统一展平后再校验，
    # 同时保留原始 spec/scenario/first_mes/mes_example 等完整字段。
    normalized = normalize_character_card(data)
    name = str(normalized.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="角色名称不能为空")

    # 生成新 ID 或保留原 ID
    character_id = sanitize_id(str(data.get("id", ""))) or str(uuid.uuid4())[:8]
    if _load_character(character_id) is not None:
        character_id = str(uuid.uuid4())[:8]
    now = datetime.now(tz=timezone.utc).isoformat()

    char_data = {
        **normalized,
        "id": character_id,
        "name": name,
        "description": normalized.get("description", ""),
        "schema_version": normalized.get("schema_version", 1),
        "personality": normalized.get("personality", {}),
        "speaking_style": normalized.get("speaking_style", {}),
        "core_anchors": normalized.get("core_anchors", []),
        "user_id": _owner_for_write(principal, normalized.get("user_id")),
        "is_active": False,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }

    _validate_card_or_400(char_data)  # F1：导入落盘前过闸（失败 400，不留半写文件）

    if not _save_character(character_id, char_data):
        raise HTTPException(status_code=500, detail="保存角色失败")

    _schedule_character_crawl(character_id, name, char_data)
    logger.info("角色已导入: %s (%s)", name, character_id)
    return {"id": character_id, "name": name, "status": "imported"}


@router.get("/characters/{character_id}/export")
async def export_character(
    character_id: str,
    format: str = Query("json", pattern="^(json|png)$", description="导出格式: json 或 png"),
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """导出角色卡

    支持格式:
      - json (默认): chara_card_v2 JSON 文件
      - png: SillyTavern 标准 PNG 角色卡（chara tEXt chunk，base64 编码 JSON）
    """
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    if format == "png":
        # PNG 导出：嵌入 chara tEXt chunk
        try:
            from shisi.character.png_codec import PNGCodecError, embed_card_to_png
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Pillow 未安装: {e}") from e
        try:
            png_bytes = embed_card_to_png(data)
        except PNGCodecError as e:
            raise HTTPException(status_code=500, detail=f"PNG 生成失败: {e}") from e

        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', data.get("name", character_id)).strip('_')[:50]
        filename = f"{safe_name or character_id}.png"
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # 默认 JSON 导出
    return JSONResponse(content=data, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="character-{character_id}.json"',
    })


# ── 内置角色预设 ────────────────────────────────────────


@router.get("/presets")
async def list_presets(
    _auth: bool = Security(verify_api_key_dep),
):
    """获取所有内置角色预设"""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)

    # 优先读取缓存的索引文件
    index_path = PRESETS_DIR / "_index.json"
    if index_path.exists():
        try:
            with open(index_path, encoding="utf-8") as fh:
                index_data = json.load(fh)
            return {"presets": index_data, "total": len(index_data)}
        except Exception as e:
            logger.debug("index file read failed: %s", e)

    # 降级：扫描目录
    presets = []
    for f in sorted(PRESETS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            d = data.get("data", {})
            presets.append({
                "id": f.stem,
                "name": d.get("name", ""),
                "description": (d.get("description", "") or "")[:500],
                "tags": d.get("tags", []),
                "has_first_mes": bool(d.get("first_mes")),
            })
        except Exception as e:
            logger.debug("读取预设失败 %s: %s", f.name, e)

    return {"presets": presets, "total": len(presets)}


@router.get("/presets/{preset_id}")
async def get_preset(
    preset_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """获取单个内置角色预设的完整数据，映射为前端 PersonaState 友好格式"""
    # 先检查安全文件名
    safe_id = re.sub(r'[^\w\u4e00-\u9fff\-]', '', preset_id)
    preset_path = PRESETS_DIR / f"{safe_id}.json"

    if not preset_path.exists():
        raise HTTPException(status_code=404, detail=f"预设不存在: {preset_id}")

    try:
        with open(preset_path, encoding="utf-8") as f:
            card = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail="读取预设失败") from e

    d = card.get("data", {})
    tags = d.get("tags", [])

    # 从 personality 文本中尝试提取性格关键词作为 anchors 补充
    personality_text = d.get("personality", "") or ""
    extracted_anchors = []
    if personality_text and not tags:
        # 尝试用常见分隔符拆分
        for sep in ["、", "，", ",", "；", ";", " "]:
            if sep in personality_text:
                parts = [p.strip() for p in personality_text.split(sep) if len(p.strip()) > 1]
                if len(parts) >= 2:
                    extracted_anchors = parts[:8]
                    break

    preset_response = {
        "preset_id": safe_id,
        "name": d.get("name", ""),
        "description": d.get("description", "") or "",
        "personality_text": personality_text,
        "scenario": d.get("scenario", "") or "",
        "first_mes": d.get("first_mes", "") or "",
        "alternate_greetings": d.get("alternate_greetings", []),
        "tags": tags,
        "anchors": tags[:8] if tags else (extracted_anchors if extracted_anchors else []),
        "personality": {
            "warmth": 0.6,
            "playfulness": 0.5,
            "independence": 0.5,
            "jealousy": 0.3,
            "stubbornness": 0.4,
        },
        "speakingStyle": {
            "formality": 0.5,
            "expressiveness": 0.5,
            "humor": 0.5,
            "directness": 0.5,
        },
    }
    return preset_response


# ── 记忆事实管理 ────────────────────────────────────────

# 锚定项目根，避免依赖进程 CWD（achievement_engine._MEMORY_FACTS_DIR 同源）
MEMORY_FACTS_DIR = project_path("data", "character_memory")


def _facts_path(character_id: str) -> Path:
    safe_id = sanitize_id(character_id)
    if not safe_id:
        raise HTTPException(status_code=400, detail="Invalid character ID")
    MEMORY_FACTS_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_FACTS_DIR / f"{safe_id}.json"


def _load_facts(character_id: str) -> list[dict]:
    path = _facts_path(character_id)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("读取记忆事实失败: %s", e)
    return []


def _save_facts(character_id: str, facts: list[dict]) -> bool:
    path = _facts_path(character_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(facts, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error("保存记忆事实失败: %s", e)
        return False


@router.get("/characters/{character_id}/memory/facts")
async def list_memory_facts(
    request: Request,
    character_id: str,
    category: str | None = Query(default=None),
    limit: int = Query(default=500, le=500),
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """回读唯一真源 `user_facts`（W4 缺陷 E，2026-09-27）。

    旧实现读 `data/character_memory/{cid}.json`——生成侧从不读取该文件，
    控制台显示条数与下一轮 prompt 真正使用的记忆脱钩。现按 misc_routes
    的归属谓词（`/api/memory/facts` 同一 owner）限定本人范围；角色维度
    不成立（事实按用户会话归属），故本端点回显的是「当前登录用户」的
    真实事实集。旧 JSON 面只作遗留数据留存，不再冒充记忆。
    """
    from api.routers import misc_routes  # 归属谓词唯一 owner，惰性 import 防环

    orch = deps.orch
    mem = getattr(orch, "_memory", None) if orch is not None else None
    if mem is None:
        return {"facts": [], "total": 0}
    prefix = misc_routes._memory_scope_prefix(request)
    if prefix is None:
        rows = mem.semantic.get_facts(category, limit=limit)
    else:
        sm = getattr(mem, "structured_memory", None)
        rows = sm.get_facts(category, limit=limit, owner_prefix=prefix) if sm else []
    return {"facts": rows, "total": len(rows)}


_FACT_SURFACE_GONE = (
    "角色维度的事实读写面已作废：旧实现只写 data/character_memory 的 JSON，"
    "生成侧从不读取它——返回「created/deleted」即谎报，写入的事实下一轮"
    "对话不会出现，删除也拦不住真记忆的复现。事实唯一写权威：会话内 "
    "remember_facts / forget_facts → ShisiMemoryService 统一入口"
    "（fact_id/action/来源水位真实回执，迟到重放不复活、新一轮重述可重记）。"
)


@router.post("/characters/{character_id}/memory/facts", status_code=201)
async def add_memory_fact(
    character_id: str,
    req: MemoryFactCreate,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """410：见 `_FACT_SURFACE_GONE`。410 先于任何写动作，不留半写态。"""
    raise HTTPException(status_code=410, detail=_FACT_SURFACE_GONE)


@router.delete("/characters/{character_id}/memory/facts/{fact_id}")
async def delete_memory_fact(
    character_id: str,
    fact_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """410：旧「删除」只动 JSON，真库 user_facts 与其向量派生原样复现。"""
    raise HTTPException(status_code=410, detail=_FACT_SURFACE_GONE)


@router.delete("/characters/{character_id}/memory")
async def clear_memory(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """410：作废端点不得顺手销毁遗留文件；真记忆的遗忘走 forget_facts。"""
    raise HTTPException(status_code=410, detail=_FACT_SURFACE_GONE)


# ── AI 角色生成 ─────────────────────────────────────────


def _character_generation_prompt(description: str, archetype: str) -> str:
    return (
        "根据以下角色描述，生成一份完整的人设卡 JSON。\n\n"
        f"角色描述：{description}\n"
        f"性格类型：{archetype}\n\n"
        "请按以下 JSON 格式回复（仅返回 JSON，不要额外文字）：\n"
        '{"name":"角色名","description":"角色概述","personality":{"warmth":0.0,'
        '"playfulness":0.0,"independence":0.0,"jealousy":0.0,"stubbornness":0.0,'
        '"creativity":0.0},"speaking_style":{"formality":0.0,"expressiveness":0.0,'
        '"humor":0.0,"directness":0.0,"emoji_freq":0.0,"catchphrases":["..."]},'
        '"core_anchors":["..."],"catchphrases":["..."]}'
    )


async def _generate_persona_preview(req: CharacterGenerateRequest) -> dict[str, Any]:
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="描述不能为空")

    try:
        from llm_provider import get_llm

        llm = get_llm()
    except Exception:
        raise HTTPException(status_code=503, detail="LLM 不可用，无法生成人设") from None

    prompt = _character_generation_prompt(req.description, req.archetype)
    try:
        if hasattr(llm, "chat"):
            response = await llm.chat(query=prompt, max_tokens=1024, temperature=0.7)
        elif hasattr(llm, "chat_sync"):
            response = await asyncio.to_thread(
                llm.chat_sync,
                query=prompt,
                max_tokens=1024,
                temperature=0.7,
            )
        else:
            raise RuntimeError("LLM 未提供 chat 接口")
        json_match = re.search(r"\{.*\}", str(response), re.DOTALL)
        if not json_match:
            raise ValueError("LLM 返回中没有 JSON 对象")
        persona_data = json.loads(json_match.group())
        if not isinstance(persona_data, dict):
            raise ValueError("LLM 返回的人设不是对象")
        return persona_data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI 人设预览生成失败")
        raise HTTPException(status_code=502, detail="LLM 返回格式异常，无法解析人设") from e


@router.post("/characters/preview-from-description", response_model=CharacterPreviewResponse)
async def preview_character_from_description(
    req: CharacterGenerateRequest,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
):
    """根据描述生成人设预览，不写入角色库。"""
    _enforce_byok_for_generation(principal)  # F2：BYOK 前置（与 chat 链同语义）
    persona_data = await _generate_persona_preview(req)
    name = sanitize_character_name(str(persona_data.get("name") or "新角色"))
    persona_data["name"] = name
    return {
        "reply": f"我整理出了「{name}」的人设草案，你可以继续描述来覆盖这份预览。",
        "persona": persona_data,
    }


@router.post("/characters/generate-from-description", status_code=201)
async def generate_character_from_description(
    req: CharacterGenerateRequest,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
):
    """从文本描述用 AI 生成角色人设卡并自动创建"""
    _enforce_byok_for_generation(principal)  # F2：BYOK 前置（失败 403，不烧平台 key）
    persona_data = await _generate_persona_preview(req)

    catchphrases = persona_data.pop("catchphrases", [])
    data = _build_character_data(
        name=persona_data.get("name", "新角色"),
        description=persona_data.get("description", req.description),
        personality=persona_data.get("personality", {}),
        speaking_style=persona_data.get("speaking_style", {}),
        catchphrases=catchphrases,
        core_anchors=persona_data.get("core_anchors", []),
        user_id=_owner_for_write(principal, req.user_id),
    )
    if not _save_character(data["id"], data):
        raise HTTPException(status_code=500, detail="保存角色失败")

    _schedule_character_crawl(data["id"], data["name"], data)
    logger.info("AI 角色已生成: %s (%s)", data["name"], data["id"])
    return {
        "id": data["id"],
        "name": data["name"],
        "status": "created",
        "persona": persona_data,
    }


# ── 对话导出 ────────────────────────────────────────────


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t")


def _csv_safe_cell(value: Any) -> str:
    """F4（P0 修复批 2026-10-04）：CSV 公式注入防护。

    单元格以 = + - @ 或制表符开头时前缀半角单引号，防止 Excel/WPS 打开导出
    文件时把单元格当公式执行（CSV Injection，聊天内容为用户可控输入）。
    """
    text = str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


@router.get("/characters/{character_id}/chat/export")
async def export_chat(
    character_id: str,
    format: str = Query(default="json", pattern=r"^(json|csv)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """导出角色对话记录（JSON 或 CSV）"""
    data = _owned or _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    session_id = data.get("user_id", "default")
    orch = deps.orch
    messages = []

    if orch and orch._memory:
        sm = getattr(orch._memory, "structured_memory", None) or getattr(orch._memory, "_sm", None)
        if sm and hasattr(sm, "get_connection"):
            try:
                with sm.get_connection() as conn:
                    rows = conn.execute(
                        "SELECT role, content, emotion_tag, created_at FROM chat_history "
                        "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                        (session_id, limit),
                    ).fetchall()
                    messages = [dict(r) for r in rows][::-1]
            except Exception as e:
                logger.warning("查询聊天记录失败: %s", e)

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["role", "content", "emotion", "created_at"])
        for m in messages:
            # F4：四个单元格统一过公式转义（role/created_at 为服务端生成值，
            # 顺带覆盖；content/emotion_tag 为用户与模型可控输入，主防护对象）
            writer.writerow([
                _csv_safe_cell(m.get("role", "")),
                _csv_safe_cell(m.get("content", "")),
                _csv_safe_cell(m.get("emotion_tag", "")),
                _csv_safe_cell(m.get("created_at", "")),
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="chat-{character_id}.csv"'},
        )

    return JSONResponse(
        content={"character_id": character_id, "character_name": data.get("name", ""),
                 "total": len(messages), "messages": messages},
        headers={"Content-Disposition": f'attachment; filename="chat-{character_id}.json"'},
    )


# ═══════════════════════════════════════════════════════
# 角色成就（ADR-0014，2026-09-01 实现）
# ═══════════════════════════════════════════════════════


@router.get("/characters/{character_id}/achievements")
async def list_achievements(
    character_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """角色成就清单（读取时幂等重算，解锁时间保持首次达标）。"""
    from api.achievement_engine import recalculate_achievements

    items = await recalculate_achievements(db, sanitize_id(character_id))
    unlocked = sum(1 for i in items if i["unlocked"])
    return {"character_id": character_id, "achievements": items, "unlocked_count": unlocked, "total": len(items)}


@router.post("/characters/{character_id}/achievements/recalculate")
async def recalculate_achievements_endpoint(
    character_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """显式触发成就重算（幂等；与 GET 同语义，供维护任务/前端手动刷新）。"""
    from api.achievement_engine import recalculate_achievements

    items = await recalculate_achievements(db, sanitize_id(character_id))
    unlocked = sum(1 for i in items if i["unlocked"])
    return {"character_id": character_id, "recalculated": True, "unlocked_count": unlocked, "total": len(items)}
