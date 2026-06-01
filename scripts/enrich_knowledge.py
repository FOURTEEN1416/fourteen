"""Enrich knowledge for low-coverage characters (林挽夏, 孙颖莎)."""
import sys, json, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, force=True)

from shisi.knowledge.character_knowledge_service import get_knowledge_service

# ======== Knowledge documents ========

ENRICHMENTS = {
    "sys_001": {  # 孙颖莎
        "document_id": "enrich_sys_001",
        "chunks": [
            "孙颖莎，2000年11月4日出生于河北省石家庄市，中国女子乒乓球运动员，世界排名第一。",
            "主要成就：2021年东京奥运会女团冠军、女单亚军；2024年巴黎奥运会女单金牌、女团冠军；2023年德班世乒赛女单冠军；多次获得WTT大满贯赛事冠军。",
            "技术特点：正手暴力进攻力量大旋转强；反手灵巧多变防守反击能力强；心理素质极佳多次上演逆转；步法灵活覆盖面广。",
            "绰号小魔王，性格活泼可爱说话软糯，喜欢毛绒玩具特别是熊猫和兔子，爱吃美食喜欢逛街，与队友关系融洽是国乒开心果。",
            "5岁开始接触乒乓球，2015年进入国家二队，2017年升入国家一队。2017年日本公开赛一鸣惊人击败多名主力夺冠。",
            "对日本选手伊藤美诚保持高胜率，与陈梦王曼昱等队友的内战同样精彩，擅长在落后情况下调整心态逆转比赛。",
            "父母均为普通职工，家庭氛围温馨父母非常支持她的事业，从小在石家庄长大对家乡感情深厚。",
        ],
    },
    "persona_林挽夏_1774701604527": {  # 林挽夏
        "document_id": "enrich_linwanxia",
        "chunks": [
            "林挽夏是一个温柔体贴善解人意的女生，总能察觉到对方的情绪变化，内心敏感需要很多安全感和关爱。",
            "她有点小任性喜欢撒娇但懂得分寸，对在乎的人会特别用心记住每一个小细节，是那种让人想捧在手心里保护的女孩。",
            "她喜欢一起做饭看电影散步等日常活动，生气时喜欢不说话需要对方主动来哄，开心时会像小孩子一样笑得很灿烂。",
            "她喜欢养花阳台上种了很多多肉植物，厨艺不错尤其擅长做甜点和汤，喜欢猫咪看到流浪猫会忍不住喂食。",
            "睡前喜欢听轻柔的音乐或者有声书，最喜欢的颜色是浅紫色和白色，最喜欢的食物是草莓蛋糕和火锅。",
            "她最喜欢秋天喜欢踩落叶的声音，最喜欢的动物是猫咪和兔子，认定一个人就会很专一希望对方也能同样用心对待自己。",
            "她不介意偶尔的小摩擦重要的是事后沟通，认为感情需要双方共同经营，期待和喜欢的人一起创造美好的回忆。",
        ],
    },
}

# ======== Execute ========

service = get_knowledge_service()

for char_id, doc in ENRICHMENTS.items():
    print(f"\n{'='*50}")
    print(f"Processing: {char_id}")
    
    # Step 1: Check if exists / index from card data
    if not service.has_index(char_id):
        print(f"  No index for {char_id}, need to load character card first")
        from pathlib import Path
        from shisi.character.character_card_v2 import CharaCardV2Parser
        
        # Try to load from data/characters/
        fpath = Path("data") / "characters" / f"{char_id}.json"
        if fpath.exists():
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            card = CharaCardV2Parser.parse(data)
            service.index_from_card(char_id, card)
            print(f"  Indexed from card: {card.data.name}")
        else:
            print(f"  Character file not found: {fpath}")
            continue
    
    # Step 2: Get stats before
    before = service.get_stats(char_id)
    print(f"  Before: {before}")
    
    # Step 3: Index enrichment chunks
    from shisi.knowledge.retriever import KnowledgeChunk
    
    knowledge_chunks = [
        KnowledgeChunk(
            content=chunk,
            source=doc["document_id"],
            source_id=f"{doc['document_id']}_{i}",
        )
        for i, chunk in enumerate(doc["chunks"])
    ]
    
    retriever = service._retrievers.get(char_id)
    if retriever and hasattr(retriever, "index"):
        retriever.index(knowledge_chunks)
        print(f"  Indexed {len(knowledge_chunks)} enrichment chunks")
        
        if char_id in service._chunk_counts:
            service._chunk_counts[char_id] += len(knowledge_chunks)
        else:
            service._chunk_counts[char_id] = len(knowledge_chunks)
    
    # Step 4: Stats after
    after = service.get_stats(char_id)
    print(f"  After: {after}")
    
    # Step 5: Test search
    queries = {
        "sys_001": ["乒乓球", "奥运会", "性格"],
        "persona_林挽夏_1774701604527": ["性格", "喜欢", "相处"],
    }
    
    for q in queries.get(char_id, []):
        result = service.search(char_id, q, top_k=3)
        top = result.get_top(3)
        if top:
            print(f"  Search '{q}': top score={top[0].score:.3f}, total chunks={len(result.chunks)}")
            for r in top[:2]:
                print(f"    [{r.score:.3f}] {r.content[:60]}...")

print(f"\n{'='*50}")
print("Final coverage overview:")
for char_id in ["sys_001", "persona_林挽夏_1774701604527"]:
    if service.has_index(char_id):
        stats = service.get_stats(char_id)
        print(f"  {char_id}: {stats}")

print("\nDONE")
