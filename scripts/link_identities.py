"""跨通道身份关联管理（2026-10-12 用户裁决「真正的库共享」）。

同一真人多条微信通道 = 库里多个互不相交的 user_key（owner 前缀 + peer wxid
都不同），库里不存在可推导的「同一真人」信号——身份关联必须显式声明。

用法（默认连生产同款路径 data/sqlite.db，可用 --db 覆盖）：
    python scripts/link_identities.py --list
    python scripts/link_identities.py --link "2:wxid_a@im.wechat" "4:wxid_b@im.wechat"
    python scripts/link_identities.py --unlink "4:wxid_b@im.wechat"

幂等：重复 link 同组无副作用。unlink 只解除该键，组内其余成员保持关联。
关联后立即生效：读侧（get_facts/search/向量复核/durable anchor 注入）按组
合并，删除按组同步等价副本与水位。解除（unlink）后各键回到完全隔离。

注意：这是身份声明操作（防串台硬约束的唯一显式开口），只在确属同一真人时
使用；每次操作记入输出（主控在 LOG.md 落账）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shisi.memory.legacy.structured_memory import StructuredMemory, default_db_path


def main() -> int:
    parser = argparse.ArgumentParser(description="跨通道身份关联管理")
    parser.add_argument("--db", default=default_db_path(), help="sqlite.db 路径")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="列出当前身份组")
    g.add_argument("--link", nargs=2, metavar=("KEY_A", "KEY_B"), help="声明两键属同一真人")
    g.add_argument("--unlink", metavar="KEY", help="解除某键的身份组归属")
    args = parser.parse_args()

    sm = StructuredMemory(args.db)
    try:
        if args.list:
            with sm._conn() as conn:  # noqa: SLF001
                rows = conn.execute(
                    "SELECT identity_id, user_key FROM user_identity_map ORDER BY identity_id, user_key"
                ).fetchall()
            if not rows:
                print("（无身份关联）")
                return 0
            groups: dict[str, list[str]] = {}
            for r in rows:
                groups.setdefault(str(r["identity_id"]), []).append(str(r["user_key"]))
            for gid, members in groups.items():
                print(f"{gid}:")
                for m in members:
                    print(f"  {m}")
            return 0
        if args.link:
            group = sm.link_identities(args.link[0], args.link[1])
            print("已关联，组内成员：")
            for k in group:
                print(f"  {k}")
            return 0
        if args.unlink:
            ok = sm.unlink_identity(args.unlink)
            print("已解除" if ok else "该键无身份关联（无操作）")
            return 0 if ok else 1
        return 2
    finally:
        sm.close()


if __name__ == "__main__":
    raise SystemExit(main())
