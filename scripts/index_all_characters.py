"""Index knowledge for all 29 characters and report coverage."""
import sys, json, os
sys.path.insert(0, ".")

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.knowledge.character_knowledge_service import get_knowledge_service

chars_dir = "config/characters"
service = get_knowledge_service()
results = []

for fname in sorted(os.listdir(chars_dir)):
    if not fname.endswith(".json"):
        continue
    with open(os.path.join(chars_dir, fname), encoding="utf-8") as f:
        data = json.load(f)

    card = CharaCardV2Parser.parse(data)
    cid = data.get("id") or fname.replace(".json", "")
    name = card.data.name

    # Index
    try:
        service.index_from_card(cid, card)
        stats = service.get_stats(cid)
        chunks = stats.get("total_chunks", 0)
        results.append((name, cid, chunks, "OK"))
    except Exception as e:
        results.append((name, cid, 0, f"ERR: {e}"))

# Report
print(f"{'Name':<30} {'ID':<30} {'Chunks':>6}  Status")
print("-" * 80)
total_chunks = 0
for name, cid, chunks, status in results:
    total_chunks += chunks
    print(f"{name:<30} {cid:<30} {chunks:>6}  {status}")

print(f"\nTotal: {len(results)} characters, {total_chunks} total knowledge chunks")
print(f"Average: {total_chunks/len(results):.1f} chunks per character")

# Show which chars have low coverage
print("\n=== Low coverage (< 3 chunks) ===")
for name, cid, chunks, status in results:
    if chunks < 3 and status == "OK":
        print(f"  {name:<30} ({cid}) - only {chunks} chunks")

print("\nDONE")
