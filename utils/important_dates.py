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


# ── 用户画像生日 → MM-DD 提示解析（2026-09-22 多用户生日祝福）────────
#
# 画像 birthday 是**用户原话表述**（"11月14" / "3月3日" / "2001-11-14"），
# 公历可解析形态直接映射；农历表述（腊月/正月初一等）需要农历换算，
# 经 `parse_lunar_birthday_hint` + `lunar_birthday_matches` 走农历通道
# （2026-09-22 用户裁决「引入农历库」——复用既有依赖 `lunarcalendar`，
#  time_awareness_tool 同源，零新增依赖）。宁缺毋错原则不变：
# 无显式农历标记的文本不走农历解释（"八月十五"仍按公历 08-15）。

_LUNAR_MARKERS = ("腊", "冬月", "正月", "初", "廿", "闰")
_LUNAR_MONTH_CN = {"正": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                   "七": 7, "八": 8, "九": 9, "十": 10, "冬": 11, "腊": 12}
_CN_DAY_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 公历中文数字月份（parse_birthday_hint 路径 5 用；与农历表分离——正/冬/腊只属农历）
_CN_MONTH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def _parse_lunar_day_cn(text: str) -> int | None:
    """农历日辰中文形态 → 整数：初X(1-10)/十X(11-19)/X十(10/20/30)/廿X(21-29)。"""
    t = str(text or "").strip()
    if not t:
        return None
    if t.startswith("初"):
        d = _CN_DAY_DIGIT.get(t[1:])
        return d if d and 1 <= d <= 10 else None
    if "廿" in t:
        tail = t.replace("廿", "")
        if not tail:
            return 20
        d = _CN_DAY_DIGIT.get(tail)
        return 20 + d if d else None
    if "十" in t:
        head, _, tail = t.partition("十")
        h = _CN_DAY_DIGIT.get(head) if head else 1  # "十五" 无头 = 10+5
        if h is None:
            return None
        tt = _CN_DAY_DIGIT.get(tail) if tail else 0
        return h * 10 + tt
    return _CN_DAY_DIGIT.get(t)


def parse_lunar_birthday_hint(text: str) -> tuple[int, int, bool] | None:
    """农历生日文本 → ``(月, 日, 是否闰月)``；无显式农历标记/解析不出返回 None。

    触发标记（宁缺毋错——只认显式农历用词）：腊月/冬月/正月/初X/廿X/闰X。
    中文数字整句但无标记（"八月十五"）**不**走农历，仍按公历解释。
    """
    import re

    s = str(text or "").strip()
    if not s or not any(m in s for m in _LUNAR_MARKERS):
        return None
    leap = "闰" in s
    if leap:
        s = s.replace("闰", "")
    m = re.search(r"([正一二三四五六七八九十冬腊])月", s)
    if not m:
        return None
    month = _LUNAR_MONTH_CN.get(m.group(1))
    if not month:
        return None
    rest = s[m.end():].strip()
    d = re.match(r"([初一二三四五六七八九十廿]{1,3})", rest)
    if not d:
        return None
    day = _parse_lunar_day_cn(d.group(1))
    if not day or not 1 <= day <= 30:
        return None
    return (month, day, leap)


def lunar_birthday_matches(text: str, today: datetime) -> bool:
    """农历生日是否命中 `today`（公历墙钟）。

    经 lunarcalendar 公→农换算当日农历月日比对；闰月生日**只在真闰月**
    命中（普通X月生日撞上闰X月不补过——宁缺毋错）。lunarcalendar 缺失
    时返回 False（优雅降级：公历通道不受影响）。
    """
    parsed = parse_lunar_birthday_hint(text)
    if not parsed:
        return False
    lm, ld, leap = parsed
    try:
        from lunarcalendar import Converter, Solar

        lunar = Converter.Solar2Lunar(Solar(today.year, today.month, today.day))
    except Exception as e:  # noqa: BLE001
        logger.warning("农历换算不可用，跳过农历生日匹配: %s", e)
        return False
    if bool(lunar.isleap) != leap:
        return False
    return int(lunar.month) == lm and int(lunar.day) == ld


def parse_birthday_hint(text: str) -> str | None:
    """画像生日文本 → ``MM-DD``（公历）；解析不出/农历表述返回 ``None``。

    支持：``M月D日/号``、``M-D`` / ``M.D`` / ``M/D``、``YYYY-MM-DD``、
    中文月份数字（``三月十四``）。拒绝：农历（腊/正月初一等）、无法定位
    月日的任意文本。匹配由调用方按本地墙钟做（与 :func:`check_today` 同口径）。
    """
    import re

    s = str(text or "").strip()
    if not s:
        return None
    if any(m in s for m in _LUNAR_MARKERS):
        return None

    def _valid(month: int, day: int) -> str | None:
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}-{day:02d}"
        return None

    # 1) ISO：YYYY-MM-DD / YYYY/M/D
    m = re.fullmatch(r"\d{4}[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)))
    # 2) M月D日 / M月D号（阿拉伯数字）
    m = re.fullmatch(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", s)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)))
    # 3) M-D / M.D / M/D
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)))
    # 4) 纯日数字带月（"11月14"已被 2 覆盖；这里兜 "14号" 缺月 → 放弃）
    # 5) 中文月份：三月十四 / 十一月十四
    m = re.fullmatch(r"([一二三四五六七八九十]{1,2})月([一二三四五六七八九十]{1,3})日?", s)
    if m:
        month = _CN_MONTH.get(m.group(1))
        day_text = m.group(2)
        day: int | None = None
        if day_text.isdigit():
            day = int(day_text)
        elif day_text == "十":
            day = 10
        elif day_text.startswith("十"):
            tail = _CN_MONTH.get(day_text[1:])
            day = 10 + tail if tail else None
        elif "十" in day_text:
            head, _, tail = day_text.partition("十")
            h, t = _CN_MONTH.get(head), _CN_MONTH.get(tail) if tail else 0
            day = h * 10 + (t or 0) if h else None
        else:
            day = _CN_MONTH.get(day_text)
        if month and day:
            return _valid(month, day)
    return None
