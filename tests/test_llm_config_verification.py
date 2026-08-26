"""
用户级 LLM 配置 — reinit 验证欠账补验（2026-08-26）

对应 `.trae/specs/reinit-multipoint-fixes/checklist.md` 未勾选验证项：
- SubTask 4.3: 迁移不影响现有用户登录
- SubTask 5.4: 用户 A 配置不影响用户 B
- SubTask 6.3: 保存→重新加载→生效全链路（API 层 + 缓存失效）
- SubTask 6.4: 非 admin 用户不再 403

端点实现：`api/routers/misc_routes.py` GET/POST `/api/user/llm-config`
权限模型：`api/auth_jwt.py` require_role("admin") → 非 admin 访问全局配置 403，
但用户级配置端点仅依赖 get_current_user_id，普通用户可读写自己的配置。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import app_factory, auth
from api.auth_jwt import create_access_token, verify_token, get_current_user_id
from api.database import get_db


# ─────────────────────────────────────────────────────
# 测试替身
# ─────────────────────────────────────────────────────


class FakeUser:
    """模拟 api.database.User 的最小面（llm_config JSON 字段 + role）。"""

    def __init__(self, uid: int, llm_config: dict | None = None, role: str = "user"):
        self.id = uid
        self.llm_config = llm_config
        self.role = role


class FakeResult:
    def __init__(self, user: FakeUser | None):
        self._user = user

    def scalar_one_or_none(self) -> FakeUser | None:
        return self._user


class FakeSession:
    """
    最小 AsyncSession 替身：
    - .get(User, uid)   → misc_routes 用户级配置读写用
    - .execute(select)  → require_role("admin") 用（永远查当前 uid）
    - .commit()         → 计数断言落库发生
    """

    def __init__(self, users: dict[int, FakeUser], current_uid: int):
        self._users = users
        self._current_uid = current_uid
        self.commit_count = 0

    async def get(self, _model, uid: int) -> FakeUser | None:
        return self._users.get(uid)

    async def execute(self, _stmt) -> FakeResult:
        return FakeResult(self._users.get(self._current_uid))

    async def commit(self) -> None:
        self.commit_count += 1


def _build_app(users: dict[int, FakeUser]):
    """构建被测 app：override API Key / 当前用户 / 数据库三层依赖。"""
    state = {"uid": 1}

    async def _fake_db():
        yield FakeSession(users, state["uid"])

    app = app_factory.create_api_app(orchestrator=MagicMock())
    app.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    app.dependency_overrides[get_current_user_id] = lambda: state["uid"]
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app), state


_LLM_A = {"provider": "zhipu", "model": "glm-4-flash", "api_key": "sk-userA-key", "temperature": 0.8}
_LLM_B = {"provider": "xunfei", "model": "spark-lite", "api_key": "sk-userB-key", "temperature": 0.9}


# ─────────────────────────────────────────────────────
# SubTask 4.3 — 迁移不影响现有用户登录
# ─────────────────────────────────────────────────────


def test_4_3_login_chain_unaffected_by_null_llm_config():
    """llm_config 为 NULL（迁移默认值）的用户：JWT 签发→解析→uid 提取全链路正常，
    用户级配置读取走回退分支不崩。"""
    users = {1: FakeUser(1, llm_config=None)}
    client, _state = _build_app(users)

    # 登录链路核心：签发 access token → 校验解析 → sub 即 uid
    token = create_access_token({"sub": "1"})
    payload = verify_token(token, expected_type="access")
    assert str(payload["sub"]) == "1"

    # 认证后读配置：无个人配置 → 回退全局（source=global 或 none），绝不 500
    resp = client.get(
        "/api/user/llm-config",
        headers={"Authorization": f"Bearer {token}", "X-API-Key": "dummy"},
    )
    assert resp.status_code == 200
    assert resp.json()["source"] in ("global", "none")


def test_4_3_non_string_sub_token_rejected():
    """安全边界：JWT 规范（RFC 7519）sub 必须为字符串；生产端 auth_routes 三处
    均以 str(user.id) 签发，非字符串 sub 的伪造 token 必须被 verify_token 拒绝，
    不得让 get_current_user_id 提取出数字 uid。"""
    from fastapi import HTTPException

    forged = create_access_token({"sub": 42})  # int 型 sub：非法令牌
    with pytest.raises(HTTPException) as exc_info:
        verify_token(forged)
    assert exc_info.value.status_code == 401

    # 对照组：合法字符串 sub 正常解析且 int() 提取无损
    legal = create_access_token({"sub": "42"})
    payload = verify_token(legal)
    assert int(payload["sub"]) == 42


# ─────────────────────────────────────────────────────
# SubTask 6.4 — 非 admin 不再 403
# ─────────────────────────────────────────────────────


def test_6_4_normal_user_can_read_and_write_own_config():
    """普通用户读写自己的 /api/user/llm-config 必须 200（修复前的 403 回归门）。"""
    users = {7: FakeUser(7)}
    client, state = _build_app(users)

    state["uid"] = 7
    save = client.post("/api/user/llm-config", json={"config": {"llm": _LLM_A}})
    assert save.status_code == 200, save.text
    assert save.json()["source"] == "user"

    load = client.get("/api/user/llm-config")
    assert load.status_code == 200
    body = load.json()
    assert body["source"] == "user"
    assert body["llm"]["provider"] == "zhipu"
    assert body["llm"]["api_key"] == "****", "API Key 必须脱敏"


def test_6_4_normal_user_still_forbidden_on_global_config():
    """普通用户写全局 /api/config 仍必须 403（admin-only 边界未被放宽）。"""
    users = {7: FakeUser(7, role="user")}
    client, state = _build_app(users)

    state["uid"] = 7
    resp = client.post("/api/config", json={"config": {"llm": {"provider": "auto"}}})
    assert resp.status_code == 403


def test_6_4_admin_can_write_global_config():
    """对照组：admin 角色写全局 /api/config 应放行（证明 403 来自角色而非环境故障）。"""
    users = {1: FakeUser(1, role="admin")}
    client, state = _build_app(users)

    state["uid"] = 1
    with patch("api.routers.misc_routes.deps") as mock_deps, \
         patch("api.routers.misc_routes.reconfigure_llm", new=MagicMock()):
        mock_cfg = MagicMock()
        mock_cfg.save.return_value = MagicMock(spec=[])  # 走 get_config_dict 分支
        mock_cfg.get_config_dict.return_value = {"llm": {"provider": "auto"}}
        mock_deps.config = mock_cfg
        resp = client.post("/api/config", json={"config": {"llm": {"provider": "auto"}}})
    assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────
# SubTask 5.4 — 用户 A 配置不影响用户 B
# ─────────────────────────────────────────────────────


def test_5_4_user_a_and_b_configs_are_isolated():
    """A/B 两用户各自保存配置后交叉回读，互不串扰（多用户隔离硬约束 L3 的配置面）。"""
    users = {1: FakeUser(1), 2: FakeUser(2)}
    client, state = _build_app(users)

    state["uid"] = 1
    assert client.post("/api/user/llm-config", json={"config": {"llm": _LLM_A}}).status_code == 200
    state["uid"] = 2
    assert client.post("/api/user/llm-config", json={"config": {"llm": _LLM_B}}).status_code == 200

    # 交叉回读
    state["uid"] = 1
    a = client.get("/api/user/llm-config").json()
    state["uid"] = 2
    b = client.get("/api/user/llm-config").json()

    assert a["llm"]["provider"] == "zhipu"
    assert b["llm"]["provider"] == "xunfei"
    assert a["llm"]["temperature"] == 0.8 and b["llm"]["temperature"] == 0.9
    # 存储对象层面确认隔离（各自 DB 行）
    assert users[1].llm_config["api_key"] == "sk-userA-key"
    assert users[2].llm_config["api_key"] == "sk-userB-key"


# ─────────────────────────────────────────────────────
# SubTask 6.3 — 保存→重载→生效全链路
# ─────────────────────────────────────────────────────


def test_6_3_empty_api_key_merges_previous_value():
    """前端「**** 占位时输入新值覆盖，空值保留原值」契约：
    空 api_key 提交必须保留原 key（misc_routes L331-334 合并逻辑）。"""
    users = {3: FakeUser(3)}
    client, state = _build_app(users)
    state["uid"] = 3

    client.post("/api/user/llm-config", json={"config": {"llm": _LLM_A}})
    # 二次提交：改 temperature 但不带 api_key
    resp = client.post(
        "/api/user/llm-config",
        json={"config": {"llm": {"provider": "zhipu", "temperature": 0.5}}},
    )
    assert resp.status_code == 200
    assert users[3].llm_config["api_key"] == "sk-userA-key", "空 key 必须继承原值"
    assert users[3].llm_config["temperature"] == 0.5
    # 回读仍脱敏
    assert client.get("/api/user/llm-config").json()["llm"]["api_key"] == "****"


def test_6_3_save_invalidates_user_llm_gateway_cache():
    """保存成功后必须调用 invalidate_user_llm(user_id)，下次对话按新配置重建 gateway。"""
    users = {9: FakeUser(9)}
    client, state = _build_app(users)
    state["uid"] = 9

    with patch("llm_provider.invalidate_user_llm") as mock_inv:
        resp = client.post("/api/user/llm-config", json={"config": {"llm": _LLM_B}})
    assert resp.status_code == 200
    mock_inv.assert_called_once_with(9)


def test_6_3_missing_llm_block_rejected_with_400():
    """缺 'llm' 块的请求体必须 400，不允许静默清空用户配置。"""
    users = {5: FakeUser(5)}
    client, state = _build_app(users)
    state["uid"] = 5

    resp = client.post("/api/user/llm-config", json={"config": {"voice": {}}})
    assert resp.status_code == 400
    assert users[5].llm_config is None, "失败请求不得改动既有配置"


def test_6_3_commit_persists_before_response():
    """响应 200 前必须完成 db.commit()（防「假成功」：内存改了没落库）。"""
    users = {11: FakeUser(11)}
    state = {"uid": 11}
    session_holder: dict = {}

    # 直接构建带捕获 session 的 app（不复用 _build_app，因其内部 state 不可注入）
    async def _capturing_db():
        sess = FakeSession(users, state["uid"])
        session_holder["sess"] = sess
        yield sess

    app = app_factory.create_api_app(orchestrator=MagicMock())
    app.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    app.dependency_overrides[get_current_user_id] = lambda: state["uid"]
    app.dependency_overrides[get_db] = _capturing_db
    client = TestClient(app)

    resp = client.post("/api/user/llm-config", json={"config": {"llm": _LLM_A}})
    assert resp.status_code == 200
    assert session_holder["sess"].commit_count >= 1
