"""Create enriched knowledge documents for low-coverage characters."""
import sys, os, urllib.request, json
sys.path.insert(0, ".")

# Knowledge documents content
KNOWLEDGE_DOCS = {
    "sys_001": {
        "filename": "孙颖莎_career.txt",
        "content": """孙颖莎，2000年11月4日出生于河北省石家庄市，中国女子乒乓球运动员，世界排名第一。
        
主要成就：
- 2021年东京奥运会女团冠军、女单亚军
- 2024年巴黎奥运会女单金牌得主、女团冠军成员
- 2023年德班世乒赛女单冠军
- 多次获得WTT大满贯赛事冠军
- 2024年国际乒联年终总决赛女单冠军

技术特点：
- 正手暴力进攻，力量大、旋转强
- 反手灵巧多变，防守反击能力强
- 心理素质极佳，多次在大赛中上演逆转
- 步法灵活，覆盖面广

场外形象：
- 被球迷爱称为"小魔王"
- 性格活泼可爱，说话软糯
- 喜欢毛绒玩具（特别喜欢熊猫和兔子）
- 爱吃美食，喜欢逛街
- 与队友关系融洽，是国乒的"开心果"

成长经历：
- 5岁开始接触乒乓球
- 2015年进入国家二队，2017年升入国家一队
- 2017年日本公开赛一鸣惊人，击败多名主力夺冠
- 迅速成长为国乒女队核心主力

对战风格：
- 对日本选手伊藤美诚保持高胜率
- 与陈梦、王曼昱等队友的"内战"同样精彩
- 擅长在落后情况下调整心态逆转比赛

家庭背景：
- 父母均为普通职工
- 家庭氛围温馨，父母非常支持她的事业
- 从小在石家庄长大，对家乡感情深厚"""
    },
    "persona_林挽夏_1774701604527": {
        "filename": "林挽夏_personality.txt",
        "content": """林挽夏的性格特点和互动模式：

核心性格：
- 温柔体贴，善解人意，总能察觉到对方的情绪变化
- 有点小任性，喜欢撒娇，但懂得分寸
- 内心敏感，需要很多安全感和关爱
- 对在乎的人会特别用心，记住每一个小细节

相处模式：
- 喜欢一起做饭、看电影、散步等日常活动
- 生气时喜欢不说话，需要对方主动来哄
- 开心时会像小孩子一样笑得很灿烂
- 偶尔会吃醋，但不会无理取闹

生活习惯：
- 喜欢养花，阳台上种了很多多肉植物
- 厨艺不错，尤其擅长做甜点和汤
- 喜欢猫咪，看到流浪猫会忍不住喂食
- 睡前喜欢听轻柔的音乐或者有声书

喜好清单：
- 最喜欢的颜色：浅紫色和白色
- 最喜欢的食物：草莓蛋糕、火锅
- 最喜欢的季节：秋天（喜欢踩落叶的声音）
- 最喜欢的动物：猫咪和兔子

对伴侣的态度：
- 认定一个人就会很专一
- 希望对方也能同样用心对待自己
- 不介意偶尔的小摩擦，重要的是事后沟通
- 认为感情需要双方共同经营"""
    }
}

API_BASE = "http://localhost:8000"
API_KEY = "CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN"
HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "multipart/form-data",
}

def upload_knowledge(character_id, filename, content):
    """Upload knowledge document via API."""
    import http.client
    import io

    # Build multipart form data manually
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = []
    body.append(f"--{boundary}")
    body.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"')
    body.append("Content-Type: text/plain")
    body.append("")
    body.append(content)
    body.append(f"--{boundary}--")
    
    data = "\r\n".join(body).encode("utf-8")
    
    req = urllib.request.Request(
        f"{API_BASE}/api/characters/{character_id}/knowledge/documents",
        data=data,
        headers={
            "X-API-Key": API_KEY,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}

# Upload knowledge docs
for char_id, doc in KNOWLEDGE_DOCS.items():
    print(f"Uploading to {char_id} ({doc['filename']})...")
    result = upload_knowledge(char_id, doc["filename"], doc["content"])
    if "error" in result:
        print(f"  FAILED: {result['error']}")
    else:
        print(f"  OK: {result}")

# Verify by checking stats
print("\n=== Verification ===")
for char_id in KNOWLEDGE_DOCS:
    try:
        req = urllib.request.Request(
            f"{API_BASE}/api/characters/{char_id}/knowledge/stats",
            headers={"X-API-Key": API_KEY},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            stats = json.loads(resp.read().decode())
            print(f"{char_id}: {stats}")
    except Exception as e:
        print(f"{char_id}: ERROR {e}")

# Verify by searching
print("\n=== Search tests ===")
search_queries = {
    "sys_001": "奥运会 金牌",
    "persona_林挽夏_1774701604527": "性格 温柔",
}
for char_id, query in search_queries.items():
    try:
        data = json.dumps({"query": query, "top_k": 3}).encode()
        req = urllib.request.Request(
            f"{API_BASE}/api/characters/{char_id}/knowledge/search",
            data=data,
            headers={
                "X-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"\n{char_id} search '{query}':")
            for r_item in result.get("results", []):
                print(f"  [{r_item['score']:.3f}] {r_item['content'][:100]}...")
    except Exception as e:
        print(f"{char_id}: ERROR {e}")

print("\nDONE")
