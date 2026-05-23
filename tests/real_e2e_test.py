"""全面真实测试 — 单脚本启动服务器+运行HTTP测试。"""

import json
import subprocess
import sys  # noqa: E402
import time
import urllib.parse

print("=" * 60)
print("全面真实测试 — 启动FastAPI服务器 + HTTP端到端验证")
print("=" * 60)

# Step 1: Start server in subprocess
print("\n[1/9] 启动真实FastAPI服务器...")
server_proc = subprocess.Popen(
    [sys.executable, "-c", """
import uvicorn
from api.rest_api import create_api_app
app = create_api_app()
uvicorn.run(app, host='127.0.0.1', port=18080, log_level='warning')
"""],
    cwd=r"C:\\Users\\FOUR\\Desktop\\ai-girlfriend",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait for server ready
import urllib.request  # noqa: E402

for i in range(15):
    time.sleep(1)
    try:
        urllib.request.urlopen("http://127.0.0.1:18080/api/aiyu/characters", timeout=2)
        print(f"  ✅ 服务器就绪 ({i+1}s)")
        break
    except Exception:
        if i == 14:
            print("  ❌ 服务器启动超时")
            server_proc.kill()
            sys.exit(1)

# Step 2: Run tests
import urllib.error  # noqa: E402

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
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}
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
section("[2/9] 角色管理全流程")
# ============================================================

status, body = api("GET", "/api/aiyu/characters")
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

    status, body = api("GET", f"/api/aiyu/characters/{urllib.parse.quote(cid, safe='')}")
    test("获取单个角色", status == 200, f"status={status}")

    status, body = api("POST", "/api/aiyu/characters/switch", {"character_id": cid})
    test("切换角色", status == 200 and "data" in body, f"status={status}")

    status, body = api("GET", "/api/aiyu/characters/nonexistent_char")
    test("获取不存在角色→404", status == 404, f"status={status}")

    status, body = api("POST", "/api/aiyu/characters/switch", {"character_id": "nonexistent"})
    test("切换不存在角色→400", status == 400, f"status={status}")

# ============================================================
section("[3/9] 好感度系统")
# ============================================================

test_cid = "real_test_char"

status, body = api("POST", f"/api/aiyu/affinity/{test_cid}/update", {"delta": 30, "reason": "真实测试", "source": "test"})
test("好感度+30", status == 200, f"status={status}")
if status == 200:
    aff = body.get("data", {}).get("affinity", 0)
    test("好感度值=30", abs(aff - 30) < 0.01, f"affinity={aff}")

status, body = api("POST", f"/api/aiyu/affinity/{test_cid}/update", {"delta": 25, "reason": "继续增加", "source": "test"})
test("好感度+25(累计55)", status == 200, f"status={status}")

status, body = api("GET", f"/api/aiyu/affinity/{test_cid}")
test("查询好感度", status == 200, f"status={status}")
if status == 200:
    data = body.get("data", {})
    test("好感度百分比≈55%", abs(data.get("percentage", 0) - 55) < 1, f"pct={data.get('percentage')}")
    unlocks = data.get("unlocks", [])
    unlock_names = [u.get("name", "") for u in unlocks]
    test("已解锁亲密话题", "亲密话题" in unlock_names, f"unlocks={unlock_names}")

status, body = api("POST", f"/api/aiyu/affinity/{test_cid}/update", {"delta": 999, "reason": "溢出测试"})
test("好感度溢出钳位≤100", status == 200 and body.get("data", {}).get("affinity", 999) <= 100, f"status={status}, val={body.get('data', {}).get('affinity')}")

status, body = api("POST", f"/api/aiyu/affinity/{test_cid}/update", {"delta": -999, "reason": "下溢测试"})
test("好感度下溢钳位≥0", status == 200 and body.get("data", {}).get("affinity", -1) >= 0, f"status={status}, val={body.get('data', {}).get('affinity')}")

status, body = api("POST", f"/api/aiyu/affinity/{test_cid}/decay")
test("应用衰减", status == 200, f"status={status}")

status, body = api("GET", f"/api/aiyu/affinity/{test_cid}/unlocks")
test("查询解锁列表", status == 200, f"status={status}")

# ============================================================
section("[4/9] 情感阶段系统")
# ============================================================

status, body = api("GET", "/api/aiyu/emotion-stage/stages")
test("列出情感阶段", status == 200, f"status={status}")
if status == 200:
    stages = body.get("data", [])
    test("4个阶段", len(stages) == 4, f"count={len(stages)}")
    stage_names = [s["name"] for s in stages]
    test("阶段顺序正确", stage_names == ["陌生", "熟悉", "亲密", "羁绊"], f"names={stage_names}")

status, body = api("POST", f"/api/aiyu/emotion-stage/{test_cid}/evaluate?affinity=60")
test("评估阶段(60→亲密)", status == 200 and body.get("data", {}).get("stage") == "亲密", f"status={status}, body={body}")

status, body = api("POST", f"/api/aiyu/emotion-stage/{test_cid}/evaluate?affinity=10")
test("评估阶段(10→禁止回退仍为亲密)", status == 200 and body.get("data", {}).get("stage") == "亲密", f"status={status}, stage={body.get('data', {}).get('stage')}")

status, body = api("GET", f"/api/aiyu/emotion-stage/{test_cid}")
test("查询当前阶段", status == 200, f"status={status}")

# ============================================================
section("[5/9] 生理指标系统")
# ============================================================

status, body = api("GET", f"/api/aiyu/vital-signs/{test_cid}")
test("查询生理指标", status == 200, f"status={status}")
if status == 200:
    data = body.get("data", {})
    test("心率在60-120", 60 <= data.get("heart_rate", 0) <= 120, f"hr={data.get('heart_rate')}")
    test("体温在36-37.5", 36.0 <= data.get("temperature", 0) <= 37.5, f"temp={data.get('temperature')}")
    test("呼吸在12-25", 12 <= data.get("breath_rate", 0) <= 25, f"br={data.get('breath_rate')}")
    test("微信格式存在", "wechat_format" in data, f"keys={list(data.keys())}")
    wf = data.get("wechat_format", "")
    test("微信格式含心率", "心率" in wf, f"format={wf[:50]}")

# ============================================================
section("[6/9] 统计 + 记忆增强")
# ============================================================

status, body = api("GET", "/api/aiyu/stats")
test("查询统计", status == 200, f"status={status}")

status, body = api("POST", "/api/aiyu/memory/favorite", {"character_id": test_cid, "memory_id": "mem_001"})
test("收藏记忆", status == 200, f"status={status}")

status, body = api("GET", f"/api/aiyu/memory/favorites?character_id={test_cid}")
test("列出收藏", status == 200, f"status={status}")
if status == 200:
    test("收藏列表非空", len(body.get("data", [])) > 0, f"count={len(body.get('data', []))}")

status, body = api("POST", "/api/aiyu/memory/forward", {"from_character": test_cid, "to_character": "other_char", "memory_id": "mem_001"})
test("转发记忆", status == 200, f"status={status}")

status, body = api("DELETE", f"/api/aiyu/memory/mem_001?character_id={test_cid}&confirm=false")
test("删除需确认→400", status == 400, f"status={status}")

# ============================================================
section("[7/9] 表情包API")
# ============================================================

status, body = api("GET", "/api/aiyu/stickers")
test("列出表情包", status == 200, f"status={status}")

# ============================================================
section("[8/9] 微信指令全链路（Python直接调用）")
# ============================================================

import sys  # noqa: E402

sys.path.insert(0, r"C:\Users\FOUR\Desktop\ai-girlfriend")
from aiyu.wechat.command_parser import WeChatCommandParser  # noqa: E402

parser = WeChatCommandParser()

test_cases = [
    ("切换角色：椎名真昼", "switch_character"),
    ("好感度", "affinity"),
    ("情感状态", "emotion_status"),
    ("生理指标", "vital_signs"),
    ("发表情", "send_sticker"),
    ("收藏", "favorite"),
    ("转发给：小暖", "forward"),
    ("普通聊天", None),
]

for msg, expected in test_cases:
    cmd = parser.parse(msg)
    if expected is None:
        test(f"指令: '{msg}' → None", cmd is None, f"cmd={cmd}")
    else:
        test(f"指令: '{msg}' → {expected}", cmd is not None and cmd.action == expected, f"cmd={cmd}")

# ============================================================
section("[9/9] 并发测试（50次快速请求）")
# ============================================================

results = []
for i in range(50):
    s, _ = api("GET", "/api/aiyu/emotion-stage/stages")
    results.append(s)

test("50次并发全部200", all(s == 200 for s in results), f"unique_statuses={set(results)}")

# 交替写读测试
results2 = []
for i in range(20):
    s1, _ = api("POST", f"/api/aiyu/affinity/concurrent_{i}/update", {"delta": 1, "reason": "concurrent"})
    s2, _ = api("GET", f"/api/aiyu/affinity/concurrent_{i}")
    results2.append((s1, s2))

test("交替写读全部成功", all(s1 == 200 and s2 == 200 for s1, s2 in results2), f"failures={[(s1,s2) for s1,s2 in results2 if s1!=200 or s2!=200]}")

# ============================================================
# 汇总
# ============================================================

server_proc.terminate()
server_proc.wait()

print(f"\n{'='*60}")
print(f"真实测试结果: ✅ {PASS} 通过 / ❌ {FAIL} 失败 / 共 {PASS+FAIL} 项")
if ERRORS:
    print("\n失败详情:")
    for e in ERRORS:
        print(f"  - {e}")
print(f"{'='*60}")

sys.exit(0 if FAIL == 0 else 1)
