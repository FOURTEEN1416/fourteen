"""
训练管线 + 主动搭话路由 — /api/training/* + /api/proactive/*

来源：原 api.main_routes.py L365/379/459/512/544/549/590/638/647/664/1252 共 11 端点

依赖：
- deps.orch（_ase 主动搭话引擎）
- deps.training_mgr（训练状态管理器）
- 训练管线: clone_training / weclone_adapter / clone_training.data_cleaner / my_character.tone_mimic
- ProactiveConfigRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import ProactiveConfigRequest

logger = logging.getLogger("api._training_routes")

router = APIRouter(tags=["training"])


# ═══════════════════════════════════════════════════════
# Training Pipeline Status
# ═══════════════════════════════════════════════════════


@router.get("/api/training/status")
async def training_status(_auth: bool = Security(verify_api_key_dep)):
    try:
        import clone_training  # noqa: F401
        available = True
        desc = "训练管线已就绪"
    except ImportError:
        available = False
        desc = "训练模块未安装"
    return {"available": available, "description": desc, "steps": [
        "data_extract", "style_analyze", "build_dataset", "lora_train", "export_model",
    ]}


@router.get("/api/training/progress")
async def get_training_progress(_auth: bool = Security(verify_api_key_dep)):
    return deps.training_mgr.get_state()


# ═══════════════════════════════════════════════════════
# Training Pipeline (admin only)
# ═══════════════════════════════════════════════════════


@router.post("/api/training/extract")
async def start_extraction(
    target: str = "",
    source: str = "wcf",
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    def _do_extract():
        try:
            from weclone_adapter import WeCloneAdapter
            adapter = WeCloneAdapter(
                data_dir=str(Path(__file__).parent.parent / "data" / "clone"),
                output_dir=str(Path(__file__).parent.parent / "data" / "training"),
            )
            result = adapter.extract(target=target, source=source)
            deps.training_mgr.update(
                status="extracted",
                extracted_turns=len(result) if isinstance(result, list) else result.get("turns", 0),
                progress=0.3,
                step_name="数据提取",
            )
        except Exception:
            logger.exception("Extraction failed")
            deps.training_mgr.update(status="error", error="internal_error")

    if not target.strip():
        raise HTTPException(status_code=400, detail="target is required")

    deps.training_mgr.update(status="extracting", start_time=time.time(), step_name="数据提取")
    deps.training_mgr.submit(_do_extract)
    return {"status": "started", "task": "extract", "target": target}


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
            data_dir = Path(__file__).parent.parent / "data" / "training"
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


@router.post("/api/training/train")
async def start_training(
    epochs: int = 3,
    lora_rank: int = 16,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    def _do_train():
        try:
            from weclone_adapter import WeCloneAdapter
            adapter = WeCloneAdapter(
                data_dir=str(Path(__file__).parent.parent / "data" / "clone"),
                output_dir=str(Path(__file__).parent.parent / "data" / "training"),
            )

            def progress_callback(step, total, loss):
                deps.training_mgr.update(
                    current_step=step,
                    total_steps=total,
                    progress=step / total if total > 0 else 0,
                    loss=loss,
                )
                if deps.training_mgr.is_stopping:
                    raise InterruptedError("Training stopped by user")

            result = adapter.train(
                config_path="",
                progress_callback=progress_callback,
                epochs=epochs,
                lora_rank=lora_rank,
            )
            deps.training_mgr.update(
                status="done" if result.get("status") == "success" else "error",
                progress=1.0,
                step_name="模型训练",
            )
            if "lora_path" in result:
                deps.training_mgr.update(lora_path=result["lora_path"])
        except InterruptedError:
            deps.training_mgr.update(status="stopped")
        except Exception:
            logger.exception("Training failed")
            deps.training_mgr.update(status="error", error="internal_error")

    deps.training_mgr.update(status="training", start_time=time.time(), step_name="模型训练")
    deps.training_mgr.submit(_do_train)
    return {"status": "started", "task": "train", "epochs": epochs}


@router.post("/api/training/stop")
async def stop_training(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    deps.training_mgr.stop()
    return {"status": "stopped"}


@router.post("/api/training/test")
async def test_clone(
    message: str,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    try:
        from my_character.tone_mimic import ToneMimic
        chroma_path = str(Path(__file__).parent.parent / "data" / "chroma_db")
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
        result_path = str(Path(__file__).parent.parent / "data" / "training")
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
        return orch._ase.health_check()
    return {}


@router.get("/api/proactive/history")
async def proactive_history(
    limit: int = Query(default=50, le=200),
    _auth: bool = Security(verify_api_key_dep),
):
    orch = deps.orch
    if orch and orch._ase:
        ase = orch._ase
        messages = getattr(ase, "_sent_messages", []) if hasattr(ase, "_sent_messages") else []
        return {"history": messages[-limit:], "total": len(messages)}
    return {"history": [], "total": 0}


@router.post("/api/proactive/config")
async def update_proactive_config(
    req: ProactiveConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    ase = orch._ase
    if req.threshold is not None:
        ase._config["speak_threshold"] = req.threshold
    if req.max_daily is not None:
        ase._config["max_daily_messages"] = req.max_daily
    if req.min_interval_minutes is not None:
        ase._config["min_interval_minutes"] = req.min_interval_minutes
    if req.cooldown_after_reply_minutes is not None:
        ase._config["cooldown_after_reply"] = req.cooldown_after_reply_minutes
    logger.info("Proactive config updated: threshold=%s, max_daily=%s",
                ase._config["speak_threshold"], ase._config["max_daily_messages"])
    return {"status": "ok", "config": ase._config}
