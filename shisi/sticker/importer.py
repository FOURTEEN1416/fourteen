"""表情包ZIP批量导入 — 解包落盘 + 逐张写库 + 安全门禁。

2026-09-28 D12-K（W14）根治：旧实现只落文件不写 ``stickers`` 表，而读侧
（list_by_category / recommend）只认表 → 导入的永远不可见。现解包成功后
逐张写库：sticker_id 由「文件名+内容」派生稳定 id（重复导入幂等不堆积）、
emotion_tags 留空（不进情感推荐，等打标）、不合规文件名经
``check_sticker_safety`` 在落盘前拦截计入 failed。
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from ..config import get_config
from .safety_check import check_sticker_safety

logger = logging.getLogger("shisi.sticker.importer")

_SUPPORTED_FORMATS = set(get_config("sticker", "supported_formats", ["png", "gif", "webp"]))
_MAX_SIZE_MB = get_config("sticker", "max_file_size_mb", 5)

# 与 StickerManager 同源：导入必须落到真实 stickers 库——不存在"只落文件
# 不入库"的模式（那正是 D12-K 根治掉的不可见导入）。
_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"

# ── category 白名单消毒（2026-10 P0）─────────────────────────────
# category 直接拼目录路径（_data_dir / category），旧实现未做任何校验：
# `../evil` 即可在数据目录外落盘（路径穿越）。现收口为 [A-Za-z0-9_-]
# 白名单 + resolve 后包含性断言（纵深防御），非法一律 ValueError，
# 由路由层转 400。
_CATEGORY_ALLOWED = re.compile(r"[A-Za-z0-9_-]")
_CATEGORY_MAX_LEN = 64


def sanitize_category(category: str) -> str:
    """category 白名单消毒：仅允许 ``[A-Za-z0-9_-]``，长度 ≤ 64。

    与 ``api/path_security.sanitize_id`` 的字符面一致，但语义取「拒绝」
    而非「静默清洗」——含任何白名单外字符（含 ``/`` ``\\`` ``.``）即
    ValueError，不给穿越串变形落盘的机会。
    """
    raw = str(category or "")
    cleaned = "".join(_CATEGORY_ALLOWED.findall(raw))[:_CATEGORY_MAX_LEN]
    if not cleaned or cleaned != raw:
        raise ValueError(
            f"非法 category: {raw!r}（仅允许 [A-Za-z0-9_-]，长度≤{_CATEGORY_MAX_LEN}）"
        )
    return cleaned


def _stable_sticker_id(filename: str, content: bytes) -> str:
    """文件名+内容派生稳定 id：同文件重复导入幂等，改内容即新 id。"""
    digest = hashlib.sha1(f"{filename}|".encode() + content).hexdigest()
    return f"stk_{digest[:16]}"


class StickerImporter:
    def __init__(self, db_path: Path | str | None = None, data_dir: Path | str = "data/stickers"):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _record(self, sticker_id: str, category: str, file_path: str, fmt: str) -> bool:
        """写 stickers 行（emotion_tags 留空=不进推荐）。写失败返回 False。"""
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO stickers "
                    "(sticker_id, category, emotion_tags, file_path, format) VALUES (?,?,?,?,?)",
                    (sticker_id, category, "[]", file_path, fmt),
                )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("表情包入库失败: %s: %s", sticker_id, e)
            return False

    def import_zip(self, zip_path: Path | str, category: str = "default") -> tuple[int, int]:
        """返回 (accepted, failed)：accepted = 真实入库张数（文件+表都成功）。

        P0：``category`` 先经白名单消毒，再做 resolve 后包含性断言——
        ``category_dir`` 必须落在 ``self._data_dir`` 内，否则 ValueError
        （路由层转 400）。消毒先于 zip 存在性早退：非法 category 无论
        包体在否一律拒绝，不给试探性探测留口。
        """
        category = sanitize_category(category)
        data_root = self._data_dir.resolve()
        category_dir = (data_root / category).resolve()
        try:
            category_dir.relative_to(data_root)
        except ValueError as e:
            raise ValueError(
                f"category 目录越界: {category_dir} 不在 {data_root} 内"
            ) from e
        if category_dir == data_root:
            raise ValueError(f"category 不得指向数据根目录本身: {category!r}")

        p = Path(zip_path)
        if not p.exists():
            return 0, 0

        accepted, failed = 0, 0
        category_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(p, 'r') as zf:
                for outcome in self._extract(zf, category_dir):
                    if outcome is None:
                        failed += 1
                        continue
                    out_name, out_path, ext = outcome
                    sticker_id = _stable_sticker_id(out_name, out_path.read_bytes())
                    if not self._record(sticker_id, category, str(out_path), ext):
                        failed += 1
                        logger.warning("表情包未入库（回执计 failed）: %s", out_name)
                        continue
                    accepted += 1
        except zipfile.BadZipFile:
            logger.error("无效ZIP: %s", zip_path)
            return 0, 0

        return accepted, failed

    def _extract(self, zf: zipfile.ZipFile, category_dir: Path) -> Iterator[tuple[str, Path, str] | None]:
        """逐条解包；产出 None 表示该条计 failed（门禁/格式/大小/解压失败）。

        安全门禁（D12-K）：``check_sticker_safety`` 对文件名先行校验，
        不合规的直接拦截——不落盘、不入库、计入 failed。
        """
        for info in zf.infolist():
            if info.is_dir():
                continue
            ext = Path(info.filename).suffix.lstrip('.').lower()
            if ext not in _SUPPORTED_FORMATS:
                yield None
                continue
            if info.file_size > _MAX_SIZE_MB * 1024 * 1024:
                logger.warning("表情包过大: %s (%d bytes)", info.filename, info.file_size)
                yield None
                continue
            ok, reason = check_sticker_safety(info.filename)
            if not ok:
                logger.warning("表情包安全门禁拦截: %s (%s)", info.filename, reason)
                yield None
                continue
            out_name = Path(info.filename).name
            if not out_name or '/' in out_name or '\\' in out_name:
                logger.warning("ZIP Slip检测: 跳过可疑文件名: %s", info.filename)
                yield None
                continue
            try:
                out_path = category_dir / out_name
                with zf.open(info) as src, open(out_path, 'wb') as dst:
                    dst.write(src.read())
            except Exception as e:  # noqa: BLE001
                logger.warning("解压失败: %s: %s", info.filename, e)
                yield None
                continue
            yield out_name, out_path, ext
