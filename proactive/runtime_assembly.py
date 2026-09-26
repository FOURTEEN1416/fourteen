"""后台运行时的公共装配（缺陷 G）。

`main.py`（控制台/微信单进程）与 `api/run_api.py`（uvicorn 多 worker）此前
各抄一遍"把调度器装配成会投递的后台运行时"。抄写必然漏项，实测漏掉的是
**提醒到期任务**——`python main.py` 这条入口下 `set_reminder` 照单落库、
工具回"已设置"，却没有任何读者（v1.30「说了会叫却没叫」同族）。

本模块是唯一 owner：提醒任务的构造（依赖取用、归属解析、凭证解析）与
LLM 主动决策注入只有一份实现。两个入口只保留**真正属于自己拓扑**的那部分
——"本机通道怎么发"：run_api 的连接器/WS 可能挂在别的 worker，投递走控制面
（`plane=runtime_plane`）；main 单进程同域，直发注入的发送器（`plane=None`）。

⚠️ 不在本模块 import 期拉 `api.byok`：console 形态没有 FastAPI 生命周期，
装配层被 import 就把整个 API 栈拖进来（且 db 连接会绑到错误的事件循环）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("runtime_assembly")


def make_ws_sender(
    ws_getter: Callable[[], Any],
    loop_getter: Callable[[], Any] | None = None,
) -> Callable[[str, str], Any]:
    """websocket **定向**投递发送器（`(session_key, text) -> awaitable[bool]`）。

    两入口此前各写一遍，且都没做**跨循环桥接**：WS 连接属于 `_run_ws` 线程自建
    的那条循环，而提醒任务跑在进程级共享循环上——在共享循环里直接 await 别人
    循环的连接对象是未定义行为（P1-23 同一教训）。传入 `loop_getter` 时改投递到
    WS 所属循环执行，再 await 其 future。

    0 送达判 False：调用方（提醒投递）据此记失败，绝不"发出去了"假成功。
    """

    async def _send(session_key: str, text: str) -> bool:
        from proactive.reminder_delivery import ChannelNotReadyError

        ws_server = ws_getter()
        if ws_server is None:
            # 服务没起来（`--no-api`、或 WS 线程尚未就绪）——本轮跳过，不吃配额
            raise ChannelNotReadyError("websocket 服务未启动（无 ws 实例）")
        coro = ws_server.send_proactive_to_session(session_key, text)
        loop = loop_getter() if loop_getter is not None else None
        try:
            if isinstance(loop, asyncio.AbstractEventLoop) and not loop.is_closed():
                if loop is not asyncio.get_running_loop():
                    delivered = await asyncio.wrap_future(
                        asyncio.run_coroutine_threadsafe(coro, loop)
                    )
                else:
                    delivered = await coro
            else:
                delivered = await coro
        except Exception as e:  # noqa: BLE001
            logger.warning("websocket 定向投递异常 session=%s: %s", session_key, e)
            return False
        return int(delivered or 0) > 0

    return _send


def install_reminder_delivery(
    scheduler: Any,
    *,
    orchestrator: Any,
    user_mgr: Any = None,
    wechat_sender: Callable[[int, str, str], bool] | None = None,
    ws_sender: Callable[[str, str], bool] | None = None,
    plane: Any = None,
) -> bool:
    """装配提醒到期投递任务。返回是否真的装上（失败不静默）。

    Args:
        scheduler: 需要 `register_reminder_task`（`ProactiveScheduler`）。
        orchestrator: 取 `memory` / `structured_memory` / `llm`。
        user_mgr: 会话键 → 角色 的解析依赖（缺省则不回退他人角色）。
        wechat_sender / ws_sender: **本入口**的定向直发实现（plane=None 时用）。
        plane: 控制面模块；非 None 时投递改走 outbox，由通道宿主认领。
    """
    from proactive.reminder_delivery import ReminderDeliveryTask

    memory = (getattr(orchestrator, "components", None) or {}).get("memory")
    sm = getattr(memory, "structured_memory", None)
    if sm is None:
        logger.error("提醒投递未装配：structured_memory 不可用（提醒将永不到点）")
        return False
    register = getattr(scheduler, "register_reminder_task", None)
    if not callable(register):
        logger.error("提醒投递未装配：scheduler 无 register_reminder_task")
        return False

    def _character_id_resolver(session_key: str) -> str:
        from utils import character_resolver

        return character_resolver.resolve_character_id(session_key, user_mgr)

    async def _llm_resolver(session_key: str) -> Any:
        from api.byok import session_llm

        return await session_llm(session_key, orchestrator)

    register(ReminderDeliveryTask(
        sm,
        llm=(getattr(orchestrator, "components", None) or {}).get("llm"),
        wechat_sender=wechat_sender,
        ws_sender=ws_sender,
        memory=memory,
        character_id_resolver=_character_id_resolver,
        llm_resolver=_llm_resolver,
        plane=plane,
    ))
    logger.info(
        "提醒到期投递任务已装配（每分钟轮询，豁免静默时段；投递通道=%s）",
        "控制面 outbox" if plane is not None else "本机直发",
    )
    return True


def install_llm_decision(scheduler: Any, orchestrator: Any) -> bool:
    """把 LLM 网关注入调度器（主动消息由模型判断时机与内容）。

    旧现实只有 run_api 注入，main 形态的主动消息因此退化为模板生成。
    """
    llm = (getattr(orchestrator, "components", None) or {}).get("llm")
    if llm is None:
        logger.warning("主动消息 LLM 决策未注入：orchestrator 无 llm 组件")
        return False
    setter = getattr(scheduler, "set_llm_provider", None)
    if not callable(setter):
        logger.warning("主动消息 LLM 决策未注入：scheduler 不支持 set_llm_provider")
        return False
    setter(llm)
    logger.info("主动消息 LLM 决策已注入调度器")
    return True
