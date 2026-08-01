"""
工具 + 插件管理路由 — /api/tools/* + /api/plugins/*

来源：原 api.main_routes.py L352/482/507/1176/1189 共 5 端点

依赖：
- deps.orch._tools.registry — register/unregister/get
- deps.tool_history_mgr — 工具启停历史
- ToolToggleRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import ToolToggleRequest

logger = logging.getLogger("api.routers.tools_routes")

router = APIRouter(tags=["tools"])


# ═══════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════


@router.get("/api/tools")
async def tools_list(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._tools:
        return {"tools": orch._tools.registry.tool_names}
    return {"tools": []}


@router.get("/api/tools/health")
async def tools_health(_auth: bool = Security(verify_api_key_dep)):
    """返回所有注册工具的真实可用性（含错误原因）。"""
    orch = deps.orch
    if not orch or not orch._tools or not orch._tools.registry:
        return {"available": False, "tools": {}, "total": 0, "online": 0}

    health = orch._tools.registry.health_check_all()
    online = sum(1 for v in health.values() if v.get("available"))
    return {
        "available": True,
        "tools": health,
        "total": len(health),
        "online": online,
    }


@router.post("/api/tools/{name}/toggle")
async def toggle_tool(
    name: str,
    req: ToolToggleRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    orch = deps.orch
    if not orch or not orch._tools or not orch._tools.registry:
        raise HTTPException(503, "Tool system not initialized")
    registry = orch._tools.registry
    tool = registry.get(name)
    if not tool:
        raise HTTPException(404, f"Tool not found: {name}")
    if req.enabled:
        registry.register(tool)
    else:
        registry.unregister(name)
    deps.tool_history_mgr.append({
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "tool": name,
        "action": "enable" if req.enabled else "disable",
    })
    logger.info("Tool '%s' toggled: enabled=%s", name, req.enabled)
    return {"status": "ok", "tool": name, "enabled": req.enabled}


@router.get("/api/tools/history")
async def tool_history(
    limit: int = Query(default=50, le=200),
    _auth: bool = Security(verify_api_key_dep),
):
    return {"history": deps.tool_history_mgr.get_recent(limit)}


# ═══════════════════════════════════════════════════════
# Plugins
# ═══════════════════════════════════════════════════════


@router.get("/api/plugins")
async def list_plugins(_auth: bool = Security(verify_api_key_dep)):
    try:
        plugin_path = Path(__file__).parent.parent.parent / "plugins" / "plugins.json"
        if plugin_path.exists():
            with open(plugin_path, encoding="utf-8") as f:
                data = json.load(f)
            return {"plugins": data.get("plugins", {})}
    except Exception as e:
        logger.debug("Failed to load plugins config: %s", e)
    return {"plugins": {}}


@router.post("/api/plugins/{name}/toggle")
async def toggle_plugin(
    name: str,
    enabled: bool = True,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    # 必须与 list_plugins 保持一致：3 个 parent 才能到达项目根目录
    plugin_path = Path(__file__).parent.parent.parent / "plugins" / "plugins.json"
    data = {}
    if plugin_path.exists():
        with open(plugin_path, encoding="utf-8") as f:
            data = json.load(f)
    plugins = data.get("plugins", {})
    if name not in plugins:
        plugins[name] = {}
    plugins[name]["enabled"] = enabled
    plugins[name]["toggled_at"] = datetime.now(tz=timezone.utc).isoformat()
    data["plugins"] = plugins
    with open(plugin_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "name": name, "enabled": enabled}
