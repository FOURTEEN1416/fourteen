"""统一角色管理API — 桥接新旧两套角色体系"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Security, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from my_character.persona_card import PersonaCardV3
from shisi.voice.character_voice import CharacterVoiceManager

from api.deps import deps

logger = logging.getLogger("api.character_routes")

router = APIRouter(prefix="/api", tags=["character"])

_orch = None
_gf = None

CHARACTERS_DIR = Path("config/characters")

# ── API Key 认证委托 ──────────────────────────────────
# 模块级 Security 函数：先由 FastAPI 提取 X-API-Key 头，
# 再委托给注入的 verify 函数（来自 app_factory.py）。
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_verify_api_key_func = None


async def _verify_api_key(api_key: str | None = Security(_api_key_header)):
    if _verify_api_key_func is not None:
        return await _verify_api_key_func(api_key)
    return True


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


class MemoryFactCreate(BaseModel):
    content: str
    category: str = "general"
    tags: list[str] = []


class MemoryFactResponse(BaseModel):
    id: str
    content: str
    category: str
    tags: list[str]
    created_at: str
    character_id: str


# ── 依赖注入 ────────────────────────────────────────────


def set_dependencies(orch, gf, verify_api_key):
    global _orch, _gf, _verify_api_key_func
    _orch = orch
    _gf = gf
    _verify_api_key_func = verify_api_key


# ── 数据持久化工具 ───────────────────────────────────────


def _get_characters_dir() -> Path:
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    return CHARACTERS_DIR


def _character_path(character_id: str) -> Path:
    return _get_characters_dir() / f"{character_id}.json"


def _load_character(character_id: str) -> dict[str, Any] | None:
    path = _character_path(character_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("加载角色 %s 失败: %s", character_id, e)
        return None


def _save_character(character_id: str, data: dict[str, Any]) -> bool:
    path = _character_path(character_id)
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
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.error("删除角色 %s 失败: %s", character_id, e)
            return False
    return False


def _list_all_characters() -> list[dict[str, Any]]:
    chars_dir = _get_characters_dir()
    characters: list[dict[str, Any]] = []
    for f in sorted(chars_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            characters.append(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("读取角色文件 %s 失败: %s", f.name, e)
    return characters


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
        "name": name,
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


# ── API 端点 ────────────────────────────────────────────


@router.get("/characters")
async def list_characters(
    user_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    _auth: bool = Security(_verify_api_key),
):
    """列出所有角色，支持 user_id 过滤和 search 搜索"""
    characters = _list_all_characters()
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
    return {"characters": characters, "total": len(characters)}


@router.post("/characters", status_code=201)
async def create_character(
    req: UnifiedCharacterCreate,
    _auth: bool = Security(_verify_api_key),
):
    """创建新角色"""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="角色名称不能为空")

    data = _build_character_data(
        name=req.name,
        description=req.description,
        personality=req.personality,
        speaking_style=req.speaking_style,
        catchphrases=req.catchphrases,
        core_anchors=req.core_anchors,
        user_id=req.user_id,
    )
    if not _save_character(data["id"], data):
        raise HTTPException(status_code=500, detail="保存角色失败")
    logger.info("角色已创建: %s (%s)", data["name"], data["id"])
    return {"id": data["id"], "name": data["name"], "status": "created"}


@router.get("/characters/{character_id}")
async def get_character(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """获取角色详情（含音色配置）"""
    data = _load_character(character_id)
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

    # 附加音色配置（如果存在）
    try:
        voice_mgr = CharacterVoiceManager()
        voice_config = voice_mgr.get_voice_config(character_id)
        if voice_config:
            data["voice_config"] = voice_config
    except Exception as e:
        logger.debug("获取角色音色配置失败: %s", e)

    return data


@router.put("/characters/{character_id}")
async def update_character(
    character_id: str,
    req: UnifiedCharacterUpdate,
    _auth: bool = Security(_verify_api_key),
):
    """更新角色（合并更新，只传要改的字段）"""
    data = _load_character(character_id)
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

    if not _save_character(character_id, data):
        raise HTTPException(status_code=500, detail="保存角色失败")
    return {"status": "updated", "character_id": character_id}


@router.delete("/characters/{character_id}")
async def delete_character(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """删除角色"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    if not _delete_character_file(character_id):
        raise HTTPException(status_code=500, detail="删除角色失败")
    logger.info("角色已删除: %s (%s)", data.get("name", ""), character_id)
    return {"status": "deleted", "character_id": character_id}


@router.post("/characters/{character_id}/activate")
async def activate_character(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """激活角色（设为当前使用的角色）"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    # 将所有其他角色设为非激活
    for c in _list_all_characters():
        if c.get("id") != character_id and c.get("is_active"):
            c["is_active"] = False
            _save_character(c["id"], c)

    data["is_active"] = True
    data["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    if not _save_character(character_id, data):
        raise HTTPException(status_code=500, detail="激活角色失败")

    # 同步到女友管理器
    if _gf and hasattr(_gf, "set_user_character"):
        user_id = data.get("user_id", "default")
        _gf.set_user_character(user_id, character_id)
        logger.info("角色激活已同步到女友管理器: %s → %s", user_id, character_id)

    return {"status": "activated", "character_id": character_id}


@router.get("/characters/{character_id}/persona")
async def get_character_persona(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """获取角色的人设详情"""
    data = _load_character(character_id)
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
    _auth: bool = Security(_verify_api_key),
):
    """更新角色人设（部分更新 personality / speaking_style / catchphrases / core_anchors）"""
    data = _load_character(character_id)
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
    return {"status": "updated", "character_id": character_id}


@router.post("/characters/import", status_code=201)
async def import_character(
    file: UploadFile = File(...),
    _auth: bool = Security(_verify_api_key),
):
    """导入角色卡（JSON 文件）"""
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="仅支持 .json 文件")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON 文件") from None

    # 校验必要字段
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="角色名称不能为空")

    # 生成新 ID 或保留原 ID
    character_id = data.get("id", str(uuid.uuid4())[:8])
    now = datetime.now(tz=timezone.utc).isoformat()

    char_data = {
        "id": character_id,
        "name": name,
        "description": data.get("description", ""),
        "schema_version": data.get("schema_version", 1),
        "personality": data.get("personality", {}),
        "speaking_style": data.get("speaking_style", {}),
        "core_anchors": data.get("core_anchors", []),
        "user_id": data.get("user_id", "default"),
        "is_active": False,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }

    if not _save_character(character_id, char_data):
        raise HTTPException(status_code=500, detail="保存角色失败")

    logger.info("角色已导入: %s (%s)", name, character_id)
    return {"id": character_id, "name": name, "status": "imported"}


@router.get("/characters/{character_id}/export")
async def export_character(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """导出角色卡为 JSON 文件下载"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return JSONResponse(content=data, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="character-{character_id}.json"',
    })


# ── 记忆事实管理 ────────────────────────────────────────

MEMORY_FACTS_DIR = Path("data") / "character_memory"


def _facts_path(character_id: str) -> Path:
    MEMORY_FACTS_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_FACTS_DIR / f"{character_id}.json"


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
    character_id: str,
    category: str | None = Query(default=None),
    _auth: bool = Security(_verify_api_key),
):
    """获取角色记忆事实，可按分类过滤"""
    facts = _load_facts(character_id)
    if category:
        facts = [f for f in facts if f.get("category") == category]
    return {"facts": facts, "total": len(facts)}


@router.post("/characters/{character_id}/memory/facts", status_code=201)
async def add_memory_fact(
    character_id: str,
    req: MemoryFactCreate,
    _auth: bool = Security(_verify_api_key),
):
    """添加角色记忆事实"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="事实内容不能为空")
    facts = _load_facts(character_id)
    fact = {
        "id": str(uuid.uuid4())[:8],
        "content": req.content,
        "category": req.category,
        "tags": req.tags,
        "character_id": character_id,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    facts.append(fact)
    if not _save_facts(character_id, facts):
        raise HTTPException(status_code=500, detail="保存事实失败")
    return {"status": "created", "fact": fact}


@router.delete("/characters/{character_id}/memory/facts/{fact_id}")
async def delete_memory_fact(
    character_id: str,
    fact_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """删除角色记忆事实"""
    facts = _load_facts(character_id)
    new_facts = [f for f in facts if f.get("id") != fact_id]
    if len(new_facts) == len(facts):
        raise HTTPException(status_code=404, detail=f"事实不存在: {fact_id}")
    if not _save_facts(character_id, new_facts):
        raise HTTPException(status_code=500, detail="删除事实失败")
    return {"status": "deleted", "fact_id": fact_id}


@router.delete("/characters/{character_id}/memory")
async def clear_memory(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """清空角色所有记忆事实"""
    path = _facts_path(character_id)
    if path.exists():
        path.unlink()
    return {"status": "cleared", "character_id": character_id}


# ── AI 角色生成 ─────────────────────────────────────────


@router.post("/characters/generate-from-description", status_code=201)
async def generate_character_from_description(
    req: CharacterGenerateRequest,
    _auth: bool = Security(_verify_api_key),
):
    """从文本描述用 AI 生成角色人设卡并自动创建"""
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="描述不能为空")

    try:
        from llm_provider import get_llm
        llm = get_llm()
    except Exception:
        raise HTTPException(status_code=503, detail="LLM 不可用，无法生成角色") from None

    prompt = (
        f"根据以下角色描述，生成一份完整的人设卡 JSON。\n\n"
        f"角色描述：{req.description}\n"
        f"性格类型：{req.archetype}\n\n"
        f"请按以下 JSON 格式回复（仅返回 JSON，不要额外文字）：\n"
        f'{{"name":"角色名","description":"角色概述","personality":{{"warmth":0.0-1.0,"playfulness":0.0-1.0,'
        f'"independence":0.0-1.0,"jealousy":0.0-1.0,"stubbornness":0.0-1.0,"creativity":0.0-1.0}},'
        f'"speaking_style":{{"formality":0.0-1.0,"expressiveness":0.0-1.0,"humor":0.0-1.0,'
        f'"directness":0.0-1.0,"emoji_freq":0.0-1.0,"catchphrases":["..."]}},'
        f'"core_anchors":["..."],"catchphrases":["..."]}}'
    )

    try:
        response = llm.chat_sync(query=prompt, max_tokens=1024, temperature=0.7)
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if not json_match:
            raise HTTPException(status_code=500, detail="LLM 返回格式异常，无法解析")
        persona_data = json.loads(json_match.group())
    except Exception as e:
        logger.exception("AI 角色生成失败")
        raise HTTPException(status_code=500, detail=f"角色生成失败: {e}") from e

    catchphrases = persona_data.pop("catchphrases", [])
    data = _build_character_data(
        name=persona_data.get("name", "新角色"),
        description=persona_data.get("description", req.description),
        personality=persona_data.get("personality", {}),
        speaking_style=persona_data.get("speaking_style", {}),
        catchphrases=catchphrases,
        core_anchors=persona_data.get("core_anchors", []),
        user_id=req.user_id,
    )
    if not _save_character(data["id"], data):
        raise HTTPException(status_code=500, detail="保存角色失败")

    logger.info("AI 角色已生成: %s (%s)", data["name"], data["id"])
    return {
        "id": data["id"],
        "name": data["name"],
        "status": "created",
        "persona": persona_data,
    }


# ── 对话导出 ────────────────────────────────────────────


@router.get("/characters/{character_id}/chat/export")
async def export_chat(
    character_id: str,
    format: str = Query(default="json", pattern=r"^(json|csv)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    _auth: bool = Security(_verify_api_key),
):
    """导出角色对话记录（JSON 或 CSV）"""
    data = _load_character(character_id)
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
            writer.writerow([m.get("role", ""), m.get("content", ""),
                           m.get("emotion_tag", ""), m.get("created_at", "")])
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
