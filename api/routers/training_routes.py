"""
训练管线 + 主动搭话路由 — /api/training/* + /api/proactive/*

来源：原 api.main_routes.py L365/379/459/512/544/549/590/638/647/664/1252 共 11 端点
（2026-08-28：/api/training/extract 已移除——微信克隆收敛为"本地工具提取 + 上传 JSON"，
服务端不做任何微信数据提取，见 /api/clone/upload）

注意：LoRA 微调训练端点已移除（项目使用外接 API + RAG + 提示词注入）。
保留：数据清洗 / 测试 / 应用（克隆到 ToneMimic）+ 主动搭话配置。

依赖：
- deps.orch（_ase 主动搭话引擎）
- deps.training_mgr（训练状态管理器）
- 训练管线: clone_training.data_cleaner / my_character.tone_mimic
- ProactiveConfigRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import ProactiveConfigRequest  # noqa: F401（兼容旧 import）

logger = logging.getLogger("api.routers.training_routes")

router = APIRouter(tags=["training"])


# ═══════════════════════════════════════════════════════
# Training Pipeline Status
# ═══════════════════════════════════════════════════════


@router.get("/api/training/status")
async def training_status(_auth: bool = Security(verify_api_key_dep)):
    try:
        import clone_training  # noqa: F401
        available = True
        desc = "风格克隆管线已就绪（提示词注入模式，无 LoRA 训练）"
    except ImportError:
        available = False
        desc = "训练模块未安装"
    return {"available": available, "description": desc, "steps": [
        "style_analyze", "tone_mimic_inject",
    ]}


@router.get("/api/training/progress")
async def get_training_progress(_auth: bool = Security(verify_api_key_dep)):
    return deps.training_mgr.get_state()


# ═══════════════════════════════════════════════════════
# Training Pipeline (admin only)
# ═══════════════════════════════════════════════════════


@router.post("/api/training/clean")
async def start_cleaning(
    accept_score: int = 2,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    def _do_clean():
        try:
            from clone_training.data_cleaner import DataCleaner
            from llm_provider import get_llm
            llm = get_llm()
            cleaner = DataCleaner(llm=llm, accept_score=accept_score)
            data_dir = Path(__file__).parent.parent.parent / "data" / "training"
            json_files = sorted(data_dir.glob("*.jsonl"))
            if not json_files:
                raise FileNotFoundError("No dataset found")
            latest = str(json_files[-1])
            result_path = cleaner.score_from_dataset(latest)
            cleaned_count = 0
            if result_path:
                try:
                    with open(result_path, encoding="utf-8") as f:
                        cleaned_data = json.load(f)
                    cleaned_count = len(cleaned_data) if isinstance(cleaned_data, list) else 0
                except Exception as e:
                    logger.debug("Failed to read cleaned data result: %s", e)
            deps.training_mgr.update(
                status="cleaned",
                cleaned_turns=cleaned_count,
                progress=0.6,
                step_name="数据清洗",
            )
        except Exception:
            logger.exception("Cleaning failed")
            deps.training_mgr.update(status="error", error="internal_error")

    deps.training_mgr.update(status="cleaning", start_time=time.time(), step_name="数据清洗")
    deps.training_mgr.submit(_do_clean)
    return {"status": "started", "task": "clean", "accept_score": accept_score}


@router.post("/api/training/test")
async def test_clone(
    message: str = Query(..., max_length=1000),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    try:
        from my_character.tone_mimic import ToneMimic
        chroma_path = str(Path(__file__).parent.parent.parent / "data" / "chroma_db")
        mimic = ToneMimic(chroma_path=chroma_path)
        style_prompt = mimic.get_style_prompt()
        return {"message": message, "style_output": style_prompt, "status": "ok"}
    except (ImportError, OSError, ValueError):
        logger.exception("Test clone failed")
        return {"message": message, "style_output": "", "status": "error", "detail": "internal_error"}


@router.post("/api/training/apply")
async def apply_clone(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    try:
        result_path = str(Path(__file__).parent.parent.parent / "data" / "training")
        return {"status": "applied", "path": result_path}
    except (ValueError, OSError):
        logger.exception("Apply clone failed")
        raise HTTPException(status_code=500, detail="internal_error") from None


# ═══════════════════════════════════════════════════════
# Proactive Engine
# ═══════════════════════════════════════════════════════


@router.get("/api/proactive/state")
async def proactive_state(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._ase:
        state = orch._ase.health_check()
        state["paused"] = getattr(orch._ase, "_paused", False)
        return state
    return {}


@router.get("/api/proactive/config")
async def get_proactive_config(_auth: bool = Security(verify_api_key_dep)):
    """运行时参数真值（阈值/频率控制器对象，非展示字典）。"""
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    return orch._ase.get_runtime_config()


@router.post("/api/proactive/config")
async def update_proactive_config(
    req: ProactiveConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """更新主动消息运行时参数——2026-08-28 修复：旧版只写展示字典不生效。"""
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    ase = orch._ase
    ase.apply_runtime_config(
        threshold=req.threshold,
        max_daily_messages=req.max_daily,
        min_interval_minutes=req.min_interval_minutes,
        cooldown_after_reply_minutes=req.cooldown_after_reply_minutes,
    )
    logger.info(
        "Proactive config updated: threshold=%s max_daily=%s",
        ase._urgency_threshold, ase.get_runtime_config()["max_daily_messages"],
    )
    return {"status": "ok", "config": ase.get_runtime_config()}


class ProactivePauseRequest(BaseModel):
    paused: bool


@router.post("/api/proactive/pause")
async def pause_proactive(
    req: ProactivePauseRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """暂停/恢复主动消息调度（暂停后 tick 直接跳过，不影响手动发送）。"""
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    orch._ase.apply_runtime_config(paused=req.paused)
    logger.info("Proactive scheduler paused=%s", req.paused)
    return {"status": "ok", "paused": req.paused}


class ProactiveSendRequest(BaseModel):
    message_type: str | None = None  # 缺省按紧迫度自动选择


@router.post("/api/proactive/send")
async def send_proactive_now(
    req: ProactiveSendRequest | None = None,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """手动立即生成并发送一条主动消息（绕过频率限制；计入统计与历史）。"""
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    ase = orch._ase

    msg_type = (req.message_type if req else None)
    if msg_type:
        try:
            from proactive.ase_engine import ProactiveType
            chosen = ProactiveType(msg_type)
        except ValueError:
            raise HTTPException(400, f"未知消息类型: {msg_type}") from None
        result = ase._generate_and_return(chosen)
    else:
        # 自动选择：更新紧迫度后按阈值/场景选型（生成不计频率门槛）
        ase._update_urgency(ase._hours_since_last_chat())
        scene = ase._check_scene_triggers()
        if scene and ase.urgency.total >= 2.0:
            result = ase._record_and_return(scene)
        else:
            result = ase._generate_and_return(ase._select_type_by_urgency())

    if not result:
        raise HTTPException(500, "消息生成失败")

    ase.record_sent_entry(result)
    scheduler = orch.components.get("scheduler") if orch.components else None
    delivered = False
    if scheduler is not None:
        try:
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(scheduler._send_to_all(result.get("message", "")))
            else:
                loop.run_until_complete(scheduler._send_to_all(result.get("message", "")))
            delivered = True
        except RuntimeError:
            if getattr(scheduler, "_send", None):
                scheduler._send(result.get("message", ""))
                delivered = True
    logger.info("Proactive manual send: [%s] delivered=%s", result.get("type"), delivered)
    return {"status": "sent", "delivered": delivered, **result}


@router.get("/api/proactive/history")
async def proactive_history(
    limit: int = Query(default=50, le=200),
    _auth: bool = Security(verify_api_key_dep),
):
    """发送历史真数据（sent_history；旧 _sent_messages 属性从未存在过，历史一直返回空）。"""
    orch = deps.orch
    if orch and orch._ase:
        history = list(getattr(orch._ase, "sent_history", []) or [])
        return {"history": history[-limit:], "total": len(history)}
    return {"history": [], "total": 0}
