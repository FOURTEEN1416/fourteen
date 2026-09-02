"""控制台聊天模式 — 从 main.py 迁出（2026-08-28 屎山治理）。

原 main.run_console_chat 复杂度 24（CODE_GRAPH §7 热点），拆分为
命令处理函数 + 主循环分派；full/fast 双模式的差异全部收敛到各处理函数内部。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

logger = logging.getLogger("main")


def _memory_of(orch: Any):
    return getattr(orch, "_memory", None)


def _is_full(orchestrator_mode: str) -> bool:
    return orchestrator_mode == "full"


def _start_session(orch: Any, session_id: str) -> None:
    mem = _memory_of(orch)
    if mem is None:
        return
    with contextlib.suppress(Exception):
        mem.working.start_session(session_id, "console")


def _reset_session(orch: Any) -> None:
    mem = _memory_of(orch)
    if mem is None:
        print("[WARN] 当前模式不支持记忆重置")
        return
    try:
        mem.structured_memory.clear_session("console")
        print("[OK] 记忆已重置")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 重置失败: {e}")


def _print_emotion_state(emotion: Any) -> None:
    state = emotion.state
    print(f"  情感: {state.primary_emotion.value} | "
          f"强度: {state.primary_intensity:.2f} | "
          f"能量: {state.energy:.1f} | "
          f"好感度: {state.affinity}")


def _cmd_status(orch: Any, mode: str, emotion_engine: Any, ase_engine: Any) -> None:
    if _is_full(mode):
        if emotion_engine:
            _print_emotion_state(emotion_engine)
            if ase_engine:
                print(f"  主动消息紧迫度: {ase_engine.urgency:.2f}")
        return
    emotion = orch.components.get("emotion")
    memory = orch.components.get("memory")
    if emotion and memory:
        _print_emotion_state(emotion)
        if hasattr(memory, "structured_memory"):
            print(f"  今日对话: {memory.structured_memory.count_chats_today()} 条")


def _cmd_health(orch: Any, mode: str) -> None:
    if _is_full(mode):
        from observability.health import health_checker
        print(f"  系统状态: {health_checker.check()}")
        return
    health = orch.health_check()
    print(f"  系统状态: {'[OK] 健康' if health['healthy'] else '[WARN] 异常'}")
    for name, status in health.get("components", {}).items():
        print(f"    {name}: {status}")


def _process_message(orch: Any, query: str, session_id: str) -> dict[str, Any]:
    result = orch.process_message(query, session_id)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return result


def run_console_chat(orchestrator_or_obj: Any, orchestrator_mode: str,
                     emotion_engine: Any = None, ase_engine: Any = None) -> None:
    print("\n" + "=" * 50)
    print(f"  [CHAT] 控制台聊天模式 ({orchestrator_mode} 模式)")
    print("  命令: /quit 退出  /status 查看状态  /health 健康检查  /reset 重置记忆")
    print("=" * 50 + "\n")

    session_id = f"console_{int(time.time())}"
    if _is_full(orchestrator_mode):
        _start_session(orchestrator_or_obj, session_id)

    commands = {
        "/status": lambda: _cmd_status(orchestrator_or_obj, orchestrator_mode,
                                       emotion_engine, ase_engine),
        "/health": lambda: _cmd_health(orchestrator_or_obj, orchestrator_mode),
        "/reset": lambda: _reset_session(orchestrator_or_obj),
    }

    try:
        while True:
            try:
                query = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[BYE] 下次再来找我哦~")
                break

            if not query:
                continue
            if query == "/quit":
                print("[BYE] 笨蛋, 记得想我！")
                break
            handler = commands.get(query)
            if handler is not None:
                handler()
                continue

            result = _process_message(orchestrator_or_obj, query, session_id)
            reply = result.get("reply", "")
            emotion = result.get("emotion")

            emotion_tag = ""
            if emotion:
                emotion_tag = f" [{emotion.get('primary', {}).get('type', '')}]"

            print(f"十四 > {reply}{emotion_tag}")

    except Exception:
        logger.exception("控制台聊天异常")
