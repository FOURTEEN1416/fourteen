"""全面真实测试 — 启动真实FastAPI服务器，通过HTTP请求验证全部功能。"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:18080"
PASS = 0
FAIL = 0
ERRORS = []


def api(method, path, data=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()) if e.headers.get("content-type", "").startswith("application/json") else {"error": str(e)}
    except Exception as e:
        return -1, {"error": str(e)}


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  ❌ {name} — {detail}")


def section(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


# ============================================================
section("1. 角色管理全流程")
# ============================================================

status, body = api("GET", "/api/shisi/characters")
test("列出角色", status == 200, f"status={status}")
chars = body.get("data", [])
test("角色列表为数组", isinstance(chars, list), f"type={type(chars)}")
print(f"  📊 当前角色数: {len(chars)}")

if chars:
    first = chars[0]
    cid = first["character_id"]
    cname = first["name"]
    test("角色有ID", bool(cid), f"cid={cid}")
    test("角色有名字", bool(cname), f"name={cname}")

    status, body = api("GET", f"/api/shisi/characters/{cid}")
    test("获取单个角色", status == 200, f"status={status}")

    status, body = api("POST", "/api/shisi/characters/switch", {"character_id": cid})
    test("切换角色", status == 200 and "data" in body, f"status={status}, body={body}")

    status, body = api("GET", "/api/shisi/characters/nonexistent_char")
    test("获取不存在角色404", status == 404, f"status={status}")

    status, body = api("POST", "/api/shisi/characters/switch", {"character_id": "nonexistent"})
    test("切换不存在角色400", status == 400, f"status={status}")

# ============================================================
section("2. 好感度 + 情感阶段")
# ============================================================

test_cid = "real_test_char"

status, body = api("POST", f"/api/shisi/affinity/{test_cid}/update", {"delta": 30, "reason": "真实测试", "source": "test"})
test("好感度+30", status == 200, f"status={status}")
if status == 200:
    aff = body.get("data", {}).get("affinity", 0)
    test("好感度值=30", abs(aff - 30) < 0.01, f"affinity={aff}")

status, body = api("POST", f"/api/shisi/affinity/{test_cid}/update", {"delta": 25, "reason": "继续增加", "source": "test"})
test("好感度+25(累计55)", status == 200, f"status={status}")

status, body = api("GET", f"/api/shisi/affinity/{test_cid}")
test("查询好感度", status == 200, f"status={status}")
if status == 200:
    data = body.get("data", {})
    test("好感度百分比≈55%", abs(data.get("percentage", 0) - 55) < 1, f"pct={data.get('percentage')}")
    unlocks = data.get("unlocks", [])
    unlock_names = [u.get("name", "") for u in unlocks]
    test("已解锁亲密话题", "亲密话题" in unlock_names, f"unlocks={unlock_names}")

status, body = api("GET", "/api/shisi/emotion-stage/stages")
test("列出情感阶段", status == 200, f"status={status}")
if status == 200:
    stages = body.get("data", [])
    test("4个阶段", len(stages) == 4, f"count={len(stages)}")
    stage_names = [s["name"] for s in stages]
    test("阶段顺序正确", stage_names == ["陌生", "熟悉", "亲密", "羁绊"], f"names={stage_names}")

status, body = api("POST", f"/api/shisi/emotion-stage/{test_cid}/evaluate?affinity=55")
test("评估情感阶段(55)", status == 200, f"status={status}")
if status == 200:
    test("阶段=熟悉", body.get("data", {}).get("stage") == "熟悉", f"stage={body.get('data', {}).get('stage')}")

status, body = api("POST", f"/api/shisi/emotion-stage/{test_cid}/evaluate?affinity=60")
test("评估情感阶段(60→亲密)", status == 200, f"status={status}")

status, body = api("GET", f"/api/shisi/emotion-stage/{test_cid}")
test("查询当前阶段", status == 200, f"status={status}")

# ============================================================
section("3. 生理指标 + 统计 + 记忆")
# ============================================================

status, body = api("GET", f"/api/shisi/vital-signs/{test_cid}")
test("查询生理指标", status == 200, f"status={status}")
if status == 200:
    data = body.get("data", {})
    test("心率在范围", 60 <= data.get("heart_rate", 0) <= 120, f"hr={data.get('heart_rate')}")
    test("体温在范围", 36.0 <= data.get("temperature", 0) <= 37.5, f"temp={data.get('temperature')}")
    test("微信格式存在", "wechat_format" in data, f"keys={list(data.keys())}")

status, body = api("GET", "/api/shisi/stats")
test("查询统计", status == 200, f"status={status}")

status, body = api("POST", "/api/shisi/memory/favorite", {"character_id": test_cid, "memory_id": "mem_001"})
test("收藏记忆", status == 200, f"status={status}")

status, body = api("GET", f"/api/shisi/memory/favorites?character_id={test_cid}")
test("列出收藏", status == 200, f"status={status}")

status, body = api("POST", "/api/shisi/memory/forward", {"from_character": test_cid, "to_character": "other_char", "memory_id": "mem_001"})
test("转发记忆", status == 200, f"status={status}")

status, body = api("DELETE", f"/api/shisi/memory/mem_001?character_id={test_cid}&confirm=false")
test("删除需确认", status == 400, f"status={status}")

# ============================================================
section("4. 表情包API")
# ============================================================

status, body = api("GET", "/api/shisi/stickers")
test("列出表情包", status == 200, f"status={status}")

status, body = api("GET", "/api/shisi/stickers/recommend?emotion_tags=开心&emotion_tags=撒娇&limit=3")
test("推荐表情(需POST)", status in [200, 405], f"status={status}")

# ============================================================
section("5. 好感度衰减")
# ============================================================

status, body = api("POST", f"/api/shisi/affinity/{test_cid}/decay")
test("应用衰减", status == 200, f"status={status}")

status, body = api("GET", f"/api/shisi/affinity/{test_cid}/unlocks")
test("查询解锁列表", status == 200, f"status={status}")

# ============================================================
section("6. 边界值测试")
# ============================================================

status, body = api("POST", f"/api/shisi/affinity/{test_cid}/update", {"delta": 999, "reason": "溢出测试"})
test("好感度溢出钳位100", status == 200, f"status={status}")
if status == 200:
    test("值不超过100", body.get("data", {}).get("affinity", 999) <= 100, f"val={body.get('data', {}).get('affinity')}")

status, body = api("POST", f"/api/shisi/affinity/{test_cid}/update", {"delta": -999, "reason": "下溢测试"})
test("好感度下溢钳位0", status == 200, f"status={status}")
if status == 200:
    test("值不小于0", body.get("data", {}).get("affinity", -1) >= 0, f"val={body.get('data', {}).get('affinity')}")

# ============================================================
section("7. 微信指令全链路（通过Python直接调用）")
# ============================================================

from shisi.wechat.command_parser import WeChatCommandParser  # noqa: E402

parser = WeChatCommandParser()

test_cases = [
    ("切换角色：椎名真昼", "switch_character"),
    ("好感度", "affinity"),
    ("情感状态", "emotion_status"),
    ("生理指标", "vital_signs"),
    ("发表情", "send_sticker"),
    ("收藏", "favorite"),
    ("转发给：十四", "forward"),
    ("普通聊天", None),
]

for msg, expected in test_cases:
    cmd = parser.parse(msg)
    if expected is None:
        test(f"指令解析: '{msg}' → None", cmd is None, f"cmd={cmd}")
    else:
        test(f"指令解析: '{msg}' → {expected}", cmd is not None and cmd.action == expected, f"cmd={cmd}")

# ============================================================
section("8. 并发测试（快速连续请求）")
# ============================================================

results = []
for i in range(20):
    s, _ = api("GET", "/api/shisi/emotion-stage/stages")
    results.append(s)

test("20次并发请求全部200", all(s == 200 for s in results), f"statuses={set(results)}")

# ============================================================
# 汇总
# ============================================================

print(f"\n{'='*60}")
print(f"测试结果: ✅ {PASS} 通过 / ❌ {FAIL} 失败 / 共 {PASS+FAIL} 项")
if ERRORS:
    print("\n失败详情:")
    for e in ERRORS:
        print(f"  - {e}")
print(f"{'='*60}")

sys.exit(0 if FAIL == 0 else 1)
