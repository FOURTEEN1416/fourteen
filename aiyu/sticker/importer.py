"""表情包ZIP批量导入 + 安全检测。"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from ..config import get_config

logger = logging.getLogger("aiyu.sticker.importer")

_SUPPORTED_FORMATS = set(get_config("sticker", "supported_formats", ["png", "gif", "webp"]))
_MAX_SIZE_MB = get_config("sticker", "max_file_size_mb", 5)


class StickerImporter:
    def __init__(self, data_dir: Path | str = "data/stickers"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def import_zip(self, zip_path: Path | str, category: str = "default") -> tuple[int, int]:
        p = Path(zip_path)
        if not p.exists():
            return 0, 0

        success, failed = 0, 0
        category_dir = self._data_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(p, 'r') as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    ext = Path(info.filename).suffix.lstrip('.').lower()
                    if ext not in _SUPPORTED_FORMATS:
                        failed += 1
                        continue
                    if info.file_size > _MAX_SIZE_MB * 1024 * 1024:
                        failed += 1
                        logger.warning("表情包过大: %s (%d bytes)", info.filename, info.file_size)
                        continue
                    try:
                        out_name = Path(info.filename).name
                        if not out_name or '/' in out_name or '\\' in out_name:
                            failed += 1
                            logger.warning("ZIP Slip检测: 跳过可疑文件名: %s", info.filename)
                            continue
                        out_path = category_dir / out_name
                        with zf.open(info) as src, open(out_path, 'wb') as dst:
                            dst.write(src.read())
                        success += 1
                    except Exception as e:
                        failed += 1
                        logger.warning("解压失败: %s: %s", info.filename, e)
        except zipfile.BadZipFile:
            logger.error("无效ZIP: %s", zip_path)
            return 0, 0

        return success, failed
