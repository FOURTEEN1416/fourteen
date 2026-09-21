"""结构化用户画像 — 按会话隔离键维护稳定事实。

2026-09-21：生产「一会军训一会上班/生日乱编」的根因是**没有用户画像槽**，
prompt 只有碎片 user_facts + 空的 user_persona(default)，模型只能编造。
本模块维护 `user_profile` 表：生日/职业/称呼/偏好/约定，用户更正即覆盖，
每轮以「# 用户画像」注入，并禁止模型编造画像外信息。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("user_profile")

# 用户更正/陈述的规则识别（高优先，直接写 profile）
_BIRTHDAY_PATTERNS = [
    re.compile(r"(?:我的?|我)生日(?:是|在)?\s*([0-9一二三四五六七八九十年月日号腊初冬]{2,20})"),
    re.compile(r"生日(?:是|在)\s*([0-9一二三四五六七八九十年月日号腊初冬]{2,20})"),
    re.compile(r"(?:不是|不对)[，,]?\s*(?:我的?生日)?(?:是|在)?\s*([0-9一二三四五六七八九十年月日号腊初冬]{2,20})"),
]
_BIRTHDAY_DENY = re.compile(r"(?:我的?|我)生日(?:不是|不对)")
_OCC_PATTERNS = [
    re.compile(r"(?:我(?:现在)?)(?:在)?(?:上班|工作|打工)"),
    re.compile(r"(?:我(?:现在)?)(?:在)?(?:上学|读书|学校|军训|上学中)"),
    re.compile(r"我是(?:上班族|学生|老师|程序员|护士|医生)"),
]
_NAME_PATTERNS = [
    re.compile(r"(?:叫我|我叫|我是)\s*([一-龥A-Za-z]{1,4})\s*[吧啊呀呢~～!！。]*$"),
]
_NAME_STOP = {
    "你", "您", "这个", "那个", "不是", "不是我", "什么", "怎么", "为什么",
    "上班", "学生", "军训", "起不来", "叫你", "叫你叫",
}


class UserProfileStore:
    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._ensure_schema()

    def _conn(self):
        return sqlite3.connect(str(self._db_path))

    def _ensure_schema(self) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profile (
                    user_key TEXT PRIMARY KEY,
                    nickname TEXT DEFAULT '',
                    birthday TEXT DEFAULT '',
                    occupation TEXT DEFAULT '',
                    location TEXT DEFAULT '',
                    preferences TEXT DEFAULT '[]',
                    commitments TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def get(self, user_key: str) -> dict[str, Any]:
        uk = str(user_key or "").strip()
        if not uk:
            return {}
        with closing(self._conn()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM user_profile WHERE user_key = ?", (uk,)
            ).fetchone()
        if not row:
            return {}
        d = dict(row)
        for k in ("preferences", "commitments"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except Exception:  # noqa: BLE001
                d[k] = []
        return d

    def upsert(self, user_key: str, **fields: Any) -> dict[str, Any]:
        uk = str(user_key or "").strip()
        if not uk:
            return {}
        cur = self.get(uk)

        def _pick(key: str) -> str:
            if key in fields:
                return str(fields.get(key) or "")
            return str(cur.get(key) or "")

        merged = {
            "nickname": _pick("nickname"),
            "birthday": _pick("birthday"),
            "occupation": _pick("occupation"),
            "location": _pick("location"),
            "preferences": fields["preferences"]
            if "preferences" in fields
            else (cur.get("preferences") or []),
            "commitments": fields["commitments"]
            if "commitments" in fields
            else (cur.get("commitments") or []),
            "notes": _pick("notes"),
        }
        if fields.get("clear_birthday"):
            merged["birthday"] = ""
        elif "birthday" in fields and fields.get("birthday"):
            merged["birthday"] = str(fields["birthday"]).strip()
        prefs = merged["preferences"] if isinstance(merged["preferences"], list) else []
        commits = merged["commitments"] if isinstance(merged["commitments"], list) else []
        with closing(self._conn()) as conn, conn:
            conn.execute(
                """
                INSERT INTO user_profile
                (user_key, nickname, birthday, occupation, location,
                 preferences, commitments, notes, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_key) DO UPDATE SET
                  nickname=excluded.nickname,
                  birthday=excluded.birthday,
                  occupation=excluded.occupation,
                  location=excluded.location,
                  preferences=excluded.preferences,
                  commitments=excluded.commitments,
                  notes=excluded.notes,
                  updated_at=excluded.updated_at
                """,
                (
                    uk,
                    merged["nickname"],
                    merged["birthday"],
                    merged["occupation"],
                    merged["location"],
                    json.dumps(prefs, ensure_ascii=False),
                    json.dumps(commits, ensure_ascii=False),
                    merged["notes"],
                    self._now(),
                ),
            )
        return self.get(uk)

    def apply_user_utterance(self, user_key: str, text: str) -> dict[str, Any]:
        """【已废弃·禁止聊天热路径调用】关键字/正则画像提取。

        2026-09-21 用户裁决：关键字外信息全漏，硬编码不可接受。
        生产路径唯一：LLM 工具 `update_user_profile` / `remember_facts`
        + 对话后 `profile_sync_agent`（tools/builtin/profile_agent_tools.py）。

        本方法仅保留给：单测钉行为、运维脚本、一次性数据修复。
        orchestrator **不得**再调用本方法。
        """
        import warnings

        warnings.warn(
            "apply_user_utterance 已废弃；聊天路径请使用 profile_agent LLM 工具",
            DeprecationWarning,
            stacklevel=2,
        )
        uk = str(user_key or "").strip()
        s = str(text or "").strip()
        if not uk or not s:
            return self.get(uk)
        updates: dict[str, Any] = {}

        # 生日更正：整句是否定时优先清空，且不再从「不是X」里抽出 X
        deny_birthday = bool(_BIRTHDAY_DENY.search(s))
        if deny_birthday:
            updates["clear_birthday"] = True
            m = re.search(r"我的?生日不是\s*([^\s，。！？,!?]+)", s)
            if m:
                cur = self.get(uk)
                note = (cur.get("notes") or "").strip()
                bad = m.group(1).strip()
                if bad and bad not in note:
                    updates["notes"] = (note + f"；用户否认生日={bad}").strip("；")
        else:
            for pat in _BIRTHDAY_PATTERNS:
                m = pat.search(s)
                if not m:
                    continue
                b = m.group(1).strip()
                matched = m.group(0)
                if "不是" in matched or "不对" in matched:
                    continue
                if b:
                    updates["birthday"] = b
                    break

        # 职业/状态
        if re.search(r"军训", s) and re.search(r"我|明天|今天|要", s):
            updates["occupation"] = "军训/在校"
        elif re.search(r"(?:我)(?:在)?上班|我是上班族", s):
            updates["occupation"] = "上班"
        elif re.search(r"(?:我)(?:在)?(?:上学|读书|学生)", s):
            updates["occupation"] = "上学"

        # 称呼：仅接受极短干净昵称，避免「叫我不是我起不来」这类整句被当成名字
        for pat in _NAME_PATTERNS:
            m = pat.search(s)
            if not m:
                continue
            name = m.group(1).strip()
            if name in _NAME_STOP or any(x in name for x in ("不是", "什么", "怎么", "我")):
                continue
            if 1 <= len(name) <= 4:
                updates["nickname"] = name
                break

        # 约定类（起床提醒等）并入 commitments
        if re.search(r"叫我|提醒我|记得.*叫我|到点", s) and re.search(
            r"\d{1,2}[:：点]\d{0,2}|早上|明早|今晚|明天", s
        ):
            cur = self.get(uk)
            commits = list(cur.get("commitments") or [])
            line = s[:80]
            if line not in commits:
                commits.append(line)
                updates["commitments"] = commits[-8:]

        if not updates:
            return self.get(uk)
        return self.upsert(uk, **updates)

    def to_prompt_block(self, user_key: str) -> str:
        """渲染「# 用户画像」注入段；无内容返回空串。"""
        p = self.get(user_key)
        if not p:
            return ""
        lines: list[str] = ["# 用户画像（稳定事实，以本段为准；段外信息禁止编造）"]
        if p.get("nickname"):
            lines.append(f"- 称呼：{p['nickname']}")
        if p.get("birthday"):
            lines.append(f"- 生日：{p['birthday']}")
        if p.get("occupation"):
            lines.append(f"- 身份/近况：{p['occupation']}")
        if p.get("location"):
            lines.append(f"- 常驻地：{p['location']}")
        prefs = p.get("preferences") or []
        if prefs:
            lines.append("- 偏好：" + "；".join(str(x) for x in prefs[:6]))
        commits = p.get("commitments") or []
        if commits:
            lines.append("- 你们的约定：")
            for c in commits[-5:]:
                lines.append(f"  · {c}")
        if p.get("notes"):
            lines.append(f"- 备注：{p['notes']}")
        if len(lines) == 1:
            return ""
        lines.append(
            "- 硬约束：画像未写的信息**不得编造**；用户更正时以用户刚说的为准，并当作唯一真相。"
        )
        return "\n".join(lines)


def default_store(db_path: Path | str | None = None) -> UserProfileStore:
    if db_path is None:
        db_path = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"
    return UserProfileStore(db_path)
