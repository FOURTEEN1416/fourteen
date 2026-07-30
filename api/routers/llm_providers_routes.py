"""
LLM 供应商管理 API — 供应商清单 + 申请教程 + 启用/禁用

端点：
  GET    /api/llm-providers           普通用户：返回启用的供应商 + 特殊选项（含教程）
  GET    /api/llm-providers/all       admin：返回所有供应商（含禁用的）
  POST   /api/llm-providers           admin：添加新供应商
  PUT    /api/llm-providers/{key}     admin：更新供应商配置/教程
  PUT    /api/llm-providers/{key}/toggle  admin：启用/禁用供应商
  DELETE /api/llm-providers/{key}     admin：删除任意供应商（特殊选项除外）

真相源：config/llm_providers.json
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth_jwt import require_role
from api.database import User

logger = logging.getLogger("api.routers.llm_providers")

router = APIRouter(prefix="/api/llm-providers", tags=["llm-providers"])

# ── 配置文件路径 ──────────────────────────────
_CONFIG_PATH = Path("config/llm_providers.json")
_CONFIG_LOCK = threading.Lock()

# 预设供应商 key（仅作为元信息标记，前端显示"预设"徽章；不影响增删改）
_PRESET_KEYS = {"sensenova", "zhipu", "xunfei", "baidu", "deepseek"}
# 特殊选项 key（系统行为，不可删除/添加，只能改 guide）
_SPECIAL_KEYS = {"auto", "custom"}


# ═══════════════════════════════════════════════════
# 请求 / 响应模型
# ═══════════════════════════════════════════════════


class ProviderGuide(BaseModel):
    apply_url: str = ""
    free_quota: str = ""
    steps: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    """供应商完整配置（admin 编辑用）"""
    name: str = Field(..., max_length=100)
    model: str = Field("", max_length=100)
    api_base: str = Field("", max_length=500)
    api_key: str = Field("", description="空或 **** 表示保留原值")
    auth_mode: str = Field("bearer", pattern=r"^(bearer|oauth)$")
    max_tokens: int = Field(2048, ge=256, le=32768)
    temperature: float = Field(0.85, ge=0, le=2)
    stream_enabled: bool = True
    description: str = ""
    enabled: bool = True
    sort_order: int = Field(50, ge=0, le=999)
    guide: ProviderGuide = Field(default_factory=ProviderGuide)
    # 可选的额外参数（如 sensenova 的 reasoning_effort）
    extra_payload: dict[str, Any] | None = None


class ProviderCreateRequest(ProviderConfig):
    """创建新供应商"""
    key: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z][a-z0-9_]*$",
                     description="供应商唯一 key，小写字母+数字+下划线")


class ProviderToggleRequest(BaseModel):
    enabled: bool


# ═══════════════════════════════════════════════════
# 内部 Helper
# ═══════════════════════════════════════════════════


def _load_config() -> dict[str, Any]:
    """读取 llm_providers.json"""
    if not _CONFIG_PATH.exists():
        logger.warning("llm_providers.json not found at %s", _CONFIG_PATH.resolve())
        return {"providers": {}, "special_options": {}, "fallback_chain": []}
    try:
        with open(_CONFIG_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Failed to load llm_providers.json: %s", e)
        return {"providers": {}, "special_options": {}, "fallback_chain": []}


def _save_config(data: dict[str, Any]) -> None:
    """写入 llm_providers.json（线程安全）"""
    with _CONFIG_LOCK:
        # 写到临时文件再替换，避免写入中途崩溃
        tmp_path = _CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(_CONFIG_PATH)


def _sanitize_provider(key: str, cfg: dict[str, Any], is_special: bool = False) -> dict[str, Any]:
    """脱敏单个供应商配置（API Key 用 **** 替换）"""
    out = dict(cfg)
    out["key"] = key
    # 特殊选项没有 api_key 等字段
    if not is_special:
        if out.get("api_key"):
            out["api_key"] = "****"
    return out


def _build_provider_list(data: dict[str, Any], include_disabled: bool = False) -> list[dict[str, Any]]:
    """构建供应商列表（已排序，已脱敏）

    普通用户：返回 enabled=true 的真实供应商 + 所有 special_options
    admin：返回所有真实供应商 + 所有 special_options
    """
    providers = data.get("providers", {})
    special_options = data.get("special_options", {})

    result: list[dict[str, Any]] = []

    # 特殊选项（auto / custom）— 总是返回
    for key, cfg in special_options.items():
        item = _sanitize_provider(key, cfg, is_special=True)
        item["is_special"] = True
        item["is_preset"] = True
        result.append(item)

    # 真实供应商
    for key, cfg in providers.items():
        enabled = cfg.get("enabled", True)
        if not include_disabled and not enabled:
            continue
        item = _sanitize_provider(key, cfg, is_special=False)
        item["is_special"] = False
        item["is_preset"] = key in _PRESET_KEYS
        result.append(item)

    # 按 sort_order 升序排序
    result.sort(key=lambda x: x.get("sort_order", 50))
    return result


# ═══════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════


@router.get("")
async def list_providers():
    """普通用户：返回启用的供应商 + 所有特殊选项（含申请教程）

    无需登录即可读取（供应商清单是公开信息，便于用户选择）。
    """
    data = _load_config()
    return {
        "providers": _build_provider_list(data, include_disabled=False),
        "default_provider": data.get("default_provider", "auto"),
    }


@router.get("/all")
async def list_all_providers(
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """admin：返回所有供应商（含禁用的）"""
    data = _load_config()
    return {
        "providers": _build_provider_list(data, include_disabled=True),
        "default_provider": data.get("default_provider", "auto"),
        "fallback_chain": data.get("fallback_chain", []),
    }


@router.post("")
async def create_provider(
    req: ProviderCreateRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """admin：添加新供应商

    - key 必须为小写字母+数字+下划线，且不能与特殊选项冲突
    - 若预设供应商已被删除，允许用相同 key 重新添加（按当前配置对待）
    - 新供应商默认 enabled=true
    """
    key = req.key
    if key in _SPECIAL_KEYS:
        raise HTTPException(409, f"Provider key '{key}' is reserved as special option")

    data = _load_config()
    providers = data.setdefault("providers", {})
    if key in providers:
        raise HTTPException(409, f"Provider '{key}' already exists")

    # 构建配置（不存 api_key 为空字符串时保留原值逻辑，新供应商直接存）
    new_cfg: dict[str, Any] = {
        "name": req.name,
        "model": req.model,
        "api_base": req.api_base,
        "api_key": req.api_key or "",
        "auth_mode": req.auth_mode,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "stream_enabled": req.stream_enabled,
        "description": req.description,
        "enabled": req.enabled,
        "sort_order": req.sort_order,
        "guide": req.guide.model_dump(),
    }
    if req.extra_payload:
        new_cfg["extra_payload"] = req.extra_payload

    providers[key] = new_cfg
    _save_config(data)

    logger.info("Admin created provider: %s (%s)", key, req.name)
    return {"detail": f"Provider '{key}' created", "provider": _sanitize_provider(key, new_cfg)}


@router.put("/{key}")
async def update_provider(
    key: str,
    req: ProviderConfig,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """admin：更新供应商配置 + 教程

    - 预设供应商和自定义供应商都可更新
    - api_key 为空或 '****' 时保留原值
    - 特殊选项（auto/custom）只能改 guide，不能改 api_base 等
    """
    data = _load_config()

    is_special = key in _SPECIAL_KEYS
    if is_special:
        target = data.get("special_options", {}).get(key)
        if target is None:
            raise HTTPException(404, f"Special option '{key}' not found")
        # 特殊选项只更新 guide（其他字段如 name/description 也允许更新）
        target["name"] = req.name
        target["description"] = req.description
        target["sort_order"] = req.sort_order
        target["guide"] = req.guide.model_dump()
        _save_config(data)
        logger.info("Admin updated special option: %s", key)
        return {"detail": f"Special option '{key}' updated", "provider": _sanitize_provider(key, target, is_special=True)}

    providers = data.get("providers", {})
    if key not in providers:
        raise HTTPException(404, f"Provider '{key}' not found")

    cfg = providers[key]
    # 保留原 api_key（如果新值为空或 ****）
    existing_api_key = cfg.get("api_key", "")
    new_api_key = req.api_key
    if not new_api_key or new_api_key == "****":
        new_api_key = existing_api_key

    cfg.update({
        "name": req.name,
        "model": req.model,
        "api_base": req.api_base,
        "api_key": new_api_key,
        "auth_mode": req.auth_mode,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "stream_enabled": req.stream_enabled,
        "description": req.description,
        "enabled": req.enabled,
        "sort_order": req.sort_order,
        "guide": req.guide.model_dump(),
    })
    if req.extra_payload is not None:
        cfg["extra_payload"] = req.extra_payload
    elif "extra_payload" in cfg:
        # 不强制清除已有的 extra_payload
        pass

    _save_config(data)
    logger.info("Admin updated provider: %s", key)
    return {"detail": f"Provider '{key}' updated", "provider": _sanitize_provider(key, cfg)}


@router.put("/{key}/toggle")
async def toggle_provider(
    key: str,
    req: ProviderToggleRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """admin：启用/禁用供应商（预设供应商不可删除，只能 toggle）"""
    data = _load_config()
    providers = data.get("providers", {})
    if key not in providers:
        raise HTTPException(404, f"Provider '{key}' not found")

    providers[key]["enabled"] = req.enabled
    _save_config(data)
    logger.info("Admin toggled provider %s -> enabled=%s", key, req.enabled)
    return {"detail": f"Provider '{key}' {'enabled' if req.enabled else 'disabled'}", "enabled": req.enabled}


@router.delete("/{key}")
async def delete_provider(
    key: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """admin：删除任意供应商

    - 预设供应商（sensenova/zhipu/xunfei/baidu/deepseek）也可删除，删除后可重新添加
    - 特殊选项（auto/custom）不可删除（系统行为）
    - 删除时自动从 fallback_chain 移除
    """
    if key in _SPECIAL_KEYS:
        raise HTTPException(400, f"Cannot delete special option '{key}'.")

    data = _load_config()
    providers = data.get("providers", {})
    if key not in providers:
        raise HTTPException(404, f"Provider '{key}' not found")

    del providers[key]
    # 从 fallback_chain 里移除（如果存在）
    chain = data.get("fallback_chain", [])
    if key in chain:
        data["fallback_chain"] = [k for k in chain if k != key]

    _save_config(data)
    logger.info("Admin deleted provider: %s", key)
    return {"detail": f"Provider '{key}' deleted"}
