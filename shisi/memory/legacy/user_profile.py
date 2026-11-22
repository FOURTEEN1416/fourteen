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

2026-11-22 对抗验收修正（M13/L4）：裸亲属/爱称（老公/姐姐/宝贝等）两条
历史写入路径（旧「叫我X」与「叫你X」）都可能产生，方向逐行不可判，裸词
命中一律按 ambiguous 保守保留 + 待复核，只有显式「叫你X/喊你X」语境才判
ai_address 迁移；判别前套用与写侧一致的 _NAME_STOP/动作补语过滤，动作
整句（叫你吃饭等）不再误判方向。
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
# 「用户对 AI 的称呼」候选词表（2026-11-21 逐行迁移判别用）。2026-11-22
# 修正（M13）：这些**裸词**本身方向不可判——旧「叫我X」写入路径同样会产生
# 该存储值（如「叫我姐姐」→ nickname=姐姐，是用户自述称呼），裸词命中一律
# 按 ambiguous 保守保留 + 待复核；只有显式「叫你X/喊你X」语境才判 ai_address
# 迁移。保守小集合，可按待复核日志扩删。
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


def _clean_address_name(name: str) -> bool:
    """干净称呼名过滤（2026-11-22 L4：与写侧 ``apply_user_utterance`` 同源）。

    动作补语（吃饭/起床/睡觉等）、代词（你/您/我）、「的」字尾、否定/疑问
    词一律不作称呼名。判别侧在写侧精确匹配之上按**子串**复查（捕获名如
    「你宝贝」「写作业」「过来一下」同样拒绝）——判别侧宁可多拒落入
    ambiguous 保守保留，不可把动作句当成称呼。
    """
    token = str(name or "").strip()
    if not token or token in _NAME_STOP:
        return False
    if "的" in token or "我" in token:
        return False
    if any(x in token for x in _NAME_STOP):
        return False
    return 1 <= len(token) <= 4


def _first_clean_name(nick: str, patterns) -> bool:
    """句式命中**且**捕获名通过写侧同款过滤才返回 True。"""
    for pat in patterns:
        m = pat.search(nick)
        if m and _clean_address_name(m.group(1)):
            return True
    return False


def _extract_clean_address(nick: str) -> str:
    """从「叫你X/喊你X」存量短语中取干净称呼名（「叫你老公」→「老公」）。

    迁移搬移时用：避免「叫你」动词残留在 user_calls_ai 槽里。取不出返回空串。
    """
    for pat in _USER_CALLS_AI_PATTERNS:
        m = pat.search(str(nick or ""))
        if m and _clean_address_name(m.group(1)):
            return m.group(1).strip()
    return ""


def _resolve_denied_names(user_key: str) -> list[str]:
    """store 渲染路径的否认名取数（2026-11-22 L5 接线）。

    唯一真源 = ``shisi.agent_plane.runtime.denied_names_for``（双源：窗口
    检测 + ledger 持久否决），与主链 ``get_profile_prompt_block`` 同源；
    懒加载避免 import 环，异常一律降级为空表（渲染不因账本故障被阻断，
    与 ``utils.address_terms._denied_names`` 同一容错口径）。
    """
    try:
        from shisi.agent_plane.runtime import denied_names_for

        return [
            str(x).strip()
            for x in denied_names_for(str(user_key or ""))
            if str(x or "").strip()
        ]
    except Exception as e:  # noqa: BLE001
        logger.debug("store 渲染路径否认名解析失败（按无否认处理）: %s", e)
        return []


def classify_legacy_nickname(nickname: str) -> str:
    """存量 nickname 逐行判别（2026-11-21 用户裁决「逐行进行修正」）。

    返回：
    - ``"self"``：匹配自述模式（我叫X/我是X/叫我X/名字叫X 等）且捕获名干净
      → 用户自述名，保留在 nickname（不清空、不搬移）；
    - ``"ai_address"``：显式「叫你X/喊你X」语境且称呼名干净 → 迁入
      user_calls_ai 并清空 nickname；
    - ``"ambiguous"``：无法判别 → 保守保留原状，记入待复核日志。命中情形：
      裸名；**裸亲属/爱称词表词**（2026-11-22 M13：旧「叫我X」写入路径同样
      会产生该存储值，方向逐行不可判）；「叫你 + 动作补语」整句（2026-11-22
      L4：与写侧过滤对齐）；「我叫你X」双信号冲突句。
    """
    nick = str(nickname or "").strip()
    if not nick:
        return "ambiguous"
    self_hit = any(pat.search(nick) for pat in _NAME_PATTERNS)
    ai_hit = any(pat.search(nick) for pat in _USER_CALLS_AI_PATTERNS)
    if self_hit and ai_hit:
        # 「我叫你宝贝」类：同时命中自述与称呼两条句式，方向信号冲突
        return "ambiguous"
    if self_hit:
        return "self" if _first_clean_name(nick, _NAME_PATTERNS) else "ambiguous"
    if ai_hit:
        return (
            "ai_address"
            if _first_clean_name(nick, _USER_CALLS_AI_PATTERNS)
            else "ambiguous"
        )
    if nick in _AI_ADDRESS_TERMS:
        # 裸亲属/爱称：两条历史写入路径都可能产生，方向不可判（M13）
        return "ambiguous"
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
                # 迁移值为「叫你X」短语时取干净称呼名（「叫你老公」→「老公」），
                # 避免「叫你」动词残留在称呼槽里（2026-11-22 M13 连带修正）。
                addr = _extract_clean_address(nick) or nick
                if not dry_run:
                    conn.execute(
                        "UPDATE user_profile SET user_calls_ai = ?, nickname = '' "
                        "WHERE user_key = ?",
                        (addr, user_key),
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

    def to_prompt_block(
        self,
        user_key: str,
        denied_names: list[str] | set[str] | frozenset[str] | None = None,
    ) -> str:
        """渲染「# 用户画像」注入段；无内容返回空串。

        denied_names（2026-11-22 L5，对齐 ``format_profile_prompt_block``
        的读侧纵深）：被用户否认过的称呼即使仍留在库里，也不得渲染成
        「用户希望被称呼」进入提示词；同时做语义版本筛查——semantics < 2
        且已有 user_calls_ai 时 nickname 视为历史污染值不展示。

        缺省（None）**自动**经 ``runtime.denied_names_for`` 取双源否认名
        （窗口检测 + ledger 持久否决），与主链 ``get_profile_prompt_block``
        同源——生产 store 渲染路径（ase_engine 主动生成 / persona_service
        legacy 回退）不依赖调用方记得传参，杜绝「否认称呼经 store 路径重回
        提示词」。显式传 ``[]`` 表示调用方确认无否认、跳过筛查；取数失败
        按空集降级，不阻塞渲染。
        """
        p = self.get(user_key)
        if not p:
            return ""
        if denied_names is None:
            denied_names = _resolve_denied_names(user_key)
        denied = {str(x).strip() for x in (denied_names or ()) if str(x or "").strip()}
        nick = str(p.get("nickname") or "").strip()
        ai_name = str(p.get("user_calls_ai") or "").strip()
        try:
            semantics = int(p.get("_address_semantics") or p.get("address_semantics") or 0)
        except (TypeError, ValueError):
            semantics = 0
        # 与 runtime.format_profile_prompt_block 同判据：否认名 / 旧语义污染
        # nickname 一律不作为「用户希望被称呼」展示（L5 双渲染器行为一致）。
        nick_visible = bool(
            nick and nick not in denied and not (semantics < 2 and ai_name)
        )
        lines: list[str] = ["# 用户画像（稳定事实，以本段为准；段外信息禁止编造）"]
        # 2026-11-07 P8：用户自述优先于模型推测字段
        if p.get("self_intro"):
            lines.append(f"- 用户自述（本人填写，优先采信）：{p['self_intro']}")
        if ai_name:
            lines.append(f"- 用户对 AI 的称呼：{ai_name}")
        if nick_visible:
            lines.append(f"- 用户希望被称呼：{nick}")
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
