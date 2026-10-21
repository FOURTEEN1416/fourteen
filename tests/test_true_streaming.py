"""R4 主链真流式（设计复核 2026-10-21）：按句边生成边下发 + 增量守卫。

设计复核（必须逐条处理）指出的核心事实：``terminal_reply_guard`` 里有两类规则
在「逐句放行」下**不可能等价**——

1. **需要全文位置**：仅剩角色名（整串等值）、错误哨兵（整串包裹）、首行复述、
   行首表情复读、行首系统标记（``re.MULTILINE`` 的 ``^``）、称呼剥除的
   ``len<2 → ""`` 兜底；
2. **命中即截断其后**：无由头反问（命中句及其**后文**全删）。

本文件的用例把这批事实钉成**新语义**，不再声称「守卫等效」：

- 需要全文位置的规则 → **流末终裁**（未发余量交给 ``_finalize_reply``，唯一权威）；
- 截断类规则 → **命中即终止上游 + 丢弃全部未发**（已发前缀天然不受影响）；
- 逐句放行会误删/误留的两处（系统标记的行首判定、称呼剥除的 ``len<2``）→
  状态机显式携带「是否逻辑行首」与「不做 ``len<2`` 短串判定」两条修正，
  用例分别钉死正反两个方向。

另有两条已知**降级**（设计复核已确认理论不可同时满足，本实现显式登记）：
- 已发前缀若恰好是「仅剩角色名」的整串（``十四。``），首句延迟到「有后续内容
  或流末」才下发；流末仍为退化形态 → 丢弃并走 empty_reply 兜底（与批式一致）。
- 上游中断（异常/超时）：**按流末处理**，已收到内容全部终裁补发；不做任何重试
  或换供应商重生成（重放会半句+整句重复）。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

# ══════════════════════════════════════════════════════════════
#  一、IncrementalReplyGuard 单元面
# ══════════════════════════════════════════════════════════════


def _guard(**kwargs):
    from utils.stream_guard import IncrementalReplyGuard

    base = dict(user_msg="我累了", character_name="十四", reply_mode="immersive")
    base.update(kwargs)
    return IncrementalReplyGuard(**base)


def test_first_sentence_is_released_before_upstream_finishes():
    """句界放行：第一句完整即出，不依赖上游结束（首字节 = 首个句界耗时）。"""
    g = _guard()
    out = g.feed("我先说一句。后面还有")
    assert "".join(out) == "我先说一句。"
    assert g.finish() == "后面还有"


def test_no_terminator_holds_everything_until_finish():
    """没有句界就不放行（不完整尾巴永不下发）。"""
    g = _guard()
    assert g.feed("我还没说完") == []
    assert g.finish() == "我还没说完"


def test_truncating_rule_stops_upstream_and_drops_unsent():
    """无由头反问：命中即终止上游 + 丢弃该句其后全部未发内容。

    批式语义是 ``head = s[:m.start()]``（命中句及其后文全删）。逐句放行下
    「其后文」尚未生成，故等价实现只能是终止上游。
    """
    g = _guard(user_msg="我今天有点累")
    out = g.feed("A句。你呢？B句。")
    assert "".join(out) == "A句。"
    assert g.stop_requested is True
    assert g.finish() == "", "截断命中后不得再补发任何余量"


def test_bare_reciprocal_line_is_dropped_without_truncation():
    """裸「你呢」独占一行：只删该行，其余内容照常（不终止上游）。"""
    g = _guard(user_msg="我今天有点累")
    out = g.feed("A句。\n你呢\nB句。")
    text = "".join(out) + g.finish()
    assert "你呢" not in text
    assert "A句。" in text and "B句。" in text
    assert g.stop_requested is False


def test_reciprocal_with_head_keeps_prefix_of_hit_sentence():
    """命中句内部的前缀仍保留（对齐批式 ``s[:m.start()]``）。"""
    g = _guard(user_msg="我今天有点累")
    out = g.feed("我吃过了，你呢？B句。")
    assert "".join(out) == "我吃过了"
    assert g.stop_requested is True


def test_system_marker_mid_line_is_not_stripped():
    """逐句判定面漂移反向保护：行中标记必须原样保留（批式同样保留）。"""
    g = _guard()
    out = g.feed("我明白了。[主动] 七点吃饭啊")
    text = "".join(out) + g.finish()
    assert text == "我明白了。[主动] 七点吃饭啊"


def test_system_marker_at_line_start_is_stripped():
    """行首标记必须剥（生产泄漏形态）。"""
    g = _guard()
    out = g.feed("[主动] [10-15 18:27] 七点吃饭啊")
    text = "".join(out) + g.finish()
    assert text == "七点吃饭啊"


def test_single_char_line_survives_vocative_strip():
    """逐句调用 ``strip_forbidden_vocatives`` 会把「嗯」判成空串（复核实测）。

    状态机必须只在**整串**上使用 ``len<2 → ""``，逐句只做词边界剥除。
    """
    g = _guard(legal="你", forbidden=frozenset({"夏夏"}))
    out = g.feed("嗯\n我在呢。")
    text = "".join(out) + g.finish()
    assert text.startswith("嗯"), f"单字行被吞: {text!r}"
    assert "我在呢。" in text


def test_stage_direction_spanning_sentence_is_held_until_closed():
    """括号可跨句（``[^（）()]`` 含句号）：未闭合时不下发，闭合后整段剥净。"""
    g = _guard(reply_mode="immersive")
    assert g.feed("（想了想。还在想") == []
    out = g.feed("）今天好冷。")
    text = "".join(out) + g.finish()
    assert text == "今天好冷。"


def test_novel_mode_keeps_stage_directions():
    g = _guard(reply_mode="novel")
    text = "".join(g.feed("（想了想）今天好冷。")) + g.finish()
    assert "（想了想）" in text


def test_meta_memory_sentence_is_deleted_incrementally():
    g = _guard(user_msg="在吗")
    text = "".join(g.feed("我想确认一下你是不是真的记得。今天风挺大。")) + g.finish()
    assert "确认一下" not in text
    assert "今天风挺大。" in text


def test_attribution_flip_sentence_is_deleted_incrementally():
    g = _guard(user_msg="点完名就吃")
    text = (
        "".join(g.feed('哦，你是说刚刚我说的“点完名就吃”吗？今天风挺大。'))
        + g.finish()
    )
    assert "刚刚我说的" not in text
    assert "今天风挺大。" in text


def test_verbatim_echo_only_applies_to_first_line():
    g = _guard(user_msg="我今天有点累")
    text = "".join(g.feed("我今天有点累。那你早点休息。")) + g.finish()
    assert not text.startswith("我今天有点累")
    assert "那你早点休息。" in text


def test_emoji_echo_only_at_text_start():
    g = _guard(user_msg="[流泪]")
    text = "".join(g.feed("[流泪]别难过了。")) + g.finish()
    assert text == "别难过了。"


def test_degenerate_name_is_held_then_released_when_more_arrives():
    """整串只剩角色名 → 不得下发；有后续内容时按正常文本放行。"""
    g = _guard(character_name="十四")
    assert g.feed("十四。") == [], "退化形态必须扣住，否则无法在流末丢弃"
    out = g.feed("\n这就是我。")
    text = "".join(out) + g.finish()
    assert text == "十四。\n这就是我。"


def test_degenerate_name_alone_yields_nothing():
    g = _guard(character_name="十四")
    assert g.feed("十四。") == []
    assert g.finish() == "十四。", "流末交给 _finalize_reply 判死（它返回空串）"


def test_error_sentinel_is_not_released_as_content():
    g = _guard()
    assert g.feed("（所有 LLM 提供商均不可用）") == []
    assert g.finish() == "（所有 LLM 提供商均不可用）", "交给 _finalize_reply 判死"


def test_empty_chain_sentinel_is_recognized():
    """复核实测：空链走 ``yield "（没有可用的 LLM 提供商）"``（不是 raise）。"""
    from utils.prompt_sanitize import _EXTRA_ERROR_SENTINELS, terminal_reply_guard

    assert terminal_reply_guard("（没有可用的 LLM 提供商）") == ""
    # 防漂移：该字面量必须仍是网关 ``chat_stream`` 空链分支的真实产出
    import inspect

    from llm_provider import multi_provider_gateway as gw

    src = inspect.getsource(gw.MultiProviderGateway.chat_stream)
    for sentinel in _EXTRA_ERROR_SENTINELS:
        assert f'"（{sentinel}）"' in src or f"'（{sentinel}）'" in src, (
            f"哨兵「{sentinel}」已不在网关真源中，_EXTRA_ERROR_SENTINELS 需同步"
        )


def test_provider_mock_reply_is_not_sent_to_user():
    """复核实测：无 api_key 时 provider ``return self._mock_reply(query)``。

    经 ``chat_stream``（``if isinstance(result, str): yield result``）被当成正常
    token 流下来 —— 真流式必须把它判死（``_is_error_reply`` 命中）并走兜底，
    不能把「未配置 API Key」当角色台词发给用户。
    """
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        yield "（agnes 未配置 API Key，无法生成回复）"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-mock")))
    text = _text(events)
    assert "未配置 API Key" not in text
    assert text, "兜底句不得为空"


def test_soft_flush_breaks_run_on_sentence():
    """一逗到底的长句：达到软阈值后在逗号处放行（否则首字节 = 整轮）。"""
    g = _guard(user_msg="在吗")
    text = "嗯我在呢，刚忙完你说的那个事儿，现在能聊了，你先说"
    out = g.feed(text)
    assert out, "长句未在软边界放行（首字节退回整轮生成）"
    joined = "".join(out) + g.finish()
    assert joined == text, f"软放行改写了内容: {joined!r}"


def test_soft_flush_holds_when_sentence_deletable_trigger_present():
    """含可句删规则触发词时不得软放行（否则半句已经出去，句子级删除失效）。"""
    g = _guard(user_msg="在吗")
    out = g.feed("我今天挺好的呀，你呢")
    assert out == [], "含「你呢」时提前放行会让截断规则无法生效"
    assert g.stop_requested is False


def test_time_word_recalibration_respects_left_context():
    g = _guard(last_turn_age_seconds=7200.0)
    text = "".join(g.feed("我刚好路过。刚才想起你。")) + g.finish()
    assert "刚好" in text, "负向断言（刚好/刚巧）被跨句改写"
    assert "刚才" not in text and "之前想起你" in text


def test_stream_mode_default_is_true(monkeypatch):
    from utils import stream_guard

    monkeypatch.setattr(stream_guard, "_CONFIG_PATH", None, raising=False)
    assert stream_guard.read_stream_mode(None) == "true"


def test_stream_mode_reads_config_manager(monkeypatch):
    from utils import stream_guard

    cm = SimpleNamespace(get_config_dict=lambda **kw: {"chat": {"stream_mode": "buffered"}})
    assert stream_guard.read_stream_mode(cm) == "buffered"
    assert stream_guard.true_streaming_enabled(cm) is False


def test_stream_mode_reads_yaml_when_no_config_manager(tmp_path, monkeypatch):
    from utils import stream_guard

    p = tmp_path / "system.yaml"
    p.write_text("chat:\n  stream_mode: buffered\n", encoding="utf-8")
    monkeypatch.setattr(stream_guard, "_CONFIG_PATH", p)
    assert stream_guard.read_stream_mode(None) == "buffered"


def test_stream_mode_accepts_boolean_true(tmp_path, monkeypatch):
    from utils import stream_guard

    p = tmp_path / "system.yaml"
    p.write_text("chat:\n  stream_mode: true\n", encoding="utf-8")
    monkeypatch.setattr(stream_guard, "_CONFIG_PATH", p)
    assert stream_guard.read_stream_mode(None) == "true"


def test_stream_mode_survives_missing_schema_field(tmp_path, monkeypatch):
    """``SystemConfig`` 没有 ``chat`` 字段（复核实测 ``model_dump()`` 无该键）。

    因此开关必须能经 ``ConfigManager.get_config_dict()``（原始/生效字典，含
    schema 扩展键）读到——否则「每请求读取」在真实装配下永远落默认值。
    """
    from utils import stream_guard

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "system.yaml").write_text(
        "env: dev\nchat:\n  stream_mode: buffered\n", encoding="utf-8",
    )
    from observability.config_manager import ConfigManager

    cm = ConfigManager(config_dir=str(cfg_dir))
    assert not hasattr(cm.config, "chat"), "SystemConfig 若新增 chat 字段，本注释与本用例需同步"
    assert "chat" in cm.get_config_dict()
    assert stream_guard.read_stream_mode(cm) == "buffered"


def test_switched_default_system_yaml_says_true():
    """仓内 ``config/system.yaml`` 必须显式带 ``chat.stream_mode: true``。"""
    from utils import stream_guard
    from utils.project_paths import project_path

    raw = project_path("config", "system.yaml").read_text(encoding="utf-8")
    assert "chat:" in raw and "stream_mode: true" in raw
    assert stream_guard._normalize_mode(True) == "true"
    assert stream_guard._normalize_mode("buffered") == "buffered"


# ══════════════════════════════════════════════════════════════
#  二、编排层集成面（process_message_stream）
# ══════════════════════════════════════════════════════════════


def _orch():
    from main import OptimizedOrchestrator

    orch = OptimizedOrchestrator()
    orch._initialized = True
    orch.components = {
        "safety": SimpleNamespace(
            check_input=lambda text: SimpleNamespace(is_safe=True, category=""),
            check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
            safe_alternative=lambda category: f"[拦截:{category}]",
        ),
        "pii": SimpleNamespace(anonymize=lambda text: (text, None)),
        "injection": SimpleNamespace(
            detect=lambda text: (False, None, None), sanitize=lambda text: text,
        ),
        "emotion": SimpleNamespace(analyze=lambda msg, ctx: None),
        "memory": SimpleNamespace(
            get_recent_context=lambda n, session_id="", character_id="": "",
            retrieve_context=lambda *a, **k: {},
            get_chat_context=lambda session_id: ([], ""),
            after_chat=lambda **kwargs: None,
        ),
        "rag": SimpleNamespace(retrieve=lambda q: {"results": []}),
        "persona": SimpleNamespace(build_system_prompt=lambda **kw: "sys"),
        "persona_extractor": None,
        "ase": SimpleNamespace(on_chat=lambda *a, **k: None),
        "world_info": None,
        "llm": SimpleNamespace(),
    }
    return orch


def _prepare(orch, *, direct_reply="", last_turn_age_seconds=None):
    async def prepare(*args, **kwargs):
        return {
            "emotion_state": None,
            "system_prompt": "sys",
            "chat_history": [],
            "direct_reply": direct_reply,
            "chat_round": 0,
            "ax_turn_id": "turn",
            "ax_reply_id": "reply",
            "last_turn_age_seconds": last_turn_age_seconds,
        }

    orch._prepare_context = prepare


def _record_after(orch):
    calls: list = []
    orch._after_process = lambda *a, **kw: calls.append((a, kw))
    return calls


async def _collect(gen):
    events = []
    async for e in gen:
        events.append(e)
    return events


def _text(events):
    return "".join(e["content"] for e in events if e["type"] == "token")


def test_early_token_before_upstream_completes():
    """集成面：上游只产出第一句后挂死，客户端已经能收到第一句。"""
    orch = _orch()
    _prepare(orch)
    _record_after(orch)
    release = asyncio.Event()

    async def stream(**kw):
        yield "第一句。"
        await release.wait()
        yield "第二句。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)

    async def scenario():
        gen = orch.process_message_stream("你好", "s-early")
        first = await asyncio.wait_for(anext(gen), 2)
        assert first["type"] == "token"
        assert first["content"] == "第一句。"
        first["_ack"]()
        release.set()
        rest = await _collect(gen)
        return rest

    rest = asyncio.run(scenario())
    assert rest[-1]["type"] == "done"
    assert rest[-1]["reply"] == "第一句。第二句。"


def test_truncation_stops_upstream_and_done_excludes_dropped_text():
    orch = _orch()
    _prepare(orch)
    calls = _record_after(orch)
    consumed = {"n": 0}

    async def stream(**kw):
        for tok in ("A句。", "你呢？", "B句。"):
            consumed["n"] += 1
            yield tok

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(
        orch.process_message_stream("我今天有点累", "s-trunc")
    ))
    assert _text(events) == "A句。"
    assert events[-1]["reply"] == "A句。"
    assert calls and calls[0][0][1] == "A句。"
    assert consumed["n"] == 2, "截断命中后必须停止消费上游（不得把 B句 读完）"


def test_buffered_mode_restores_old_behaviour(monkeypatch):
    """开关回滚：``chat.stream_mode=buffered`` 时首句不得提前下发。"""
    from utils import stream_guard

    monkeypatch.setattr(stream_guard, "read_stream_mode", lambda cm=None: "buffered")
    orch = _orch()
    _prepare(orch)
    _record_after(orch)
    release = asyncio.Event()

    async def stream(**kw):
        yield "第一句。"
        await release.wait()
        yield "第二句。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)

    async def scenario():
        gen = orch.process_message_stream("你好", "s-buffered")
        task = asyncio.create_task(anext(gen))
        done, _ = await asyncio.wait({task}, timeout=0.3)
        assert not done, "buffered 模式不得提前下发"
        release.set()
        return await task

    first = asyncio.run(scenario())
    assert first["content"].startswith("第一句。")


def test_interruption_after_partial_tokens_is_flushed():
    """上游中断：已收到内容按流末终裁补发（不重试、不换供应商重生成）。"""
    orch = _orch()
    _prepare(orch)
    calls = _record_after(orch)

    async def stream(**kw):
        yield "前半"
        raise RuntimeError("llm fail mid-stream")

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-partial")))
    assert _text(events) == "前半"
    assert events[-1]["reply"] == "前半"
    assert calls[0][0][1] == "前半"


def test_interruption_before_any_token_keeps_error_sentence():
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        if False:
            yield ""
        raise RuntimeError("llm fail")

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-fail")))
    assert "异常" in events[0]["content"]
    assert events[-1]["type"] == "done"


def test_degenerate_reply_streams_fallback_line():
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        yield "十四。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(
        orch.process_message_stream("你好", "s-degen", character_id="default")
    ))
    text = _text(events)
    assert text and text != "十四。", "退化回复必须走 empty_reply 兜底"
    assert events[-1]["reply"] == text


def test_chunks_never_exceed_transport_size():
    """传输分块仍不得超过 8 字符（保留既有的 accepted_end 前缀算术）。"""
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        yield "一二三四五六七八九十。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-chunk")))
    for e in events:
        if e["type"] == "token":
            assert len(e["content"]) <= 8


def test_disconnect_keeps_only_acked_prefix():
    orch = _orch()
    _prepare(orch)
    calls = _record_after(orch)

    async def stream(**kw):
        yield "一二三四五六七八九十。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)

    async def scenario():
        gen = orch.process_message_stream("你好", "s-ack")
        first = await anext(gen)
        first["_ack"]()
        await gen.aclose()

    asyncio.run(scenario())
    assert calls[0][0][1] == "一二三四五六七八"


def test_direct_reply_still_skips_generation():
    orch = _orch()
    _prepare(orch, direct_reply="明天上午还是下午？")
    calls = _record_after(orch)

    async def unexpected(**kw):  # pragma: no cover
        raise AssertionError("直复不得调用主模型")
        yield ""

    orch.components["llm"] = SimpleNamespace(chat_stream=unexpected)
    events = asyncio.run(_collect(orch.process_message_stream("明天提醒我", "s-direct")))
    assert _text(events) == "明天上午还是下午？"
    assert calls[0][0][1] == "明天上午还是下午？"


def test_speaker_label_line_never_reaches_client():
    """复核条目「真流式少了四道关」：发言标签必须在**不完整尾巴**阶段就挡掉。

    批式下 ``sanitize_reply_text`` 在终裁时整行删除；真流式下若不提前拦，
    「Assistant：」这类标签会以尾巴形式等到句界后原样下发。
    """
    orch = _orch()
    _prepare(orch)
    calls = _record_after(orch)

    async def stream(**kw):
        yield "用户：我先睡了\nAssistant："
        yield "我吃过了\n你也记得吃饭"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-label")))
    text = _text(events)
    assert "用户：" not in text and "Assistant：" not in text
    assert text == "我吃过了\n你也记得吃饭"
    assert calls[0][0][1] == text


def test_format_noise_is_stripped_incrementally():
    """格式残留（分号噪声）在句界就清掉，不能只靠流末终裁。

    2026-10-14 生产实证的 markdown 粗体走 ``sanitize_reply_text``；真流式下首句
    不经过终裁，故增量守卫复用同一 ``strip_format_noise`` 真源。此处用分号噪声
    钉接线（粗体与 ``_STAGE_DIRECTION_RE`` 的既有交叠属批式侧历史行为，不在本批）。
    """
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        yield "好呀；；今天冷。"
        yield "再来一句。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-md")))
    text = _text(events)
    assert "；；" not in text, f"分号噪声漏到首句: {text!r}"
    assert text.startswith("好呀今天冷。")


def test_output_safety_gate_stops_before_release():
    """输出安全闸：命中句不下发，改由兜底文案收尾（未发任何前缀时）。"""
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    def check_output(text):
        unsafe = "危险" in text
        return SimpleNamespace(is_safe=not unsafe, category="violence" if unsafe else "")

    orch.components["safety"] = SimpleNamespace(
        check_input=lambda text: SimpleNamespace(is_safe=True, category=""),
        check_output=check_output,
        safe_alternative=lambda category: f"[拦截:{category}]",
    )

    async def stream(**kw):
        yield "这是危险内容。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-safe")))
    text = _text(events)
    assert "危险内容" not in text
    assert text == "[拦截:violence]"


def test_output_safety_after_prefix_does_not_append_replacement():
    """已有前缀时安全闸只终止后续，不改写已发内容（不回滚不追加）。"""
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    def check_output(text):
        unsafe = "危险" in text
        return SimpleNamespace(is_safe=not unsafe, category="violence" if unsafe else "")

    orch.components["safety"] = SimpleNamespace(
        check_input=lambda text: SimpleNamespace(is_safe=True, category=""),
        check_output=check_output,
        safe_alternative=lambda category: f"[拦截:{category}]",
    )

    async def stream(**kw):
        yield "先说一句。"
        yield "这是危险内容。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-safe2")))
    text = _text(events)
    assert text.startswith("先说一句。")
    assert "危险内容" not in text
    assert "[拦截" not in text


def test_degenerate_only_reply_does_not_emit_name():
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        yield "十四。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    events = asyncio.run(_collect(orch.process_message_stream("你好", "s-degen2")))
    text = _text(events)
    assert "十四。" not in text, "退化形态不得作为已发前缀出现"
    assert text


def test_soft_flush_keeps_one_to_end_comma_sentence_visible():
    """一逗到底的长句：必须在软边界放行，否则首字节 = 整轮（复核性能条目）。"""
    orch = _orch()
    _prepare(orch)
    _record_after(orch)
    release = asyncio.Event()

    async def stream(**kw):
        yield "嗯我在呢，刚忙完你说的那个事儿，现在能聊了，你先说"
        await release.wait()
        yield "第二句好了。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)

    async def scenario():
        gen = orch.process_message_stream("在吗", "s-soft")
        first = await asyncio.wait_for(anext(gen), 2)
        assert first["type"] == "token" and first["content"]
        first["_ack"]()
        release.set()
        return await _collect(gen)

    rest = asyncio.run(scenario())
    assert rest[-1]["reply"].startswith("嗯我在呢")


def test_slow_consumer_does_not_spend_llm_budget(monkeypatch):
    """P1（复核实测）：传输/消费耗时不得计入 LLM 预算。

    旧实现把 ``stream_deadline`` 在**生成开始前**一次性钉死（``_stream_mixin``
    旧 ``:405``），而 ``yield`` 阻塞在慢消费者上的时间照算，于是「客户端慢」
    被误判成「模型慢」：第 2 个 token 之前即抛 ``TimeoutError`` 并 ``aclose``
    上游 —— 用户只收到第一句。用例让每个传输块的消费耗时 = 单跳预算，断言
    **完整三句**仍然送达。
    """
    import orchestrator.optimized_orchestrator as om

    monkeypatch.setattr(om, "MAIN_CHAIN_LLM_TIMEOUT_SECONDS", 0.05)
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        for tok in ("第一句。", "第二句。", "第三句。"):
            yield tok

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)

    async def scenario():
        events = []
        async for e in orch.process_message_stream("你好", "s-backpressure"):
            if e["type"] == "token":
                # 慢消费者：每块传输耗时与单跳 LLM 预算同量级
                await asyncio.sleep(0.05)
            events.append(e)
        return events

    events = asyncio.run(scenario())
    assert _text(events) == "第一句。第二句。第三句。", "慢客户端截断了健康生成"
    assert events[-1]["reply"] == "第一句。第二句。第三句。"


def test_hung_upstream_still_times_out_within_budget(monkeypatch):
    """反方向保护：真正的挂死上游仍必须在单跳预算内失败（修 P1 不得取消超时）。"""
    import orchestrator.optimized_orchestrator as om

    monkeypatch.setattr(om, "MAIN_CHAIN_LLM_TIMEOUT_SECONDS", 0.1)
    orch = _orch()
    _prepare(orch)
    _record_after(orch)
    hang = asyncio.Event()

    async def stream(**kw):
        if False:  # pragma: no cover - 令其成为异步生成器
            yield ""
        await hang.wait()

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    started = time.monotonic()
    events = asyncio.run(asyncio.wait_for(
        _collect(orch.process_message_stream("你好", "s-hang")), 5,
    ))
    assert time.monotonic() - started < 3.0, "挂死上游未在单跳预算内失败"
    assert "异常" in events[0]["content"]


def test_llm_budget_still_accumulates_across_token_waits(monkeypatch):
    """反方向的行为契约：额度是**跨 token 累积**的（不是每次重置），慢上游仍会被砍。

    与 ``test_slow_consumer_does_not_spend_llm_budget`` 合起来才唯一确定语义：
    ①传输/消费不计入；②等 token 的耗时跨 token 累积。只满足 ① 而把计时器每轮
    重置的实现（等于取消了轮次预算）会在这里变红。
    """
    import orchestrator.optimized_orchestrator as om

    monkeypatch.setattr(om, "MAIN_CHAIN_LLM_TIMEOUT_SECONDS", 0.1)
    orch = _orch()
    _prepare(orch)
    _record_after(orch)

    async def stream(**kw):
        for i in range(1, 6):
            await asyncio.sleep(0.04)
            yield f"第{i}句。"

    orch.components["llm"] = SimpleNamespace(chat_stream=stream)
    started = time.monotonic()
    events = asyncio.run(asyncio.wait_for(
        _collect(orch.process_message_stream("你好", "s-slow-upstream")), 5,
    ))
    assert time.monotonic() - started < 3.0, "慢上游未被预算砍断"
    assert "第5句。" not in _text(events), "额度被逐 token 重置（等价于取消了轮次预算）"


@pytest.mark.parametrize("text", ["今天好冷。", "嗯。", "第一句。\n第二句。"])
def test_single_finalize_call_site_contract(text, monkeypatch):
    """AST 契约：``process_message_stream`` 内 ``_finalize_reply`` 调用点必须唯一。"""
    import ast
    import inspect

    from orchestrator._stream_mixin import _StreamPipelineMixin

    tree = ast.parse(inspect.cleandoc(
        inspect.getsource(_StreamPipelineMixin.process_message_stream)
    ))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_finalize_reply"
    ]
    assert len(calls) == 1
