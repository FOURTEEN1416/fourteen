"""热点采集入口脚本 —— 手动跑一轮热点池采集（运维/调试/scheduler 挂线前的过渡）。

链路：config/hot_topics.yaml 查询表 → SearchTool 采集 → data/hot_topics.json 全局池。
供出经 proactive/ase_engine._try_knowledge_share 既有知识分享通道，本脚本不涉及。

用法：
  python scripts/run_hot_topics_collect.py            # 自限速（间隔未到即 skip，安全重复跑）
  python scripts/run_hot_topics_collect.py --force    # 无视限速强制采集一轮
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from shisi.knowledge.hot_topics import collect_if_due, collect_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="热点知识链采集一轮")
    parser.add_argument("--force", action="store_true",
                        help="跳过 interval 自限速，强制采集（仍受 enabled 总闸约束）")
    args = parser.parse_args()

    if args.force:
        from shisi.knowledge.hot_topics import load_config

        cfg = load_config()
        if not cfg["enabled"]:
            print(json.dumps({"skipped": "disabled"}, ensure_ascii=False))
            return 0
        stats = collect_once(config=cfg)
    else:
        stats = collect_if_due()

    print(json.dumps(stats, ensure_ascii=False, default=str))
    # ok=False 视为失败退出码（供 cron/排查感知）；skipped 不算失败
    return 0 if stats.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
