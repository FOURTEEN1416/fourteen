"""重建全部角色知识索引 + 孤儿索引/孤儿源审计与清理。

背景：`enrich_persona_web.py --all-sources` 是**覆盖式**写入 —— 实测把
"上杉绘梨衣"的 148 块卡字段知识冲成 4 块 B 站 UP 主简介（含 QQ 群号），
属数据损坏。本脚本从权威真源（config/characters/*.json）重建干净索引。

孤儿对象分两类裁决（源材料与派生物语义不同）：
  - 孤儿索引：data/knowledge/ 顶层 stem 不在现役卡 id 集合的 BM25 索引
    （W5 语义下是可随时重建的纯派生物），执行模式照旧清理；
  - 孤儿源：data/knowledge/sources/ 同规则的源存储文件 —— W5 口径
    「源材料不可丢」，默认只审计不删，须显式 --prune-orphan-sources 授权。

用法：
  python scripts/rebuild_knowledge_index.py --dry-run   # 只审计（孤儿索引+孤儿源分列，不删）
  python scripts/rebuild_knowledge_index.py             # 执行重建 + 清孤儿索引
  python scripts/rebuild_knowledge_index.py --root <dir> --dry-run  # 指定项目根（测试用 tmp 根）
  python scripts/rebuild_knowledge_index.py --prune-orphan-sources  # 显式才删孤儿源
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, ".")

from shisi.core.models.character_aggregate import CharacterAggregate  # noqa: E402
from shisi.core.models.persona_profile import PersonaProfile  # noqa: E402
from shisi.knowledge.character_knowledge_service import (  # noqa: E402
    CharacterKnowledgeService,
    get_knowledge_service,
)


def audit_orphans(know: pathlib.Path,
                  active_ids: set[str]) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """孤儿分列审计：孤儿索引（顶层 stem 不在现役 id）与孤儿源（sources/ 同规则）。"""
    orphan_idx = [f for f in sorted(know.glob("*.json")) if f.stem not in active_ids]
    src_dir = know / "sources"
    orphan_src: list[pathlib.Path] = []
    if src_dir.is_dir():
        orphan_src = [f for f in sorted(src_dir.glob("*.json"))
                      if f.stem not in active_ids]
    return orphan_idx, orphan_src


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=".",
                    help="项目根（默认当前目录；测试传 tmp 根）")
    ap.add_argument("--prune-orphan-sources", action="store_true",
                    help="删除孤儿源文件（源材料不可丢，删除须显式授权）")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root)
    chars = root / "config" / "characters"
    know = root / "data" / "knowledge"
    # --root 缺省走单例（含 AI_GF_KNOWLEDGE_INDEX_DIR env 口径，行为不变）；
    # 显式 --root 时按该根构造独立服务（测试 tmp 根）。
    svc = (get_knowledge_service() if str(args.root) == "."
           else CharacterKnowledgeService(use_bm25=True, index_dir=know))

    active_ids: set[str] = set()

    print("=== 重建现役角色索引 ===")
    for f in sorted(chars.glob("*.json")):
        try:
            card = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  跳过 {f.name}: {e}")
            continue
        cid = card.get("id") or f.stem
        active_ids.add(cid)
        name = card.get("name", "?")
        # 直接用权威真源构造聚合根（不依赖 PersonaService 实例）
        # 2026-09-20 补齐透传：core_anchors（经 persona）与 source_data（mes_example 等）
        # 此前不传 → 重建索引比运行时抽取**少两类知识块**（锚点/示例对话），
        # 磁盘索引被运行时 load_index 优先加载，缺口会一直存在。
        ch = CharacterAggregate(
            id=cid,
            name=name,
            description=card.get("description", "") or "",
            personality_text=card.get("personality_text", "") or "",
            scenario=card.get("scenario", "") or "",
            creator_notes=card.get("creator_notes", "") or "",
            persona=PersonaProfile(core_anchors=card.get("core_anchors", []) or []),
            source_data=card,
        )
        if args.dry_run:
            print(f"  [dry] {name[:10]:<12} ({cid})")
            continue
        svc.index_character(cid, ch)
        svc.save_index(cid)
        print(f"  ✓ {name[:10]:<12} → {svc._chunk_counts.get(cid, 0)} 块")

    # ── 孤儿分列审计（索引=可重建派生物；源=不可丢源材料，删除须显式授权）──
    orphan_idx, orphan_src = audit_orphans(know, active_ids)
    print(f"\n=== 孤儿索引审计（{len(orphan_idx)} 个，可重建派生物）===")
    for f in orphan_idx:
        print(f"  {f.name}")
    print(f"\n=== 孤儿源审计（{len(orphan_src)} 个，源材料·须显式 --prune-orphan-sources 才删）===")
    for f in orphan_src:
        print(f"  sources/{f.name}")

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return

    # ── 清理孤立索引 ──
    # 2026-09-20 修复：旧逻辑的 `startswith(p) for p in ("",)` 恒为真 →
    # 任何文件都会被 continue，清理从未生效（09-18/09-20 两次实跑均 0 删除，
    # 服务器实测残留 29 个旧 persona_ 时戳形态与已删卡索引）。
    # 索引是可重建的派生缓存、运行时仅按卡 id（文件 stem）加载，
    # 故 stem 不在现役 id 集合内的文件一律为孤立，精确匹配删除。
    print("\n=== 清理孤立索引 ===")
    removed = 0
    for f in orphan_idx:
        f.unlink()
        removed += 1
        print(f"  删除孤立索引: {f.name}")
    print(f"  共删除 {removed} 个")

    # ── 清理孤儿源（仅显式授权；连 .lock 一并清理）──
    if args.prune_orphan_sources:
        print("\n=== 清理孤儿源 ===")
        removed_src = 0
        for f in orphan_src:
            f.unlink(missing_ok=True)
            lock = f.with_name(f.name + ".lock")
            lock.unlink(missing_ok=True)
            removed_src += 1
            print(f"  删除孤儿源: sources/{f.name}")
        print(f"  共删除 {removed_src} 个")

    # ── 校验 ──
    print("\n=== 校验 ===")
    for f in sorted(know.glob("*.json")):
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
