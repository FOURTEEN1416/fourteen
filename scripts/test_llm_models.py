"""测试 OpenCode Zen 上不同模型的可用性"""
import json
import urllib.request
import urllib.error

API_BASE = "https://opencode.ai/zen/v1"
URL = f"{API_BASE}/chat/completions"

CANDIDATES = [
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
    "gemini-3-flash",
    "gemini-3.5-flash",
    "claude-haiku-4-5",
    "claude-sonnet-4",
]

for model in CANDIDATES:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 20,
    }).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")[:120]
            )
            print(f"[OK]  {model:25s} → {content!r}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"[ERR] {model:25s} → HTTP {e.code}: {body}")
    except Exception as e:
        print(f"[FAIL] {model:25s} → {type(e).__name__}: {e}")
