"""AX P2 一次做完：web 可调 LLM 主动 + curator + 工具账本 + 回放 API。"""

from __future__ import annotations

from proactive.llm_proactive import (
    DEFAULT_WEB_CONFIG,
    build_proactive_context,
    parse_decision,
    read_web_proactive_config,
)
from shisi.agent_plane.curator import bigram_overlap, curate_facts_rule_based


def test_curator_drops_garbage_and_merges_near_dup():
    facts = [
        {"id": 1, "fact": "用户生日是腊月初一", "confidence": 0.8},
        {"id": 2, "fact": "叫我", "confidence": 0.5},
        {"id": 3, "fact": "用户生日是腊月初一。", "confidence": 0.7},
        {"id": 4, "fact": "用户在上班", "confidence": 0.6},
        {"id": 5, "fact": "用户喜欢安静", "confidence": 0.9},
    ]
    r = curate_facts_rule_based(facts, dup_threshold=0.75)
    texts = [k["fact"] for k in r["kept"]]
    assert any("腊月初一" in t for t in texts)
    assert not any(t.strip() in ("叫我",) for t in texts)
    assert r["merged"], "near-dup 应合并"
    assert bigram_overlap("用户生日是腊月初一", "用户生日是腊月初一。") >= 0.75


def test_web_config_injected_into_context():
    ctx = build_proactive_context(
        session_key="N:u",
        hours_since_last_chat=20,
        local_time="2026-09-21 15:00",
        profile={"nickname": "彩儿", "occupation": "上班"},
        persona_hint="角色：米彩\n性格安静",
        web_config={
            "style_hint": "温柔少感叹号",
            "intensity": "high",
            "respect_quiet_hours": True,
            "character_hint": "别太粘人",
        },
        quiet_hours=(23, 7),
    )
    assert "米彩" in ctx
    assert "彩儿" in ctx
    assert "温柔少感叹号" in ctx
    assert "控制台力度=高" in ctx
    assert "23:00–07:00" in ctx or "23:00" in ctx
    assert "别太粘人" in ctx


def test_parse_decision_still_works():
    d = parse_decision('{"should_contact": true, "wait_minutes": 30, "message": "在吗", "reason": "两天"}')
    assert d["should_contact"] and d["message"] == "在吗"


def test_read_web_config_defaults():
    cfg = read_web_proactive_config()
    assert cfg["enabled"] is True
    assert cfg["intensity"] in ("low", "normal", "high")
    for k in DEFAULT_WEB_CONFIG:
        assert k in cfg


def test_scheduler_config_roundtrip_llm_proactive(tmp_path, monkeypatch):
    from proactive.scheduler import ProactiveScheduler

    cfg_path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg_path)
    ProactiveScheduler.write_config_file(
        llm_proactive={"enabled": True, "style_hint": "傲娇", "intensity": "low"}
    )
    data = ProactiveScheduler._read_config_file()
    assert data["llm_proactive"]["style_hint"] == "傲娇"
    assert data["llm_proactive"]["intensity"] == "low"


def test_orchestrator_appends_tool_result_ledger():
    import inspect

    from orchestrator import optimized_orchestrator as orch

    src = inspect.getsource(orch.OptimizedOrchestrator)
    assert "EVENT_TOOL_RESULT" in src or "tool result ledger" in src.lower() or "tool_results" in src
    assert "append" in src


def test_agent_plane_router_mounted():
    from api.routers import agent_plane_routes

    paths = [getattr(r, "path", "") for r in agent_plane_routes.router.routes]
    assert any("replay" in p for p in paths)
    assert any("curate" in p for p in paths)
    assert any("probes" in p for p in paths)


def test_proactive_config_request_has_llm_fields():

    from api.main_routes import ProactiveConfigRequest

    fields = ProactiveConfigRequest.model_fields
    assert "llm_proactive_enabled" in fields
    assert "llm_proactive_style_hint" in fields
    assert "llm_proactive_intensity" in fields
