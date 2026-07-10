import sys, time
sys.path.insert(0, "D:\\Desktop\\ai-girlfriend")

from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService
from persona_extractor.web_enricher import WebPersonaEnricher

character_id = "上杉绘梨衣"
character_name = "上杉绘梨衣"

ks = CharacterKnowledgeService(use_bm25=True, index_dir="data/knowledge")
ks.load_index(character_id)
stats_before = ks.get_stats(character_id)
print(f"Before: {stats_before.get('total_chunks', 0)} chunks")

enricher = WebPersonaEnricher(knowledge_service=ks)
print(f"Available sources: {enricher._available_sources}")

# Test URL scrape
print("\n=== URL Scrape Test ===")
doc = enricher.add_url("https://baike.baidu.com/item/上杉绘梨衣")
if doc and doc.content:
    print(f"  Baidu: {doc.title[:50]} ({len(doc.content)} chars)")
else:
    print("  Baidu: failed (expected - 403)")

# Test Bilibili search only
print("\n=== Bilibili Search ===")
docs = enricher.search_agent_reach(character_name, platform="bilibili")
print(f"  Found {len(docs)} docs")
for d in docs[:3]:
    print(f"  [{d.source}] {d.title[:50]} | {len(d.content)} chars")

# Test enrich with bilibili results
print("\n=== Enrich Test ===")
if docs:
    t0 = time.time()
    result = enricher.enrich(
        character_id=character_id,
        character_name=character_name,
        docs=docs[:3],
        interactive=True,
    )
    print(f"  Duration: {time.time() - t0:.1f}s")
    print(f"  Chunks generated: {result.chunks_generated}")
    print(f"  Chunks added: {result.chunks_added}")
    print(f"  Sources: {result.sources_used}")

    stats_after = ks.get_stats(character_id)
    print(f"  After: {stats_after.get('total_chunks', 0)} chunks")

    # Verify search works
    print("\n=== Search Verification ===")
    for q in [character_name, f"{character_name} 性格"]:
        sr = ks.search(character_id, q, top_k=2)
        top = sr.get_top(2)
        if top:
            print(f"  [{q}] -> {top[0].content[:80]}...")
        else:
            print(f"  [{q}] -> (no results)")
else:
    print("  No bilibili docs found")
