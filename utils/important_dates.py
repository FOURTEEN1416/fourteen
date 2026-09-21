"""重要日期存储（候选 D，2026-08-28）。

data/important_dates.json: {character_id: [{name, date("MM-DD"或"YYYY-MM-DD"), kind}]}
kind: birthday / anniversary / custom
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from utils import json_state
from utils.local_time import now_local
from utils.project_paths import project_path

logger = logging.getLogger("utils.important_dates")

_PATH = project_path("data", "important_dates.json")


def load_dates(character_id: str) -> list[dict[str, Any]]:
    data = json_state.read_json(_PATH, default={})
    return data.get(character_id, [])


def save_dates(character_id: str, dates: list[dict[str, Any]]) -> None:
    clean = [
        {"name": str(d.get("name", ""))[:40], "date": str(d.get("date", ""))[:10], "kind": str(d.get("kind", "custom"))}
        for d in dates
        if d.get("name") and d.get("date")
    ]

    def _mutate(data: dict) -> None:
        if clean:
            data[character_id] = clean
        else:
            data.pop(character_id, None)

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:
        logger.warning("保存重要日期失败: %s", e)
        raise


def check_today(character_id: str, today: datetime | None = None) -> list[dict[str, Any]]:
    """今天命中的日期（MM-DD 匹配；YYYY-MM-DD 存储忽略年份部分）。

    墙钟一律走 `now_local`：原用裸 `datetime.now()` 在主机时区非北京时
    会静默错位（AGENTS v1.19 观察项，本批收口）。
    """
    now = today or now_local()
    mmdd = now.strftime("%m-%d")
    hits = []
    for d in load_dates(character_id):
        raw = d.get("date", "")
        if raw[-5:] == mmdd:
            hits.append(d)
    return hits
