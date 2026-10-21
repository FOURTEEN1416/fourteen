"""主链「能力声明」段（2026-10-21 R3 · 设计复核后修订版）。

**为什么只做能力声明、不做主链 function calling**（复核 2026-10-21 三条致命项）：
1. `openai_compatible_provider._chat` 在 `content` 为空时**直接抛错**（空内容响应
   根治，`:491-498`），即使 message 带 `tool_calls`；实测 → `RuntimeError 模型请求
   失败，未生成角色回复`；
2. 生产 `stream_enabled=true` 时 `chat()` 走 `_chat_aggregated`，而 `_stream_chat`
   只 yield `delta["content"]`（`:591-597`）——**完全丢弃 delta.tool_calls**；
3. `MultiProviderGateway.chat` 只看文本判成败（`:520-542`），tool_calls-only
   响应会被判失败并**触发熔断记账**，正好加剧 v1.55 要根治的质量悬崖。

且 provider 层不在本任务允许清单内。故本批按复核给的**最小可行修订**落地：
- 能力声明段（本节，静态白名单，只声明真实能力）；
- 「按需检索」交给既有的 L0 晋级 → L1 终审链（`tool_gate.build_review_messages`
  里「你还记得…→ 调用 memory 工具」规则已存在，2026-10-21 FIX-2），并**启用**
  `memory` 工具使那条规则不再悬空（见 `tests/test_memory_probe_on_demand.py`）。

**能力矩阵才是真源**（逐条核对，不是文案想象）：
- 长期记忆检索：`tools/builtin/extra_tools.py:31 MemoryTool`（name="memory"），
  `_meta.session_key` 服务端注入、无键即拒 → 真实存在（启用后经 L1 可用）；
- 提醒/天气/搜索/日历：真实存在但**由 L1 在你开口前执行**，主链拿不到它们的
  schema（`_run_tools_if_needed` 的 tools 参数只给 L1 自己）→ 声明必须写成
  「由别的系统在你开口前处理」，不能写成「你没有这个能力」；
- 画像/事实写入：`_enqueue_profile_sync` → `run_profile_sync_agent` 在回复后
  真实执行（`optimized_orchestrator.py` 的 `_enqueue_profile_sync`）→ 属真能力，
  若声明「你没有别的能力…不要假装你做了」会让模型否认一个真实发生的行为
  （复核条目 #9）。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest


def _service():
    from shisi.application.persona_service import PersonaService

    return PersonaService(config_loader=None, llm_gateway=None)


def _build(memory_recall: bool | None = None, character_id: str = "default") -> str:
    """最小可用 prompt 构建（不依赖 config/characters 的卡数，基线可复现）。"""
    ps = _service()
    kwargs = {"character_id": character_id, "user_message": "在吗"}
    if memory_recall is not None:
        kwargs["capability_flags"] = {"memory_recall": memory_recall}
    return ps.build_system_prompt(**kwargs)


# ── 1. 段落在场且只注入一次 ─────────────────────────────────


def test_capability_block_present_exactly_once():
    prompt = _build()
    assert "# 你的能力" in prompt, "能力声明段未注入 system prompt"
    assert prompt.count("# 你的能力") == 1, "能力声明段重复注入（2026-10-14 事故同型）"


def test_capability_block_position_after_memory_rule_before_topic():
    """位置钉死：角色说明 < 记忆使用守则 < 你的能力 < 当前话题。

    复核条目 #10：能力声明落在 `persona_service.injection_parts` 内，因此它在
    编排器后续追加的工具结果段与 reply_mode 段**之前**——这是有意为之（能力是
    事实性前置，工具结果才是本轮材料）。
    """
    prompt = _build(memory_recall=True)
    role = prompt.index("# 对话角色说明")
    mem = prompt.index("# 记忆使用守则")
    cap = prompt.index("# 你的能力")
    topic = prompt.index("# 当前话题")
    assert role < mem < cap < topic, (
        f"顺序应为 角色说明({role}) < 记忆守则({mem}) < 能力({cap}) < 话题({topic})"
    )


# ── 2. 只声明真实能力（逐条对得上代码）────────────────────


def test_declares_memory_as_system_provided_not_self_service():
    """记忆是「系统在开口前给」，不是「你去查库」——与 MEMORY_USAGE_RULE 同向。"""
    block = _build(memory_recall=True)
    assert "长期记忆" in block
    assert "系统" in block
    # 不得教模型主动索取记忆（test_memory_usage_rule 已钉同一红线）
    assert "<search" not in block
    assert "主动索取" not in block


def test_memory_recall_line_is_conditional_on_flag():
    """组件缺失（未注册 memory 工具）时不得声称有检索能力。"""
    with_recall = _build(memory_recall=True)
    without = _build(memory_recall=False)
    assert "# 你的能力" in without, "静态部分是总是真的，不该整段消失"
    assert "检索" in with_recall
    assert "检索" not in without, "未注册 memory 工具却声明了检索能力（假声明）"


def test_declares_delegated_tools_are_handled_before_reply():
    """提醒/天气/搜索由别的系统在开口前处理 —— 不得写成「模型自己会做」。"""
    block = _build(memory_recall=True)
    for word in ("提醒", "天气", "搜索"):
        assert word in block, f"能力声明未提及 {word}（模型无法回答「你能做什么」）"
    assert "开口前" in block or "回复前" in block
    # 反向：不得声称模型自己执行这些动作
    assert "你可以自己设提醒" not in block
    assert "你可以自己查天气" not in block


def test_declares_profile_sync_is_real_not_denied():
    """画像/事实写入在回复后真实发生（`_enqueue_profile_sync`）——模型不得否认。

    复核条目 #9：把「没有别的特殊能力、不要假装你做了」写成通杀句，会让模型对
    用户说「记住我喜欢吃辣」时回答「我没法记住」——而系统确实会写。
    """
    block = _build(memory_recall=True)
    assert "画像" in block or "偏好" in block
    assert "自动" in block, "必须说明写入是系统自动完成的（而非模型自己记）"
    # 不得出现通杀式否认
    assert "你没有别的能力" not in block
    assert "不要假装你做了" not in block


def test_does_not_narrate_retrieval_mechanism():
    """不得诱导模型旁白「我查一下记忆 / 检索一下」——出站守卫不拦这些形态。

    复核条目 #7：`_strip_meta_memory_talk` 只拦四种身份/记忆解释形态，
    「我查一下记忆」既不违规也不被剥除，会直接触发用户报障过的元记忆语音。
    故这一条只能靠 prompt 侧措辞杜绝；本用例锁住「不得教它旁白检索过程」。
    """
    block = _build(memory_recall=True)
    assert "不要" in block
    # 具体禁止旁白（与 MEMORY_USAGE_RULE「不要声明你在回忆」同口径）
    joined = block.replace("\n", "")
    assert "旁白" in joined or "不要说自己正在查" in joined or "不要提" in joined


def test_capability_block_does_not_contradict_memory_usage_rule():
    """两段不得互相打架（复核条目 #6 的核心诉求）。

    判定范围是**能力段本身**（`capability_block`），不是整份 prompt ——
    `MEMORY_USAGE_RULE` 里出现「记忆库」是禁令措辞（「不要说我的记忆库…」），
    属正常用法，不能拿整份 prompt 做「含关键词即冲突」的粗糙判定。
    """
    from shisi.application.persona_service import capability_block
    from utils.prompt_sanitize import MEMORY_USAGE_RULE

    block = capability_block(True)
    # 能力段自己不得**教**模型去说「记忆库/数据库里显示」（禁令措辞归 MEMORY_USAGE_RULE，
    # 两处重复禁令只会稀释注意力 —— v1.54 教训）
    for forbidden_phrase in ("记忆库", "数据库里显示"):
        assert forbidden_phrase not in block, (
            f"能力段重复了 MEMORY_USAGE_RULE 的禁令措辞「{forbidden_phrase}」（指令竞争）"
        )
    # 守则要求「系统自动」，能力段必须同向（不能反过来讲成用户/模型手查）
    assert "自动" in MEMORY_USAGE_RULE
    assert "自动" in block or "开口前" in block
    # 文字版不得出现「主动索取」这种反向教导
    assert "主动索取" not in block
    assert "<search" not in block


# ── 3. 接线：编排器必须把真实可用性传进来 ──────────────────


def test_orchestrator_passes_memory_recall_flag():
    """源码级防漂移：`_prepare_context` 必须按 registry 实际注册情况传参。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    assert "capability_flags" in src, "未把能力可用性传给 build_system_prompt"
    assert "memory_recall" in src
    assert "_memory_recall_available" in src, "能力可用性必须有唯一判定函数（防各写一套）"


def test_memory_recall_helper_reads_registry():
    """行为级：判定函数只看 registry 里是否真有 `memory` 工具。"""
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import _memory_recall_available

    class _Registry:
        def __init__(self, names):
            self._names = names

        def get(self, name):
            return SimpleNamespace(name=name) if name in self._names else None

    assert _memory_recall_available({"tools": SimpleNamespace(registry=_Registry({"memory"}))})
    assert not _memory_recall_available({"tools": SimpleNamespace(registry=_Registry({"weather"}))})
    assert not _memory_recall_available({"tools": None})
    assert not _memory_recall_available({})


def test_flag_reflects_real_registry_in_prompt(monkeypatch):
    """端到端（无编排器重依赖）：注册了 memory → prompt 里出现检索能力；没注册 → 不出现。"""
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import _memory_recall_available

    class _Registry:
        def __init__(self, names):
            self._names = names

        def get(self, name):
            return SimpleNamespace(name=name) if name in self._names else None

    have = _memory_recall_available({"tools": SimpleNamespace(registry=_Registry({"memory"}))})
    prompt = _build(memory_recall=have)
    assert "检索" in prompt


# ── 4. 不许碰 pre-existing 顺序/唯一性不变量 ────────────────


def test_identity_and_role_rules_order_unchanged():
    prompt = _build(memory_recall=True)
    assert prompt.index("用户身份铁则") < prompt.index("# 对话角色说明")
    assert prompt.count("身份拷问应答") == 1


def test_capability_block_is_short():
    """控制长度：能力段是事实告知，不是第二套风格指令（v1.54 教训）。"""
    import re

    from shisi.application import persona_service as mod

    block = mod.CAPABILITY_DECLARATION
    # 去掉标题与空行后的正文长度
    body = re.sub(r"\s+", "", block)
    assert len(body) < 400, f"能力段过长({len(body)}字)，会稀释角色指令注意力"
    assert body.count("**") <= 10, "硬指令（加粗禁令）过多"


def test_no_hard_style_commands():
    """不得把「像真人」拆成又一批竞争性硬指令（v1.54 根因）。"""
    from shisi.application import persona_service as mod

    block = mod.CAPABILITY_DECLARATION
    for banned in ("口语", "语气", "像真人", "不要太"):
        assert banned not in block, f"能力段混入了风格指令「{banned}」"


# ── 5. 与 config/characters 无关（基线可复现）─────────────


def test_capability_prompt_does_not_depend_on_character_cards():
    """用不存在的外部 id 也能构建（卡目录被 gitignore，基线不得依赖卡数）。"""
    prompt = _build(memory_recall=True, character_id="no-such-card-xyz")
    assert "# 你的能力" in prompt


@pytest.mark.parametrize("card_present", [True])
def test_block_present_with_real_card_when_available(card_present):
    root = Path(__file__).resolve().parent.parent / "config" / "characters"
    card_id = ""
    for f in sorted(root.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("id"):
            card_id = str(data["id"])
            break
    if not card_id:
        pytest.skip("config/characters 无可用卡（该目录受 gitignore，环境相关）")
    assert "# 你的能力" in _build(memory_recall=True, character_id=card_id)
