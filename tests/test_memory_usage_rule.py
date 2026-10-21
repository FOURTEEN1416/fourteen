"""主链「记忆使用守则」（2026-10-21 FIX-1）。

缺陷：主链注入层（`shisi/application/persona_service.py` 的 injection_parts）
与 `utils/prompt_sanitize.py` 里**没有任何一句规范「怎么用记忆」** —— 唯一近似
句只有 `USER_IDENTITY_RULE` 里的「真实记得的事要自然地用（那是你的记忆）」与
`ROLE_CLARITY_RULE` 的几条禁令。于是模型不知道记忆是系统自动给的，会去
「声明自己在回忆」「逐条念记忆条目」「说我的记忆库/数据库里显示」，甚至试图
在正文里写记忆指令（对照爱语 `aiyu_prompts_full.json` #7 的自动 `<search:>` 与
#0 的四档记忆规则）。

修法：仿 `EMOJI_USE_RULE` 新增常量 `MEMORY_USAGE_RULE`，并加入
`PersonaService.build_system_prompt` 的 injection_parts（紧邻 ROLE_CLARITY_RULE
之后）。纯 prompt 文案，不改任何执行分支。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.prompt_sanitize import MEMORY_USAGE_RULE

# ── 1. 常量文案：必须把该说的都说到 ─────────────────────────


def test_rule_has_header():
    assert "# 记忆使用守则" in MEMORY_USAGE_RULE


def test_rule_says_memory_is_system_given():
    """核心语义：记忆是系统**自动**给的，直接自然使用，不要声明在回忆。"""
    assert "自动" in MEMORY_USAGE_RULE
    assert "回忆" in MEMORY_USAGE_RULE
    # 不得反过来教模型「主动索取记忆」
    assert "<search" not in MEMORY_USAGE_RULE


def test_rule_bans_reciting_and_memory_db_talk():
    """禁止逐条念记忆条目、禁止说「我的记忆库/数据库里显示」。"""
    assert "念" in MEMORY_USAGE_RULE
    assert "记忆库" in MEMORY_USAGE_RULE or "数据库" in MEMORY_USAGE_RULE


def test_rule_bans_inline_memory_commands():
    """禁止在正文里写记忆指令（爱语式的 <add:> 之类）。"""
    assert "<add:" in MEMORY_USAGE_RULE, "必须点名禁止正文里写记忆指令"


def test_rule_bans_claiming_to_remember():
    """禁止声称「我记住了/我记下来了」——记忆由系统自动维护。"""
    assert "记住了" in MEMORY_USAGE_RULE or "记下来" in MEMORY_USAGE_RULE


def test_rule_precedence_and_no_fabrication():
    """冲突时以用户刚说的为准；想不起来就说记不清，不得编造。"""
    assert "以用户刚说的为准" in MEMORY_USAGE_RULE
    assert "编造" in MEMORY_USAGE_RULE
    assert "记不清" in MEMORY_USAGE_RULE


def test_rule_names_the_actual_memory_sections():
    """文案必须点名上下文里真实存在的记忆段标题，模型才对得上号。

    这些标题由 persona_service 生成（「我记得的」「我对你的观察」「相关回忆」
    「早期对话摘要」），用户画像另有独立段。
    """
    for section in ("我记得的", "我对你的观察", "相关回忆", "用户画像"):
        assert section in MEMORY_USAGE_RULE, f"守则未点名记忆段「{section}」"


# ── 2. 接线：必须真的进 system prompt，且位置紧邻角色说明 ────


def _build_prompt(monkeypatch, character_id: str = "") -> str:
    from shisi.application.persona_service import PersonaService
    from shisi.core.services import prompt_builder as pb

    class _NoIndexSvc:
        def has_index(self, cid):  # noqa: ANN001, ANN201
            return False

        def index_character(self, cid, character):  # noqa: ANN001, ANN201
            pass

        def get_knowledge_context(self, cid, query, top_k=8, exclude_sources=None):  # noqa: ANN001, ANN201
            return ""

    monkeypatch.setattr(pb, "get_knowledge_service", lambda: _NoIndexSvc())

    cid = character_id
    if not cid:
        root = Path(__file__).resolve().parent.parent / "config" / "characters"
        for f in sorted(root.glob("*.json")):
            try:
                card = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if card.get("id"):
                cid = str(card["id"])
                break
        if not cid:
            pytest.skip("config/characters 无可用卡（该目录受 gitignore，环境相关）")

    ps = PersonaService(config_loader=None, llm_gateway=None)
    return ps.build_system_prompt(character_id=cid, user_message="在吗")


def test_rule_injected_into_system_prompt(monkeypatch):
    """守则必须真的出现在主链 system prompt 里（不是只定义了个常量）。"""
    prompt = _build_prompt(monkeypatch)
    assert "# 记忆使用守则" in prompt, "记忆使用守则未注入 system prompt"


def test_rule_injected_exactly_once(monkeypatch):
    """同一 prompt 里不得重复注入（生产 2026-10-14 重复注入事故的同类防线）。"""
    prompt = _build_prompt(monkeypatch)
    assert prompt.count("# 记忆使用守则") == 1


def test_rule_adjacent_after_role_clarity(monkeypatch):
    """位置：紧邻 ROLE_CLARITY_RULE 之后、TOPIC_CONTINUITY_RULE 之前。"""
    prompt = _build_prompt(monkeypatch)
    role_pos = prompt.index("# 对话角色说明")
    mem_pos = prompt.index("# 记忆使用守则")
    topic_pos = prompt.index("# 当前话题")
    assert role_pos < mem_pos < topic_pos, (
        f"顺序应为 角色说明({role_pos}) < 记忆守则({mem_pos}) < 当前话题({topic_pos})"
    )


def test_identity_rule_still_precedes_role_clarity(monkeypatch):
    """既有顺序不变量不得被本次插入破坏（身份铁则在角色说明之前）。"""
    prompt = _build_prompt(monkeypatch)
    assert prompt.index("用户身份铁则") < prompt.index("# 对话角色说明")
