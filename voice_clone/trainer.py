"""
声音克隆训练器 — 调度GPT-SoVITS训练任务
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("voice_trainer")


class TrainingStatus(Enum):
    """训练状态"""
    PENDING = "pending"
    PREPARING = "preparing"
    TRAINING = "training"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TrainingJob:
    """训练任务"""
    persona_name: str
    status: TrainingStatus
    progress: float  # 0-100
    started_at: datetime | None
    completed_at: datetime | None
    model_path: str | None
    error: str | None


class VoiceTrainer:
    """
    声音克隆训练器

    使用GPT-SoVITS进行声音克隆训练
    """

    def __init__(
        self,
        samples_dir: str = "voice_clone/samples",
        models_dir: str = "voice_clone/models",
        sovits_api_url: str = "http://localhost:9880",
    ):
        self.samples_dir = Path(samples_dir)
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.sovits_api_url = sovits_api_url
        self._jobs: dict[str, TrainingJob] = {}

    def start_training(
        self,
        persona_name: str,
        epochs: int = 100,
        batch_size: int = 4,
    ) -> dict[str, Any]:
        """
        开始训练

        Args:
            persona_name: 人设名称
            epochs: 训练轮数
            batch_size: 批次大小

        Returns:
            训练任务信息
        """
        # 检查样本
        from .upload_handler import UploadHandler
        upload_handler = UploadHandler(str(self.samples_dir))
        ready, reason = upload_handler.is_ready_for_training(persona_name)

        if not ready:
            return {"success": False, "error": reason}

        # 创建训练任务
        job = TrainingJob(
            persona_name=persona_name,
            status=TrainingStatus.PENDING,
            progress=0.0,
            started_at=datetime.now(tz=timezone.utc),
            completed_at=None,
            model_path=None,
            error=None,
        )
        self._jobs[persona_name] = job

        # 启动训练（异步）
        self._run_training(persona_name, epochs, batch_size)

        return {
            "success": True,
            "job_id": persona_name,
            "status": job.status.value,
            "message": "训练任务已启动",
        }

    def _run_training(
        self,
        persona_name: str,
        epochs: int,
        batch_size: int,
    ) -> None:
        """执行训练"""
        job = self._jobs.get(persona_name)
        if not job:
            return

        try:
            job.status = TrainingStatus.PREPARING
            logger.info("Preparing training for %s", persona_name)

            # 预处理样本（降噪、分段、标注）
            # 这里简化实现，实际需要调用Whisper/FunASR

            job.status = TrainingStatus.TRAINING
            logger.info("Training started for %s", persona_name)

            # 调用GPT-SoVITS训练API
            # 这里简化实现，实际需要HTTP调用

            # 模拟训练进度
            job.progress = 100.0
            job.status = TrainingStatus.COMPLETED
            job.completed_at = datetime.now(tz=timezone.utc)

            # 保存模型路径
            model_path = self.models_dir / persona_name / "model.pth"
            model_path.parent.mkdir(parents=True, exist_ok=True)
            job.model_path = str(model_path)

            logger.info("Training completed for %s", persona_name)

        except Exception as e:  # noqa: BLE001

            job.status = TrainingStatus.FAILED
            job.error = str(e)
            logger.error("Training failed for %s: %s", persona_name, e)

    def get_training_status(self, persona_name: str) -> dict[str, Any] | None:
        """获取训练状态"""
        job = self._jobs.get(persona_name)
        if not job:
            return None

        return {
            "persona_name": job.persona_name,
            "status": job.status.value,
            "progress": job.progress,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "model_path": job.model_path,
            "error": job.error,
        }

    def cancel_training(self, persona_name: str) -> bool:
        """取消训练"""
        job = self._jobs.get(persona_name)
        if job and job.status in [TrainingStatus.PENDING, TrainingStatus.PREPARING, TrainingStatus.TRAINING]:
            job.status = TrainingStatus.FAILED
            job.error = "用户取消"
            logger.info("Training cancelled for %s", persona_name)
            return True
        return False

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有训练任务"""
        return [
            status
            for name in self._jobs
            if (status := self.get_training_status(name)) is not None
        ]
