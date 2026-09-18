"""重建全部角色知识索引 + 清理孤立索引。

背景：`enrich_persona_web.py --all-sources` 是**覆盖式**写入 —— 实测把
"上杉绘梨衣"的 148 块卡字段知识冲成 4 块 B 站 UP 主简介（含 QQ 群号），
属数据损坏。本脚本从权威真源（config/characters/*.json）重建干净索引。

另清理孤立索引：data/knowledge/ 下存在与现役 25 张卡无关的旧索引文件
（如 上杉绘梨衣.json、重度病娇.json —— 对应已删除的旧卡）。

用法：
  python scripts/rebuild_knowledge_index.py --dry-run   # 只审计
  python scripts/rebuild_knowledge_index.py             # 执行重建
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

sys.path.insert(0, ".")

from shisi.core.models.character_aggregate import CharacterAggregate  # noqa: E402
from shisi.knowledge.character_knowledge_service import get_knowledge_service  # noqa: E402

ROOT = pathlib.Path(".")
CHARS = ROOT / "config" / "characters"
KNOW = ROOT / "data" / "knowledge"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    svc = get_knowledge_service()
    active_ids: set[str] = set()

    print("=== 重建现役角色索引 ===")
    for f in sorted(CHARS.glob("*.json")):
        try:
            card = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  跳过 {f.name}: {e}")
            continue
        cid = card.get("id") or f.stem
        active_ids.add(cid)
        name = card.get("name", "?")
        # 直接用权威真源构造聚合根（不依赖 PersonaService 实例）
        ch = CharacterAggregate(
            id=cid,
            name=name,
            description=card.get("description", "") or "",
            personality_text=card.get("personality_text", "") or "",
            scenario=card.get("scenario", "") or "",
            creator_notes=card.get("creator_notes", "") or "",
        )
        if args.dry_run:
            print(f"  [dry] {name[:10]:<12} ({cid})")
            continue
        svc.index_character(cid, ch)
        svc.save_index(cid)
        print(f"  ✓ {name[:10]:<12} → {svc._chunk_counts.get(cid, 0)} 块")

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return

    # ── 清理孤立索引 ──
    print("\n=== 清理孤立索引 ===")
    removed = 0
    for f in sorted(KNOW.glob("*.json")):
        stem = f.stem
        if stem in active_ids:
            continue
        # 兼容 persona_xxx_timestamp 形态
        if any(stem.startswith(p) or stem.endswith(i) for i in active_ids for p in ("",)):
            continue
        if any(i in stem for i in active_ids):
            continue
        f.unlink()
        removed += 1
        print(f"  删除孤立索引: {f.name}")
    print(f"  共删除 {removed} 个")

    # ── 校验 ──
    print("\n=== 校验 ===")
    for f in sorted(KNOW.glob("*.json")):
        if f.stem not in active_ids:
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        srcs = {}
        for c in d.get("chunks", []):
            s = c.get("source", "?")
            srcs[s] = srcs.get(s, 0) + 1
        bad = [s for s in srcs if s.startswith("web_enricher")]
        warn = "  ⚠️含web污染" if bad else ""
        print(f"  {f.stem:<12} {d.get('total_chunks', 0):>3} 块  {srcs}{warn}")


if __name__ == "__main__":
    main()
