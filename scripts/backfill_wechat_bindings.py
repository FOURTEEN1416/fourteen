"""存量 `wechat_bindings` 补登脚本（D-D 2026-11-08 用户裁决）。

**为什么需要**：`my-settings` 等 15 处调用点经
`api/routers/training_routes.py::_user_session_key_for` 只用 `wechat_bindings`
还原会话键，而扫码登录在 D-D 写侧修复之前**从不写这张表**——存量「先网页激活
→ 后扫码登录」的用户库里没有绑定行，读侧只能 409。写侧修复只对**今后**的扫码
生效，存量必须补登一次。

**推导规则（与写侧同一份实现：`wechat_direct/binding_sync.py`）**：
1. 每人只用自己的**扫码人 wxid**（`channel_paths.scanner_ids_for_user`，即各
   slot `credentials.json` 的 `user_id` / `ilink_user_id`）；同人多 slot 同
   wxid 去重（D16 一人一 bot 一条通道）。
2. 角色取该用户 `user_active_characters` 现值；无激活行 → `default`。
3. 已存在同 wxid：user 与角色都相同 → noop（不写）；否则 update（重新绑定/换角）。
4. 两个用户声称同一 wxid（数据异常）：按 user_id 升序先到者写，后者记 conflict
   不写（不猜归属）。

**安全**：默认 dry-run，只打印计划；加 `--apply` 才真写。写操作是幂等 upsert，
不删除任何行。

用法（在仓库根目录）：
    python scripts/backfill_wechat_bindings.py                 # dry-run
    python scripts/backfill_wechat_bindings.py --apply         # 真写
    python scripts/backfill_wechat_bindings.py --db data/users.db --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_direct import binding_sync

logger = logging.getLogger("backfill_wechat_bindings")


def collect_scanner_ids() -> dict[int, list[str]]:
    """扫描通道目录 → `{注册用户 id: [扫码人 wxid]}`。

    复用 `wechat_direct.channel_paths`（通道路径与凭证解析的单一真源）：
    通道根下每个数字命名目录 = 一个 owner，其各 slot 凭证里的 `user_id`
    即扫码人 `ilink_user_id`。
    """
    from wechat_direct import channel_paths

    mapping: dict[int, list[str]] = {}
    try:
        root = Path(channel_paths.sessions_root())
        if not root.exists():
            return mapping
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not child.name.isdigit():
                continue
            user_id = int(child.name)
            ids = channel_paths.scanner_ids_for_user(user_id)
            if ids:
                mapping[user_id] = ids
    except OSError as exc:  # pragma: no cover - 目录读取极端情况
        logger.warning("扫描通道目录失败（按空处理）: %s", exc)
    return mapping


def _describe(plan: binding_sync.BindingPlan) -> str:
    if plan.action == binding_sync.ACTION_UPDATE:
        return (
            f"[{plan.action}] user={plan.user_id} wxid={plan.wxid} "
            f"char={plan.character_card_id} "
            f"(原 user={plan.previous_user_id} char={plan.previous_character_card_id})"
        )
    return f"[{plan.action}] user={plan.user_id} wxid={plan.wxid} char={plan.character_card_id}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补登扫码登录漏写的 wechat_bindings 行")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库（默认 dry-run，只打印将补写的行）",
    )
    parser.add_argument(
        "--db", default=None, help="users.db 路径（默认按 APP_DATABASE_URL 解析）"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db_arg = args.db or binding_sync.users_db_path()
    if not db_arg:
        logger.error("无法确定 users.db 路径（非 SQLite 部署？请用 --db 指定）")
        return 2
    db_path = Path(db_arg)
    if not db_path.exists():
        logger.error("users.db 不存在：%s", db_path)
        return 2

    scanner_ids = collect_scanner_ids()
    active_characters = binding_sync.read_active_characters(db_path)
    existing = binding_sync.read_existing_bindings(db_path)
    plans = binding_sync.plan_bindings(scanner_ids, active_characters, existing)

    counts = Counter(p.action for p in plans)
    logger.info(
        "通道 owner %d 个，计划 %d 行（insert=%d update=%d noop=%d conflict=%d）",
        len(scanner_ids),
        len(plans),
        counts.get(binding_sync.ACTION_INSERT, 0),
        counts.get(binding_sync.ACTION_UPDATE, 0),
        counts.get(binding_sync.ACTION_NOOP, 0),
        counts.get(binding_sync.ACTION_CONFLICT, 0),
    )
    for plan in plans:
        logger.info("  %s", _describe(plan))

    if not args.apply:
        logger.info("dry-run：未写库（加 --apply 才真写）")
        return 0

    written = binding_sync.apply_plan(db_path, plans)
    logger.info("完成：%s", dict(written))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
