"""P1 批5 · api/工具链修复回归（全量审查项 32–41）。

item32 dashboard 真源 / item33 删用户级联 / item34 persona PUT 缓存失效 /
item35 工具停用后可再启用 / item36 训练 apply/test 真执行 /
item37 activate 不再写损 / item38 persona-card 双写 / item39 日志 user_id /
item40 邀请码原子消费 / item41 _meta 归属与 SSRF
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket

import pytest

from tools.base_tool import BaseTool, ToolRegistry, ToolResult


class _DummyTool(BaseTool):
    name = "dummy_toggle"
    description = "d"

    def execute(self, **kwargs):
        return ToolResult(True, data="ok")


# ═════════════════ item35：停用实例保活 ═════════════════


def test_registry_unregister_keeps_instance_and_reenable_roundtrip() -> None:
    reg = ToolRegistry()
    tool = _DummyTool()
    reg.register(tool)
    reg.unregister("dummy_toggle")
    assert reg.get("dummy_toggle") is None
    assert "dummy_toggle" in reg.disabled_names
    assert reg.tool_names == []
    assert reg.reenable("dummy_toggle") is True
    assert reg.get("dummy_toggle") is tool
    assert reg.disabled_names == []
    assert reg.reenable("never_existed") is False


def test_registry_register_overwrites_disabled_stash() -> None:
    reg = ToolRegistry()
    t = _DummyTool()
    reg.register(t)
    reg.unregister("dummy_toggle")
    reg.register(t)
    assert reg.disabled_names == []


def test_toggle_tool_disable_then_enable_roundtrip(monkeypatch) -> None:
    from fastapi import HTTPException

    from api.deps import deps
    from api.main_routes import ToolToggleRequest
    from api.routers import tools_routes

    reg = ToolRegistry()
    reg.register(_DummyTool())
    fake_orch = type("O", (), {"_tools": type("T", (), {"registry": reg})()})()
    hist: list = []
    monkeypatch.setattr(deps, "orch", fake_orch)
    monkeypatch.setattr(
        deps, "tool_history_mgr",
        type("H", (), {"append": staticmethod(lambda e: hist.append(e))})(),
        raising=False,
    )

    r1 = asyncio.run(tools_routes.toggle_tool(
        "dummy_toggle", ToolToggleRequest(enabled=False), True, (1, None)))
    assert r1["enabled"] is False
    assert reg.get("dummy_toggle") is None
    # 旧缺陷：再启用时 registry.get 拿不到被 pop 的实例 → 永远 404
    r2 = asyncio.run(tools_routes.toggle_tool(
        "dummy_toggle", ToolToggleRequest(enabled=True), True, (1, None)))
    assert r2["enabled"] is True
    assert reg.get("dummy_toggle") is not None
    with pytest.raises(HTTPException):
        asyncio.run(tools_routes.toggle_tool(
            "ghost_tool", ToolToggleRequest(enabled=True), True, (1, None)))


# ═════════════════ item36：训练 apply 真灌库、test 离线程 ═════════════════


def test_apply_cleaned_sync_feeds_tone_mimic(tmp_path, monkeypatch) -> None:
    from api.routers import training_routes

    class FakeTone:
        def __init__(self) -> None:
            self.added: list = []

        def add_conversation(self, user_msg, reply, metadata=None) -> None:
            self.added.append((user_msg, reply))

    tone = FakeTone()
    monkeypatch.setattr(training_routes, "_training_dir", lambda: tmp_path)
    monkeypatch.setattr(training_routes, "_live_tone_mimic", lambda: tone)
    (tmp_path / "a_cleaned.json").write_text(json.dumps([
        {"user_msg": "hi", "reply": "hello"},
        {"user": "u2", "reply_msg": "r2"},
        {"user_msg": "", "reply": "x"},
        "junk",
    ]), encoding="utf-8")
    out = training_routes._apply_cleaned_sync()
    assert out["status"] == "applied"
    assert out["applied"] == 2
    assert tone.added == [("hi", "hello"), ("u2", "r2")]


def test_apply_cleaned_sync_requires_dataset(tmp_path, monkeypatch) -> None:
    from api.routers import training_routes

    monkeypatch.setattr(training_routes, "_training_dir", lambda: tmp_path)
    with pytest.raises(FileNotFoundError):
        training_routes._apply_cleaned_sync()


def test_apply_clone_route_no_longer_fakes_applied(tmp_path, monkeypatch) -> None:
    """旧实现无清洗产物也返回 {"status":"applied"} —— 假成功。"""
    from fastapi import HTTPException

    from api.routers import training_routes

    monkeypatch.setattr(training_routes, "_training_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(training_routes.apply_clone(_auth=True, _admin=(1, None)))
    assert ei.value.status_code == 404


def test_test_clone_route_returns_style_via_live_mimic(monkeypatch) -> None:
    from api.routers import training_routes

    class FakeTone:
        def get_style_prompt(self) -> str:
            return "SP"

        def retrieve_style_examples(self, query, top_k=3):
            return [f"ex:{query}"]

    monkeypatch.setattr(training_routes, "_live_tone_mimic", lambda: FakeTone())
    res = asyncio.run(training_routes.test_clone(message="m", _auth=True, _admin=(1, None)))
    assert res["status"] == "ok"
    assert res["style_output"] == "SP"
    assert res["style_examples"] == ["ex:m"]


def test_test_clone_runs_blocking_part_off_event_loop() -> None:
    from api.routers.training_routes import test_clone

    src = inspect.getsource(test_clone)
    assert "asyncio.to_thread" in src


# ═════════════════ item32/33/34/37/40：路由修复钉住 ═════════════════


def test_dashboard_reads_real_sources_not_bypassed_route() -> None:
    from api.routers.misc_routes import get_dashboard_stats

    src = inspect.getsource(get_dashboard_stats)
    assert "get_wechat_state()" in src
    assert "online_count()" in src
    # 绕过 DI 直调 get_wechat_status 的旧路径必须消失（只看代码行，注释里的记述除外）
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    assert "get_wechat_status(" not in "\n".join(code_lines)
    # 失败不得毒缓存：缓存写入必须发生在 try 内取数成功之后
    assert "cache[\"data\"] = wechat_info" in src


def test_admin_delete_user_removes_dependent_rows() -> None:
    from api.routers.admin_routes import delete_user

    src = inspect.getsource(delete_user)
    assert "WechatBinding" in src
    assert "WechatChannelSession" in src
    assert "delete(" in src


def test_persona_put_invalidates_persona_and_knowledge_caches() -> None:
    from api.routers.character_routes import update_character_persona

    src = inspect.getsource(update_character_persona)
    assert "invalidate_character_persona_cache" in src
    assert "_invalidate_knowledge_index" in src


def test_activate_character_uses_raw_cards() -> None:
    """归一化输出写回会以lossy清洗污染真源卡文件。"""
    from api.routers.character_routes import activate_character

    src = inspect.getsource(activate_character)
    assert "normalize=False" in src
    assert "normalize=True" not in src


def test_sanitize_text_keeps_plain_english_by() -> None:
    from utils.character_helpers import sanitize_character_text

    cleaned = sanitize_character_text("She always stood by me in rain")
    assert " by " in cleaned


def test_invite_consume_is_atomic_cas() -> None:
    from api.routers.invite_routes import register_with_invite

    src = inspect.getsource(register_with_invite)
    assert "update(InviteCode)" in src
    assert "rowcount" in src
    # 旧的「先读校验后直接赋值」模式必须消失
    assert "invite.used_by = user.id" not in src


# ═════════════════ item38：persona-card PUT 双写回真源 ═════════════════


def test_persona_card_put_mirrors_to_config_characters(tmp_path, monkeypatch) -> None:
    from api.deps import deps
    from api.routers import character_routes, persona_card_routes

    seed = {"id": "ka", "name": "旧名", "description": "old", "custom_field": "keep-me"}
    (tmp_path / "ka.json").write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", tmp_path)

    class FakeMgr:
        def update_character(self, character_id, card) -> bool:
            return True

    monkeypatch.setattr(
        deps, "shisi_reg", type("R", (), {"character_manager": FakeMgr()})(),
        raising=False,
    )
    monkeypatch.setattr(deps, "orch", None, raising=False)

    req = persona_card_routes.PersonaCardUpdateRequest(card={
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {"name": "新名", "description": "新描述"},
    })
    out = asyncio.run(persona_card_routes.update_persona_card("ka", req, _auth=True))
    assert out["status"] == "updated"
    saved = json.loads((tmp_path / "ka.json").read_text(encoding="utf-8"))
    assert saved["name"] == "新名"
    assert saved["description"] == "新描述"
    assert saved["custom_field"] == "keep-me"  # 镜像合并不得丢扩展字段


# ═════════════════ item39：传播记录也带 user_id ═════════════════


def test_propagated_records_get_user_id_in_ring_buffer() -> None:
    from observability import logging_setup as ls

    root = logging.getLogger()
    added = ls.ring_buffer not in root.handlers
    if added:
        root.addHandler(ls.ring_buffer)
    token = ls._user_id.set(4242)
    try:
        logging.getLogger("unrelated.child.logger").warning("propagated-marker-xyz")
        entries = ls.ring_buffer.get_recent(search="propagated-marker-xyz", user_id=4242)
        assert entries, "子 logger 传播记录未注入 user_id（过滤器挂错位置）"
    finally:
        ls._user_id.reset(token)
        if added:
            root.removeHandler(ls.ring_buffer)


def test_user_context_filter_lives_on_handler_not_root() -> None:
    from observability import logging_setup as ls

    src = inspect.getsource(ls.setup_logging)
    assert "root_logger.addFilter" not in src
    assert ls.ring_buffer.filters, "user_id 过滤器必须挂在 ring_buffer handler 上"


# ═════════════════ item41：_meta 归属 + SSRF 闸门 ═════════════════


def test_profile_tools_reject_llm_supplied_session_key() -> None:
    """session_key 是 LLM 可自填参数，回落过去=允许指定写谁画像；必须拒绝。"""
    from tools.builtin.profile_agent_tools import (
        ForgetFactsTool,
        QueryProfileTool,
        RememberFactsTool,
        UpdateUserProfileTool,
    )

    for cls in (UpdateUserProfileTool, RememberFactsTool, ForgetFactsTool, QueryProfileTool):
        res = cls().execute(session_key="2:victim@im.wechat", birthday="4月1日",
                            facts=[], fact_ids=[])
        assert not res.success, f"{cls.__name__} 竟接受 LLM 自报 session_key"


def test_memory_tool_requires_meta_and_scopes_query() -> None:
    from tools.builtin.extra_tools import MemoryTool

    class FakeSM:
        def __init__(self) -> None:
            self.searched_with: list = []

        def user_key_from_session(self, session: str) -> str:
            return session.split(":", 1)[-1]

        def search_facts(self, keyword, user_key=None):
            self.searched_with.append(user_key)
            return [{"id": 1, "fact": "f", "user_key": user_key}]

    sm = FakeSM()
    tool = MemoryTool(sm)
    r0 = tool.execute(query="x")
    assert not r0.success and r0.error == "missing_session_key"
    # LLM 自报他人键也不得采信
    r0b = tool.execute(query="x", user_key="someone@else")
    assert not r0b.success and r0b.error == "missing_session_key"
    r1 = tool.execute(query="x", _meta={"session_key": "2:alice@im.wechat"})
    assert r1.success
    assert sm.searched_with == ["alice@im.wechat"]
    assert r1.data["user_key"] == "alice@im.wechat"


def test_memory_tool_legacy_sm_without_user_key_param_falls_back_filtered() -> None:
    from tools.builtin.extra_tools import MemoryTool

    class LegacySM:
        def user_key_from_session(self, session: str) -> str:
            return session.split(":", 1)[-1]

        def search_facts(self, keyword):  # 不支持 user_key → TypeError
            return [{"id": 9, "fact": "别人家的事", "user_key": "bob@x"}]

        def get_facts(self, category=None, min_confidence=0.0, limit=50):
            return [
                {"id": 1, "fact": "mine", "user_key": "alice@im"},
                {"id": 9, "fact": "theirs", "user_key": "bob@x"},
            ]

    tool = MemoryTool(LegacySM())
    res = tool.execute(query="x", _meta={"session_key": "2:alice@im"})
    assert res.success
    assert [f["id"] for f in res.data["facts"]] == [1], "无用户维度实现必须按键过滤"


def test_scheduler_tool_binds_session_from_meta() -> None:
    from tools.builtin.extra_tools import SchedulerTool

    class RecSM:
        def __init__(self) -> None:
            self.got: tuple = ()

        def add_reminder(self, content, trigger_time, session_key="", user_id=None):
            self.got = (session_key, user_id)
            return 7

    sm = RecSM()
    tool = SchedulerTool(sm)
    r0 = tool.execute(content="叫我")
    assert not r0.success and r0.error == "missing_session_key", "无主提醒永不可投递"
    assert sm.got == ()
    r1 = tool.execute(content="叫我", trigger_time="2026-09-22 06:00",
                      _meta={"session_key": "4:u@im.wechat", "user_id": 4})
    assert r1.success
    assert sm.got == ("4:u@im.wechat", 4)


def test_web_summary_ssrf_guard(monkeypatch) -> None:
    from tools.builtin.extra_tools import WebSummaryTool

    mapping = {
        "loop.local": "127.0.0.1",
        "meta.local": "169.254.169.254",
        "intranet.local": "10.0.0.8",
        "pub.local": "93.184.216.34",
    }

    def fake_dns(host, port, *a, **kw):
        return [(2, 1, 6, "", (mapping[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    check = WebSummaryTool._check_url_ssrf
    assert check("http://loop.local/x") == "禁止访问内网/保留地址"
    assert check("http://meta.local/x") == "禁止访问内网/保留地址"
    assert check("http://intranet.local/x") == "禁止访问内网/保留地址"
    assert check("https://pub.local/x") is None
    assert check("file:///etc/passwd") == "仅允许 http/https URL"

    res = WebSummaryTool().execute(url="http://loop.local/x")
    assert not res.success and res.error.startswith("web_summary_blocked")
