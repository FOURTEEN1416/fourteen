"""
工具 + 插件管理路由 — /api/tools/* + /api/plugins/*

来源：原 api.main_routes.py L352/482/507/1176/1189 共 5 端点

依赖：
- deps.orch._tools.registry — register/unregister/get
- deps.tool_history_mgr — 工具启停历史
- tools.tool_state — 工具/插件运行时开关唯一 owner（跨 worker + 重启持久）
- ToolToggleRequest 模型来自 api.main_routes

W6 缺陷 F/G 根治：
- 旧 GET /api/tools 只列启用项，禁用后刷新即从 UI 消失无法恢复；
  现返回统一库存 inventory（enabled/disabled/unavailable + 原因）。
- 旧开关只改本进程 registry（跨 worker 不生效、重启即恢复）；
  现写穿 tools.tool_state（data/runtime_switches.json），dispatch 侧
  以该状态为门，重启后依然拦截。
- 旧插件开关写 tracked 的 plugins/plugins.json 且全仓零消费者；
  现统一走 tool_state 并被 WeatherTool 真实消费。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import ToolToggleRequest
from tools import tool_state

logger = logging.getLogger("api.routers.tools_routes")

router = APIRouter(tags=["tools"])

# 已知插件目录（与 plugins/__init__.py 保持一致；新增插件在此登记）
_KNOWN_PLUGINS: dict[str, str] = {
    "weather": "天气查询（OpenWeatherMap + wttr.in 降级）",
}


# ═══════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════


def _declared_tool_names() -> list[str]:
    """配置里声明应注册的工具（注册失败/依赖缺失时它只存在于这个清单）。"""
    try:
        cfg = deps.config
        if cfg is not None and hasattr(cfg, "get_config_dict"):
            tools_cfg = cfg.get_config_dict().get("tools", {})
            declared = tools_cfg.get("builtin_tools", []) or []
            if isinstance(declared, list):
                return [str(x) for x in declared]
    except Exception:  # noqa: BLE001
        pass
    return []


@router.get("/api/tools")
async def tools_list(_auth: bool = Security(verify_api_key_dep)):
    """统一库存：enabled + disabled + unavailable 全量可见（G：刷新不丢入口）。"""
    orch = deps.orch
    registry = orch._tools.registry if orch and orch._tools else None

    enabled_names = list(registry.tool_names) if registry else []
    registry_disabled = list(registry.disabled_names) if registry else []
    state_disabled = tool_state.disabled_tools()
    declared = _declared_tool_names()

    seen: dict[str, str] = {}
    for name in [*enabled_names, *registry_disabled, *state_disabled, *declared]:
        seen.setdefault(str(name), "")

    inventory: list[dict[str, Any]] = []
    for name in seen:
        if registry and registry.get(name) is not None:
            if tool_state.is_tool_disabled(name):
                inventory.append({
                    "name": name,
                    "status": "disabled",
                    "reason": "运行时开关禁用（dispatch 已拦截）",
                    "permission_level": registry.get(name).permission_level,
                })
            else:
                inventory.append({
                    "name": name,
                    "status": "enabled",
                    "reason": "",
                    "permission_level": registry.get(name).permission_level,
                })
            continue
        disabled_inst = registry.get_disabled(name) if registry else None
        permission = disabled_inst.permission_level if disabled_inst else None
        if tool_state.is_tool_disabled(name) or name in registry_disabled:
            inventory.append({
                "name": name,
                "status": "disabled",
                "reason": "运行时开关禁用，可重新启用",
                "permission_level": permission,
            })
        elif name in declared:
            inventory.append({
                "name": name,
                "status": "unavailable",
                "reason": "配置已声明但未注册成功（依赖缺失或注册失败）",
                "permission_level": permission,
            })
        # 既未注册、未禁用、也未声明 → 不进库存（历史幽灵名不复活）

    return {"tools": enabled_names, "inventory": inventory}


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
    if req.enabled:
        # G：进程内没有实例时，可能只是本 worker 未注册但开关存在——
        # 只要开关状态里有它，清掉开关即视为启用（跨 worker 一致语义）
        if (
            registry.get(name) is None
            and not registry.reenable(name)
            and not tool_state.is_tool_disabled(name)
        ):
            raise HTTPException(404, f"Tool not found: {name}")
        tool_state.set_tool_disabled(name, False)
    else:
        if registry.get(name) is None and name not in registry.disabled_names:
            if not tool_state.is_tool_disabled(name):
                raise HTTPException(404, f"Tool not found: {name}")
        else:
            registry.unregister(name)
        tool_state.set_tool_disabled(name, True)
    deps.tool_history_mgr.append({
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "tool": name,
        "action": "enable" if req.enabled else "disable",
    })
    logger.info("Tool '%s' toggled: enabled=%s (persisted)", name, req.enabled)
    return {"status": "ok", "tool": name, "enabled": req.enabled, "persisted": True}


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
    """插件统一库存：已知目录 ∪ 状态库已有条目，含启停状态与说明。"""
    states = tool_state.plugin_states()
    plugins: dict[str, dict[str, Any]] = {}
    for name in sorted(set(_KNOWN_PLUGINS) | set(states)):
        enabled = tool_state.is_plugin_enabled(name)
        entry = states.get(name) if isinstance(states.get(name), dict) else {}
        plugins[name] = {
            "enabled": enabled,
            "status": "enabled" if enabled else "disabled",
            "description": _KNOWN_PLUGINS.get(name, ""),
            "toggled_at": entry.get("toggled_at"),
        }
    return {"plugins": plugins}


@router.post("/api/plugins/{name}/toggle")
async def toggle_plugin(
    name: str,
    enabled: bool = True,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    # 收口：只允许已知插件或状态库已有条目（旧实现对任意名字建垃圾条目）
    if name not in _KNOWN_PLUGINS and name not in tool_state.plugin_states():
        raise HTTPException(404, f"Unknown plugin: {name}")
    tool_state.set_plugin_enabled(name, enabled)
    logger.info("Plugin '%s' toggled: enabled=%s (persisted)", name, enabled)
    return {"status": "ok", "name": name, "enabled": enabled, "persisted": True}
