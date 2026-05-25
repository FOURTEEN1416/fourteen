"""
声音模型管理器 — 管理训练好的声音模型
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("model_manager")


@dataclass
class VoiceModel:
    """声音模型"""
    persona_name: str
    model_path: str
    created_at: datetime
    size_mb: float
    is_active: bool = False


class ModelManager:
    """
    声音模型管理器

    功能：
    - 模型列表
    - 模型切换
    - 模型删除
    - 模型信息
    """

    def __init__(self, models_dir: str = "voice_clone/models"):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._active_models: dict[str, str] = {}  # persona_name -> model_path

    def list_models(self) -> list[dict[str, Any]]:
        """列出所有模型"""
        models = []

        for persona_dir in self.models_dir.iterdir():
            if persona_dir.is_dir():
                for model_file in persona_dir.glob("*.pth"):
                    models.append({
                        "persona_name": persona_dir.name,
                        "model_path": str(model_file),
                        "filename": model_file.name,
                        "size_mb": model_file.stat().st_size / (1024 * 1024),
                        "created_at": datetime.fromtimestamp(
                            model_file.stat().st_ctime, tz=timezone.utc
                        ).isoformat(),
                        "is_active": self._active_models.get(persona_dir.name) == str(model_file),
                    })

        return models

    def get_model(self, persona_name: str) -> dict[str, Any] | None:
        """获取人设的模型"""
        persona_dir = self.models_dir / persona_name
        if not persona_dir.exists():
            return None

        # 找最新的模型
        models = list(persona_dir.glob("*.pth"))
        if not models:
            return None

        latest = max(models, key=lambda f: f.stat().st_ctime)
        return {
            "persona_name": persona_name,
            "model_path": str(latest),
            "filename": latest.name,
            "size_mb": latest.stat().st_size / (1024 * 1024),
        }

    def set_active(self, persona_name: str, model_path: str | None = None) -> bool:
        """
        设置活跃模型

        Args:
            persona_name: 人设名称
            model_path: 模型路径，None则使用最新

        Returns:
            是否成功
        """
        if model_path is None:
            model = self.get_model(persona_name)
            if model is None:
                return False
            model_path = model["model_path"]

        if not Path(model_path).exists():
            return False

        self._active_models[persona_name] = model_path
        logger.info("Set active model for %s: %s", persona_name, model_path)
        return True

    def get_active(self, persona_name: str) -> str | None:
        """获取活跃模型路径"""
        return self._active_models.get(persona_name)

    def delete_model(self, persona_name: str, filename: str | None = None) -> bool:
        """
        删除模型

        Args:
            persona_name: 人设名称
            filename: 文件名，None则删除所有
        """
        persona_dir = self.models_dir / persona_name
        if not persona_dir.exists():
            return False

        if filename:
            model_path = persona_dir / filename
            if model_path.exists():
                model_path.unlink()
                logger.info("Deleted model: %s", model_path)
                return True
            return False
        else:
            # 删除所有
            count = 0
            for f in persona_dir.glob("*.pth"):
                f.unlink()
                count += 1
            logger.info("Deleted %d models for %s", count, persona_name)
            return count > 0

    def get_model_info(self, persona_name: str) -> dict[str, Any]:
        """获取模型详细信息"""
        model = self.get_model(persona_name)
        if model is None:
            return {
                "exists": False,
                "persona_name": persona_name,
                "message": "未找到模型",
            }

        return {
            "exists": True,
            **model,
            "is_active": self._active_models.get(persona_name) == model["model_path"],
        }
