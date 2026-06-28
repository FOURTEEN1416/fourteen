"""测试 MiMo LLM API 可用性"""
import json
import urllib.request
import urllib.error

API_BASE = "https://token-plan-cn.xiaomimimo.com/v1"
API_KEY = "tp-ce14qava7ey1030qkg2jki0ksa6ay8qewgp6891nlav76jft"

# 1. 测试 /models 端点
print("=" * 60)
print("[1] GET /v1/models")
print("=" * 60)
req = urllib.request.Request(
    f"{API_BASE}/models",
    headers={"Authorization": f"Bearer {API_KEY}"},
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
        if isinstance(data, dict) and "data" in data:
            models = [m.get("id", "?") for m in data["data"][:20]]
            print(f"  HTTP 200, 模型数: {len(data['data'])}")
            for m in models:
                print(f"    - {m}")
        else:
            print(f"  HTTP 200, body: {str(data)[:500]}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")

# 2. 测试 /chat/completions 端点
print("\n" + "=" * 60)
print("[2] POST /v1/chat/completions")
print("=" * 60)
body = json.dumps({
    "model": "mimo-2.5-flash",
    "messages": [{"role": "user", "content": "你好，请用一句话回复"}],
    "max_tokens": 50,
    "stream": False,
}).encode("utf-8")
req = urllib.request.Request(
    f"{API_BASE}/chat/completions",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        print(f"  HTTP 200")
        print(f"  model: {data.get('model', '?')}")
        print(f"  usage: {data.get('usage', {})}")
        print(f"  content: {content!r}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}")
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")

# 3. 尝试其他模型名
print("\n" + "=" * 60)
print("[3] 尝试其他模型名")
print("=" * 60)
for model in ["mimo-2.5-flash", "mimo-v2.5-flash", "mimo-flash", "glm-4-flash"]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 10,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"  [OK]  {model:25s} → {content[:60]!r}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:150]
        print(f"  [ERR] {model:25s} → HTTP {e.code}: {msg}")
    except Exception as e:
        print(f"  [FAIL] {model:25s} → {type(e).__name__}: {e}")
