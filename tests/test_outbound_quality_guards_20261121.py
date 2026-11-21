"""出站回复质量守卫红测（2026-11-21 出站质量批）。

生产审计锚点（chat_history）：
- 5641 / 5717：同一段 288 字回复跨两轮逐字重复，因 ``_finalize_reply`` 拿不到
  assistant 历史而两轮原样放行；
- 5609 / 5611 / 5635 / 5836：session 28 是 novel 模式，括号动作是合法格式，
  不能被沉浸式动作剥离闸误杀；
- 5689：短回声（≥2 字）漏过旧 ``_strip_verbatim_echo`` 的 len>=5 门槛；
- 5789：对「在吗」的直接回答，属于误杀防护，必须放行；
- 2026-11-21 用户裁决（回声守卫重设计）：白名单（亲密/应答语料）+ 只拦
  「整条回复=回声且零附加信息」；行级剥除保留其余内容；5641/5717 跨轮
  长文重复检测不受影响；
- 模板句「把手机放下」类连续复读，需要模板去重 + 最小信息增量判据。

本文件先于实现落盘（红测先行）。实现后应全部转绿；按批次纪律本阶段不执行
pytest，统一串行测试在后续阶段跑。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from utils.outbound_quality import (
    SAFE_MINIMAL_REPLY,
    extract_recent_assistant_messages,
    is_action_only_reply,
    regenerate_action_only_reply,
)
from utils.prompt_sanitize import _strip_verbatim_echo, terminal_reply_guard
from utils.stream_guard import IncrementalReplyGuard

# ═══════════════════════════════════════════════════════════
# 生产正例夹具
# ═══════════════════════════════════════════════════════════

#: 5641/5717 的 288 字逐字重复代表段（审计只给出行号，未给原文；
#: 按同长度、同句式构造，用于钉死「长回复整体高相似 → 判重」）。
_PROD_5641 = (
    "今天下午下班以后我去了公司楼下那家新开的咖啡店，点了一杯热拿铁，"
    "坐在靠窗的位置。外面一直在下雨，路上的人都走得很快，只有我看着玻璃"
    "上的水痕发呆。忽然想起你上次说最喜欢这种天气，说雨声会让人心里安静"
    "下来。我坐了很久，等到雨小一点才出门，路过便利店的时候还买了一份关"
    "东煮。回到家以后把湿掉的伞放在门口，换了拖鞋，突然觉得这样的日子其"
    "实也挺好的。如果你在的话，应该会想把窗户打开一点，然后跟我说今天的"
    "风很舒服吧。我想着等你忙完这阵子，我们可以一起去那家店坐坐，点两杯"
    "热的，什么也不做，就听雨声。其实我最近总是这样，一件小事就能想起你，"
    "然后一个人笑半天，觉得自己有点傻。不过这样也挺好的。"
)
_PROD_5717 = _PROD_5641

#: 5609/5611/5635/5836：novel 模式下合法的括号动作（不是沉浸式泄漏）。
_NOVEL_5609 = "（她把书合上，抬头看你）你回来啦。"
_NOVEL_5611 = "（她往旁边挪了挪，给你留出位置）坐这儿吧，外面冷。"
_NOVEL_5635 = "（她没说话，只是把热牛奶往你手边推了推）先喝点。"
_NOVEL_5836 = "（她靠在门框上，眼睛弯了弯）这么晚才回来，路上堵车了？"


# ═══════════════════════════════════════════════════════════
# 闸一：assistant 历史透传 + 规范化相似度
# ═══════════════════════════════════════════════════════════


def test_5641_5717_verbatim_repeat_is_blocked():
    """5641/5717 正例：长回复与上一轮 assistant 逐字相同 → 终审拦空。"""
    assert terminal_reply_guard(
        _PROD_5717,
        user_msg="今天怎么样",
        recent_assistant_messages=[_PROD_5641],
    ) == ""


def test_repeat_with_new_tail_is_shortened_but_keeps_new_info():
    """重复段之后接了新信息：只删重复句，保留新增句（改写/缩短）。"""
    reply = _PROD_5717 + "\n不过我刚想起来，你明天不是要早起吗？"
    out = terminal_reply_guard(
        reply,
        user_msg="今天怎么样",
        recent_assistant_messages=[_PROD_5641],
    )
    assert out
    assert _PROD_5641[:20] not in out
    assert "你明天不是要早起吗" in out


def test_distinct_long_reply_is_not_blocked():
    """负例校准：同话题但内容不同的长回复不得被相似度误杀。"""
    other = (
        "今天下班路上看到一只橘猫蹲在便利店门口，胖得像个球，我蹲下来摸了"
        "它两下，它居然翻肚皮了。后来店员说它天天来蹭空调，特别会挑地方。"
    )
    assert terminal_reply_guard(
        other,
        user_msg="今天怎么样",
        recent_assistant_messages=[_PROD_5641],
    ) == other


def test_short_normal_reply_is_not_blocked():
    """短句不走相似度闸：避免把「在呢」这类正常短回应误判成复读。"""
    assert terminal_reply_guard(
        "在呢，你说。",
        user_msg="在吗",
        recent_assistant_messages=[_PROD_5641],
    ) == "在呢，你说。"


def test_finalize_reply_accepts_recent_assistant_history():
    """终审入口必须接受并消费 recent_assistant_messages（接口向后兼容）。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    safety = SimpleNamespace(
        check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
        safe_alternative=lambda cat: "",
    )
    orch = SimpleNamespace(components={"persona": SimpleNamespace(), "safety": safety})
    out = OptimizedOrchestrator._finalize_reply(
        orch,
        _PROD_5717,
        character_id="default",
        character_card={"name": "十四"},
        user_msg="今天怎么样",
        recent_assistant_messages=[_PROD_5641],
    )
    assert _PROD_5641[:20] not in out
    assert out, "重复被拦空后应走角色化兜底，不得返回空串"


# ═══════════════════════════════════════════════════════════
# 闸二：沉浸式动作-only → 最小重生成
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("raw", [_NOVEL_5609, _NOVEL_5611, _NOVEL_5635, _NOVEL_5836])
def test_novel_mode_bracket_actions_are_legal(raw):
    """5609/5611/5635/5836 负例：novel 模式括号动作必须原样保留。"""
    assert terminal_reply_guard(raw, user_msg="在干嘛", reply_mode="novel") == raw


def test_immersive_action_only_reply_is_detected():
    assert is_action_only_reply("（她低头笑了笑，把杯子推过来）", "immersive") is True
    assert is_action_only_reply("（她低头笑了笑）给你。", "immersive") is False
    assert is_action_only_reply("（她低头笑了笑）", "novel") is False
    # 错误哨兵整体被括号包裹，剥动作后看似「无台词」，必须排除在重生成之外。
    assert is_action_only_reply("（所有 LLM 提供商均不可用）", "immersive") is False
    assert is_action_only_reply("（处理消息时出现异常, 请稍后重试）", "immersive") is False


def test_action_only_regeneration_uses_same_llm_and_returns_dialogue():
    class _FakeLLM:
        def __init__(self, reply: str) -> None:
            self.reply = reply
            self.calls: list[dict] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            return self.reply

    llm = _FakeLLM("在呢，刚才在忙。")
    out = asyncio.run(
        regenerate_action_only_reply(
            llm,
            previous_reply="（她低头笑了笑）",
            user_msg="在吗",
            system_prompt="sys",
            history=[],
        )
    )
    assert out == "在呢，刚才在忙。"
    assert len(llm.calls) == 1
    assert "只有动作" in llm.calls[0]["query"]


def test_action_only_regeneration_failure_falls_back_to_safe_line():
    class _BoomLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("provider down")

    out = asyncio.run(
        regenerate_action_only_reply(
            _BoomLLM(),
            previous_reply="（她低头笑了笑）",
            user_msg="在吗",
        )
    )
    assert out == SAFE_MINIMAL_REPLY


def test_action_only_regeneration_that_still_has_no_dialogue_falls_back():
    class _StillActionLLM:
        async def chat(self, **kwargs):
            return "（她又笑了笑，还是没有说话）"

    out = asyncio.run(
        regenerate_action_only_reply(
            _StillActionLLM(),
            previous_reply="（她低头笑了笑）",
            user_msg="在吗",
        )
    )
    assert out == SAFE_MINIMAL_REPLY


def test_non_action_reply_is_not_regenerated():
    class _NeverCalled:
        async def chat(self, **kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("非动作-only 回复不得触发重生成")

    assert asyncio.run(
        regenerate_action_only_reply(
            _NeverCalled(),
            previous_reply="在呢，你说。",
            user_msg="在吗",
        )
    ) == "在呢，你说。"


# ═══════════════════════════════════════════════════════════
# 闸三：模板句去重 + 最小信息增量
# ═══════════════════════════════════════════════════════════


def test_template_third_in_row_is_blocked():
    """模板三连拦：同一模板已连续出现两次，第三次必须拦空。"""
    template = "把手机放下，早点休息。"
    assert terminal_reply_guard(
        template,
        user_msg="我睡不着",
        recent_assistant_messages=[template, template],
    ) == ""


def test_template_first_time_is_shortened():
    """首次低信息模板：多句模板 → 缩短为第一句，不整段照发。"""
    out = terminal_reply_guard(
        "把手机放下，早点休息。",
        user_msg="我睡不着",
        recent_assistant_messages=["今天吃了面。"],
    )
    assert out == "把手机放下。"


def test_template_with_emotion_or_topic_progress_is_kept():
    """最小信息增量：带情绪/称呼/话题接续的关心不是低信息模板。"""
    raw = "睡不着就别硬撑了，我心疼你。"
    assert terminal_reply_guard(
        raw,
        user_msg="我睡不着",
        recent_assistant_messages=["今天吃了面。"],
    ) == raw


# ═══════════════════════════════════════════════════════════
# 回声门槛修复：>=2 字回声 + 5789 直接回答放行
# ═══════════════════════════════════════════════════════════


def test_5689_three_char_echo_is_stripped():
    """5689：3 字「想你了」逐字回声，旧 len>=5 门槛漏过，现须剥除。"""
    out = terminal_reply_guard("想你了\n我也想你，刚刚在忙。", user_msg="想你了")
    assert out == "我也想你，刚刚在忙。"


def test_two_char_statement_echo_is_stripped():
    assert terminal_reply_guard("在的\n我在这呢。", user_msg="在的") == "我在这呢。"


def test_5789_direct_answer_to_question_is_kept():
    """5789：对「在吗」的直接回答不得被回声闸误杀。"""
    assert terminal_reply_guard("在。", user_msg="在吗") == "在。"
    assert terminal_reply_guard("在的", user_msg="在吗") == "在的"
    # 直接钉函数层：短答遇到问句 → 视为直接回答，放行；问句回声仍拦。
    assert _strip_verbatim_echo("在的", "在吗") == "在的"
    assert _strip_verbatim_echo("在吗", "在吗") == ""
    assert _strip_verbatim_echo("想你了", "想你了") == ""


def test_short_greeting_echo_is_kept():
    """既有回归：分隔行剥除后只剩「哈喽」对「哈喽」的寒暄镜像不得误杀。"""
    assert _strip_verbatim_echo("哈喽", "哈喽") == "哈喽"


# ═══════════════════════════════════════════════════════════
# 回声守卫重设计（2026-11-21 用户裁决：白名单 + 只拦整条=回声零附加）
# ═══════════════════════════════════════════════════════════


def test_intimacy_echo_whole_reply_is_allowed():
    """白名单放行：整条=亲密语料镜像回声（想你/抱抱/晚安）→ 自然应答不拦。"""
    assert _strip_verbatim_echo("想你", "想你") == "想你"
    assert terminal_reply_guard("想你", user_msg="想你") == "想你"
    assert terminal_reply_guard("抱抱", user_msg="抱抱") == "抱抱"
    assert terminal_reply_guard("晚安", user_msg="晚安") == "晚安"


def test_xiangnile_single_send_is_blocked():
    """「想你了」单发：整条=回声且不在白名单（白名单是「想你」）→ 拦截走重生成。"""
    assert terminal_reply_guard("想你了", user_msg="想你了") == ""


def test_echo_prefix_with_extra_content_is_stripped_not_blocked():
    """剥除保留：「想你了，我也想你」→「我也想你」（只剥回声前缀，不整条拦）。"""
    assert _strip_verbatim_echo("想你了，我也想你", "想你了") == "我也想你"
    assert terminal_reply_guard("想你了，我也想你。", user_msg="想你了") == "我也想你。"


def test_informative_verbatim_echo_is_blocked():
    """信息性逐字复读（非亲密语料）：整条=回声零附加 → 拦截。"""
    assert terminal_reply_guard("我今天去了医院。", user_msg="我今天去了医院。") == ""


def test_echo_line_inside_multiline_reply_is_stripped():
    """多行回复：回声行剥除、其余行保留（不再只看首行）。"""
    out = terminal_reply_guard("今天好累。\n想你了\n早点休息。", user_msg="想你了")
    assert out == "今天好累。\n早点休息。"


def test_echo_suffix_is_stripped():
    """行尾镜像回声同样剥除：「我也想你，想你了」→「我也想你」。"""
    assert _strip_verbatim_echo("我也想你，想你了", "想你了") == "我也想你"


def test_unclean_echo_boundary_is_kept():
    """边界不干净（回声后直接接正文、无标点）→ 保守不剥，避免切残正文。"""
    raw = "想你了的时候我就看看照片"
    assert _strip_verbatim_echo(raw, "想你了") == raw


def test_fullwidth_punct_echo_is_normalized():
    """全半角/标点归一后仍判回声：全角叹号、空白不影响判定。"""
    assert _strip_verbatim_echo("想你了！！", "想你了") == ""


# ═══════════════════════════════════════════════════════════
# 流式：首屏前判定 / 已发前缀永不回滚
# ═══════════════════════════════════════════════════════════


def test_stream_duplicate_first_sentence_never_emits():
    """真流式：首句即与历史重复 → 首屏前丢弃，不产生任何已发前缀。"""
    guard = IncrementalReplyGuard(
        user_msg="今天怎么样",
        reply_mode="immersive",
        recent_assistant_messages=[_PROD_5641],
    )
    assert guard.feed(_PROD_5641) == []
    assert guard.emitted_text == ""
    assert guard.finish() == ""


def test_stream_already_emitted_prefix_is_not_retracted():
    """已发前缀不可撤回：后续重复句只能丢弃，前缀文本保持不变。"""
    previous = "我今天下午去了一趟医院，医生说只是普通感冒，让我多喝水多休息。"
    guard = IncrementalReplyGuard(
        user_msg="检查结果怎么样",
        reply_mode="immersive",
        recent_assistant_messages=[previous],
    )
    first = guard.feed("今天风挺大的。")
    assert first == ["今天风挺大的。"]
    assert guard.feed(previous) == []
    assert guard.emitted_text == "今天风挺大的。"


def test_stream_action_only_never_emits_and_keeps_raw_for_retry():
    """动作-only 在流式首屏前判空；raw_text 留给上层做一次同模型重生成。"""
    guard = IncrementalReplyGuard(user_msg="在干嘛", reply_mode="immersive")
    assert guard.feed("（她低头笑了笑，把杯子推过来）") == []
    assert guard.emitted_text == ""
    # 未加句号的括号段会留在 finish() 余量里交给上层丢弃/重生成；
    # 关键是它从未进入 emitted_text（首屏前判定）。
    assert guard.finish() == "（她低头笑了笑，把杯子推过来）"
    assert is_action_only_reply(guard.raw_text, "immersive") is True


# ═══════════════════════════════════════════════════════════
# 历史透传：提取与 _prepare_context 接线
# ═══════════════════════════════════════════════════════════


def test_extract_recent_assistant_messages_limits_and_filters():
    rows = [{"role": "user", "content": "u0"}]
    for i in range(10):
        rows.append({"role": "assistant", "content": f"a{i}"})
        rows.append({"role": "user", "content": f"u{i + 1}"})
    rows.append({"role": "assistant", "content": "刚才没接上，你再说一句？"})
    out = extract_recent_assistant_messages(rows, limit=8)
    assert out == [f"a{i}" for i in range(2, 10)], "只取最近 8 条且排除兜底池句"


@pytest.mark.asyncio
async def test_prepare_context_exposes_recent_assistant_messages(monkeypatch):
    """_prepare_context 必须把 assistant 历史挂到 ctx 上供终审消费。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    rows: list[dict] = []
    for i in range(10):
        rows.append({"role": "user", "content": f"u{i}"})
        rows.append({"role": "assistant", "content": f"a{i}"})

    class _Mem:
        def get_recent_context(self, n, session_id="", character_id=""):
            return ""

        def retrieve_context(self, query, session_id, top_k, character_id=""):
            return {}

        def get_chat_context(self, session_id="", keep_recent=20, character_id="", summarize=True):
            return rows, ""

    orch = OptimizedOrchestrator()
    orch._initialized = True
    orch.components = {
        "memory": _Mem(),
        "rag": SimpleNamespace(retrieve=lambda query, character_id="": {"results": []}),
        "emotion": SimpleNamespace(analyze=lambda msg, ctx: None),
        "persona": SimpleNamespace(build_system_prompt=lambda **kw: "sys"),
    }
    monkeypatch.setattr(
        "utils.topic_state.get_topic_snapshot", lambda *a, **k: {}
    )
    ctx = await orch._prepare_context("你好", "s-quality", "default")
    assert ctx["recent_assistant_messages"] == [f"a{i}" for i in range(2, 10)]


# ═══════════════════════════════════════════════════════════
# 主动线 / 提醒线守卫扩面（2026-11-21 治理批缺陷 3）
# ═══════════════════════════════════════════════════════════


def test_reminder_recent_assistant_texts_extracts_and_fails_open():
    """提醒线历史读取辅助：取到 → 提取最近 assistant 正文；取不到 → None。"""
    from proactive.reminder_delivery import ReminderDeliveryTask

    rows = [
        {"role": "assistant", "content": "把手机放下，早点休息。"},
        {"role": "user", "content": "知道了"},
        {"role": "assistant", "content": "在等你呀。"},
    ]
    task = ReminderDeliveryTask(
        structured_memory=None,
        memory=SimpleNamespace(get_chat_context=lambda **kw: (rows, "")),
    )
    assert task._recent_assistant_texts("9:web:abc12345", "default") == [
        "把手机放下，早点休息。",
        "在等你呀。",
    ]
    # memory 缺位 → None（守卫按缺省行为放行，不把提醒线打哑）
    assert (
        ReminderDeliveryTask(None, memory=None)._recent_assistant_texts("9:web:abc")
        is None
    )

    def _boom(**kw):
        raise RuntimeError("db down")

    task2 = ReminderDeliveryTask(None, memory=SimpleNamespace(get_chat_context=_boom))
    assert task2._recent_assistant_texts("9:web:abc") is None
    assert task2._recent_assistant_texts("") is None
    # 无 assistant 行 → None
    task3 = ReminderDeliveryTask(
        None,
        memory=SimpleNamespace(
            get_chat_context=lambda **kw: ([{"role": "user", "content": "嗨"}], "")
        ),
    )
    assert task3._recent_assistant_texts("9:web:abc") is None


def test_proactive_and_reminder_guards_are_wired_with_history():
    """治理批缺陷 3 结构断言：主动线 ``_deliver`` 与提醒线（正文线 + 兜底线）
    的每处 ``terminal_reply_guard`` 调用都必须透传 ``recent_assistant_messages``
    ——删掉任一 kwarg 本测试即红，防止历史重复闸再次只剩聊天主链覆盖。
    """
    import ast
    import inspect
    import textwrap

    from proactive import reminder_delivery, scheduler

    def _assert_all_guard_calls_pass_history(src: str, where: str) -> int:
        tree = ast.parse(textwrap.dedent(src))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "terminal_reply_guard"
        ]
        assert calls, f"{where}: 未找到 terminal_reply_guard 调用"
        for node in calls:
            kw_names = {kw.arg for kw in node.keywords}
            assert "recent_assistant_messages" in kw_names, (
                f"{where}: 行 {node.lineno} 的 terminal_reply_guard 调用未透传 "
                "recent_assistant_messages"
            )
        return len(calls)

    n_deliver = _assert_all_guard_calls_pass_history(
        inspect.getsource(scheduler.ProactiveScheduler._deliver), "scheduler._deliver"
    )
    n_reminder = _assert_all_guard_calls_pass_history(
        inspect.getsource(reminder_delivery), "reminder_delivery"
    )
    assert n_deliver >= 1
    assert n_reminder >= 2, "提醒线正文与兜底两条路径都要接历史闸"
