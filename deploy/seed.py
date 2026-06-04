#!/usr/bin/env python3
"""预置角色知识库初始化脚本 — 部署前运行，预建所有角色 BM25 索引。

用法:
    python deploy/seed.py                          # 构建全部角色知识索引
    python deploy/seed.py --rebuild                # 强制重建（删除已存在索引再建）
    python deploy/seed.py --audit-only             # 仅审计，不构建
    python deploy/seed.py --verify                 # 构建后验证搜索是否正常

说明:
    - 从 config/characters/*.json 读取所有角色卡
    - 用 BM25 建索引，持久化到 data/knowledge/{character_id}.json
    - 重建 data/presets/_index.json（前端预设列表用）
    - 适合 CI/CD 部署流水线中运行
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 确保项目根在 sys.path
_project_root = Path(__file__).parent.parent.absolute()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed")

# 抑制第三方库日志
logging.getLogger("shisi").setLevel(logging.WARNING)

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.knowledge.character_knowledge_service import (
    get_knowledge_service,
    _DEFAULT_INDEX_DIR,
)

CHARS_DIR = Path("config/characters")
PRESETS_DIR = Path("data/presets")
MIN_CHUNKS = 2


def build_preset_index():
    """重建 data/presets/_index.json。"""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for f in sorted(PRESETS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            d = data.get("data", data)
            index.append({
                "id": f.stem,
                "name": d.get("name", ""),
                "description": (d.get("description", "") or "")[:500],
                "tags": d.get("tags", []),
                "has_first_mes": bool(d.get("first_mes")),
                "has_scenario": bool(d.get("scenario")),
                "char_count": len(d.get("description", "") or ""),
            })
        except Exception as e:
            logger.warning("  ⚠ 读取预设失败 %s: %s", f.name, e)

    idx_path = PRESETS_DIR / "_index.json"
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    logger.info("  ✅ 预设索引已更新: %d 个角色 → %s", len(index), idx_path)
    return index


def build_knowledge_indexes(rebuild=False):
    """为所有角色预建 BM25 知识索引。"""
    CHARS_DIR.mkdir(parents=True, exist_ok=True)
    _DEFAULT_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    json_files = sorted(CHARS_DIR.glob("*.json"))
    if not json_files:
        logger.warning("  ⚠ config/characters/ 下没有角色文件，跳过知识索引")
        return []

    service = get_knowledge_service()
    results = []
    start = time.time()

    for path in json_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            cid = path.stem
            logger.warning("  ⚠ %s 解析失败: %s", path.name, e)
            results.append((path.stem, cid, 0, "ERR: parse failed"))
            continue

        cid = data.get("id") or path.stem
        name = data.get("name") or cid

        # 如果已存在索引且不强制重建，跳过
        index_path = _DEFAULT_INDEX_DIR / f"{cid}.json"
        if index_path.exists() and not rebuild:
            try:
                with open(index_path, encoding="utf-8") as f:
                    idx_data = json.load(f)
                chunks = len(idx_data) if isinstance(idx_data, list) else len(idx_data.get("chunks", []))
                service.load_index(cid)
                results.append((name, cid, chunks, "SKIP (cached)"))
                continue
            except Exception:
                pass  # 索引损坏，重新构建

        # 解析角色卡并建索引
        try:
            card = CharaCardV2Parser.parse(data)
            service.index_from_card(cid, card)
            service.save_index(cid)
            stats = service.get_stats(cid)
            chunks = stats.get("total_chunks", 0)
            results.append((name, cid, chunks, "OK" if chunks >= MIN_CHUNKS else "LOW"))
        except Exception as e:
            logger.warning("  ⚠ %s (%s) 索引失败: %s", name, cid, e)
            results.append((name, cid, 0, f"ERR: {e}"))

    elapsed = time.time() - start
    return results, elapsed


def print_report(results, elapsed=None):
    """打印索引报告。"""
    if not results:
        return

    print(f"\n{'角色名':<24} {'ID':<40} {'知识块':>6}  状态")
    print("-" * 80)
    total_chunks = 0
    low_coverage = []
    errors = []
    for name, cid, chunks, status in results:
        total_chunks += chunks
        line = f"{name:<24} {cid:<40} {chunks:>6}  {status}"
        if status == "LOW":
            low_coverage.append((name, cid, chunks))
        elif status.startswith("ERR"):
            errors.append((name, cid, status))
        print(line)

    print(f"\n📊 总计: {len(results)} 角色, {total_chunks} 知识块")
    if results:
        print(f"  平均: {total_chunks / len(results):.1f} 块/角色")
    if elapsed:
        print(f"  耗时: {elapsed:.1f}s")

    if low_coverage:
        print(f"\n⚠ 低覆盖角色 ({len(low_coverage)}):")
        for name, cid, chunks in low_coverage:
            print(f"  {name:<24} ({cid}): 仅 {chunks} 块")

    if errors:
        print(f"\n❌ 索引失败 ({len(errors)}):")
        for name, cid, err in errors:
            print(f"  {name:<24} ({cid}): {err}")

    return low_coverage, errors


def verify_search(results):
    """对前 10 个角色做搜索验证。"""
    service = get_knowledge_service()
    ok = 0
    fail = 0
    print("\n🔍 搜索验证（前 10 个角色）...")
    for name, cid, chunks, status in results[:10]:
        if status.startswith("ERR"):
            continue
        try:
            result = service.search(cid, "基础设定", top_k=2)
            if result and result.total_chunks > 0:
                ok += 1
            else:
                fail += 1
                print(f"  ⚠ {name} ({cid}): 搜索返回空")
        except Exception as e:
            fail += 1
            print(f"  ❌ {name} ({cid}): 搜索异常 - {e}")
    print(f"  ✅ {ok} 通过, ❌ {fail} 失败")


def main():
    parser = argparse.ArgumentParser(description="预置角色知识库初始化")
    parser.add_argument("--rebuild", action="store_true", help="强制重建所有索引")
    parser.add_argument("--audit-only", action="store_true", help="仅审计不构建")
    parser.add_argument("--verify", action="store_true", help="构建后验证搜索")
    args = parser.parse_args()

    print("=" * 60)
    print("  预置角色知识库初始化")
    print("=" * 60)

    # Step 1: 同步角色文件
    print("\n[1/3] 同步角色文件...")
    try:
        from scripts.sync_character_files import main as sync_main
        sync_main()
    except ImportError:
        logger.warning("  ⚠ sync_character_files 不可用，跳过")
    except Exception as e:
        logger.warning("  ⚠ 同步异常: %s", e)

    # Step 2: 重建预设索引
    print("\n[2/3] 重建预设索引...")
    presets = build_preset_index()

    # Step 3: 构建知识索引
    print("\n[3/3] 构建知识索引...")
    if args.audit_only:
        print("  --audit-only 模式，跳过构建")
    else:
        results, elapsed = build_knowledge_indexes(rebuild=args.rebuild)
        low, errors = print_report(results, elapsed)

        if args.verify:
            verify_search(results)

        if errors:
            print("\n⚠ 部分角色索引失败，请检查日志")
            sys.exit(1)

    print("\n✅ 种子数据初始化完成")
    print(f"  预设角色: {len(presets)}")
    print("  现在可以启动服务了!")


if __name__ == "__main__":
    main()
