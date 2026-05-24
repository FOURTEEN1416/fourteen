"""DefaultStickerProvider - 预置表情包初始化器"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .sticker_manager import StickerManager

logger = logging.getLogger("shisi.sticker.default_provider")


class DefaultStickerProvider:
    DEFAULT_STICKERS = [
        {"id": "happy_01", "category": "开心", "emotion_tags": ["开心", "可爱"], "file": "happy_01.png"},
        {"id": "happy_02", "category": "开心", "emotion_tags": ["开心", "撒娇"], "file": "happy_02.png"},
        {"id": "happy_03", "category": "开心", "emotion_tags": ["开心", "可爱"], "file": "happy_03.png"},
        {"id": "sad_01", "category": "伤心", "emotion_tags": ["伤心", "委屈"], "file": "sad_01.png"},
        {"id": "sad_02", "category": "伤心", "emotion_tags": ["伤心"], "file": "sad_02.png"},
        {"id": "angry_01", "category": "生气", "emotion_tags": ["生气", "傲娇"], "file": "angry_01.png"},
        {"id": "angry_02", "category": "生气", "emotion_tags": ["生气"], "file": "angry_02.png"},
        {"id": "coy_01", "category": "撒娇", "emotion_tags": ["撒娇", "可爱"], "file": "coy_01.png"},
        {"id": "coy_02", "category": "撒娇", "emotion_tags": ["撒娇", "开心"], "file": "coy_02.png"},
        {"id": "shy_01", "category": "害羞", "emotion_tags": ["害羞", "可爱"], "file": "shy_01.png"},
        {"id": "fear_01", "category": "害怕", "emotion_tags": ["害怕", "紧张"], "file": "fear_01.png"},
    ]

    def __init__(self, sticker_manager: StickerManager, data_dir: str = "data/stickers/default"):
        self._mgr = sticker_manager
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> int:
        existing = self._mgr.list_by_category()
        if existing:
            logger.info("表情库已有 %d 条数据，跳过初始化", len(existing))
            return 0

        count = 0
        for meta in self.DEFAULT_STICKERS:
            file_path = self._data_dir / meta["file"]
            if not file_path.exists():
                logger.warning("预置表情文件不存在: %s，跳过", file_path)
                continue
            try:
                self._mgr.add_sticker(
                    sticker_id=meta["id"],
                    category=meta["category"],
                    emotion_tags=meta["emotion_tags"],
                    file_path=str(file_path),
                    fmt="png",
                )
                count += 1
            except Exception as e:
                logger.warning("添加预置表情失败 %s: %s", meta["id"], e)

        logger.info("预置表情包初始化完成: %d/%d", count, len(self.DEFAULT_STICKERS))
        return count
