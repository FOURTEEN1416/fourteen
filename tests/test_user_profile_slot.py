"""用户画像槽回归（2026-09-21 生产生日/军训/上班乱编）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from shisi.memory.legacy.user_profile import UserProfileStore


def test_birthday_correction_and_deny():
    db = Path(tempfile.mkdtemp()) / "p.db"
    st = UserProfileStore(db)
    st.apply_user_utterance("2:x@im.wechat", "我的生日是腊月初一")
    assert st.get("2:x@im.wechat")["birthday"] == "腊月初一"
    st.apply_user_utterance("2:x@im.wechat", "我的生日不是腊月初一")
    assert st.get("2:x@im.wechat")["birthday"] == ""
    st.apply_user_utterance("2:x@im.wechat", "我生日是11月14号")
    assert st.get("2:x@im.wechat")["birthday"] == "11月14号"
    block = st.to_prompt_block("2:x@im.wechat")
    assert "11月14号" in block
    assert "不得编造" in block


def test_occupation_and_commitment_isolated_per_user():
    db = Path(tempfile.mkdtemp()) / "p2.db"
    st = UserProfileStore(db)
    st.apply_user_utterance("2:a@im.wechat", "明天要军训")
    st.apply_user_utterance("2:a@im.wechat", "明天早上七点二十分叫我起床")
    st.apply_user_utterance("4:b@im.wechat", "我在上班")
    pa = st.get("2:a@im.wechat")
    pb = st.get("4:b@im.wechat")
    assert "军训" in pa["occupation"]
    assert any("七点二十" in c for c in pa["commitments"])
    assert "上班" in pb["occupation"]
    assert pb["commitments"] == []
    # 画像互不串
    assert "上班" not in st.to_prompt_block("2:a@im.wechat")
    assert "军训" not in st.to_prompt_block("4:b@im.wechat")


def test_prompt_block_empty_without_profile():
    db = Path(tempfile.mkdtemp()) / "p3.db"
    st = UserProfileStore(db)
    assert st.to_prompt_block("9:ghost@im.wechat") == ""


def test_persona_service_uses_profile_slot():
    import inspect

    from shisi.application import persona_service

    src = inspect.getsource(persona_service.PersonaService.build_system_prompt)
    assert "用户画像" in src
    assert (
        "get_profile_prompt_block" in src
        or "to_prompt_block" in src
        or "user_profile" in src
    )


def test_orchestrator_updates_profile_from_user_only():
    import inspect

    from orchestrator import optimized_orchestrator as orch

    src = inspect.getsource(orch.OptimizedOrchestrator)
    # 正则热路径已剔除；只剩 LLM profile_sync_agent
    assert "apply_user_utterance" not in src
    assert "run_profile_sync_agent" in src
