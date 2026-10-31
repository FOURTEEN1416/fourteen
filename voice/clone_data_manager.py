"""
聊天克隆数据管理器 — 管理已提取的聊天记录数据库

功能：
- 按人物分组查看已提取的聊天记录
- 查看/删除单条或多条记录
- 按日期/关键词筛选

归属分区（2026-10-30 风格提取数据集 per-user 化）:
- 所有读写入口统一接受 ``owner``：空串 = 现状根目录（admin / 机器面，
  含**存量无主数据**）；非空 = ``CLONE_DATA_DIR / owner`` 子目录。
- 归属**只由服务端从认证主体派生**，且只在该主体自己的目录内 glob ——
  不猜归属、不从客户端自报取值（D15 显式声明原则）。
- 存量根目录数据对普通用户**不可见**（不是「猜成共享」），仅 admin 可见。

剥离历史（2026-08-27）:
- 移除"从解密数据库获取联系人列表"路径（需本机微信进程，云端不可用）
- 联系人来源仅保留：从已有克隆数据 (*_raw.json) 提取
"""

import glob
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PATH_TRAVERSAL_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]|\.\.')


def _safe_person_id(person_id: str) -> str:
    """路径段校验：禁路径分隔符、``..`` 与 Windows 非法文件名字符。

    覆盖三类边界：
    - 路径穿越：``/``、``\\``、``..``；
    - Windows 盘符语法：``:``（``C:evil`` 是驱动器相对路径，``Path(base) /
      "C:evil_raw.json"`` 会**整体替换 base**）；NTFS 备用数据流同属该字符；
    - Windows 非法文件名字符：``* ? " < > |`` 与控制字符 ``\\x00-\\x1f``
      ——否则 ``save_dataset/open`` 才抛 ``OSError``（路由层转 500，应为 400），
      且 ``*``/``?`` 会在训练目录 ``glob`` 中变成通配符（R3 验收打回）。
    """
    if not person_id:
        raise ValueError("person_id不能为空")
    if person_id in {".", ".."}:
        # R3 打回：``.`` 能通过旧校验，而 ``TRAINING_DATA_DIR.glob(".*")``
        # 会匹配并删除所有隐藏文件；``..`` 是上级目录占位符。二者都不是
        # 合法人物标识，显式拒绝（``..`` 虽已被下面的正则命中，这里仍保留
        # 一个语义更明确的错误信息）。
        raise ValueError(f"person_id不能是目录占位符: {person_id!r}")
    if _PATH_TRAVERSAL_RE.search(person_id):
        raise ValueError(f"person_id含非法字符: {person_id!r}")
    return person_id


def safe_person_id(person_id: str) -> str:
    """公开契约入口：person_id / target 路径段合法性校验。

    与内部 ``_safe_person_id`` **同一实现**（单一真源，无第二套正则），
    供路由层在落盘前校验上传 target 时复用；非法输入抛 ``ValueError``。
    """
    return _safe_person_id(person_id)


def _text_lower(value: Any) -> str:
    """任意值 → 小写文本；非字符串一律视作空串。

    存量/导入数据集里的 ``user`` / ``reply`` 可能是 int / None / list / dict
    （上传链已 ``str()`` 化，只影响 legacy 数据）；直接 ``.lower()`` 会
    AttributeError → detail 500（R3 验收打回）。
    """
    return value.lower() if isinstance(value, str) else ""


def _coerce_epoch_seconds(value: Any) -> int | None:
    """时间戳归一为「秒级整数」；无法解析返回 None。

    落盘数据存在两种形态：上传链 ``normalize_clone_conversations`` 会把数字
    ``str()`` 化（``"1700000000"``），存量/导入数据可能是原始 int/float。
    过滤与日期区间统计**必须先归一类型**，否则 `str >= int` 直接 TypeError
    （R3 验收打回：persist=True 的数据集 100% 命中，detail 日期筛选 500）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except (ValueError, OverflowError):
            return None
    return None

logger = logging.getLogger("clone.manager")

PROJECT_ROOT = Path(__file__).parent.parent.absolute()
CLONE_DATA_DIR = PROJECT_ROOT / "data" / "clone"
TRAINING_DATA_DIR = PROJECT_ROOT / "data" / "training"


class CloneDataManager:
    """克隆数据管理器"""

    def __init__(self):
        self._contacts_cache: list[dict[str, Any]] | None = None
        self._contacts_cache_time = 0

    def _owner_dir(self, owner: str = "") -> Path:
        """归属分区路径解析 —— 本类**唯一**的数据根入口。

        Args:
            owner: 归属键（注册用户 id 字符串）。空串 = 现状根目录
                （admin / 机器面，含存量无主数据）；非空 = 该归属的子目录。

        owner 段复用 ``_safe_person_id`` 的穿越防护（禁 ``/``、``\\``、``..``、
        冒号），并追加 ``resolve().relative_to(base)`` 边界复核——即便未来正则
        漏掉某种平台特有语法，也不会拼出 ``CLONE_DATA_DIR`` 之外的路径。
        """
        if not owner:
            return CLONE_DATA_DIR
        base = CLONE_DATA_DIR
        candidate = base / _safe_person_id(owner)
        try:
            candidate.resolve().relative_to(base.resolve())
        except ValueError as exc:
            raise ValueError(f"owner 路径越界: {owner!r}") from exc
        return candidate

    # ── 联系人列表（需求4） ──

    def get_contacts(self, keyword: str = "", force_refresh: bool = False,
                     owner: str = "") -> list[dict[str, Any]]:
        """获取已克隆的联系人列表（仅从已有克隆数据 *raw.json 提取）

        Args:
            keyword: 搜索关键词（按昵称/wxid筛选）
            force_refresh: 强制刷新缓存
            owner: 归属分区（空串 = 根目录全量视图）

        Returns:
            [{"username": "wxid_xxx", "display_name": "昵称", "source": "clone_data"}, ...]
        """
        # 2026-08-27 剥离：移除"从解密数据库获取"路径（需本机微信进程，云端不可用）
        # 联系人来源仅保留：从已有克隆数据 *raw.json 提取
        contacts = self._get_contacts_from_clone_data(owner)

        if keyword:
            keyword = keyword.lower()
            contacts = [
                c for c in contacts
                if keyword in c.get("username", "").lower()
                or keyword in c.get("display_name", "").lower()
            ]

        return contacts

    def _get_contacts_from_clone_data(self, owner: str = "") -> list[dict[str, Any]]:
        """从已有克隆数据提取联系人（仅 owner 分区内）"""
        contacts = []
        target_dir = self._owner_dir(owner)
        if target_dir.exists():
            for f in sorted(target_dir.glob("*_raw.json")):
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

    def list_datasets(self, owner: str = "") -> list[dict[str, Any]]:
        """列出已提取的克隆数据集（仅 owner 分区内，按人物分组）

        Args:
            owner: 归属分区（空串 = 根目录全量视图，仅 admin / 机器面使用）

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
        target_dir = self._owner_dir(owner)
        if not target_dir.exists():
            return datasets

        for f in sorted(target_dir.glob("*_raw.json"), key=os.path.getmtime, reverse=True):
            name = f.name.replace("_raw.json", "")
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                msg_count = len(data) if isinstance(data, list) else 0
            except Exception:  # noqa: BLE001
                msg_count = 0

            mtime = os.path.getmtime(f)
            style_path = target_dir / f"{name}_style_report.json"
            lora_dir = target_dir / "lora_output"

            datasets.append({
                "person_id": name,
                "person_name": name,
                "source": self._detect_source(name, owner),
                "message_count": msg_count,
                "extracted_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "has_style": style_path.exists(),
                "has_lora": lora_dir.exists() and any(lora_dir.iterdir()),
            })

        return datasets

    def has_dataset(self, person_id: str, owner: str = "") -> bool:
        """该归属分区内是否存在此数据集（404 口径的唯一判据来源）。"""
        safe_id = _safe_person_id(person_id)
        return (self._owner_dir(owner) / f"{safe_id}_raw.json").exists()

    def count_datasets(self, owner: str = "") -> int:
        """该归属分区内的数据集数量（配额校验用）。"""
        target_dir = self._owner_dir(owner)
        if not target_dir.exists():
            return 0
        return sum(1 for _ in target_dir.glob("*_raw.json"))

    def save_dataset(self, person_id: str, conversations: list[dict[str, Any]],
                     owner: str = "") -> str:
        """把标准化后的对话写入归属分区（``<owner_dir>/<person_id>_raw.json``）。

        这是「每用户独立数据集」唯一的服务端创建路径；路径穿越由
        ``_safe_person_id`` 挡在写入之前。
        """
        safe_id = _safe_person_id(person_id)
        target_dir = self._owner_dir(owner)
        target_dir.mkdir(parents=True, exist_ok=True)
        raw_path = target_dir / f"{safe_id}_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(conversations, f, ensure_ascii=False, indent=2)
        logger.info("已保存克隆数据集: %s (owner=%r, %d 条)", raw_path, owner, len(conversations))
        return safe_id

    def get_dataset_detail(self, person_id: str, page: int = 1, page_size: int = 50,
                           keyword: str = "", date_from: str = "", date_to: str = "",
                           only_user: bool = False, owner: str = "") -> dict[str, Any]:
        """查看某个人物的聊天记录详情（支持分页/筛选）

        Args:
            person_id: 人物标识
            page: 页码（从1开始）
            page_size: 每页条数
            keyword: 关键词筛选
            date_from: 起始日期 "2024-01-01"
            date_to: 截止日期 "2024-12-31"
            only_user: 只看对方的发言（不看自己的）
            owner: 归属分区（空串 = 根目录全量视图）

        Returns:
            {
                "person_id": str,
                "person_name": str,
                "total": int,
                "page": int,
                "page_size": int,
                "conversations": [{"user":.., "reply":.., "timestamp":..}, ...],
                "stats": {"date_range": str, ...}
                或 {"error": "..."}（不存在/读取失败）
            }
        """
        safe_id = _safe_person_id(person_id)
        raw_path = self._owner_dir(owner) / f"{safe_id}_raw.json"
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

        # R3 打回：legacy/导入数据可能是非 dict 行；后续所有 ``c.get(...)``
        # 都假定 dict，先在此逐出（丢弃非法行，不影响正常数据）。
        all_convs = [c for c in all_convs if isinstance(c, dict)]

        # 筛选
        filtered = all_convs
        if keyword:
            kw = keyword.lower()
            filtered = [
                c for c in filtered
                if kw in _text_lower(c.get("user")) or kw in _text_lower(c.get("reply"))
            ]
        if date_from:
            ts_from = self._date_to_ts(date_from)
            filtered = [
                c for c in filtered
                if (ts := _coerce_epoch_seconds(c.get("timestamp"))) is not None
                and ts >= ts_from
            ]
        if date_to:
            ts_to = self._date_to_ts(date_to, end_of_day=True)
            filtered = [
                c for c in filtered
                if (ts := _coerce_epoch_seconds(c.get("timestamp"))) is not None
                and ts <= ts_to
            ]
        if only_user:
            filtered = [c for c in filtered if not c.get("is_self", False)]

        # 统计
        total = len(filtered)
        timestamps = [
            ts for c in filtered
            if (ts := _coerce_epoch_seconds(c.get("timestamp"))) is not None
        ]
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

    def delete_dataset(self, person_id: str, owner: str = "") -> bool:
        """删除某个人的整个克隆数据集（仅在 owner 分区内）

        Args:
            person_id: 人物标识
            owner: 归属分区（空串 = 根目录；训练产物删除仅在根目录视图执行——
                训练数据无 owner 分区，绝不让普通用户触达他人训练件）

        Returns:
            bool: 是否成功
        """
        safe_id = _safe_person_id(person_id)
        target_dir = self._owner_dir(owner)
        deleted_any = False
        for pattern in [f"{safe_id}_raw.json", f"{safe_id}_style_report.json"]:
            f = target_dir / pattern
            if f.exists():
                f.unlink()
                deleted_any = True
                logger.info("已删除: %s", f)

        if not owner:
            # 删除训练数据（仅 admin / 机器面全量视图；无归属信息，不下放）
            # glob.escape：person_id 即便含 ``[``/``]`` 也只按字面匹配，
            # 绝不让 ``?``/``*`` 之类（已由 _safe_person_id 拒绝）或字符类
            # 通配误删他人训练件（R3 验收打回：``?`` 曾清空 training 顶层）。
            for f in TRAINING_DATA_DIR.glob(f"{glob.escape(safe_id)}*"):
                f.unlink()
                deleted_any = True

        return deleted_any

    def delete_conversation(self, person_id: str, index: int, owner: str = "") -> bool:
        """删除某个人物数据集中的单条对话（仅在 owner 分区内）

        Args:
            person_id: 人物标识
            index: 对话索引（从0开始）
            owner: 归属分区

        Returns:
            bool: 是否成功
        """
        safe_id = _safe_person_id(person_id)
        raw_path = self._owner_dir(owner) / f"{safe_id}_raw.json"
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

    def batch_delete_conversations(self, person_id: str, indices: list[int],
                                   owner: str = "") -> int:
        """批量删除多条对话（仅在 owner 分区内）

        Args:
            person_id: 人物标识
            indices: 要删除的索引列表
            owner: 归属分区

        Returns:
            int: 实际删除条数
        """
        safe_id = _safe_person_id(person_id)
        raw_path = self._owner_dir(owner) / f"{safe_id}_raw.json"
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

    def get_stats(self, owner: str = "") -> dict[str, Any]:
        """获取克隆数据统计（owner 分区内；空串 = 根目录全量视图）"""
        datasets = self.list_datasets(owner)
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

    def _detect_source(self, name: str, owner: str = "") -> str:
        """猜测数据来源（仅 owner 分区内）"""
        raw_path = self._owner_dir(owner) / f"{name}_raw.json"
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
