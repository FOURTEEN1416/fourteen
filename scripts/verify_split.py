"""P0 拆分静态验证脚本：import + 端点计数 + 路由总数。"""
import os
import re
import sys

os.chdir(r"C:\Users\FOUR\Desktop\ai-girlfriend")
sys.path.insert(0, ".")

print("=== 1. 静态 import 检查（8 个新子路由 + app_factory）===")
from api import _misc_routes, _chat_routes, _personality_routes, _users_routes
from api import _training_routes, _tools_routes, _safety_routes, _clone_routes
print("OK: 8 new sub-routers importable")
from api import app_factory
print("OK: app_factory importable")

print()
print("=== 2. main_routes.py 缩身后仍导出全部 6 模型 + 4 常量 + Helper ===")
from api.main_routes import (
    ChatRequest, ChatResponse, EmotionStateResponse,
    CreateSessionRequest, ConfigUpdateRequest, ProactiveConfigRequest,
    ToolToggleRequest, _sanitize_config,
    SENSITIVE_FIELDS, UPLOAD_DIR, MAX_UPLOAD_SIZE, MAX_RAG_UPLOAD_SIZE,
)
print("OK: 6 models + _sanitize_config + 4 constants all exported")

print()
print("=== 3. main_routes.py 端点应为 0（全部移走）===")
src = open("api/main_routes.py", encoding="utf-8").read()
endpoints = re.findall(r"@router\.(get|post|put|delete|patch)\(", src)
print(f"main_routes.py residual endpoints: {len(endpoints)} (expected 0)")

print()
print("=== 4. 8 个新子路由端点分布 ===")
total = 0
for name in ["_misc_routes.py", "_chat_routes.py", "_personality_routes.py",
             "_users_routes.py", "_training_routes.py", "_tools_routes.py",
             "_safety_routes.py", "_clone_routes.py"]:
    src = open(os.path.join("api", name), encoding="utf-8").read()
    n = len(re.findall(r"@router\.(get|post|put|delete|patch)\(", src))
    print(f"  {name:30s}  {n:3d} endpoints")
    total += n
print(f"  {'TOTAL':30s}  {total:3d} endpoints (expected 71)")

print()
print("=== 5. 启动 app_factory 列出全部 /api/* 路由 ===")
try:
    from api.deps import deps
    deps.set_deps(orch=None, health=None, config=None, sessions=None, gf=None)
    app = app_factory.create_api_app()
    api_routes = []
    for route in app.routes:
        if hasattr(route, "path") and route.path.startswith("/api/"):
            methods = ",".join(sorted(route.methods)) if hasattr(route, "methods") else "?"
            api_routes.append(f"  [{methods:10s}] {route.path}")
    print(f"Total /api/* routes: {len(api_routes)}")
    for r in api_routes[:25]:
        print(r)
    if len(api_routes) > 25:
        print(f"  ... +{len(api_routes) - 25} more")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
