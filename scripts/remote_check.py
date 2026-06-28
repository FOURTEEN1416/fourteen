"""远程健康检查脚本：测试 LLM 连通性、API 端点、角色加载状态。
通过 SSH 上传到服务器后执行。
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8000"


def http_get(path: str, timeout: float = 10.0):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)


def http_post_json(path: str, body: dict, timeout: float = 20.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)


def check_endpoints():
    """扫描常见健康检查端点"""
    print("=" * 60)
    print("[1] API 端点扫描")
    print("=" * 60)
    for path in ["/health", "/api/health", "/api/v1/health", "/docs", "/openapi.json"]:
        code, _ = http_get(path, timeout=5.0)
        print(f"  {path:25s} → {code}")

    # 列出所有 API 路径
    code, body = http_get("/openapi.json", timeout=10.0)
    if code == 200:
        try:
            data = json.loads(body)
            paths = sorted(data.get("paths", {}).keys())
            print(f"\n  OpenAPI 路径总数: {len(paths)}")
            for p in paths:
                print(f"    {p}")
        except Exception as e:
            print(f"  解析 openapi.json 失败: {e}")


def check_characters():
    """检查角色列表"""
    print("\n" + "=" * 60)
    print("[2] 角色库加载状态")
    print("=" * 60)
    code, body = http_get("/api/characters", timeout=10.0)
    print(f"  /api/characters → HTTP {code}")
    if code != 200:
        print(f"  Body: {body[:500]}")
        return []

    try:
        data = json.loads(body)
    except Exception as e:
        print(f"  JSON 解析失败: {e}")
        return []

    # 兼容多种返回格式
    if isinstance(data, list):
        chars = data
    elif isinstance(data, dict):
        chars = (
            data.get("characters")
            or data.get("data")
            or data.get("items")
            or []
        )
    else:
        chars = []

    print(f"  角色总数: {len(chars)}")
    active_count = 0
    for c in chars:
        name = c.get("name", "?")
        cid = c.get("id", "?")
        is_active = c.get("is_active", False)
        if is_active:
            active_count += 1
        flag = "★" if is_active else " "
        print(f"    {flag} {name:30s} (id={cid})")
    print(f"\n  活跃角色: {active_count} / {len(chars)}")
    return chars


def check_llm_via_demo_chat():
    """通过 demo chat 端点测试 LLM 连通性"""
    print("\n" + "=" * 60)
    print("[3] LLM 连通性测试（通过 /api/demo/chat/stream）")
    print("=" * 60)
    body = {
        "message": "你好，请用一句话自我介绍",
        "session_id": "health_check_llm_test_001",
        "character_id": "default",
    }
    code, body_text = http_post_json(
        "/api/demo/chat/stream", body, timeout=30.0
    )
    print(f"  HTTP {code}")
    if code != 200:
        print(f"  Error body: {body_text[:500]}")
        return False

    # SSE 流：提取 token 内容
    tokens = []
    for line in body_text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                evt = json.loads(payload)
            except Exception:
                continue
            # 兼容多种事件格式
            t = (
                evt.get("token")
                or evt.get("content")
                or evt.get("delta", {}).get("content")
                or ""
            )
            if t:
                tokens.append(t)
            # 错误事件
            if evt.get("type") == "error" or evt.get("error"):
                err = evt.get("error") or evt.get("message", "")
                print(f"  [ERROR event] {err}")

    full_reply = "".join(tokens)
    print(f"  回复 token 数: {len(tokens)}")
    print(f"  回复内容（前 300 字）: {full_reply[:300]}")
    if full_reply.strip():
        print("\n  [OK] LLM 连通正常")
        return True
    else:
        print("\n  [FAIL] LLM 返回空回复")
        print(f"  Raw body (前 800 字):\n{body_text[:800]}")
        return False


def check_tools_endpoint():
    """检查工具相关端点"""
    print("\n" + "=" * 60)
    print("[4] 工具调取端点检查")
    print("=" * 60)
    candidates = [
        "/api/tools",
        "/api/tools/list",
        "/api/v1/tools",
        "/api/characters/tools",
    ]
    for path in candidates:
        code, body = http_get(path, timeout=5.0)
        if code == 200:
            print(f"  [OK]  {path} → 200")
            try:
                data = json.loads(body)
                if isinstance(data, list):
                    print(f"        工具数: {len(data)}")
                    for t in data[:10]:
                        if isinstance(t, dict):
                            print(
                                f"          - {t.get('name', '?')}: {t.get('description', '')[:50]}"
                            )
                        else:
                            print(f"          - {t}")
            except Exception:
                print(f"        Body: {body[:200]}")
        else:
            print(f"  [--]  {path} → {code}")


def main():
    print("服务器端健康检查开始")
    print(f"BASE URL: {BASE}")
    check_endpoints()
    check_characters()
    check_llm_via_demo_chat()
    check_tools_endpoint()
    print("\n" + "=" * 60)
    print("检查完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
