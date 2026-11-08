"""把三张旧 per-user 覆盖表迁入「用户 × 当前激活角色」的 my-settings 快照。

## 为什么需要

2026-11-08 用户裁决（D-A 逐字）：「个人偏好区……整块从账号页删除，能力划归为
用户×角色的角色设置；后端 /api/user/reply-mode 与 /api/user/humanize 端点、
user_reply_mode 与 user_humanize 读层整体退役」。本批同时把
`user_proactive_config` 的 per-user 层一并退役（保留
`GET /api/user/proactive/history` 只读面）。

读层退役后，这三张表里的存量行**不再被任何代码读取** —— 若不迁移，用户
之前的调参会静默失效（不是删除，而是「看不见、也不再生效」）。

## 迁移语义

- 每张旧表一行 = 一个 `user_key`（完整会话键 `N:wxid`）的覆盖值；
- 目标 = 该用户的**当前激活角色**的快照（`user_character_settings`）：
  1. `user_active_characters` 最新一行（按 `id` 倒序）；
  2. 否则 `wechat_bindings.character_card_id`（最新绑定，按 `bound_at` 倒序）；
  3. 都没有 → 内置默认卡 `default`（v1.65.2 裁决：`default` 是正式默认卡，
     快照对它同样可用）；
- 只写**旧表里真的出现过的键**（不是整份默认），逐键宽松过滤（脏值丢弃，
  见 `character_settings.sanitize_patch`）；写侧仍走 `character_settings.write`
  的严格校验（失败即整条 skipped 并打印原因，绝不静默）；
- 目标快照已存在时默认**跳过**（避免覆盖用户在新界面里刚改过的值），
  `--overwrite` 才覆盖；这也让脚本可重复执行（幂等）。

## 用法（仓库根目录）

    # 只打印迁移计划（默认；不写任何库）
    python -m scripts.migrate_per_user_prefs_to_my_settings

    # 真写
    python -m scripts.migrate_per_user_prefs_to_my_settings --apply

    # 真写 + 覆盖已物化的快照
    python -m scripts.migrate_per_user_prefs_to_my_settings --apply --overwrite

    # 迁移成功后再删旧行（可选，二次执行才建议；默认保留旧行以便回滚）
    python -m scripts.migrate_per_user_prefs_to_my_settings --apply --purge-legacy
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from utils import character_settings

logger = logging.getLogger("migrate_per_user_prefs")

_REPO = Path(__file__).resolve().parent.parent
_DB = _REPO / "data" / "sqlite.db"

#: 旧表名（本批退役的三张；缺表 = 该库从未启用该层，直接跳过）。
REPLY_TABLE = "user_reply_mode"
HUMANIZE_TABLE = "user_humanize"
PROACTIVE_TABLE = "user_proactive_config"

#: 内置默认卡 id（v1.65.2 用户裁决：进入系统默认激活的就是十四这张卡）。
DEFAULT_CHARACTER_ID = "default"


# ── 纯映射函数（测试直接调用，不碰库） ────────────────────────


def humanize_patch(raw: Any) -> dict[str, Any]:
    """`user_humanize.config` 原始载荷 → my-settings 补丁（只留 humanize 组内字段）。"""
    return _scoped_patch(raw, "humanize")


def proactive_patch(raw: Any) -> dict[str, Any]:
    """`user_proactive_config.config` 原始载荷 → my-settings 补丁（proactive 组）。

    墓碑行里的内部键 ``__revert__`` 不在字段表内，会在 `sanitize_patch` 里被丢弃
    —— 墓碑是旧实现「清覆盖后回滚引擎」的记账，不属于用户设置。
    """
    return _scoped_patch(raw, "proactive")


def reply_patch(raw_mode: Any) -> dict[str, Any]:
    """`user_reply_mode.mode` → `{"reply_mode": ...}`；非法值 → 空补丁。"""
    mode = str(raw_mode or "").strip()
    if mode not in ("immersive", "novel"):
        return {}
    return {"reply_mode": mode}


def _scoped_patch(raw: Any, group: str) -> dict[str, Any]:
    """按字段组过滤并宽松校验一份旧覆盖载荷。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    allowed = set(character_settings.FIELDS_BY_GROUP[group])
    return character_settings.sanitize_patch(
        {k: v for k, v in raw.items() if k in allowed}
    )


def combine_patches(*patches: dict[str, Any]) -> dict[str, Any]:
    """按顺序合并补丁（后面的覆盖前面的）；空补丁不影响结果。"""
    out: dict[str, Any] = {}
    for patch in patches:
        if patch:
            out.update(patch)
    return out


def active_character_for(
    user_id: int,
    active_rows: list[tuple[int, str]],
    binding_rows: list[tuple[int, str, str]],
) -> str:
    """该用户当前激活的角色 id（确定性，四步回落，绝不猜随机值）。

    Args:
        active_rows: `[(user_id, character_id), ...]`，**按 id 升序**（后者更新）。
        binding_rows: `[(user_id, wxid, character_card_id), ...]`，**按 bound_at 升序**。
    """
    wanted = int(user_id)
    active = [str(cid).strip() for uid, cid in active_rows if int(uid) == wanted]
    for cid in reversed(active):
        if cid:
            return cid
    cards = [str(card or "").strip() for uid, _wxid, card in binding_rows if int(uid) == wanted]
    for cid in reversed(cards):
        if cid:
            return cid
    return DEFAULT_CHARACTER_ID


def session_key_for(
    user_id: int, binding_rows: list[tuple[int, str, str]]
) -> str:
    """该用户最近一条绑定 → 完整会话键 `N:wxid`；无绑定 → 空串（不猜键）。"""
    wanted = int(user_id)
    wxids = [str(wxid or "").strip() for uid, wxid, _card in binding_rows if int(uid) == wanted]
    for wxid in reversed(wxids):
        if wxid:
            return f"{wanted}:{wxid}"
    return ""


def build_plan(
    reply_rows: list[tuple[str, str]],
    humanize_rows: list[tuple[str, Any]],
    proactive_rows: list[tuple[str, Any]],
    active_rows: list[tuple[int, str]],
    binding_rows: list[tuple[int, str, str]],
) -> list[dict[str, Any]]:
    """把旧表行折算成迁移计划（纯函数，不读写库）。

    Args:
        reply_rows: `[(user_key, mode), ...]`
        humanize_rows / proactive_rows: `[(user_key, raw_config), ...]`
        active_rows / binding_rows: 见 :func:`active_character_for`

    返回每条计划：``{"user_key", "user_id", "character_id", "patch", "sources"}``。
    `user_key` 以旧表的键为准（它就是运行时隔离域）；没有合法 `N:wxid` 形态
    （无冒号 / owner 不是数字）的行无法定位用户，``user_id`` 为 None，
    调用方必须把它们计入 skipped。
    """
    by_user: dict[str, dict[str, Any]] = {}

    def _bucket(user_key: str) -> dict[str, Any]:
        key = str(user_key or "").strip()
        slot = by_user.setdefault(key, {"patch": {}, "sources": []})
        return slot

    for user_key, mode in reply_rows:
        patch = reply_patch(mode)
        if patch:
            slot = _bucket(user_key)
            slot["patch"].update(patch)
            slot["sources"].append(REPLY_TABLE)

    for user_key, raw in humanize_rows:
        patch = humanize_patch(raw)
        if patch:
            slot = _bucket(user_key)
            slot["patch"].update(patch)
            slot["sources"].append(HUMANIZE_TABLE)

    for user_key, raw in proactive_rows:
        patch = proactive_patch(raw)
        if patch:
            slot = _bucket(user_key)
            slot["patch"].update(patch)
            slot["sources"].append(PROACTIVE_TABLE)

    plan: list[dict[str, Any]] = []
    for user_key, slot in by_user.items():
        owner = user_key.split(":", 1)[0] if ":" in user_key else ""
        user_id = int(owner) if owner.isdigit() else None
        character_id = (
            active_character_for(user_id, active_rows, binding_rows)
            if user_id is not None
            else ""
        )
        plan.append(
            {
                "user_key": user_key,
                "user_id": user_id,
                "character_id": character_id,
                "patch": dict(slot["patch"]),
                "sources": sorted(set(slot["sources"])),
            }
        )
    plan.sort(key=lambda item: item["user_key"])
    return plan


# ── 库读取 / 写入 ────────────────────────────────────────────


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _fetch_rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error as exc:  # noqa: BLE001
        logger.warning("读取失败（按空表处理）: %s", exc)
        return []


def load_legacy(db_path: Path) -> dict[str, list]:
    """读三张旧表 + 定位激活角色所需的绑定/激活行（缺表一律按空处理）。"""
    empty = {
        "reply": [],
        "humanize": [],
        "proactive": [],
        "active": [],
        "bindings": [],
    }
    path = Path(db_path)
    if not path.exists():
        return empty
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        out = dict(empty)
        if _table_exists(conn, REPLY_TABLE):
            out["reply"] = _fetch_rows(
                conn, f"SELECT user_key, mode FROM {REPLY_TABLE}"
            )
        if _table_exists(conn, HUMANIZE_TABLE):
            out["humanize"] = _fetch_rows(
                conn, f"SELECT user_key, config FROM {HUMANIZE_TABLE}"
            )
        if _table_exists(conn, PROACTIVE_TABLE):
            out["proactive"] = _fetch_rows(
                conn, f"SELECT user_key, config FROM {PROACTIVE_TABLE}"
            )
        if _table_exists(conn, "user_active_characters"):
            out["active"] = _fetch_rows(
                conn, "SELECT user_id, character_id FROM user_active_characters ORDER BY id ASC"
            )
        if _table_exists(conn, "wechat_bindings"):
            out["bindings"] = _fetch_rows(
                conn,
                "SELECT user_id, wxid, COALESCE(character_card_id, '') "
                "FROM wechat_bindings WHERE wxid IS NOT NULL AND wxid != '' "
                "ORDER BY bound_at ASC, id ASC",
            )
        return out
    finally:
        conn.close()


def purge_legacy(db_path: Path, user_keys: list[str]) -> int:
    """删除这些 user_key 在旧表里的行；返回删除行数。失败上抛（不静默）。"""
    keys = [str(k).strip() for k in user_keys if str(k or "").strip()]
    if not keys:
        return 0
    path = Path(db_path)
    if not path.exists():
        return 0
    removed = 0
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        with conn:
            for table in (REPLY_TABLE, HUMANIZE_TABLE, PROACTIVE_TABLE):
                if not _table_exists(conn, table):
                    continue
                for key in keys:
                    cur = conn.execute(f"DELETE FROM {table} WHERE user_key = ?", (key,))
                    removed += int(cur.rowcount or 0)
        return removed
    finally:
        conn.close()


def apply_plan(
    plan: list[dict[str, Any]], *, overwrite: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """执行迁移计划；返回 `(applied, skipped)`。

    - 无合法 user_id / 空补丁 → skipped（原因写明）；
    - 快照已存在且未 `--overwrite` → skipped（避免覆盖新界面里的改动）；
    - `character_settings.write` 抛 ValueError → skipped（写侧严格，绝不假成功）。
    """
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in plan:
        user_key = item["user_key"]
        if item.get("user_id") is None:
            skipped.append({**item, "reason": "user_key 不是 N:wxid 形态，无法定位用户"})
            continue
        if not item["patch"]:
            skipped.append({**item, "reason": "旧行里没有可迁移的合法字段"})
            continue
        character_id = item["character_id"] or DEFAULT_CHARACTER_ID
        if not overwrite and character_settings.has_row(user_key, character_id):
            skipped.append({**item, "reason": "目标快照已存在（加 --overwrite 覆盖）"})
            continue
        try:
            character_settings.write(user_key, character_id, item["patch"])
        except ValueError as exc:
            skipped.append({**item, "reason": f"写侧校验拒绝: {exc}"})
            continue
        applied.append({**item, "character_id": character_id})
    return applied, skipped


# ── CLI ─────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="旧 per-user 覆盖表 → 「用户 × 当前激活角色」my-settings 快照"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="真写库（默认 dry-run，只打印计划）",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="覆盖已物化的目标快照（默认跳过，避免覆盖新界面里的改动）",
    )
    parser.add_argument(
        "--purge-legacy", action="store_true",
        help="迁移成功（applied）的 user_key 从旧表删行（默认保留以便回滚）",
    )
    parser.add_argument("--db", default=str(_DB), help="sqlite.db 路径")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db_path = Path(args.db)
    legacy = load_legacy(db_path)
    plan = build_plan(
        legacy["reply"], legacy["humanize"], legacy["proactive"],
        legacy["active"], legacy["bindings"],
    )

    logger.info(
        "发现 %d 个 user_key（reply=%d humanize=%d proactive=%d）",
        len(plan), len(legacy["reply"]), len(legacy["humanize"]), len(legacy["proactive"]),
    )
    for item in plan:
        logger.info(
            "  %s → 角色 %s  keys=%s  来源=%s",
            item["user_key"],
            item["character_id"] or "<无法定位>",
            ",".join(sorted(item["patch"])) or "<空>",
            ",".join(item["sources"]),
        )
        # 旧键与「当前最新绑定」不一致时如实提示（键以旧表为准，不猜、不改键）
        if item["user_id"] is not None:
            current = session_key_for(item["user_id"], legacy["bindings"])
            if current and current != item["user_key"]:
                logger.info(
                    "    注意：旧键不是该用户最新绑定（最新=%s），迁移按旧键落快照",
                    current,
                )

    if not args.apply:
        logger.info("dry-run：未写库（加 --apply 才真写）")
        return 0

    applied, skipped = apply_plan(plan, overwrite=args.overwrite)
    logger.info("已迁移 %d 条，跳过 %d 条", len(applied), len(skipped))
    for item in skipped:
        logger.info("  跳过 %s：%s", item["user_key"], item["reason"])
    if args.purge_legacy and applied:
        removed = purge_legacy(db_path, [i["user_key"] for i in applied])
        logger.info("已清理旧表行 %d 条", removed)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
