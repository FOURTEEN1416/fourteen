"""剧情线 API — 角色剧情线配置的**存储面**与进度查询。

响应的 `affects_chat` 字段声明配置当前是否影响对话（见 `CHAT_AFFECTING`）；
生产 prompt 路径未接线，故为 False。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.path_security import sanitize_id
from api.routers.character_routes import require_character_access
from shisi.storyline.config import StorylineConfig
from shisi.storyline.detector import StorylineDetector
from shisi.storyline.engine import get_storyline_engine
from utils.project_paths import project_path

logger = logging.getLogger("api.storyline_routes")

router = APIRouter(prefix="/api/characters", tags=["storyline"])


CHARACTERS_DIR = project_path("config", "characters")

# 剧情线是否影响对话回复 —— **由 prompt 闸的实际状态决定的自我声明**（W8 缺陷 D）。
#
# 事实：唯一生产 prompt 路径 `shisi/application/persona_service.py` 传
# `use_storyline=False`，`prompt_builder._get_storyline_context` 在闸门关闭时
# 恒返回空串，因此卡里的 `storyline_config`（含 `enabled=true`）写入后**不会
# 改变角色说的话**；本路由只是配置的存储面。
# 测试钉：`tests/test_w8_storyline_honesty.py` 同时静态钉住那个实参 —— 一旦
# 接线（改 True）该钉即红，强制同批改此常量与前端横幅，禁止"悄悄生效"。
# 接线 vs 撤除入口属产品裁决（D 类），本模块不自决。
CHAT_AFFECTING = False


def _character_path(character_id: str) -> Path:
    safe_id = sanitize_id(character_id)
    if not safe_id:
        raise HTTPException(status_code=400, detail="Invalid character ID")
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    return CHARACTERS_DIR / f"{safe_id}.json"


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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        logger.error("保存角色 %s 失败: %s", character_id, e)
        return False


# ── 请求/响应模型 ──


class StorylineConfigRequest(BaseModel):
    enabled: bool = False
    time_per_turn: int = 10
    max_duration_minutes: int = 10080
    stages: list[dict[str, Any]] = []
    ending: dict[str, Any] = {}


# ── API 端点 ──


@router.get("/{character_id}/storyline")
async def get_storyline_config(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """获取角色剧情线配置。"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    config_data = data.get("storyline_config")
    if not config_data:
        return {"enabled": False, "configured": False, "affects_chat": CHAT_AFFECTING}

    # 恢复持久化的状态到引擎
    state_data = data.get("storyline_state")
    if state_data:
        engine = get_storyline_engine()
        engine.set_state_from_dict(character_id, state_data)

    return {
        "enabled": config_data.get("enabled", False),
        "configured": True,
        "config": config_data,
        "affects_chat": CHAT_AFFECTING,
    }


@router.put("/{character_id}/storyline")
async def update_storyline_config(
    character_id: str,
    req: StorylineConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """更新角色剧情线配置（落盘 + 同步引擎；是否影响对话见 `CHAT_AFFECTING`）。"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    # 构建配置

    stages = []
    for s in req.stages:
        timing = s.get("timing", {})
        stages.append({
            "name": s.get("name", ""),
            "display_name": s.get("display_name", ""),
            "timing": {
                "start_minutes": timing.get("start_minutes", 0),
                "end_minutes": timing.get("end_minutes", 1440),
            },
            "style_rules": s.get("style_rules", []),
            "behavior_rules": s.get("behavior_rules", []),
            "dialogue_notes": s.get("dialogue_notes", ""),
            "transition_message": s.get("transition_message", ""),
        })

    config_data = {
        "enabled": req.enabled,
        "time_per_turn": req.time_per_turn,
        "time_unit_label": "分钟",
        "max_duration_minutes": req.max_duration_minutes,
        "start_day": 1,
        "start_hour": 0,
        "start_minute": 0,
        "stages": stages,
        "ending": {
            "type": req.ending.get("type", "memory_cabinet"),
            "final_dialogue": req.ending.get("final_dialogue", ""),
            "narrative": req.ending.get("narrative", ""),
            "memorial_items": req.ending.get("memorial_items", []),
            "blank_after_end": req.ending.get("blank_after_end", True),
        },
        "auto_detected": False,
        "detection_confidence": 0.0,
    }

    data["storyline_config"] = config_data
    data["updated_at"] = datetime.now(tz=timezone.utc).isoformat()

    if not _save_character(character_id, data):
        raise HTTPException(status_code=500, detail="保存剧情线配置失败")

    # 同步到引擎
    config = StorylineConfig.from_dict(config_data)
    engine = get_storyline_engine()
    engine.set_config(character_id, config)

    # 如果有持久化的状态，恢复
    existing_state = data.get("storyline_state")
    if existing_state and not req.enabled:
        # 关闭剧情线 → 清除状态
        data.pop("storyline_state", None)
        _save_character(character_id, data)
    elif existing_state:
        engine.set_state_from_dict(character_id, existing_state)

    return {
        "status": "updated",
        "character_id": character_id,
        "affects_chat": CHAT_AFFECTING,
    }


@router.delete("/{character_id}/storyline")
async def delete_storyline_config(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """删除角色剧情线配置。"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    data.pop("storyline_config", None)
    data.pop("storyline_state", None)
    _save_character(character_id, data)

    # 清理引擎
    engine = get_storyline_engine()
    engine.remove_config(character_id)

    return {"status": "deleted", "character_id": character_id}


@router.get("/{character_id}/storyline/progress")
async def get_storyline_progress(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """获取角色剧情线进度。"""
    engine = get_storyline_engine()
    progress = engine.get_progress(character_id)
    return progress


@router.post("/{character_id}/storyline/detect")
async def detect_storyline(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """自动检测角色人设是否适合开启剧情线。"""
    data = _load_character(character_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    # 从角色数据中提取文本
    search_text = " ".join([
        data.get("description", ""),
        str(data.get("personality", "")),
        str(data.get("core_anchors", "")),
    ])

    result = StorylineDetector.detect_from_text(search_text)

    return {
        "has_storyline": result.has_storyline,
        "confidence": result.confidence,
        "matched_patterns": result.matched_patterns,
        "suggested": result.suggested_config.to_dict() if result.suggested_config else None,
    }


@router.post("/{character_id}/storyline/reset")
async def reset_storyline(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """重置角色剧情线进度（从头开始）。"""
    engine = get_storyline_engine()
    engine.reset_state(character_id)
    return {"status": "reset", "character_id": character_id}
