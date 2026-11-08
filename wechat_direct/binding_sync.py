"""`wechat_bindings` 写侧唯一 owner（D-D 2026-11-08 用户裁决）。

**为什么存在**：`my-settings` 等 15 处调用点共用
`api/routers/training_routes.py::_user_session_key_for`，后者只用
`wechat_bindings` 还原会话键 `N:wxid`。而该表历史上只有
`POST /api/characters/{id}/activate`（`api/routers/character_routes.py`）与遗留
`POST /api/wechat/bind` 会写；**扫码登录**（D16「谁扫码谁聊」的唯一正规入口，
`wechat_direct/wechat_connector.py::WeChatConnector.login`）不写。于是
「先网页激活角色 → 后扫码登录」的用户永远没有绑定行，`GET
/api/characters/{id}/my-settings` 恒 409「尚未绑定微信」，与 /wechat 页
「通道已连接」自相矛盾。

**本模块承担两件事（同一份规则，不得在别处复制）**：
1. `sync_login_binding()` —— 扫码登录确认后的幂等补写（登录链调用）；
2. `plan_bindings()` / `apply_plan()` —— 存量补登脚本
   `scripts/backfill_wechat_bindings.py` 的推导与写入（默认 dry-run）。

**为什么是同步 sqlite3 而不是异步 session**：调用方是连接器登录线程（无事件
循环），与 `UserManager.refresh_bindings_from_db` 同一口径——见 `user_scheduler.py`
里「谎言家族 asyncio 桥」的注释：在那里 `asyncio.run` /
`run_coroutine_threadsafe().result()` 都是死法。补写只落一行、持锁时间极短。

**幂等**：按 `wxid`（唯一键）upsert；同 user 同角色 → `noop` 不写。
**失败不阻断登录**：任何异常降级为 WARNING + 返回 `"error"`，由补登脚本与日志
兜住可观测性（登录是用户主路径，不能因补写失败而失败）。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("wechat_direct.binding_sync")

DEFAULT_CHARACTER_ID = "default"

# 动作取值（日志 / 脚本输出 / 测试断言共用一个词表）
ACTION_INSERT = "insert"
ACTION_UPDATE = "update"
ACTION_NOOP = "noop"
ACTION_CONFLICT = "conflict"
ACTION_SKIP = "skip"
ACTION_ERROR = "error"


def users_db_path() -> Path | None:
    """users.db 文件路径；非 SQLite 部署返回 None（不猜路径）。

    单一真源在 `UserManager._resolve_users_db_path()`（同一函数也被
    `refresh_bindings_from_db` 使用）：延迟导入避免连接器模块级耦合。
    """
    try:
        from user_scheduler import UserManager

        return UserManager._resolve_users_db_path()
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 users.db 路径失败：%s", exc)
        return None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读连接（`mode=ro`）——dry-run 阶段连打开都不带写意图。

    与 `UserManager._read_binding_rows` 同一口径（那处同样是 `mode=ro`）：
    读侧不抢写锁、不会因打开而在主库留下任何变更。写路径一律走 `_connect`。
    """
    conn = sqlite3.connect(
        f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=5.0
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _utc_stamp() -> str:
    """与 SQLAlchemy `DateTime` + SQLite 的存储格式一致（naive UTC，微秒）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _normalize_character_id(character_id: str | None) -> str:
    return str(character_id or "").strip() or DEFAULT_CHARACTER_ID


def active_character_id(conn: sqlite3.Connection, user_id: int) -> str:
    """该用户当前激活角色卡 id；无激活行/表缺失 → `default`（D2 个人激活）。"""
    try:
        row = conn.execute(
            "SELECT character_id FROM user_active_characters WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("读取用户激活角色失败 user=%s: %s", user_id, exc)
        return DEFAULT_CHARACTER_ID
    return _normalize_character_id(row["character_id"] if row else "")


def upsert_binding(
    conn: sqlite3.Connection,
    user_id: int,
    wxid: str,
    character_card_id: str,
) -> str:
    """按 `wxid` 幂等 upsert 一行绑定；返回动作词（见 `ACTION_*`）。

    - 新行 → `insert`（`bound_at` 写当前 UTC，与 ORM 默认同格式）；
    - 已存在且 user/角色都相同 → `noop`（不白写、不动 `bound_at`）；
    - 已存在但归属或角色不同 → `update`（重新扫码换绑 / 网页换角色后重新登录）；
    - 空 wxid / 非法 user → `skip`（不猜键）。
    """
    uid = int(user_id or 0)
    peer = str(wxid or "").strip()
    cid = _normalize_character_id(character_card_id)
    if uid <= 0 or not peer:
        return ACTION_SKIP

    row = conn.execute(
        "SELECT id, user_id, character_card_id FROM wechat_bindings WHERE wxid = ?",
        (peer,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO wechat_bindings "
            "(user_id, wxid, nickname, avatar, character_card_id, bound_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, peer, "", "", cid, _utc_stamp()),
        )
        return ACTION_INSERT

    prev_uid = int(row["user_id"] or 0)
    prev_cid = _normalize_character_id(row["character_card_id"])
    if prev_uid == uid and prev_cid == cid:
        return ACTION_NOOP
    conn.execute(
        "UPDATE wechat_bindings SET user_id = ?, character_card_id = ? WHERE wxid = ?",
        (uid, cid, peer),
    )
    return ACTION_UPDATE


def sync_login_binding(owner_user_id: int | None, scanner_wxid: str) -> str:
    """扫码登录确认后的补写入口（幂等；异常降级为告警，绝不阻断登录）。

    `owner_user_id` = 通道 owner 注册账号（`WeChatConnector.owner_user_id`），
    `scanner_wxid` = 登录确认返回的 `ilink_user_id`（D16：扫码人即该账号在微信侧
    的唯一化身）。角色取该用户 `user_active_characters` 现值，无激活 → `default`。
    """
    uid = int(owner_user_id or 0)
    peer = str(scanner_wxid or "").strip()
    if uid <= 0 or not peer:
        logger.warning(
            "扫码登录补写绑定跳过：owner=%r scanner_wxid 为空或非法", owner_user_id
        )
        return ACTION_SKIP

    db_path = users_db_path()
    if db_path is None:
        logger.warning("扫码登录补写绑定跳过：非 SQLite 部署 user=%s", uid)
        return ACTION_SKIP
    if not Path(db_path).exists():
        logger.warning("扫码登录补写绑定跳过：users.db 不存在 user=%s", uid)
        return ACTION_SKIP

    try:
        with closing(_connect(Path(db_path))) as conn:
            cid = active_character_id(conn, uid)
            action = upsert_binding(conn, uid, peer, cid)
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning(
            "扫码登录补写绑定失败（不影响登录）user=%s: %s", uid, exc
        )
        return ACTION_ERROR

    logger.info(
        "扫码登录补写绑定 action=%s user=%s char=%s", action, uid, cid
    )
    return action


@dataclass(frozen=True)
class BindingPlan:
    """补登计划的一行（纯数据，供脚本打印与写入）。"""

    user_id: int
    wxid: str
    character_card_id: str
    action: str
    previous_user_id: int | None = None
    previous_character_card_id: str = ""

    @property
    def writable(self) -> bool:
        return self.action in (ACTION_INSERT, ACTION_UPDATE)


def plan_bindings(
    scanner_ids_by_user: dict[int, list[str]],
    active_character_by_user: dict[int, str],
    existing_by_wxid: dict[str, tuple[int, str]],
) -> list[BindingPlan]:
    """纯函数：从「通道扫码人 + 激活角色 + 现有绑定」推导应补写的行。

    参数全部为值（不碰 IO，方便测试与 dry-run）：

    - `scanner_ids_by_user`：`{注册用户 id: [扫码人 wxid, …]}`（多 slot 可能重复）；
    - `active_character_by_user`：`{注册用户 id: 激活角色 id}`（缺键 = 无激活）；
    - `existing_by_wxid`：`{wxid: (user_id, character_card_id)}`（当前绑定表快照）。

    规则（D-D）：
    1. 每人只写自己的扫码人 wxid；同一用户的重复 wxid 去重（D16 一人一 bot）。
    2. 角色 = 该用户激活角色，无激活 → `default`。
    3. 已存在同 wxid：user 与角色都相同 → `noop`；否则 `update`。
    4. 两个用户声称同一 wxid（数据异常，正常扫描不可能）：按 user_id 升序先到者
       写，后者记 `conflict`（不写、不猜归属）。
    """
    plans: list[BindingPlan] = []
    claimed: dict[str, int] = {}

    # 先把入参口径钉死（int 键、去空、保序），避免 str/int 键混用导致某个用户
    # 的扫码人被静默丢掉（丢 = 该用户拿不到绑定，正是本单要修的病）。
    normalized: dict[int, list[str]] = {}
    for raw_uid, raw_ids in scanner_ids_by_user.items():
        uid = int(raw_uid)
        bucket = normalized.setdefault(uid, [])
        for raw in raw_ids or []:
            peer = str(raw or "").strip()
            if peer and peer not in bucket:
                bucket.append(peer)

    for uid in sorted(normalized):
        wxids = normalized[uid]
        cid = _normalize_character_id(active_character_by_user.get(uid, ""))

        for peer in wxids:
            owner = claimed.get(peer)
            if owner is not None and owner != uid:
                plans.append(
                    BindingPlan(
                        user_id=uid,
                        wxid=peer,
                        character_card_id=cid,
                        action=ACTION_CONFLICT,
                    )
                )
                continue
            claimed[peer] = uid

            existing = existing_by_wxid.get(peer)
            if existing is None:
                plans.append(
                    BindingPlan(
                        user_id=uid,
                        wxid=peer,
                        character_card_id=cid,
                        action=ACTION_INSERT,
                    )
                )
                continue
            prev_uid, prev_cid = int(existing[0]), _normalize_character_id(existing[1])
            if prev_uid == uid and prev_cid == cid:
                plans.append(
                    BindingPlan(
                        user_id=uid,
                        wxid=peer,
                        character_card_id=cid,
                        action=ACTION_NOOP,
                        previous_user_id=prev_uid,
                        previous_character_card_id=prev_cid,
                    )
                )
            else:
                plans.append(
                    BindingPlan(
                        user_id=uid,
                        wxid=peer,
                        character_card_id=cid,
                        action=ACTION_UPDATE,
                        previous_user_id=prev_uid,
                        previous_character_card_id=prev_cid,
                    )
                )
    return plans


def read_existing_bindings(db_path: Path) -> dict[str, tuple[int, str]]:
    """现有绑定快照 `{wxid: (user_id, character_card_id)}`（表缺失 → `{}`）。"""
    path = Path(db_path)
    if not path.exists():
        return {}
    rows: dict[str, tuple[int, str]] = {}
    with closing(_connect_ro(path)) as conn:
        try:
            for row in conn.execute(
                "SELECT wxid, user_id, "
                "COALESCE(character_card_id, 'default') AS character_card_id "
                "FROM wechat_bindings WHERE wxid IS NOT NULL AND wxid != ''"
            ):
                peer = str(row["wxid"] or "").strip()
                if peer:
                    rows[peer] = (
                        int(row["user_id"] or 0),
                        _normalize_character_id(row["character_card_id"]),
                    )
        except sqlite3.Error as exc:
            logger.warning("读取现有绑定失败（按空处理）: %s", exc)
    return rows


def read_active_characters(db_path: Path) -> dict[int, str]:
    """`{user_id: 激活角色 id}`（表缺失 → `{}`，缺失的用户在计划里落 `default`）。"""
    path = Path(db_path)
    if not path.exists():
        return {}
    rows: dict[int, str] = {}
    with closing(_connect_ro(path)) as conn:
        try:
            for row in conn.execute(
                "SELECT user_id, character_id FROM user_active_characters"
            ):
                rows[int(row["user_id"] or 0)] = _normalize_character_id(
                    row["character_id"]
                )
        except sqlite3.Error as exc:
            logger.warning("读取用户激活角色失败（按空处理）: %s", exc)
    return rows


def apply_plan(db_path: Path, plans: list[BindingPlan]) -> dict[str, int]:
    """写入可执行计划行（只写 insert/update，conflict/noop 跳过）；返回动作计数。"""
    counts: dict[str, int] = {}
    writable = [p for p in plans if p.writable]
    if not writable:
        return counts
    with closing(_connect(Path(db_path))) as conn:
        for plan in writable:
            action = upsert_binding(conn, plan.user_id, plan.wxid, plan.character_card_id)
            counts[action] = counts.get(action, 0) + 1
        conn.commit()
    return counts
