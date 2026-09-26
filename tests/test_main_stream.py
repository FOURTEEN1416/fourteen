"""main.py process_message_stream 核心路径测试。"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, ".")


def _emotion_state():
    return SimpleNamespace(
        primary_emotion=SimpleNamespace(value="开心"),
        to_dict=lambda: {"primary": "开心", "intensity": 0.8},
        affection_points=0.5,
    )


def _make_orchestrator(with_chat_stream: bool = True):
    from main import OptimizedOrchestrator

    orch = OptimizedOrchestrator()
    orch._initialized = True

    # 安全层 mock
    def safe_alternative(category: str) -> str:
        return f"[安全拦截:{category}]"

    safety = SimpleNamespace(
        check_input=lambda text: SimpleNamespace(is_safe=True, category=""),
        check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
        safe_alternative=safe_alternative,
    )

    # PII / 注入检测 mock
    pii = SimpleNamespace(anonymize=lambda text: (text, None))
    injection = SimpleNamespace(
        detect=lambda text: (False, None, None),
        sanitize=lambda text: text,
    )

    # 情感引擎 mock
    emotion = SimpleNamespace(
        analyze=lambda msg, context: _emotion_state(),
    )

    # 记忆 mock
    memory = SimpleNamespace(
        get_recent_context=lambda n, session_id="", character_id="": "最近上下文",
        retrieve_context=lambda query, session_id, top_k, character_id="": {"facts": ["喜欢猫"]},
        get_chat_context=lambda session_id: ([{"role": "user", "content": "hi"}], ""),
        after_chat=lambda **kwargs: None,
    )

    # RAG mock
    rag = SimpleNamespace(retrieve=lambda query: {"results": [{"content": "事实"}]})

    # 人格引擎 mock
    persona = SimpleNamespace(
        build_system_prompt=lambda **kwargs: "system prompt",
    )

    # ASE mock
    ase = SimpleNamespace(on_chat=lambda user_msg, reply: None)

    # 世界信息 mock
    world_info = SimpleNamespace(render=lambda: "世界信息")

    # LLM mock
    if with_chat_stream:
        async def chat_stream(**kwargs):
            for token in ["你", "好", "呀"]:
                yield token
        llm = SimpleNamespace(chat_stream=chat_stream)
    else:
        llm = SimpleNamespace()

    orch.components = {
        "safety": safety,
        "pii": pii,
        "injection": injection,
        "emotion": emotion,
        "memory": memory,
        "rag": rag,
        "persona": persona,
        "persona_extractor": None,
        "ase": ase,
        "world_info": world_info,
        "llm": llm,
    }
    return orch


def test_transport_confirms_only_accepted_text_before_history(monkeypatch):
    from unittest.mock import AsyncMock

    from my_character import consistency_checker

    orch = _make_orchestrator(False)
    orch._prepare_context = AsyncMock(return_value={
        "emotion_state": None, "system_prompt": "sys", "chat_history": [],
        "direct_reply": "第一段\n第二段", "ax_turn_id": "turn-accepted", "ax_reply_id": "reply-accepted",
    })
    monkeypatch.setattr(consistency_checker, "check_and_correct_reply", AsyncMock(return_value="第一段\n第二段"))
    history = []
    orch._after_process = lambda *a, **kw: history.append((a, kw)) or ""

    async def sender(result):
        assert history == [], "尚未获得发送回执却已记录为角色发言"
        assert result["reply"] == "第一段\n第二段"
        return "第一段"

    result = asyncio.run(orch.process_message(
        "你好", "4:peer", character_id="charA", reply_sender=sender,
    ))
    assert result["reply"] == "第一段"
    assert history[0][0][1] == "第一段"
    assert history[0][0][4] == "charA"
    assert history[0][1]["turn_id"] == "turn-accepted"


def test_transport_failed_reply_keeps_user_but_never_assistant(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from my_character import consistency_checker
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline
    from shisi.memory.legacy.structured_memory import StructuredMemory

    orch = _make_orchestrator(False)
    sm = StructuredMemory(str(tmp_path / "accepted.db"))
    memory = MemoryPipeline(vector_memory=SimpleNamespace(), structured_memory=sm)
    orch.components["memory"] = memory
    orch._prepare_context = AsyncMock(return_value={
        "emotion_state": None, "system_prompt": "sys", "chat_history": [],
        "direct_reply": "未发出的回复", "ax_turn_id": "turn-failed", "ax_reply_id": "reply-failed",
    })
    monkeypatch.setattr(consistency_checker, "check_and_correct_reply", AsyncMock(return_value="未发出的回复"))

    async def sender(reply):
        assert sm.get_chats_by_session("4:peer") == []
        return ""

    try:
        result = asyncio.run(orch.process_message(
            "实际收到的用户原话", "4:peer", character_id="charA", reply_sender=sender,
        ))
        orch._get_background_executor().shutdown(wait=True)
        assert result["reply"] == ""
        rows = sm.get_chats_by_session("4:peer")
        assert [(r["role"], r["content"]) for r in rows] == [("user", "实际收到的用户原话")]
        assert rows[0]["character_id"] == "charA"
        assert rows[0]["turn_id"] == "turn-failed"
    finally:
        memory._executor.shutdown(wait=True)
        sm.close()


async def _collect_stream(orch, **kwargs):
    events = []
    async for event in orch.process_message_stream(**kwargs):
        events.append(event)
    return events


def test_stream_not_initialized():
    from main import OptimizedOrchestrator
    orch = OptimizedOrchestrator()
    orch._initialized = False
    events = asyncio.run(_collect_stream(orch, user_msg="你好"))
    assert events[0] == {"type": "token", "content": "系统初始化中, 请稍候..."}
    assert events[-1]["type"] == "done"
    assert events[-1]["reply"] == "系统初始化中, 请稍候..."


def test_stream_true_path_yields_tokens_and_done():
    orch = _make_orchestrator(with_chat_stream=True)
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s1"))
    types = [e["type"] for e in events]
    assert types.count("token") >= 1
    assert "".join(e["content"] for e in events if e["type"] == "token") == "你好呀"
    assert types[-1] == "done"
    done = events[-1]
    assert done["reply"] == "你好呀"
    assert done["emotion"] is not None
    assert "process_time" in done


def test_stream_true_path_does_not_block_on_consistency_check(monkeypatch):
    """完整草稿轻量定稿后分块推送，额外一致性诊断仍在后台且不改已发内容。"""
    from my_character import consistency_checker

    orch = _make_orchestrator(with_chat_stream=True)
    orch.components["persona"] = SimpleNamespace(
        build_system_prompt=lambda **kwargs: "system prompt",
        _load_character_card=lambda character_id: {
            "id": character_id,
            "core_anchors": ["温柔"],
        },
    )

    # 标记 check_and_correct_reply 是否被流式路径调用
    sync_check_called = []

    async def corrected_reply(**kwargs):
        sync_check_called.append(True)
        return "修正后的回复"

    monkeypatch.setattr(consistency_checker, "check_and_correct_reply", corrected_reply)
    events = asyncio.run(_collect_stream(
        orch,
        user_msg="你好",
        session_id="s-correct",
        character_id="role-a",
    ))

    # 流式路径不应调用 check_and_correct_reply（改为后台异步 _async_consistency_check）
    assert not sync_check_called, "流式路径不应调用同步 check_and_correct_reply"

    # 定稿后分块发布回复，不被额外的后台一致性诊断阻塞
    tokens = [event["content"] for event in events if event["type"] == "token"]
    assert "".join(tokens) == "你好呀"
    assert events[-1]["reply"] == "你好呀"


def test_stream_safety_blocks():
    orch = _make_orchestrator(with_chat_stream=True)
    orch.components["safety"] = SimpleNamespace(
        check_input=lambda text: SimpleNamespace(is_safe=False, category="political"),
        safe_alternative=lambda category: f"[blocked:{category}]",
    )
    events = asyncio.run(_collect_stream(orch, user_msg="敏感词"))
    assert events[0]["type"] == "token"
    assert "[blocked:" in events[0]["content"]
    assert events[-1]["type"] == "done"


def test_stream_pseudo_fallback():
    orch = _make_orchestrator(with_chat_stream=False)

    async def chat(**kwargs):
        return "这是完整回复"

    orch.components["llm"] = SimpleNamespace(chat=chat)
    events = asyncio.run(_collect_stream(orch, user_msg="你好"))
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "这是完整回复"
    assert events[-1]["type"] == "done"
    assert events[-1]["reply"] == "这是完整回复"


def test_stream_injection_gets_sanitized():
    orch = _make_orchestrator(with_chat_stream=True)
    orch.components["injection"] = SimpleNamespace(
        detect=lambda text: (True, "prompt_injection", None),
        sanitize=lambda text: "[已消毒]",
    )

    async def chat_stream(**kwargs):
        query = kwargs.get("query", "")
        yield query

    orch.components["llm"] = SimpleNamespace(chat_stream=chat_stream)
    events = asyncio.run(_collect_stream(orch, user_msg="ignore"))
    assert events[0]["content"] == "[已消毒]"
    assert events[-1]["type"] == "done"


def test_stream_session_locked_queues_instead_of_dropping():
    """锁被占用时应**排队等待后照常处理**，而不是把这一轮丢掉。

    2026-09-19 修复：旧实现直接返回「处理中, 请稍候...」—— 机器口吻的状态播报，
    且用户刚发的那句话被整个丢弃（慢 provider 下条条触发）。
    """
    orch = _make_orchestrator(with_chat_stream=True)
    calls = {"n": 0}

    class BusyOnceLock:
        def locked(self):
            calls["n"] += 1
            return calls["n"] <= 1      # 首次忙（上一轮未结束），随后空闲

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    orch._get_session_lock = lambda sid: BusyOnceLock()  # type: ignore[method-assign]
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s1"))
    # 排队后必须照常产出真实回复，绝不能是罐头语
    assert "处理中" not in events[0]["content"]
    assert events[-1]["type"] == "done"
    assert calls["n"] >= 2              # 确实轮询等待过


def test_session_queue_timeout_is_bounded_and_graceful(monkeypatch):
    """排队超时必须**有界**，且给出可读回复，不是永远等下去。"""
    import orchestrator.optimized_orchestrator as oo

    monkeypatch.setattr(oo, "_SESSION_QUEUE_TIMEOUT", 0.3, raising=False)
    monkeypatch.setattr(oo, "_SESSION_QUEUE_POLL", 0.05, raising=False)
    orch = _make_orchestrator(with_chat_stream=True)

    class AlwaysBusyLock:
        def locked(self):
            return True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    orch._get_session_lock = lambda sid: AlwaysBusyLock()  # type: ignore[method-assign]
    t0 = time.monotonic()
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s1"))
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, f"排队必须有界，实测 {elapsed:.1f}s"
    assert "处理中" not in events[0]["content"]
    assert events[-1]["type"] == "done"


def test_stream_chat_stream_raises_with_no_tokens():
    orch = _make_orchestrator(with_chat_stream=True)

    async def failing_stream(**kwargs):
        if False:
            yield ""  # noqa: PIE798
        raise RuntimeError("llm fail")

    orch.components["llm"] = SimpleNamespace(chat_stream=failing_stream)
    events = asyncio.run(_collect_stream(orch, user_msg="你好"))
    assert events[0]["type"] == "token"
    assert "异常" in events[0]["content"]
    assert events[-1]["type"] == "done"
    assert events[-1]["emotion"] is None


def test_stream_chat_stream_raises_after_partial_tokens():
    orch = _make_orchestrator(with_chat_stream=True)
    memory_calls = []
    orch.components["memory"].after_chat = lambda **kwargs: memory_calls.append(kwargs)

    async def partial_then_fail(**kwargs):
        yield "前"
        yield "半"
        raise RuntimeError("llm fail mid-stream")

    orch.components["llm"] = SimpleNamespace(chat_stream=partial_then_fail)
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s2"))
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "前半"
    done = events[-1]
    assert done["type"] == "done"
    assert done["reply"] == "前半"
    # 验证 after_chat 被调用
    assert any(k.get("reply") == "前半" for k in memory_calls)


def test_stream_nonstream_gateway_raises():
    orch = _make_orchestrator(with_chat_stream=False)

    async def failing_process(*args, **kwargs):
        raise RuntimeError("process fail")

    orch.components["llm"] = SimpleNamespace(chat=failing_process)
    events = asyncio.run(_collect_stream(orch, user_msg="你好"))
    assert events[-1]["type"] == "done"
    assert "异常" in events[-1]["reply"]


def test_stream_empty_user_message_yields_done():
    orch = _make_orchestrator(with_chat_stream=True)
    events = asyncio.run(_collect_stream(orch, user_msg=""))
    assert events[-1]["type"] == "done"
    assert "process_time" in events[-1]


def test_stream_after_chat_exception_does_not_break_flow():
    orch = _make_orchestrator(with_chat_stream=True)

    def failing_after_chat(**kwargs):
        raise RuntimeError("memory fail")

    orch.components["memory"] = SimpleNamespace(
        get_recent_context=lambda n, session_id="", character_id="": "最近上下文",
        retrieve_context=lambda query, session_id, top_k, character_id="": {"facts": ["喜欢猫"]},
        get_chat_context=lambda session_id: ([{"role": "user", "content": "hi"}], ""),
        after_chat=failing_after_chat,
    )
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s3"))
    assert events[-1]["type"] == "done"
    assert events[-1]["reply"] == "你好呀"


def test_stream_retrieve_context_exception_returns_done():
    orch = _make_orchestrator(with_chat_stream=True)
    orch.components["memory"] = SimpleNamespace(
        get_recent_context=lambda n, session_id="", character_id="": "最近上下文",
        retrieve_context=lambda query, session_id, top_k, character_id="": (_ for _ in ()).throw(RuntimeError("context fail")),
        get_chat_context=lambda session_id: ([{"role": "user", "content": "hi"}], ""),
        after_chat=lambda **kwargs: None,
    )
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s4"))
    assert events[-1]["type"] == "done"


def test_stream_emotion_none_fallback():
    orch = _make_orchestrator(with_chat_stream=True)
    orch.components["emotion"] = SimpleNamespace(
        analyze=lambda msg, context: None,
    )
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s5"))
    assert events[-1]["type"] == "done"
    assert events[-1]["emotion"] is None


def test_stream_publishes_only_final_text_and_stores_the_same(monkeypatch):
    orch = _make_orchestrator()
    emitted = []
    committed = []

    async def raw_stream(**kwargs):
        yield "用户：我先睡了\nAssistant："
        assert emitted == [], "完整校验前不能向客户端泄露模型草稿"
        yield "我吃过了\n你也记得吃饭"

    orch.components["llm"] = SimpleNamespace(chat_stream=raw_stream)
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: committed.append(a[1]))

    async def collect():
        async for event in orch.process_message_stream("你好", "s", character_id="a"):
            emitted.append(event)

    asyncio.run(collect())
    text = "".join(e["content"] for e in emitted if e["type"] == "token")
    assert text == emitted[-1]["reply"] == "我吃过了\n你也记得吃饭"
    assert committed == [text]


def test_stream_direct_reply_skips_main_generation_and_keeps_history(monkeypatch):
    orch = _make_orchestrator()
    committed = []

    async def prepare(*args, **kwargs):
        return {"emotion_state": None, "system_prompt": "", "chat_history": [],
                "direct_reply": "你说的是明天上午还是下午？", "ax_turn_id": "turn"}

    async def unexpected_stream(**kwargs):
        raise AssertionError("直复不能再调用主模型")
        yield ""  # pragma: no cover

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: committed.append((a[1], kw["turn_id"])))
    orch.components["llm"] = SimpleNamespace(chat_stream=unexpected_stream)
    events = asyncio.run(_collect_stream(orch, user_msg="明天提醒我", session_id="s"))
    text = "".join(e["content"] for e in events if e["type"] == "token")
    assert text == events[-1]["reply"] == "你说的是明天上午还是下午？"
    assert committed == [(text, "turn")]


@pytest.mark.asyncio
async def test_user_gateway_reaches_auxiliary_threads_and_resets(monkeypatch):
    import llm_provider
    from utils.llm_bridge import current_llm, to_sync_callable

    orch = _make_orchestrator(True)
    selected = SimpleNamespace(chat_sync=lambda **kw: "user-result")
    monkeypatch.setattr(llm_provider, "get_user_llm", lambda uid, cfg: selected)
    observed = []

    async def prepare(*args, **kwargs):
        observed.append(current_llm())
        wrapped = to_sync_callable(SimpleNamespace(chat_sync=lambda **kw: "WRONG-platform"))
        assert await asyncio.to_thread(wrapped, "summary") == "user-result"
        return {"emotion_state": None, "system_prompt": "", "chat_history": [], "direct_reply": "正确回复"}

    monkeypatch.setattr(orch, "_prepare_context", prepare)
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: observed.append(current_llm()))
    await _collect_stream(orch, user_msg="你好", session_id="7:web:x", user_id=7, user_llm_config={"provider": "test"})
    assert observed == [selected, selected]
    assert current_llm() is None


@pytest.mark.asyncio
async def test_http_response_executes_real_orchestrator_before_persist(monkeypatch):
    from unittest.mock import AsyncMock

    from api.routers.chat_routes import _ChatDeliveryResponse
    from my_character import consistency_checker

    orch = _make_orchestrator(False)
    orch._prepare_context = AsyncMock(return_value={
        "emotion_state": None, "system_prompt": "", "chat_history": [], "direct_reply": "实际正文",
    })
    monkeypatch.setattr(consistency_checker, "check_and_correct_reply", AsyncMock(return_value="实际正文"))
    saved, frames = [], []
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: saved.append(a[1]))

    async def generate(publish):
        return await orch.process_message("你好", "7:web:asgi", reply_sender=publish)

    async def send(frame):
        assert saved == []
        frames.append(frame)

    await _ChatDeliveryResponse(generate, "7:web:asgi")({"type": "http"}, AsyncMock(), send)
    assert saved == ["实际正文"]
    assert len(frames) == 2


@pytest.mark.asyncio
async def test_cancel_during_send_keeps_accepted_reply_and_turn(monkeypatch):
    from unittest.mock import AsyncMock

    from my_character import consistency_checker

    orch = _make_orchestrator(False)
    orch._prepare_context = AsyncMock(return_value={
        "emotion_state": None, "system_prompt": "", "chat_history": [], "direct_reply": "已发文字",
        "ax_turn_id": "cancel-accepted",
    })
    monkeypatch.setattr(consistency_checker, "check_and_correct_reply", AsyncMock(return_value="已发文字"))
    stored = []
    orch.components["memory"].write_chat_history_sync = lambda **kw: stored.append(kw) or True
    started = asyncio.Event()
    release = asyncio.Event()

    async def sender(result):
        started.set()
        await release.wait()
        return result["reply"]

    task = asyncio.create_task(orch.process_message("用户话", "7:web:c", reply_sender=sender))
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stored[0]["reply"] == "已发文字"
    assert stored[0]["turn_id"] == "cancel-accepted"
    assert not orch._get_session_lock("7:web:c").locked()


def test_profile_sync_serializes_all_corrections_instead_of_skipping(monkeypatch):
    import threading

    from tools.builtin import profile_agent_tools as pa

    orch = _make_orchestrator(False)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    async def sync(llm, sid, text, reply, sm, **kwargs):
        calls.append((text, llm))
        if text == "旧生日":
            entered.set()
            await asyncio.to_thread(release.wait, 3)
        return []

    monkeypatch.setattr(pa, "run_profile_sync_agent", sync)
    first_llm, second_llm = object(), object()
    first = orch._enqueue_profile_sync("7:web:x", "旧生日", "", first_llm)
    try:
        assert entered.wait(3)
        second = orch._enqueue_profile_sync("7:web:x", "更正生日", "", second_llm)
        assert calls == [("旧生日", first_llm)]
    finally:
        release.set()
    first.result(timeout=5)
    second.result(timeout=5)
    assert calls == [("旧生日", first_llm), ("更正生日", second_llm)]


def test_stream_postprocess_failure_does_not_append_new_reply(monkeypatch):
    orch = _make_orchestrator(True)

    def fail(*args, **kwargs):
        raise RuntimeError("postprocess failed after transport acknowledgement")

    monkeypatch.setattr(orch, "_after_process", fail)
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="failed-post"))
    assert "".join(e["content"] for e in events if e["type"] == "token") == "你好呀"
    assert events[-1]["type"] == "error"
    assert events[-1]["reply"] == "你好呀"


@pytest.mark.parametrize("upstream_stream", [True, False])
@pytest.mark.parametrize("acknowledged_chunks", [0, 1])
def test_stream_disconnect_records_only_transport_acknowledged_prefix(monkeypatch, upstream_stream, acknowledged_chunks):
    from unittest.mock import AsyncMock

    from my_character import consistency_checker

    orch = _make_orchestrator(upstream_stream)
    draft = "abcdefghABCDEFGHijklmnop"
    committed = []
    orch._prepare_context = AsyncMock(return_value={
        "emotion_state": None, "system_prompt": "sys", "chat_history": [],
        "direct_reply": "", "ax_turn_id": "cancel-turn", "ax_reply_id": "cancel-reply",
    })
    monkeypatch.setattr(consistency_checker, "check_and_correct_reply", AsyncMock(side_effect=lambda **kw: kw["reply"]))
    monkeypatch.setattr(orch, "_after_process", lambda *a, **kw: committed.append((a, kw)) or "")

    async def stream(**kwargs):
        yield draft

    orch.components["llm"] = (
        SimpleNamespace(chat_stream=stream) if upstream_stream
        else SimpleNamespace(chat=AsyncMock(return_value=draft))
    )

    async def consume():
        gen = orch.process_message_stream("用户实际输入", "7:web:test", character_id="charA")
        try:
            for _ in range(acknowledged_chunks):
                token = await anext(gen)
                assert token["type"] == "token"
                token["_ack"]()
            pending = await anext(gen)
            assert pending["type"] == "token"
            assert committed == [], "未确认的全文不能提前落库"
        finally:
            await gen.aclose()
        assert not orch._get_session_lock("7:web:test").locked()

    asyncio.run(consume())
    assert len(committed) == 1
    args, kw = committed[0]
    assert args[0] == "用户实际输入"
    assert args[1] == draft[:8 * acknowledged_chunks]
    assert args[4] == "charA"
    assert kw["turn_id"] == "cancel-turn"


if __name__ == "__main__":
    test_stream_not_initialized()
    test_stream_true_path_yields_tokens_and_done()
    test_stream_safety_blocks()
    test_stream_pseudo_fallback()
    print("All main stream tests passed!")
