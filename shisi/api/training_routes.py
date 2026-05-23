"""音色训练API端点 — 上传/预处理/训练/状态查询。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from voice.voice_training import VoiceTrainingManager
from .common import ApiResponse

logger = logging.getLogger("shisi.api.training_routes")

router = APIRouter(prefix="/api/shisi/voice/training", tags=["voice-training"])

_manager: Optional[VoiceTrainingManager] = None


def set_manager(mgr: VoiceTrainingManager) -> None:
    global _manager
    _manager = mgr


def _get_manager() -> VoiceTrainingManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="VoiceTrainingManager未初始化")
    return _manager


class TrainRequest(BaseModel):
    model_name: str
    epochs: int = 8
    batch_size: int = 4


class PreprocessRequest(BaseModel):
    model_name: str


@router.post("/upload", response_model=ApiResponse)
async def upload_training_audio(
    files: list[UploadFile] = File(...),
    model_name: str = "default",
):
    mgr = _get_manager()
    file_data = []
    for f in files:
        content = await f.read()
        file_data.append((f.filename or "unknown.wav", content))
    result = await mgr.save_uploads(file_data, model_name)
    return ApiResponse(data=result)


@router.post("/preprocess", response_model=ApiResponse)
async def preprocess_audio(req: PreprocessRequest):
    mgr = _get_manager()
    result = await mgr.preprocess(req.model_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return ApiResponse(data=result)


@router.post("/train", response_model=ApiResponse)
async def start_training(req: TrainRequest):
    mgr = _get_manager()
    result = await mgr.train(req.model_name, epochs=req.epochs, batch_size=req.batch_size)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error", "训练失败"))
    return ApiResponse(data=result)


@router.get("/status", response_model=ApiResponse)
async def get_training_status():
    mgr = _get_manager()
    return ApiResponse(data=mgr.state)
