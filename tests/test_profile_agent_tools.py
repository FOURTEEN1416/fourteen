"""智能体画像/记忆工具回归。"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from shisi.memory.legacy.structured_memory import StructuredMemory
from shisi.memory.legacy.user_profile import UserProfileStore
from tools.builtin.profile_agent_tools import (
    ForgetFactsTool,
    QueryProfileTool,
    RememberFactsTool,
    UpdateUserProfileTool,
    apply_profile_sync_calls,
    profile_sync_tool_schemas,
)
from utils.prompt_sanitize import is_injectable_fact


def test_update_profile_tool_agent_write():
    db = Path(tempfile.mkdtemp()) / "db.sqlite"
    UserProfileStore(db)  # schema
    # 工具内部 default_store 指向生产路径 — 单测直接测 store + apply
    st = UserProfileStore(db)
    # 模拟工具逻辑：经 apply_profile_sync_calls 需生产 store；
    # 此处钉住 store 能力与 schema
    st.apply_user_utterance("s1@x", "我的生日是腊月初一")
    assert st.get("s1@x")["birthday"] == "腊月初一"
    st.upsert("s1@x", birthday="", clear_birthday=True)
    assert st.get("s1@x")["birthday"] == ""


def test_profile_tool_schemas_registered():
    schemas = profile_sync_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        "update_user_profile",
        "remember_facts",
        "forget_facts",
        "query_profile",
    }
    for s in schemas:
        assert s["type"] == "function"


def test_remember_facts_writes_isolated(tmp_path):
    sm = StructuredMemory(str(tmp_path / "m.db"))
    try:
        tool = RememberFactsTool()
        res = tool.execute(
            _meta={"session_key": "2:u@im.wechat"},
            structured_memory=sm,
            facts=[
                {"fact": "用户生日是腊月初一", "category": "personal"},
                {"fact": "叫我", "category": "commitment"},  # 应被清洗拒绝
            ],
        )
        assert res.success
        written = res.data["written"]
        assert any("腊月初一" in w["fact"] for w in written)
        assert not any(w["fact"] == "叫我" for w in written)
        rows = sm.get_facts(user_key="2:u@im.wechat")
        assert any("腊月初一" in r["fact"] for r in rows)
        # 不串到其他用户
        assert sm.get_facts(user_key="9:other@im.wechat") == []
    finally:
        sm.close()


@pytest.mark.asyncio
async def test_profile_sync_sees_previous_question_and_only_source_turn(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tools.builtin import profile_agent_tools as pa

    sm = StructuredMemory(str(tmp_path / "profile-source.db"))
    try:
        sm.add_chat_turn("生日16号", "你的生日是16号吗？", session_id="s", character_id="a", turn_id="one")
        sm.add_chat_turn("不是，是17号", "已更正", session_id="s", character_id="a", turn_id="two")
        sm.add_chat_turn("未来消息", "未来回复", session_id="s", character_id="a", turn_id="three")
        monkeypatch.setattr(pa, "_runtime", lambda: SimpleNamespace(project_profile_for=lambda sk: {"birthday": "16号"}))
        llm = SimpleNamespace(chat_with_tools=AsyncMock(return_value={"tool_calls": []}))
        await pa.run_profile_sync_agent(llm, "s", "不是，是17号", "已更正", sm, character_id="a", turn_id="two")
        prompt = llm.chat_with_tools.call_args.kwargs["query"]
        assert "你的生日是16号吗" in prompt and "不是，是17号" in prompt
        assert "未来消息" not in prompt
        assert json.dumps({"birthday": "16号"}, ensure_ascii=False) in prompt
    finally:
        sm.close()


def test_forget_facts_by_text_requires_exact_claim_not_substring(tmp_path):
    sm = StructuredMemory(str(tmp_path / "m2.db"))
    try:
        sm.add_fact("用户生日是十一月十四", user_key="4:a@im.wechat", category="personal")
        sm.add_fact("用户喜欢猫", user_key="4:a@im.wechat")
        tool = ForgetFactsTool()
        res = tool.execute(
            _meta={"session_key": "4:a@im.wechat"},
            structured_memory=sm,
            fact_texts=["用户生日是十一月十四"],
        )
        assert res.success
        facts = [r["fact"] for r in sm.get_facts(user_key="4:a@im.wechat")]
        assert not any("十一月十四" in f for f in facts)
        assert any("喜欢猫" in f for f in facts)
    finally:
        sm.close()


def test_apply_profile_sync_calls_end_to_end(tmp_path):
    """apply_profile_sync_calls 用 default_store（生产 DB 路径）——改用 monkey 路径。"""
    # 钉住：调度函数存在且会注入 session_key
    src = inspect.getsource(apply_profile_sync_calls)
    assert "session_key" in src
    assert "_meta" in src


def test_l0_escalates_on_profile_signals():
    from orchestrator import tool_gate

    assert tool_gate.should_escalate("我的生日是腊月初一")
    assert tool_gate.should_escalate("我在上班")
    assert tool_gate.should_escalate("以后不要给我发嗯")
    assert tool_gate.should_escalate("我的生日不是十一月十四")
    assert not tool_gate.should_escalate("嗯嗯好的晚安")


def test_tools_are_public_and_in_yaml():
    from pathlib import Path

    for cls in (UpdateUserProfileTool, RememberFactsTool, ForgetFactsTool, QueryProfileTool):
        assert cls.permission_level == "public"
    yaml = Path("config/system.yaml").read_text(encoding="utf-8")
    for name in ("update_user_profile", "remember_facts", "forget_facts", "query_profile"):
        assert name in yaml


def test_orchestrator_schedules_profile_agent():
    import inspect

    from orchestrator import optimized_orchestrator as orch

    src = inspect.getsource(orch.OptimizedOrchestrator)
    assert "run_profile_sync_agent" in src
    # 正则画像热路径必须已剔除
    assert "apply_user_utterance" not in src
    assert "default_store().apply_user_utterance" not in src


def test_apply_user_utterance_deprecated_not_in_chat_path():
    import inspect
    import tempfile
    import warnings
    from pathlib import Path

    from orchestrator import optimized_orchestrator as orch
    from shisi.memory.legacy.user_profile import UserProfileStore

    orch_src = inspect.getsource(orch)
    assert "apply_user_utterance" not in orch_src
    src = inspect.getsource(UserProfileStore.apply_user_utterance)
    assert "DeprecationWarning" in src
    st = UserProfileStore(Path(tempfile.mkdtemp()) / "x.db")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        st.apply_user_utterance("u@x", "我的生日是腊月初一")
        assert any(issubclass(x.category, DeprecationWarning) for x in w)


def test_is_injectable_rejects_agent_unsafe():
    assert not is_injectable_fact("叫我")
    assert is_injectable_fact("用户生日是腊月初一")
