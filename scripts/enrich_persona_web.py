"""网络人设增强 CLI — 从互联网搜索并完善角色人设知识库。

融合四种内容源：
1. Direct URL scraping — requests + BeautifulSoup（通用网页抓取）
2. Agent-Reach CLI 工具 — bili-cli（B站搜索）、mcporter（Exa搜索）、Jina Reader
3. Crawl4AI — 免授权网页抓取与搜索（替代 Firecrawl）
4. 管道模式 — 从 stdin 接收内容（与 LLM/ai-first-scraper 配合）

用法（请选一种模式运行）：

  [模式1] 直接抓取 URL:
    python scripts/enrich_persona_web.py --id 上杉绘梨衣 --name "上杉绘梨衣" ^
        --urls "https://zh.moegirl.org.cn/上杉绘梨衣"

  [模式2] 全源搜索（B站 + Crawl4AI + Jina，自动收集）:
    python scripts/enrich_persona_web.py --id 上杉绘梨衣 --all-sources

  [模式3] 交互搜索 + 手动指定 URL:
    python scripts/enrich_persona_web.py --id 洛十六 --name "洛十六" --interactive

  [模式4] 管道输入（与 LLM 配合）:
    echo "人设内容..." | python scripts/enrich_persona_web.py --id 洛十六 --pipe

  [模式5] LLM 协作模式: 先用 ai-first-scraper MCP 搜索，提取 URL，再交给脚本:
    python scripts/enrich_persona_web.py --id 上杉绘梨衣 ^
        --urls "URL1" "URL2" "URL3"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("enrich_persona_web")


def resolve_character_name(char_id: str) -> str:
    """从 knowledge JSON 或 character JSON 推断角色显示名。"""
    kpath = Path("data/knowledge") / f"{char_id}.json"
    if kpath.exists():
        try:
            data = json.loads(kpath.read_text("utf-8"))
            name = data.get("name") or data.get("character_name", "")
            if name:
                return name
        except Exception:
            pass
    cpath = Path("config/characters") / f"{char_id}.json"
    if cpath.exists():
        try:
            data = json.loads(cpath.read_text("utf-8"))
            name = data.get("name") or data.get("data", {}).get("name", "")
            if name:
                return name
        except Exception:
            pass
    return char_id


def main():
    parser = argparse.ArgumentParser(
        description="网络人设增强 — 融合 Firecrawl、Agent-Reach、DirectScraper 完善人设知识库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 指定 URL 抓取
  python scripts/enrich_persona_web.py --id 上杉绘梨衣 --urls "https://zh.moegirl.org.cn/上杉绘梨衣"

  # 全源自动搜索（B站 + Firecrawl + Jina + Exa）
  python scripts/enrich_persona_web.py --id 洛十六 --all-sources

  # 交互搜索 + URL 补充
  python scripts/enrich_persona_web.py --id 洛十六 --interactive --urls "https://example.com/page"

  # 管道模式（与 LLM 配合）
  type content.txt | python scripts/enrich_persona_web.py --id 上杉绘梨衣 --pipe

  # Agent-Reach 搜索（B站 + Exa，仅显示不写入）
  python scripts/enrich_persona_web.py --id 洛十六 --all-sources --search-only
        """,
    )
    parser.add_argument("--name", type=str, default="", help="角色名称（如：上杉绘梨衣）")
    parser.add_argument("--id", type=str, required=True, help="角色 ID")
    parser.add_argument("--urls", type=str, nargs="*", default=None,
                        help="要抓取的 URL 列表")
    parser.add_argument("--pipe", action="store_true", default=False,
                        help="管道模式：从 stdin 读取内容")
    parser.add_argument("--interactive", "-i", action="store_true", default=False,
                        help="交互模式：显示详细进度")
    parser.add_argument("--all-sources", action="store_true", default=False,
                        help="全源搜索模式：自动用所有可用源搜索（B站/Firecrawl/Jina/Exa）")
    parser.add_argument("--search-only", action="store_true", default=False,
                        help="仅搜索显示结果，不写入知识库")
    parser.add_argument("--max-docs", type=int, default=5,
                        help="最大文档数（默认5）")

    args = parser.parse_args()

    character_name = args.name if args.name else resolve_character_name(args.id)
    character_id = args.id

    print(f"\n{'='*60}")
    print(f"  网络人设增强引擎 v2")
    print(f"  角色: {character_name}  [{character_id}]")
    print(f"{'='*60}")

    # 初始化组件
    from persona_extractor.web_enricher import (
        WebPersonaEnricher, DirectScraper, EnrichResult, RawDocument,
    )
    from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService

    knowledge_service = CharacterKnowledgeService(use_bm25=True, index_dir="data/knowledge")
    knowledge_service.load_index(character_id)

    enricher = WebPersonaEnricher(knowledge_service=knowledge_service)

    print(f"  可用内容源: {', '.join(enricher._available_sources)}")

    # 收集文档
    all_docs: list[RawDocument] = []

    # Phase 1: 指定 URL 抓取
    if args.urls:
        print(f"\n📄 抓取 URL ({len(args.urls)} 个)...")
        for url in args.urls:
            doc = enricher.add_url(url)
            if doc and doc.content:
                all_docs.append(doc)
                print(f"  ✓ {doc.title[:50] or url[:50]} ({len(doc.content)} 字)")
            else:
                print(f"  ✗ {url[:60]} (抓取失败)")

    # Phase 2: 管道输入
    if args.pipe:
        if not sys.stdin.isatty():
            print(f"\n📥 读取管道输入...")
            pipe_content = sys.stdin.read().strip()
            if pipe_content:
                doc = enricher.add_content(pipe_content, "pipe_input")
                all_docs.append(doc)
                print(f"  ✓ 管道内容 ({len(pipe_content)} 字符)")
        else:
            print("  ⚠ 未检测到管道输入")

    # Phase 3: 全源搜索
    if args.all_sources:
        print(f"\n🔍 全源搜索 [{character_name}] ...")
        queries = [f"{character_name} 角色介绍", character_name, f"{character_name} 龙族"]
        found_any = False
        for q in queries:
            if found_any:
                break
            for doc in enricher.search_all_sources(q, max_per_source=3):
                all_docs.append(doc)
                found_any = True
                print(f"  ✓ [{doc.source}] {doc.title[:50] or doc.url[:50]}")

    # Phase 4: 交互搜索
    if args.interactive and not args.all_sources:
        if args.interactive:
            print(f"\n🔍 搜索...")
        q = f"{character_name} 角色介绍"

        # B站搜索
        print("  📡 bili-cli (B站搜索) ...")
        for doc in enricher.agent_reach.search_bilibili(q, 3):
            if doc.content:
                all_docs.append(doc)
                print(f"    ✓ [B站] {doc.title[:60]}")

# Crawl4AI
        print("  🕷️ Crawl4AI ...")
        for doc in enricher.search_firecrawl(q, 2):
            if doc.content:
                all_docs.append(doc)
                print(f"    ✓ [Crawl4AI] {doc.title[:50] or doc.url[:50]}")

        # Exa
        print("  🔎 Exa (mcporter) ...")
        for doc in enricher.agent_reach.search_exa(q, 2):
            if doc.content:
                all_docs.append(doc)
                print(f"    ✓ [Exa] {doc.title[:50] or doc.url[:50]}")

    # 如果只有搜索模式
    if args.search_only:
        print(f"\n结果: {len(all_docs)} 个文档")
        for i, doc in enumerate(all_docs, 1):
            print(f"  [{i}] [{doc.source}] {doc.title[:60]}")
            if doc.url:
                print(f"      URL: {doc.url}")
            if doc.content:
                print(f"      内容: {doc.content[:120]}...")
        print()
        return

    # 执行增强
    if not all_docs:
        print("\n⚠ 没有找到任何内容。请指定 --urls 或使用 --all-sources 模式。")
        print("  提示: 先用 ai-first-scraper MCP 搜索内容，再用 --urls 传入 URL。")
        print("  或: 使用 --all-sources 自动搜索所有可用源。")
        print()
        return

    docs_to_use = all_docs[:args.max_docs]
    print(f"\n{'─'*40}")
    print(f"  处理并写入知识库 ({len(docs_to_use)} 个文档)...")

    result = enricher.enrich(
        character_id=character_id,
        character_name=character_name,
        docs=docs_to_use,
        interactive=args.interactive,
    )

    # 输出结果
    print(f"\n{'─'*40}")
    print(f"  ⏱ 耗时: {result.duration_seconds:.1f}s")
    print(f"  📄 文档: {result.documents_found}")
    print(f"  🔧 生成知识块: {result.chunks_generated}")
    print(f"  💾 写入知识库: {result.chunks_added}")
    print(f"  📡 数据来源: {', '.join(result.sources_used) or '无'}")

    if result.errors:
        for err in result.errors:
            print(f"  ⚠ {err}")

    stats = knowledge_service.get_stats(character_id)
    print(f"\n  📊 知识库统计: {stats.get('total_chunks', 0)} 知识块")

    # 搜索验证
    print(f"\n{'─'*40}")
    print(f"  搜索验证:")
    for q in [character_name, f"{character_name} 性格", f"{character_name} 背景"]:
        search_result = knowledge_service.search(character_id, q, top_k=2)
        top = search_result.get_top(2)
        if top:
            print(f"    [{q}] → {top[0].content[:80]}...")
        else:
            print(f"    [{q}] → (无结果)")

    print(f"\n{'='*60}")
    print(f"  ✅ 完成")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
