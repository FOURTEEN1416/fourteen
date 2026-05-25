"""
声音样本上传处理器 — 管理声音样本的上传和存储
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("upload_handler")


class UploadHandler:
    """
    声音样本上传处理器

    支持：
    - 样本上传与存储
    - 格式验证
    - 样本管理
    """

    SUPPORTED_FORMATS = [".wav", ".mp3", ".m4a", ".ogg", ".flac"]
    MIN_DURATION_SECONDS = 5
    MAX_DURATION_SECONDS = 60
    RECOMMENDED_SAMPLES = 5  # 推荐5-10个样本
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 最大文件大小 50MB

    def __init__(self, samples_dir: str = "voice_clone/samples"):
        self.samples_dir = Path(samples_dir)
        self.samples_dir.mkdir(parents=True, exist_ok=True)

    def upload(
        self,
        persona_name: str,
        audio_data: bytes,
        filename: str,
    ) -> dict[str, Any]:
        """
        上传声音样本

        Args:
            persona_name: 人设名称
            audio_data: 音频数据
            filename: 文件名

        Returns:
            上传结果
        """
        # 验证格式
        ext = Path(filename).suffix.lower()
        if ext not in self.SUPPORTED_FORMATS:
            return {
                "success": False,
                "error": f"不支持的格式: {ext}，支持: {self.SUPPORTED_FORMATS}",
            }

        # 验证文件大小
        if len(audio_data) > self.MAX_FILE_SIZE:
            return {
                "success": False,
                "error": f"文件大小超过限制: {len(audio_data) / (1024*1024):.1f}MB > {self.MAX_FILE_SIZE / (1024*1024):.0f}MB",
            }

        # 创建人设目录
        persona_dir = self.samples_dir / persona_name
        persona_dir.mkdir(parents=True, exist_ok=True)

        # 生成唯一文件名
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        saved_filename = f"sample_{timestamp}{ext}"
        saved_path = persona_dir / saved_filename

        # 保存文件
        try:
            with open(saved_path, "wb") as f:
                f.write(audio_data)

            logger.info("Saved voice sample: %s", saved_path)

            return {
                "success": True,
                "path": str(saved_path),
                "filename": saved_filename,
                "persona": persona_name,
            }

        except Exception as e:  # noqa: BLE001

            logger.error("Failed to save sample: %s", e)
            return {"success": False, "error": str(e)}

    def list_samples(self, persona_name: str) -> list[dict[str, Any]]:
        """列出人设的所有样本"""
        persona_dir = self.samples_dir / persona_name
        if not persona_dir.exists():
            return []

        samples = []
        for f in persona_dir.iterdir():
            if f.suffix.lower() in self.SUPPORTED_FORMATS:
                samples.append({
                    "filename": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "created": datetime.fromtimestamp(f.stat().st_ctime, tz=timezone.utc).isoformat(),
                })

        return sorted(samples, key=lambda x: x["created"], reverse=True)

    def delete_sample(self, persona_name: str, filename: str) -> bool:
        """删除样本"""
        sample_path = self.samples_dir / persona_name / filename
        if sample_path.exists():
            sample_path.unlink()
            logger.info("Deleted sample: %s", sample_path)
            return True
        return False

    def get_sample_count(self, persona_name: str) -> int:
        """获取样本数量"""
        return len(self.list_samples(persona_name))

    def is_ready_for_training(self, persona_name: str) -> tuple[bool, str]:
        """
        检查是否准备好训练

        Returns:
            (是否准备好, 原因)
        """
        count = self.get_sample_count(persona_name)

        if count < 3:
            return False, f"样本不足：需要至少3个，当前{count}个"

        if count < self.RECOMMENDED_SAMPLES:
            return True, f"可以训练，但建议再添加{self.RECOMMENDED_SAMPLES - count}个样本"

        return True, f"样本充足：{count}个"

    def clear_samples(self, persona_name: str) -> int:
        """清空人设的所有样本"""
        persona_dir = self.samples_dir / persona_name
        if not persona_dir.exists():
            return 0

        count = 0
        for f in persona_dir.iterdir():
            if f.suffix.lower() in self.SUPPORTED_FORMATS:
                f.unlink()
                count += 1

        logger.info("Cleared %d samples for %s", count, persona_name)
        return count
