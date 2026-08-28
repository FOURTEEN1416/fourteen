"""重要日期存储（候选 D，2026-08-28）。

data/important_dates.json: {character_id: [{name, date("MM-DD"或"YYYY-MM-DD"), kind}]}
kind: birthday / anniversary / custom
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("utils.important_dates")

_PATH = Path("data/important_dates.json")


def load_dates(character_id: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data.get(character_id, [])
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning("读取重要日期失败: %s", e)
        return []


def save_dates(character_id: str, dates: list[dict[str, Any]]) -> None:
    try:
        data: dict[str, Any] = {}
        if _PATH.exists():
            data = json.loads(_PATH.read_text(encoding="utf-8"))
        clean = [
            {"name": str(d.get("name", ""))[:40], "date": str(d.get("date", ""))[:10], "kind": str(d.get("kind", "custom"))}
            for d in dates
            if d.get("name") and d.get("date")
        ]
        if clean:
            data[character_id] = clean
        else:
            data.pop(character_id, None)
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("保存重要日期失败: %s", e)
        raise


def check_today(character_id: str, today: datetime | None = None) -> list[dict[str, Any]]:
    """今天命中的日期（MM-DD 匹配；YYYY-MM-DD 存储忽略年份部分）。"""
    now = today or datetime.now()
    mmdd = now.strftime("%m-%d")
    hits = []
    for d in load_dates(character_id):
        raw = d.get("date", "")
        if raw[-5:] == mmdd:
            hits.append(d)
    return hits
