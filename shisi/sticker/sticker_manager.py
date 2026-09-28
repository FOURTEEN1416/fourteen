"""StickerManager表情包管理器 — CRUD + 分类 + 推荐。"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from utils.project_paths import resolve_project_path

from ..config import get_config

logger = logging.getLogger("shisi.sticker.sticker_manager")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


class StickerManager:
    def __init__(
        self,
        db_path: Path | str | None = None,
        data_dir: Path | str | None = None,
        affinity_provider: Callable[[str, str], float | None] | None = None,
    ):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT
        # 配置缺省值也须锚定项目根，否则从非仓库根启动时会写到错误目录
        self._data_dir = (
            Path(data_dir) if data_dir
            else resolve_project_path(get_config("sticker", "data_dir", "data/stickers"))
        )
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # W14（D10）：角色绑定表情按好感档位过滤的亲和来源。
        # 签名 (character_id, user_id) -> float | None；None = 装配缺失（跳过门禁）。
        self._affinity_provider = affinity_provider

    def list_by_category(self, category: str | None = None) -> list[dict[str, Any]]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            if category:
                rows = conn.execute("SELECT * FROM stickers WHERE category=?", (category,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM stickers").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _affinity_of(self, character_id: str, user_id: str) -> float | None:
        """shisi 刻度 user×character 亲和；评估不了返回 None（门禁跳过）。"""
        if not user_id:
            return None
        if self._affinity_provider is not None:
            try:
                value = self._affinity_provider(character_id, user_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("表情包亲和读取失败（门禁跳过）: %s", e)
                return None
            return None if value is None else float(value)
        from ..affinity.unlock_manager import read_user_affinity

        return read_user_affinity(character_id, user_id)

    def recommend(
        self,
        emotion_tags: list[str],
        limit: int = 5,
        character_id: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        from .emotion_recommender import EmotionRecommender
        rec = EmotionRecommender()
        all_stickers = self.list_by_category()
        if character_id:
            # W14（D10）：带用户上下文时按 unlock_threshold 硬过滤——
            # 用户对该角色好感未达标的绑定卡不进推荐；兜底池排除**全部**
            # 绑定卡（含未解锁），否则锁定卡经 general 池又可见。
            affinity = self._affinity_of(character_id, user_id)
            if affinity is None:
                char_ids = self._get_character_sticker_ids(character_id)
                excluded = char_ids
                gated = False
            else:
                char_ids = self._get_character_sticker_ids(character_id, affinity=affinity)
                excluded = self._get_character_sticker_ids(character_id)
                gated = True
            if char_ids or gated:
                char_stickers = [s for s in all_stickers if s.get("sticker_id") in char_ids]
                result = rec.recommend(emotion_tags, char_stickers, limit)
                if len(result) < limit:
                    general = [s for s in all_stickers if s.get("sticker_id") not in excluded]
                    extra = rec.recommend(emotion_tags, general, limit - len(result))
                    result.extend(extra)
                return result
        return rec.recommend(emotion_tags, all_stickers, limit)

    def _get_character_sticker_ids(
        self, character_id: str, affinity: float | None = None
    ) -> set[str]:
        """角色绑定表情 id 集。

        ``affinity`` 为 None 时返回全部绑定（管理面/无用户上下文，旧口径）；
        给定 shisi 亲和值时只返回 unlock_threshold ≤ 亲和 的卡（阈值 0 恒可见）。
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                "SELECT sticker_id, unlock_threshold FROM character_stickers WHERE character_id=?",
                (character_id,),
            ).fetchall()
        finally:
            conn.close()
        if affinity is None:
            return {r[0] for r in rows}
        return {r[0] for r in rows if int(r[1] or 0) <= affinity}

    def import_zip(self, zip_path: Path | str, category: str = "default") -> tuple[int, int]:
        """ZIP 导入：返回 (accepted=真实入库张数, failed=门禁/写库失败数)。"""
        from .importer import StickerImporter
        imp = StickerImporter(db_path=self._db_path, data_dir=self._data_dir)
        return imp.import_zip(zip_path, category)

    def bind_to_character(self, character_id: str, sticker_ids: list[str], unlock_threshold: int = 0) -> int:
        conn = sqlite3.connect(str(self._db_path))
        try:
            count = 0
            for sid in sticker_ids:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO character_stickers (character_id, sticker_id, unlock_threshold) VALUES (?,?,?)",
                        (character_id, sid, unlock_threshold),
                    )
                    count += 1
                except Exception as e:  # noqa: BLE001
                    logger.debug("单条表情包插入跳过: %s", e)
            conn.commit()
            return count
        finally:
            conn.close()

    def get_sticker(self, sticker_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM stickers WHERE sticker_id=?", (sticker_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_sticker(self, sticker_id: str, category: str, emotion_tags: list[str], file_path: str, fmt: str = "png", character_id: str | None = None) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO stickers (sticker_id, category, emotion_tags, file_path, format, character_id) VALUES (?,?,?,?,?,?)",
                (sticker_id, category, json.dumps(emotion_tags, ensure_ascii=False), file_path, fmt, character_id),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_sticker(self, sticker_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute("DELETE FROM stickers WHERE sticker_id=?", (sticker_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
