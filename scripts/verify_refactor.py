"""重构验证脚本：8 子路由拆分后静态检查"""
import os
import re
import sys
import io

# Force UTF-8 stdout (Windows console is GBK by default)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJ = r"C:\Users\FOUR\Desktop\ai-girlfriend"
os.chdir(PROJ)
sys.path.insert(0, PROJ)

print("=== 1. 静态 import 检查（8 个新子路由 + app_factory）===")
from api import _misc_routes, _chat_routes, _personality_routes, _users_routes
from api import _training_routes, _tools_routes, _safety_routes, _clone_routes
print("  8 个新子路由 import OK")
from api import app_factory
print("  app_factory import OK")
print()

print("=== 2. main_routes.py 缩身后仍可导出全部 6 个模型 ===")
from api.main_routes import (
    ChatRequest, ChatResponse, EmotionStateResponse,
    CreateSessionRequest, ConfigUpdateRequest, ProactiveConfigRequest,
    ToolToggleRequest, _sanitize_config,
    SENSITIVE_FIELDS, UPLOAD_DIR, MAX_UPLOAD_SIZE, MAX_RAG_UPLOAD_SIZE,
)
print("  6 个模型 + Helper + 4 常量 全部导出 OK")
print()

print("=== 3. main_routes.py 缩身后 端点应为 0（全部移走）===")
src = open('api/main_routes.py', encoding='utf-8').read()
endpoints = re.findall(r'@router\.(get|post|put|delete|patch)\(', src)
print(f"  main_routes.py 残余端点数: {len(endpoints)}（期望 0）")
print()

print("=== 4. 8 个新子路由端点分布 ===")
total = 0
for name in ['_misc_routes.py', '_chat_routes.py', '_personality_routes.py',
             '_users_routes.py', '_training_routes.py', '_tools_routes.py',
             '_safety_routes.py', '_clone_routes.py']:
    src = open(os.path.join('api', name), encoding='utf-8').read()
    n = len(re.findall(r'@router\.(get|post|put|delete|patch)\(', src))
    print(f"  {name:30s}  {n:3d} 端点")
    total += n
print(f"  {'合计':30s}  {total:3d} 端点（期望 71）")
print()

print("=== 5. 端点去重检查（按路径+方法）===")
all_paths = set()
all_methods_paths = set()
duplicates = []
for name in ['_misc_routes.py', '_chat_routes.py', '_personality_routes.py',
             '_users_routes.py', '_training_routes.py', '_tools_routes.py',
             '_safety_routes.py', '_clone_routes.py']:
    src = open(os.path.join('api', name), encoding='utf-8').read()
    matches = re.findall(r'@router\.(get|post|put|delete|patch)\(\s*"([^"]+)"', src)
    for method, path in matches:
        key = f"{method.upper()}:{path}"
        if key in all_methods_paths:
            duplicates.append((name, method, path))
        all_methods_paths.add(key)
        all_paths.add(path)

print(f"  唯一端点 (method+path): {len(all_methods_paths)}")
print(f"  唯一路径:               {len(all_paths)}")
if duplicates:
    print(f"  [WARN] found {len(duplicates)} duplicate endpoints:")
    for d in duplicates[:5]:
        print(f"    {d}")
else:
    print("  [OK] no duplicate endpoints")
print()

print("=== 6. app_factory 实际可创建 + 8 个子路由贡献 71 端点 ===")
try:
    app = app_factory.create_api_app()

    # Build set of (method, path) for all /api/* routes in the app
    app_routes = set()
    for r in app.routes:
        if hasattr(r, "path") and hasattr(r, "methods") and r.path.startswith("/api/"):
            for m in r.methods:
                if m != "HEAD":
                    app_routes.add((m, r.path))

    # Count routes from each of the 8 new sub-routers
    sub_router_modules = [
        _misc_routes, _chat_routes, _personality_routes, _users_routes,
        _training_routes, _tools_routes, _safety_routes, _clone_routes,
    ]
    missing = []
    found = 0
    for mod in sub_router_modules:
        for r in mod.router.routes:
            if hasattr(r, "path") and hasattr(r, "methods") and r.path.startswith("/api/"):
                for m in r.methods:
                    if m == "HEAD":
                        continue
                    if (m, r.path) in app_routes:
                        found += 1
                    else:
                        missing.append((m, r.path, mod.__name__))

    total_api = len([r for r in app.routes
                     if hasattr(r, "path") and r.path.startswith("/api/")])
    print(f"  total /api/* routes in app: {total_api}")
    print(f"  8 sub-routers contributed:   {found} / 71")
    if missing:
        print(f"  [FAIL] {len(missing)} sub-router endpoints not mounted:")
        for m, p, src in missing[:5]:
            print(f"    {m:6s} {p:50s} (from {src})")
        sys.exit(1)
    if found == 71:
        print(f"  [OK] all 71 endpoints from 8 sub-routers are mounted")
    else:
        print(f"  [FAIL] expected 71, got {found}")
        sys.exit(1)
except SystemExit:
    raise
except Exception as e:
    print(f"  [FAIL] create_api_app failed: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
