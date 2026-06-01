"""Verify knowledge retrieval pipeline works for enriched characters."""
import sys, json, os, textwrap
sys.path.insert(0, ".")
os.environ["KNOWLEDGE_ENABLED"] = "true"

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.knowledge.character_knowledge_service import get_knowledge_service

service = get_knowledge_service()

# Index and test the 2 enriched characters
for fname in ["孙颖莎.json", "persona_林挽夏_1774701604527.json"]:
    fpath = os.path.join("config/characters", fname)
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)
    card = CharaCardV2Parser.parse(data)
    cid = data.get("id") or fname.replace(".json", "")
    service.index_from_card(cid, card)
    print(f"=== {card.data.name} ({cid}) ===")

    queries = ["乒乓球 奥运 冠军", "性格 爱好", "日常 相处"]
    for q in queries:
        result = service.search(cid, q, top_k=3)
        top = result.get_top(3)
        print(f"  q='{q}': {len(result.chunks)} hits, top_score={top[0].score if top else 0:.3f}")
        for r in top[:2]:
            print(f"    [{r.score:.3f}] {textwrap.shorten(r.content, 60)}")
    print()

# Simulate prompt_builder._get_knowledge_context() for 孙颖莎
print("=== 模拟 prompt 注入 (孙颖莎) ===")
cid_sys = "sys_001"
# Re-index so get_knowledge_context will find it
svc = get_knowledge_service()
if not svc.has_index(cid_sys):
    fpath = os.path.join("config/characters", "孙颖莎.json")
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)
    card = CharaCardV2Parser.parse(data)
    svc.index_from_card(cid_sys, card)

ctx = svc.get_knowledge_context(cid_sys, "你今天训练累吗 我们去吃火锅吧", top_k=3)
print(f"知识上下文 ({len(ctx)} chars):")
print(ctx[:600] if ctx else "(空)")
print()

# Same for 林挽夏
print("=== 模拟 prompt 注入 (林挽夏) ===")
cid_lin = "persona_林挽夏_1774701604527"
if not svc.has_index(cid_lin):
    fpath = os.path.join("config/characters", "persona_林挽夏_1774701604527.json")
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)
    card = CharaCardV2Parser.parse(data)
    svc.index_from_card(cid_lin, card)

ctx2 = svc.get_knowledge_context(cid_lin, "今天天气真好 我们一起出去走走吧", top_k=3)
print(f"知识上下文 ({len(ctx2)} chars):")
print(ctx2[:600] if ctx2 else "(空)")

print("\n[DONE]")
