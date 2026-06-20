"""main.py process_message_stream 核心路径测试。"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

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
        get_recent_context=lambda n: "最近上下文",
        retrieve_context=lambda query, session_id, top_k: {"facts": ["喜欢猫"]},
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
    assert types.count("token") >= 3
    assert types[-1] == "done"
    done = events[-1]
    assert done["reply"] == "你好呀"
    assert done["emotion"] is not None
    assert "process_time" in done


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

    async def fake_process_message(*args, **kwargs):
        return {
            "reply": "这是完整回复",
            "emotion": _emotion_state().to_dict(),
            "process_time": 0.1,
        }

    orch.process_message = fake_process_message
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


def test_stream_session_locked_returns_wait():
    orch = _make_orchestrator(with_chat_stream=True)

    class FakeLock:
        def locked(self):
            return True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    orch._get_session_lock = lambda sid: FakeLock()  # type: ignore[method-assign]
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s1"))
    assert events[0]["content"] == "处理中, 请稍候..."
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


def test_stream_pseudo_fallback_process_message_raises():
    orch = _make_orchestrator(with_chat_stream=False)

    async def failing_process(*args, **kwargs):
        raise RuntimeError("process fail")

    orch.process_message = failing_process
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
        get_recent_context=lambda n: "最近上下文",
        retrieve_context=lambda query, session_id, top_k: {"facts": ["喜欢猫"]},
        get_chat_context=lambda session_id: ([{"role": "user", "content": "hi"}], ""),
        after_chat=failing_after_chat,
    )
    events = asyncio.run(_collect_stream(orch, user_msg="你好", session_id="s3"))
    assert events[-1]["type"] == "done"
    assert events[-1]["reply"] == "你好呀"


def test_stream_retrieve_context_exception_returns_done():
    orch = _make_orchestrator(with_chat_stream=True)
    orch.components["memory"] = SimpleNamespace(
        get_recent_context=lambda n: "最近上下文",
        retrieve_context=lambda query, session_id, top_k: (_ for _ in ()).throw(RuntimeError("context fail")),
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


if __name__ == "__main__":
    test_stream_not_initialized()
    test_stream_true_path_yields_tokens_and_done()
    test_stream_safety_blocks()
    test_stream_pseudo_fallback()
    print("All main stream tests passed!")
