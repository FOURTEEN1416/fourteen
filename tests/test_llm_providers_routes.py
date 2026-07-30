"""
LLM 供应商管理 API 测试

覆盖：
- GET /api/llm-providers（普通用户可读，返回启用的 + 特殊选项）
- GET /api/llm-providers/all（admin only，含禁用的）
- POST /api/llm-providers（admin 添加新供应商）
- PUT /api/llm-providers/{key}（admin 更新配置/教程）
- PUT /api/llm-providers/{key}/toggle（admin 启用/禁用）
- DELETE /api/llm-providers/{key}（admin 删除任意供应商，特殊选项除外）
- api_key 脱敏 + 保留原值逻辑
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

from api.app_factory import create_api_app
from api.auth_jwt import get_current_user_id, hash_password
from api.database import Base, User, get_db

# ── 测试用户 ID ──
_ADMIN_USER_ID = 1
_VIEWER_USER_ID = 2


# ═══════════════════════════════════════════════════════════
# 测试用配置文件（最小化，避免污染真实 config/llm_providers.json）
# ═══════════════════════════════════════════════════════════

_TEST_CONFIG = {
    "default_provider": "auto",
    "providers": {
        "sensenova": {
            "name": "商汤日日新",
            "model": "glm-5.2",
            "api_base": "https://token.sensenova.cn/v1",
            "api_key": "sk-test-secret-key",
            "auth_mode": "bearer",
            "max_tokens": 8192,
            "temperature": 0.85,
            "stream_enabled": True,
            "description": "测试用商汤",
            "enabled": True,
            "sort_order": 1,
            "guide": {
                "apply_url": "https://platform.sensenova.cn",
                "free_quota": "100万 Token",
                "steps": ["步骤1", "步骤2"],
                "tips": ["提示1"],
                "warnings": [],
            },
        },
        "deepseek": {
            "name": "DeepSeek",
            "model": "deepseek-chat",
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "",
            "auth_mode": "bearer",
            "max_tokens": 4096,
            "temperature": 0.85,
            "stream_enabled": True,
            "description": "付费",
            "enabled": False,  # 禁用
            "sort_order": 5,
            "guide": {
                "apply_url": "https://platform.deepseek.com",
                "free_quota": "无",
                "steps": ["步骤1"],
                "tips": [],
                "warnings": ["付费"],
            },
        },
    },
    "special_options": {
        "auto": {
            "name": "自动回退",
            "description": "推荐",
            "sort_order": 0,
            "guide": {"apply_url": "", "free_quota": "免费", "steps": [], "tips": [], "warnings": []},
        },
        "custom": {
            "name": "自定义",
            "description": "OpenAI 兼容",
            "sort_order": 98,
            "guide": {"apply_url": "", "free_quota": "取决于", "steps": [], "tips": [], "warnings": []},
        },
    },
    "fallback_chain": ["sensenova"],
}


def _build_app_with_users(tmp_db_path: str):
    """创建测试用 FastAPI 实例 + engine，预建 admin 和 viewer 用户"""
    db_url = f"sqlite+aiosqlite:///{tmp_db_path}"
    engine = create_async_engine(db_url, echo=False)
    test_session = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with test_session() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with test_session() as session:
            admin = User(
                id=_ADMIN_USER_ID,
                email="admin@test.com",
                username="admin",
                hashed_password=hash_password("admin123"),
                display_name="Admin",
                role="admin",
                is_active=True,
                is_verified=True,
            )
            session.add(admin)
            viewer = User(
                id=_VIEWER_USER_ID,
                email="viewer@test.com",
                username="viewer",
                hashed_password=hash_password("viewer123"),
                display_name="Viewer",
                role="viewer",
                is_active=True,
                is_verified=True,
            )
            session.add(viewer)
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    return app, engine, test_session


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def admin_app(tmp_path, monkeypatch):
    """admin 身份 + 临时配置文件"""
    tmp_config = tmp_path / "llm_providers.json"
    tmp_config.write_text(json.dumps(_TEST_CONFIG, ensure_ascii=False), encoding="utf-8")

    from api.routers import llm_providers_routes
    monkeypatch.setattr(llm_providers_routes, "_CONFIG_PATH", tmp_config)

    db_path = str(tmp_path / "test_providers.db")
    app, engine, _ = _build_app_with_users(db_path)
    app.dependency_overrides[get_current_user_id] = lambda: _ADMIN_USER_ID

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, engine, app, tmp_config
    asyncio.run(engine.dispose())


@pytest.fixture
def viewer_app(tmp_path, monkeypatch):
    """viewer 身份 + 临时配置文件"""
    tmp_config = tmp_path / "llm_providers.json"
    tmp_config.write_text(json.dumps(_TEST_CONFIG, ensure_ascii=False), encoding="utf-8")

    from api.routers import llm_providers_routes
    monkeypatch.setattr(llm_providers_routes, "_CONFIG_PATH", tmp_config)

    db_path = str(tmp_path / "test_providers_viewer.db")
    app, engine, _ = _build_app_with_users(db_path)
    app.dependency_overrides[get_current_user_id] = lambda: _VIEWER_USER_ID

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, engine, app, tmp_config
    asyncio.run(engine.dispose())


# ═══════════════════════════════════════════════════════════
# Tests: GET /api/llm-providers
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_providers_no_auth_required(viewer_app):
    """普通用户可读取启用供应商清单 + 特殊选项"""
    client, _, _, _ = viewer_app
    resp = await client.get("/api/llm-providers")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "providers" in body
    assert body["default_provider"] == "auto"

    keys = [p["key"] for p in body["providers"]]
    # auto + custom 是特殊选项，总是返回
    assert "auto" in keys
    assert "custom" in keys
    # sensenova enabled=true，应返回
    assert "sensenova" in keys
    # deepseek enabled=false，不应返回给普通用户
    assert "deepseek" not in keys


@pytest.mark.asyncio
async def test_list_providers_api_key_masked(viewer_app):
    """API Key 必须脱敏为 ****"""
    client, _, _, _ = viewer_app
    resp = await client.get("/api/llm-providers")
    body = resp.json()
    for p in body["providers"]:
        if not p.get("is_special") and p.get("api_key"):
            assert p["api_key"] == "****", f"Provider {p['key']} api_key not masked"


@pytest.mark.asyncio
async def test_list_providers_sorted(viewer_app):
    """供应商按 sort_order 升序排序"""
    client, _, _, _ = viewer_app
    resp = await client.get("/api/llm-providers")
    body = resp.json()
    orders = [p.get("sort_order", 50) for p in body["providers"]]
    assert orders == sorted(orders), f"Not sorted: {orders}"


@pytest.mark.asyncio
async def test_list_providers_includes_guide(viewer_app):
    """返回结果包含 guide 字段（申请教程）"""
    client, _, _, _ = viewer_app
    resp = await client.get("/api/llm-providers")
    body = resp.json()
    sensenova = next(p for p in body["providers"] if p["key"] == "sensenova")
    assert "guide" in sensenova
    assert sensenova["guide"]["apply_url"] == "https://platform.sensenova.cn"
    assert len(sensenova["guide"]["steps"]) >= 2


# ═══════════════════════════════════════════════════════════
# Tests: GET /api/llm-providers/all
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_all_providers_admin_only(viewer_app):
    """非 admin 访问 /all 返回 403"""
    client, _, _, _ = viewer_app
    resp = await client.get("/api/llm-providers/all")
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_list_all_providers_includes_disabled(admin_app):
    """admin 能看到所有供应商（含禁用的）"""
    client, _, _, _ = admin_app
    resp = await client.get("/api/llm-providers/all")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = [p["key"] for p in body["providers"]]
    assert "sensenova" in keys
    assert "deepseek" in keys  # 禁用的也返回
    assert "fallback_chain" in body


# ═══════════════════════════════════════════════════════════
# Tests: POST /api/llm-providers
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_provider_admin_only(viewer_app):
    """非 admin 创建供应商返回 403"""
    client, _, _, _ = viewer_app
    resp = await client.post("/api/llm-providers", json={
        "key": "groq", "name": "Groq", "model": "llama3", "api_base": "https://api.groq.com/v1",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_provider_success(admin_app):
    """admin 创建新供应商成功"""
    client, _, _, tmp_config = admin_app
    resp = await client.post("/api/llm-providers", json={
        "key": "groq",
        "name": "Groq",
        "model": "llama3-70b",
        "api_base": "https://api.groq.com/openai/v1",
        "api_key": "gsk_test_key",
        "auth_mode": "bearer",
        "max_tokens": 4096,
        "temperature": 0.7,
        "stream_enabled": True,
        "description": "Groq 高速推理",
        "enabled": True,
        "sort_order": 10,
        "guide": {
            "apply_url": "https://console.groq.com",
            "free_quota": "免费",
            "steps": ["步骤1"],
            "tips": [],
            "warnings": [],
        },
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detail"] == "Provider 'groq' created"
    assert body["provider"]["api_key"] == "****"  # 脱敏

    # 验证已写入配置文件
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert "groq" in cfg["providers"]
    assert cfg["providers"]["groq"]["api_key"] == "gsk_test_key"  # 原文存储


@pytest.mark.asyncio
async def test_create_provider_duplicate_key_fails(admin_app):
    """创建已存在的 key 失败（sensenova 已在配置中）"""
    client, _, _, _ = admin_app
    resp = await client.post("/api/llm-providers", json={
        "key": "sensenova", "name": "重复", "model": "x", "api_base": "x",
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_provider_reserved_special_key_fails(admin_app):
    """创建与特殊选项 key 冲突的供应商失败"""
    client, _, _, _ = admin_app
    resp = await client.post("/api/llm-providers", json={
        "key": "auto", "name": "重复", "model": "x", "api_base": "x",
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_provider_reuses_deleted_preset_key(admin_app):
    """删除预设后，可用相同 key 重新添加（按当前配置对待）"""
    client, _, _, tmp_config = admin_app
    # 先删除 sensenova
    resp = await client.delete("/api/llm-providers/sensenova")
    assert resp.status_code == 200
    # 用相同 key 重新创建
    resp = await client.post("/api/llm-providers", json={
        "key": "sensenova", "name": "新商汤", "model": "glm-5.2",
        "api_base": "https://token.sensenova.cn/v1", "api_key": "sk-new",
        "auth_mode": "bearer", "max_tokens": 8192, "temperature": 0.85,
        "stream_enabled": True, "description": "重新添加", "enabled": True, "sort_order": 1,
        "guide": {"apply_url": "", "free_quota": "", "steps": [], "tips": [], "warnings": []},
    })
    assert resp.status_code == 200, resp.text
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert cfg["providers"]["sensenova"]["name"] == "新商汤"


@pytest.mark.asyncio
async def test_create_provider_invalid_key_fails(admin_app):
    """key 格式非法（大写/特殊字符）失败"""
    client, _, _, _ = admin_app
    resp = await client.post("/api/llm-providers", json={
        "key": "Invalid-Key", "name": "test", "model": "x", "api_base": "x",
    })
    assert resp.status_code == 422  # Pydantic 校验失败


# ═══════════════════════════════════════════════════════════
# Tests: PUT /api/llm-providers/{key}
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_provider_preserves_api_key_when_empty(admin_app):
    """更新时 api_key 为空字符串，保留原值"""
    client, _, _, tmp_config = admin_app
    # 原值：sk-test-secret-key
    resp = await client.put("/api/llm-providers/sensenova", json={
        "name": "商汤日日新（更新）",
        "model": "glm-5.2",
        "api_base": "https://token.sensenova.cn/v1",
        "api_key": "",  # 空 → 保留原值
        "auth_mode": "bearer",
        "max_tokens": 8192,
        "temperature": 0.85,
        "stream_enabled": True,
        "description": "更新后",
        "enabled": True,
        "sort_order": 1,
        "guide": {
            "apply_url": "https://platform.sensenova.cn",
            "free_quota": "更新",
            "steps": ["新步骤"],
            "tips": [],
            "warnings": [],
        },
    })
    assert resp.status_code == 200, resp.text
    # 验证配置文件中原 api_key 仍存在
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert cfg["providers"]["sensenova"]["api_key"] == "sk-test-secret-key"


@pytest.mark.asyncio
async def test_update_provider_preserves_api_key_when_masked(admin_app):
    """更新时 api_key 为 ****，保留原值"""
    client, _, _, tmp_config = admin_app
    resp = await client.put("/api/llm-providers/sensenova", json={
        "name": "商汤",
        "model": "glm-5.2",
        "api_base": "https://token.sensenova.cn/v1",
        "api_key": "****",  # 脱敏占位 → 保留原值
        "auth_mode": "bearer",
        "max_tokens": 8192,
        "temperature": 0.85,
        "stream_enabled": True,
        "description": "test",
        "enabled": True,
        "sort_order": 1,
        "guide": {"apply_url": "", "free_quota": "", "steps": [], "tips": [], "warnings": []},
    })
    assert resp.status_code == 200, resp.text
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert cfg["providers"]["sensenova"]["api_key"] == "sk-test-secret-key"


@pytest.mark.asyncio
async def test_update_provider_updates_api_key_when_new_value(admin_app):
    """更新时 api_key 为新值，覆盖原值"""
    client, _, _, tmp_config = admin_app
    resp = await client.put("/api/llm-providers/sensenova", json={
        "name": "商汤",
        "model": "glm-5.2",
        "api_base": "https://token.sensenova.cn/v1",
        "api_key": "sk-new-key-123",
        "auth_mode": "bearer",
        "max_tokens": 8192,
        "temperature": 0.85,
        "stream_enabled": True,
        "description": "test",
        "enabled": True,
        "sort_order": 1,
        "guide": {"apply_url": "", "free_quota": "", "steps": [], "tips": [], "warnings": []},
    })
    assert resp.status_code == 200, resp.text
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert cfg["providers"]["sensenova"]["api_key"] == "sk-new-key-123"


@pytest.mark.asyncio
async def test_update_provider_not_found(admin_app):
    """更新不存在的供应商返回 404"""
    client, _, _, _ = admin_app
    resp = await client.put("/api/llm-providers/nonexistent", json={
        "name": "x", "model": "x", "api_base": "x", "api_key": "x",
        "auth_mode": "bearer", "max_tokens": 2048, "temperature": 0.85,
        "stream_enabled": True, "description": "x", "enabled": True, "sort_order": 1,
        "guide": {"apply_url": "", "free_quota": "", "steps": [], "tips": [], "warnings": []},
    })
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# Tests: PUT /api/llm-providers/{key}/toggle
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_toggle_provider_admin_only(viewer_app):
    """非 admin 启用/禁用供应商返回 403"""
    client, _, _, _ = viewer_app
    resp = await client.put("/api/llm-providers/sensenova/toggle", json={"enabled": False})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_toggle_provider_success(admin_app):
    """admin 启用/禁用供应商成功"""
    client, _, _, tmp_config = admin_app
    # 禁用 sensenova
    resp = await client.put("/api/llm-providers/sensenova/toggle", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False

    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert cfg["providers"]["sensenova"]["enabled"] is False

    # 重新启用
    resp = await client.put("/api/llm-providers/sensenova/toggle", json={"enabled": True})
    assert resp.status_code == 200
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert cfg["providers"]["sensenova"]["enabled"] is True


@pytest.mark.asyncio
async def test_toggle_provider_not_found(admin_app):
    """启用/禁用不存在的供应商返回 404"""
    client, _, _, _ = admin_app
    resp = await client.put("/api/llm-providers/nonexistent/toggle", json={"enabled": False})
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# Tests: DELETE /api/llm-providers/{key}
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_provider_admin_only(viewer_app):
    """非 admin 删除供应商返回 403"""
    client, _, _, _ = viewer_app
    resp = await client.delete("/api/llm-providers/groq")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_preset_provider_success(admin_app):
    """admin 可删除预设供应商（删除后从 fallback_chain 移除）"""
    client, _, _, tmp_config = admin_app
    resp = await client.delete("/api/llm-providers/sensenova")
    assert resp.status_code == 200, resp.text
    # 验证已从配置文件移除
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert "sensenova" not in cfg["providers"]
    # fallback_chain 应同步移除
    assert "sensenova" not in cfg.get("fallback_chain", [])


@pytest.mark.asyncio
async def test_delete_special_option_fails(admin_app):
    """删除特殊选项失败（400）"""
    client, _, _, _ = admin_app
    resp = await client.delete("/api/llm-providers/auto")
    assert resp.status_code == 400
    assert "special" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_custom_provider_success(admin_app):
    """admin 删除自定义供应商成功"""
    client, _, _, tmp_config = admin_app
    # 先创建一个自定义供应商
    await client.post("/api/llm-providers", json={
        "key": "groq", "name": "Groq", "model": "llama3",
        "api_base": "https://api.groq.com/v1", "api_key": "gsk_x",
        "auth_mode": "bearer", "max_tokens": 4096, "temperature": 0.7,
        "stream_enabled": True, "description": "test", "enabled": True, "sort_order": 10,
        "guide": {"apply_url": "", "free_quota": "", "steps": [], "tips": [], "warnings": []},
    })

    # 删除
    resp = await client.delete("/api/llm-providers/groq")
    assert resp.status_code == 200, resp.text

    # 验证已从配置文件移除
    cfg = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert "groq" not in cfg["providers"]


@pytest.mark.asyncio
async def test_delete_provider_not_found(admin_app):
    """删除不存在的供应商返回 404"""
    client, _, _, _ = admin_app
    resp = await client.delete("/api/llm-providers/nonexistent")
    assert resp.status_code == 404
