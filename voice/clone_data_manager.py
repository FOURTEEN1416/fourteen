"""
聊天克隆数据管理器 — 管理已提取的聊天记录数据库

功能：
- 按人物分组查看已提取的聊天记录
- 查看/删除单条或多条记录
- 按日期/关键词筛选

剥离历史（2026-08-27）:
- 移除"从解密数据库获取联系人列表"路径（需本机微信进程，云端不可用）
- 联系人来源仅保留：从已有克隆数据 (*_raw.json) 提取
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PATH_TRAVERSAL_RE = re.compile(r'[/\\]|\.\.')


def _safe_person_id(person_id: str) -> str:
    if _PATH_TRAVERSAL_RE.search(person_id):
        raise ValueError(f"person_id含非法字符: {person_id!r}")
    return person_id

logger = logging.getLogger("clone.manager")

PROJECT_ROOT = Path(__file__).parent.parent.absolute()
CLONE_DATA_DIR = PROJECT_ROOT / "data" / "clone"
TRAINING_DATA_DIR = PROJECT_ROOT / "data" / "training"


class CloneDataManager:
    """克隆数据管理器"""

    def __init__(self):
        self._contacts_cache: list[dict[str, Any]] | None = None
        self._contacts_cache_time = 0

    # ── 联系人列表（需求4） ──

    def get_contacts(self, keyword: str = "", force_refresh: bool = False) -> list[dict[str, Any]]:
        """获取已克隆的联系人列表（仅从已有克隆数据 *raw.json 提取）

        Args:
            keyword: 搜索关键词（按昵称/wxid筛选）
            force_refresh: 强制刷新缓存

        Returns:
            [{"username": "wxid_xxx", "display_name": "昵称", "source": "clone_data"}, ...]
        """
        # 2026-08-27 剥离：移除"从解密数据库获取"路径（需本机微信进程，云端不可用）
        # 联系人来源仅保留：从已有克隆数据 *raw.json 提取
        contacts = self._get_contacts_from_clone_data()

        if keyword:
            keyword = keyword.lower()
            contacts = [
                c for c in contacts
                if keyword in c.get("username", "").lower()
                or keyword in c.get("display_name", "").lower()
            ]

        return contacts

    def _get_contacts_from_clone_data(self) -> list[dict[str, Any]]:
        """从已有克隆数据提取联系人"""
        contacts = []
        if CLONE_DATA_DIR.exists():
            for f in sorted(CLONE_DATA_DIR.glob("*_raw.json")):
                name = f.name.replace("_raw.json", "")
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                    msg_count = len(data) if isinstance(data, list) else 0
                    contacts.append({
                        "username": name,
                        "display_name": name,
                        "source": "clone_data",
                        "msg_count": msg_count,
                    })
                except Exception:  # noqa: BLE001
                    contacts.append({
                        "username": name,
                        "display_name": name,
                        "source": "clone_data",
                        "msg_count": 0,
                    })
        return contacts

    # ── 克隆数据集管理（需求3） ──

    def list_datasets(self) -> list[dict[str, Any]]:
        """列出所有已提取的克隆数据集（按人物分组）

        Returns:
            [{
                "person_id": str,       # 人物标识
                "person_name": str,     # 显示名称
                "source": str,          # 数据来源
                "message_count": int,   # 消息条数
                "extracted_at": str,    # 提取时间
                "has_style": bool,      # 是否有风格分析
                "has_lora": bool,       # 是否有训练模型
            }, ...]
        """
        datasets = []  # type: ignore[var-annotated]
        if not CLONE_DATA_DIR.exists():
            return datasets

        for f in sorted(CLONE_DATA_DIR.glob("*_raw.json"), key=os.path.getmtime, reverse=True):
            name = f.name.replace("_raw.json", "")
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                msg_count = len(data) if isinstance(data, list) else 0
            except Exception:  # noqa: BLE001
                msg_count = 0

            mtime = os.path.getmtime(f)
            style_path = CLONE_DATA_DIR / f"{name}_style_report.json"
            lora_dir = CLONE_DATA_DIR / "lora_output"

            datasets.append({
                "person_id": name,
                "person_name": name,
                "source": self._detect_source(name),
                "message_count": msg_count,
                "extracted_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "has_style": style_path.exists(),
                "has_lora": lora_dir.exists() and any(lora_dir.iterdir()),
            })

        return datasets

    def get_dataset_detail(self, person_id: str, page: int = 1, page_size: int = 50,
                           keyword: str = "", date_from: str = "", date_to: str = "",
                           only_user: bool = False) -> dict[str, Any]:
        _safe_person_id(person_id)
        """查看某个人物的聊天记录详情（支持分页/筛选）

        Args:
            person_id: 人物标识
            page: 页码（从1开始）
            page_size: 每页条数
            keyword: 关键词筛选
            date_from: 起始日期 "2024-01-01"
            date_to: 截止日期 "2024-12-31"
            only_user: 只看对方的发言（不看自己的）

        Returns:
            {
                "person_id": str,
                "person_name": str,
                "total": int,
                "page": int,
                "page_size": int,
                "conversations": [{"user":.., "reply":.., "timestamp":..}, ...],
                "stats": {"date_range": str, ...}
            }
        """
        raw_path = CLONE_DATA_DIR / f"{person_id}_raw.json"
        if not raw_path.exists():
            return {"person_id": person_id, "error": "数据集不存在", "conversations": []}

        try:
            with open(raw_path, encoding="utf-8") as f:
                all_convs = json.load(f)
        except Exception:
            logger.exception("Failed to load dataset %s", person_id)
            return {"person_id": person_id, "error": "读取数据集失败", "conversations": []}

        if not isinstance(all_convs, list):
            return {"person_id": person_id, "error": "数据格式错误", "conversations": []}

        # 筛选
        filtered = all_convs
        if keyword:
            kw = keyword.lower()
            filtered = [
                c for c in filtered
                if kw in c.get("user", "").lower() or kw in c.get("reply", "").lower()
            ]
        if date_from:
            ts_from = self._date_to_ts(date_from)
            filtered = [c for c in filtered if c.get("timestamp", 0) >= ts_from]
        if date_to:
            ts_to = self._date_to_ts(date_to, end_of_day=True)
            filtered = [c for c in filtered if c.get("timestamp", 0) <= ts_to]
        if only_user:
            filtered = [c for c in filtered if not c.get("is_self", False)]

        # 统计
        total = len(filtered)
        timestamps = [c.get("timestamp", 0) for c in filtered if c.get("timestamp")]
        stats = {
            "total": total,
            "date_range": "",
        }
        if timestamps:
            try:
                min_dt = datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
                max_dt = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
                stats["date_range"] = f"{min_dt.strftime('%Y-%m-%d')} ~ {max_dt.strftime('%Y-%m-%d')}"
            except Exception:  # noqa: BLE001
                pass

        # 分页
        start = (page - 1) * page_size
        end = start + page_size
        page_convs = filtered[start:end]

        return {
            "person_id": person_id,
            "person_name": person_id,
            "total": total,
            "page": page,
            "page_size": page_size,
            "conversations": page_convs,
            "stats": stats,
        }

    def delete_dataset(self, person_id: str) -> bool:
        _safe_person_id(person_id)
        """删除某个人的整个克隆数据集

        Args:
            person_id: 人物标识

        Returns:
            bool: 是否成功
        """
        deleted_any = False
        for pattern in [f"{person_id}_raw.json", f"{person_id}_style_report.json"]:
            f = CLONE_DATA_DIR / pattern
            if f.exists():
                f.unlink()
                deleted_any = True
                logger.info("已删除: %s", f)

        # 删除训练数据
        for f in TRAINING_DATA_DIR.glob(f"{person_id}*"):
            f.unlink()
            deleted_any = True

        return deleted_any

    def delete_conversation(self, person_id: str, index: int) -> bool:
        _safe_person_id(person_id)
        """删除某个人物数据集中的单条对话

        Args:
            person_id: 人物标识
            index: 对话索引（从0开始）

        Returns:
            bool: 是否成功
        """
        raw_path = CLONE_DATA_DIR / f"{person_id}_raw.json"
        if not raw_path.exists():
            return False

        try:
            with open(raw_path, encoding="utf-8") as f:
                convs = json.load(f)
        except Exception:  # noqa: BLE001
            return False

        if not isinstance(convs, list) or index < 0 or index >= len(convs):
            return False

        convs.pop(index)

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(convs, f, ensure_ascii=False, indent=2)

        return True

    def batch_delete_conversations(self, person_id: str, indices: list[int]) -> int:
        _safe_person_id(person_id)
        """批量删除多条对话

        Args:
            person_id: 人物标识
            indices: 要删除的索引列表

        Returns:
            int: 实际删除条数
        """
        raw_path = CLONE_DATA_DIR / f"{person_id}_raw.json"
        if not raw_path.exists():
            return 0

        try:
            with open(raw_path, encoding="utf-8") as f:
                convs = json.load(f)
        except Exception:  # noqa: BLE001
            return 0

        if not isinstance(convs, list):
            return 0

        # 从大到小删除，避免索引偏移
        sorted_indices = sorted(set(indices), reverse=True)
        deleted = 0
        for idx in sorted_indices:
            if 0 <= idx < len(convs):
                convs.pop(idx)
                deleted += 1

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(convs, f, ensure_ascii=False, indent=2)

        return deleted

    # ── 统计 ──

    def get_stats(self) -> dict[str, Any]:
        """获取克隆数据全局统计"""
        datasets = self.list_datasets()
        total_persons = len(datasets)
        total_messages = sum(d.get("message_count", 0) for d in datasets)
        cloned_persons = [d for d in datasets if d.get("has_lora")]

        return {
            "total_persons": total_persons,
            "total_messages": total_messages,
            "cloned_persons": len(cloned_persons),
            "persons": datasets,
        }

    # ── 工具 ──

    def _detect_source(self, name: str) -> str:
        """猜测数据来源"""
        raw_path = CLONE_DATA_DIR / f"{name}_raw.json"
        if not raw_path.exists():
            return "unknown"
        try:
            with open(raw_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data[0].get("source", "unknown")  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001
            pass
        return "unknown"

    def _date_to_ts(self, date_str: str, end_of_day: bool = False) -> int:
        """日期字符串 → 时间戳"""
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")  # noqa: DTZ007
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp())
        except ValueError:
            return 0
