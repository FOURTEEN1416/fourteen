"""结构化用户画像 — 按会话隔离键维护稳定事实。

2026-09-21：生产「一会军训一会上班/生日乱编」的根因是**没有用户画像槽**，
prompt 只有碎片 user_facts + 空的 user_persona(default)，模型只能编造。
本模块维护 `user_profile` 表：生日/职业/称呼/偏好/约定，用户更正即覆盖，
每轮以「# 用户画像」注入，并禁止模型编造画像外信息。

2026-11-21 称呼语义矫正：`nickname` = AI 对用户的称呼（仅用户明确自述）；
`user_calls_ai` = 用户对 AI 的称呼（用户说「叫你 X」）。存量 nickname 按
用户裁决「逐行进行修正」：自述名保留、对 AI 的称呼迁入 user_calls_ai、
无法判别保守保留并记待复核日志；迁移前全表备份，见 `_ensure_schema` 的
幂等迁移 `_migrate_nickname_semantics`。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from utils.local_time import now_utc_naive

logger = logging.getLogger("user_profile")

# 用户更正/陈述的规则识别（高优先，直接写 profile）
_BIRTHDAY_PATTERNS = [
    re.compile(r"(?:我的?|我)生日(?:是|在)?\s*([0-9一二三四五六七八九十年月日号腊初冬]{2,20})"),
    re.compile(r"生日(?:是|在)\s*([0-9一二三四五六七八九十年月日号腊初冬]{2,20})"),
    re.compile(r"(?:不是|不对)[，,]?\s*(?:我的?生日)?(?:是|在)?\s*([0-9一二三四五六七八九十年月日号腊初冬]{2,20})"),
]
_BIRTHDAY_DENY = re.compile(r"(?:我的?|我)生日(?:不是|不对)")
_NAME_PATTERNS = [
    re.compile(r"(?:叫我|我叫|我是)\s*([一-龥A-Za-z]{1,4})\s*[吧啊呀呢~～!！。]*$"),
    # 2026-11-21 迁移判别扩展：我的名字是X / 名字叫X（同为用户自述名）
    re.compile(r"(?:我的?名字|名字)(?:是|叫)\s*([一-龥A-Za-z]{1,4})\s*[吧啊呀呢~～!！。]*$"),
]
# 「用户对 AI 的称呼」：叫你/喊你 X（2026-11-21 与 nickname 方向分开）
_USER_CALLS_AI_PATTERNS = [
    re.compile(r"(?:以后|今后|就)?\s*(?:叫你|喊你)\s*([一-龥A-Za-z]{1,4})\s*[吧啊呀呢~～!！。]*$"),
]
# 「用户对 AI 的称呼」词表（2026-11-21 逐行迁移判别用）：历史 nickname 中
# 出现这些词，实为用户对 AI 的称呼（生产实证老公/姐姐/哥哥等），迁移时搬入
# user_calls_ai 并清空 nickname。保守小集合，可按待复核日志扩删。
_AI_ADDRESS_TERMS = frozenset({
    "老公", "老婆", "姐姐", "哥哥", "弟弟", "妹妹",
    "宝贝", "亲爱的", "老板", "大人", "小哥哥", "小姐姐",
    "欧巴", "欧尼", "爸比", "妈咪", "叔叔", "阿姨",
})
_NAME_STOP = {
    "你", "您", "这个", "那个", "不是", "不是我", "什么", "怎么", "为什么",
    "上班", "学生", "军训", "起不来", "叫你", "叫你叫",
    # 「叫你 X」的常见动作补语，避免被当成 AI 名字
    "起床", "吃饭", "回家", "过来", "出去", "进来", "睡觉", "上课", "放学",
    "作业", "一声", "一下", "名字",
}


def classify_legacy_nickname(nickname: str) -> str:
    """存量 nickname 逐行判别（2026-11-21 用户裁决「逐行进行修正」）。

    返回：
    - ``"self"``：匹配自述模式（我叫X/我是X/叫我X/名字叫X 等）→ 用户自述
      名，保留在 nickname（不清空、不搬移）；
    - ``"ai_address"``：对 AI 的称呼（叫你X/喊你X，或老公/姐姐/哥哥等词表）
      → 迁入 user_calls_ai 并清空 nickname；
    - ``"ambiguous"``：无法判别（裸名等，历史写入两条路径都可能产生）→
      保守保留原状，记入待复核日志。
    """
    nick = str(nickname or "").strip()
    if not nick:
        return "ambiguous"
    for pat in _NAME_PATTERNS:
        if pat.search(nick):
            return "self"
    for pat in _USER_CALLS_AI_PATTERNS:
        if pat.search(nick):
            return "ai_address"
    if nick in _AI_ADDRESS_TERMS:
        return "ai_address"
    return "ambiguous"


class UserProfileStore:
    def __init__(
        self,
        db_path: Path | str,
        *,
        nickname_migration_dry_run: bool = False,
        migration_backup_dir: Path | str | None = None,
    ):
        """``nickname_migration_dry_run``：只判别与写审计、不改库（运维预演）。

        ``migration_backup_dir``：迁移备份目录覆盖项；缺省落在库同目录的
        ``backups/user_profile_nickname_migration/``（生产库在 data/ 下时
        即 ``data/backups/user_profile_nickname_migration/``）。
        """
        self._db_path = Path(db_path)
        self._nickname_migration_dry_run = bool(nickname_migration_dry_run)
        self._migration_backup_dir = (
            Path(migration_backup_dir) if migration_backup_dir else None
        )
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
                    user_calls_ai TEXT DEFAULT '',
                    birthday TEXT DEFAULT '',
                    occupation TEXT DEFAULT '',
                    location TEXT DEFAULT '',
                    preferences TEXT DEFAULT '[]',
                    commitments TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    self_intro TEXT DEFAULT '',
                    address_semantics INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT ''
                )
                """
            )
            # 2026-11-07 P8：存量库补列（CREATE IF NOT EXISTS 不会改已有表）。
            cols = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(user_profile)").fetchall()
            }
            if "self_intro" not in cols:
                conn.execute("ALTER TABLE user_profile ADD COLUMN self_intro TEXT DEFAULT ''")
            # 2026-11-21 称呼语义矫正：新增 user_calls_ai（用户对 AI 的称呼）
            # 与 address_semantics（语义版本，2=已升级）。
            if "user_calls_ai" not in cols:
                conn.execute("ALTER TABLE user_profile ADD COLUMN user_calls_ai TEXT DEFAULT ''")
            if "address_semantics" not in cols:
                conn.execute(
                    "ALTER TABLE user_profile ADD COLUMN address_semantics INTEGER DEFAULT 0"
                )
            # 兼容迁移（幂等）：2026-11-21 用户裁决「逐行进行修正」——
            # 存量 nickname 不能再无条件搬进 user_calls_ai（:34 旧
            # _NAME_PATTERNS 证明历史写入可能是用户真实自述名）。逐行判别 +
            # 迁移前全表备份 + 审计日志，见 `_migrate_nickname_semantics`。
            self._migrate_nickname_semantics(conn)

    def _backup_dir(self) -> Path:
        """迁移备份目录：覆盖项优先，否则库同目录 backups/（生产即 data/backups/）。"""
        if self._migration_backup_dir is not None:
            return Path(self._migration_backup_dir)
        return self._db_path.parent / "backups" / "user_profile_nickname_migration"

    def _backup_user_profile_table(self, conn) -> Path:
        """迁移前把 user_profile **全表**导出为 JSONL（不可逆操作的事后凭据）。"""
        backup_dir = self._backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = now_utc_naive().strftime("%Y%m%d_%H%M%S_%f")
        path = backup_dir / f"{ts}.jsonl"
        cols = [
            str(r[1]) for r in conn.execute("PRAGMA table_info(user_profile)").fetchall()
        ]
        with path.open("w", encoding="utf-8") as fh:
            for row in conn.execute("SELECT * FROM user_profile").fetchall():
                fh.write(
                    json.dumps(
                        dict(zip(cols, row, strict=True)),
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        return path

    def _migrate_nickname_semantics(self, conn) -> None:
        """存量 nickname 语义矫正（幂等，``address_semantics=2`` 为已处理标记）。

        逐行判别（``classify_legacy_nickname``）：
        - 自述名 → 保留 nickname；
        - 对 AI 的称呼 → 迁入 user_calls_ai 并清空 nickname（user_calls_ai
          已有值时保守不覆盖，转待复核）；
        - 无法判别 → 保留原状，记入待复核日志。
        迁移前全表备份；每次执行写一行审计日志（logging + 备份目录内
        ``migration_audit.log``）；``dry_run`` 只判别不落库。
        """
        rows = conn.execute(
            "SELECT user_key, nickname, user_calls_ai FROM user_profile "
            "WHERE COALESCE(address_semantics, 0) < 2"
        ).fetchall()
        if not rows:
            return
        backup_path = self._backup_user_profile_table(conn)
        dry_run = self._nickname_migration_dry_run
        migrated: list[str] = []
        kept_self: list[str] = []
        review: list[str] = []
        for user_key, nickname, user_calls_ai in rows:
            nick = str(nickname or "").strip()
            if not nick:
                continue  # 空 nickname 无需搬移，仅统一打版本标记
            kind = classify_legacy_nickname(nick)
            if kind == "self":
                kept_self.append(f"{user_key}:{nick}")
                continue
            if kind == "ai_address":
                if str(user_calls_ai or "").strip():
                    review.append(f"{user_key}:{nick}(user_calls_ai 已有值)")
                    continue
                if not dry_run:
                    conn.execute(
                        "UPDATE user_profile SET user_calls_ai = ?, nickname = '' "
                        "WHERE user_key = ?",
                        (nick, user_key),
                    )
                migrated.append(f"{user_key}:{nick}")
                continue
            review.append(f"{user_key}:{nick}")
        if not dry_run:
            # 全部待扫行统一打版本标记（含空 nickname 行），保证幂等。
            conn.execute(
                "UPDATE user_profile SET address_semantics = 2 "
                "WHERE COALESCE(address_semantics, 0) < 2"
            )
        summary = (
            f"dry_run={dry_run} scanned={len(rows)} migrated={len(migrated)} "
            f"kept_self={len(kept_self)} review={len(review)} backup={backup_path}"
        )
        logger.info("[profile_migration] nickname 语义矫正 %s", summary)
        if review:
            logger.warning(
                "[profile_migration] 待复核（无法判别，保留原状）：%s",
                "; ".join(review),
            )
        try:
            audit_path = self._backup_dir() / "migration_audit.log"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"{now_utc_naive().isoformat(timespec='seconds')} {summary} "
                    f"migrated={migrated} kept_self={kept_self} review={review}\n"
                )
        except OSError as exc:  # 审计失败不阻断迁移
            logger.warning("[profile_migration] 审计日志写入失败：%s", exc)

    @staticmethod
    def _now() -> str:
        """UTC naive ISO（时间口径统一批：原 ``datetime.now()`` 写本地墙钟）。"""
        return now_utc_naive().isoformat(timespec="seconds")

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
            "user_calls_ai": _pick("user_calls_ai"),
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
            "self_intro": _pick("self_intro"),
        }
        # 新写入的 nickname 属于升级后的语义（用户明确自述）；旧代码路径
        # 未显式给版本时，只要本行已有版本标记就沿用，否则按 0 处理。
        try:
            address_semantics = int(
                fields.get("address_semantics", cur.get("address_semantics") or 0) or 0
            )
        except (TypeError, ValueError):
            address_semantics = 0
        if ("nickname" in fields or "user_calls_ai" in fields) and "address_semantics" not in fields:
            address_semantics = max(address_semantics, 2)
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
                (user_key, nickname, user_calls_ai, birthday, occupation, location,
                 preferences, commitments, notes, self_intro, address_semantics, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_key) DO UPDATE SET
                  nickname=excluded.nickname,
                  user_calls_ai=excluded.user_calls_ai,
                  birthday=excluded.birthday,
                  occupation=excluded.occupation,
                  location=excluded.location,
                  preferences=excluded.preferences,
                  commitments=excluded.commitments,
                  notes=excluded.notes,
                  self_intro=excluded.self_intro,
                  address_semantics=excluded.address_semantics,
                  updated_at=excluded.updated_at
                """,
                (
                    uk,
                    merged["nickname"],
                    merged["user_calls_ai"],
                    merged["birthday"],
                    merged["occupation"],
                    merged["location"],
                    json.dumps(prefs, ensure_ascii=False),
                    json.dumps(commits, ensure_ascii=False),
                    merged["notes"],
                    merged["self_intro"],
                    address_semantics,
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

        # 用户对 AI 的称呼：叫你 X / 喊你 X → user_calls_ai（绝不写 nickname）
        for pat in _USER_CALLS_AI_PATTERNS:
            m = pat.search(s)
            if not m:
                continue
            name = m.group(1).strip()
            if name in _NAME_STOP or any(x in name for x in ("不是", "什么", "怎么", "我")):
                continue
            if 1 <= len(name) <= 4:
                updates["user_calls_ai"] = name
                break

        # 用户自称：仅接受极短干净昵称，避免「叫我不是我起不来」这类整句被当成名字
        for pat in _NAME_PATTERNS:
            m = pat.search(s)
            if not m:
                continue
            name = m.group(1).strip()
            if (
                name in _NAME_STOP
                or "的" in name
                or any(x in name for x in ("不是", "什么", "怎么", "我"))
            ):
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
        # 2026-11-07 P8：用户自述优先于模型推测字段
        if p.get("self_intro"):
            lines.append(f"- 用户自述（本人填写，优先采信）：{p['self_intro']}")
        if p.get("user_calls_ai"):
            lines.append(f"- 用户对 AI 的称呼：{p['user_calls_ai']}")
        # 只有语义升级后的 nickname 才能作为「用户希望被称呼」展示；旧库
        # 未升级行已在 _ensure_schema 迁移为 user_calls_ai + 清空 nickname。
        if p.get("nickname"):
            lines.append(f"- 用户希望被称呼：{p['nickname']}")
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
        from utils.project_paths import PROJECT_ROOT

        db_path = PROJECT_ROOT / "data" / "sqlite.db"
    return UserProfileStore(db_path)
