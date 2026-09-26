"""W3 块6 —— 两个启动入口的后台装配必须同源（缺陷 G）。

钉的现实（2026-09-27 排查）：`main.py` 与 `api/run_api.py` 各自手写一遍
"调度器怎么变成会投递的后台运行时"，抄漏了三样：

- **提醒到期任务**：只有 run_api 装配（`register_reminder_task`）。`python main.py`
  这条入口下提醒**永远不到点**——`set_reminder` 照单落库、工具回"已设置"，
  但没有任何读者（v1.30「说了会叫却没叫」同族：写了却没人跑）。
- **LLM 主动决策注入**（`set_llm_provider`）：只有 run_api 注入，main 形态
  主动消息退化成模板生成。
- **`_send` 兜底**：main 把它覆盖成"ws 广播 + 微信全员 send_text"——一次
  不被记账、不扣配额、无归属的**跨用户广播**旁路（P1-21 已判定禁止，
  却在 main 入口回归）。

修法：装配下沉到唯一 owner `proactive.runtime_assembly`；两入口只各自注入
"本机通道怎么发"（拓扑差异：run_api 走控制面、main 单进程直发），
其余（提醒任务构造、LLM 注入、失败即报不假成功）只有一份实现。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from proactive.reminder_delivery import ChannelNotReadyError, ReminderDeliveryTask
from proactive.runtime_assembly import (
    install_llm_decision,
    install_reminder_delivery,
    make_ws_sender,
)
from shisi.memory.legacy.structured_memory import StructuredMemory

WECHAT_KEY = "7:o9cq80wx@im.wechat"
WEB_KEY = "7:web:c1a2"


class _FakeMemory:
    def __init__(self, sm=None):
        self.structured_memory = sm


class _FakeOrchestrator:
    def __init__(self, components: dict):
        self.components = components


class _FakeScheduler:
    """只暴露装配契约：注册提醒任务 / 注入 LLM / 注册通道 / 不许被塞广播兜底。"""

    def __init__(self):
        self._reminder_task = None
        self._llm_provider = "unset"
        self._send = "init_mixin_log_only"  # _init_mixin 给的仅留痕兜底
        self.channels: list[str] = []
        self.delivery_loop = None

    def register_reminder_task(self, task) -> None:
        self._reminder_task = task

    def set_llm_provider(self, llm) -> None:
        self._llm_provider = llm

    def register_channel(self, name, factory) -> None:
        self.channels.append(name)

    def set_delivery_loop(self, getter) -> None:
        self.delivery_loop = getter


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(db_path=str(tmp_path / "asm.db"))
    yield memory
    memory.close()


@pytest.fixture()
def orch(sm):
    return _FakeOrchestrator({"memory": _FakeMemory(sm), "llm": object()})


# ── 公共装配本体 ──────────────────────────────────────────


def test_install_registers_reminder_task(orch):
    sched = _FakeScheduler()
    ok = install_reminder_delivery(
        sched, orchestrator=orch,
        wechat_sender=lambda o, p, t: True, ws_sender=lambda k, t: True,
    )
    assert ok is True
    assert isinstance(sched._reminder_task, ReminderDeliveryTask)


def test_install_without_structured_memory_fails_loudly():
    """缺依赖必须返回 False 且不注册：装配失败却报成功 = 提醒静默不跑。"""
    sched = _FakeScheduler()
    broken = _FakeOrchestrator({"memory": _FakeMemory(None), "llm": None})
    assert install_reminder_delivery(sched, orchestrator=broken) is False
    assert sched._reminder_task is None


async def test_local_wechat_sender_is_directed_not_broadcast(orch):
    """plane=None（单进程入口）时投递走注入的本机发送器，且按 owner:peer 定向。"""
    sent: list[tuple] = []
    sched = _FakeScheduler()
    install_reminder_delivery(
        sched, orchestrator=orch,
        wechat_sender=lambda owner, peer, text: sent.append((owner, peer, text)) or True,
    )
    task = sched._reminder_task
    assert task._plane is None
    assert await task._send_to_session(WECHAT_KEY, "该喝水了") is True
    # peer 必须**带** @im.wechat 后缀：它是 wxid 自身的一部分（剥掉即发错目标，
    # main.py 的 `_wechat_sender_factory` 同一教训）
    assert sent == [(7, "o9cq80wx@im.wechat", "该喝水了")]


async def test_local_ws_sender_is_directed_by_session_key(orch):
    calls: list[tuple] = []
    sched = _FakeScheduler()
    install_reminder_delivery(
        sched, orchestrator=orch,
        ws_sender=lambda key, text: calls.append((key, text)) or True,
    )
    assert await sched._reminder_task._send_to_session(WEB_KEY, "早") is True
    assert calls == [(WEB_KEY, "早")]


def test_llm_decision_injected(orch):
    sched = _FakeScheduler()
    assert install_llm_decision(sched, orchestrator=orch) is True
    assert sched._llm_provider is orch.components["llm"]


def test_llm_decision_absent_reports_false(orch):
    sched = _FakeScheduler()
    empty = _FakeOrchestrator({"memory": _FakeMemory(orch.components["memory"])})
    assert install_llm_decision(sched, orchestrator=empty) is False
    assert sched._llm_provider == "unset"


# ── websocket 发送器：定向 + 跨循环桥接 ───────────────────


class _FakeWsServer:
    def __init__(self, delivered: int):
        self.delivered = delivered
        self.calls: list[tuple] = []
        self.bound_loop = None

    async def send_proactive_to_session(self, session_key: str, text: str) -> int:
        # 记录执行这条协程的循环，用于钉「桥到了 WS 所属循环」
        self.bound_loop = asyncio.get_running_loop()
        self.calls.append((session_key, text))
        return self.delivered


def test_ws_sender_reports_no_connection_as_failure():
    """0 送达必须判 False：否则提醒被记成「已投递」而用户什么都没收到。"""
    server = _FakeWsServer(delivered=0)
    sender = make_ws_sender(lambda: server)
    assert asyncio.run(sender(WEB_KEY, "早")) is False
    assert server.calls == [(WEB_KEY, "早")]


def test_ws_sender_without_server_is_not_ready(tmp_path):
    """WS 服务没起来（--no-api / 尚未启动）= 通道未就绪，不是「发送失败」。

    计入 fail_count 会让提醒在三次轮询（3 分钟）后被判死——用户只是没开控制台，
    她却永远不叫了（v1.30「说了会叫却没叫」同族）。
    """
    sender = make_ws_sender(lambda: None)
    with pytest.raises(ChannelNotReadyError):
        asyncio.run(sender(WEB_KEY, "早"))


def test_ws_sender_bridges_to_ws_loop():
    """连接对象属于 WS 线程自建循环；在别的循环上直接 await 属跨循环未定义行为。"""
    ws_loop = asyncio.new_event_loop()

    def _serve() -> None:
        asyncio.set_event_loop(ws_loop)
        ws_loop.run_forever()

    threading.Thread(target=_serve, daemon=True).start()
    server = _FakeWsServer(delivered=1)
    try:
        sender = make_ws_sender(lambda: server, lambda: ws_loop)
        assert asyncio.run(sender(WEB_KEY, "早")) is True
        assert server.bound_loop is ws_loop
    finally:
        ws_loop.call_soon_threadsafe(ws_loop.stop)
        ws_loop.call_soon_threadsafe(ws_loop.close)


# ── main.py 入口必须吃到同一份装配 ────────────────────────


def test_main_entry_wires_reminder_task_and_llm(orch):
    import main as main_entry

    hook = getattr(main_entry, "_wire_proactive_runtime", None)
    assert callable(hook), "main.py 没有可测的装配入口（仍在 _run_orchestrator 内联抄写）"
    sched = _FakeScheduler()
    hook(sched, orchestrator=orch, user_mgr=None, ws_holder={}, wechat_holder={})
    assert isinstance(sched._reminder_task, ReminderDeliveryTask), (
        "main 入口未装配提醒到期任务：这条入口下提醒永不到点"
    )
    assert sched._llm_provider is orch.components["llm"]
    # 通道注册也必须在同一个装配函数里（收口时漏掉就等于主动消息整条断线）
    assert sorted(sched.channels) == ["console", "wechat"]
    assert callable(sched.delivery_loop)


def test_run_orchestrator_actually_calls_the_wiring(orch):
    """装配函数存在 ≠ 启动路径调用它——钉住 `_run_orchestrator` 里的调用点。"""
    import ast

    import main as main_entry

    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_orchestrator"
    )
    called = {
        n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_wire_proactive_runtime" in called, (
        "_run_orchestrator 不再调用装配函数：函数留着不接等于缺陷 G 未修"
    )
    assert main_entry._wire_proactive_runtime is not None


def test_main_entry_does_not_install_broadcast_fallback(orch):
    """`_send` 保持"仅留痕"：不得覆盖成 ws 广播 + 微信全员直发。"""
    import main as main_entry

    sched = _FakeScheduler()
    main_entry._wire_proactive_runtime(
        sched, orchestrator=orch, user_mgr=None, ws_holder={}, wechat_holder={},
    )
    assert sched._send == "init_mixin_log_only"


def test_main_entry_source_has_no_broadcast_sender_copy():
    """手抄的广播发送器必须整删，而不是"留着不调"（留着就会被下一次抄回去）。"""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_create_proactive_sender" not in src
    assert "scheduler._send =" not in src, "main 仍在覆盖投递兜底 = 无归属跨用户广播"
    assert "scheduler._daily_maintenance =" not in src, (
        "_daily_maintenance 的唯一 owner 是 orchestrator/_init_mixin 的构造参数"
    )


# ── 通道未就绪 ≠ 投递失败（不吃失败配额、不判死） ─────────


class _RecordingSM:
    def __init__(self):
        self.marks: list[tuple] = []

    def mark_reminder_result(self, reminder_id, delivered):
        self.marks.append((reminder_id, delivered))


def _sm_task(wechat_sender=None, ws_sender=None):
    sm = _RecordingSM()
    task = ReminderDeliveryTask(
        sm, wechat_sender=wechat_sender, ws_sender=ws_sender,
        character_id_resolver=lambda key: "default",
    )
    return sm, task


def test_channel_not_ready_does_not_consume_failure_budget():
    """未扫码/通道实例缺席时必须**跳过本轮不记账**，下分钟再试。

    旧行为：`_send_to_session` 把「通道未就绪」压成 False → fail_count 累加 →
    3 分钟判死。用户重启后慢了几分钟扫码，6 点的叫醒就永久失效了——这正是
    验收条款里的「不误 failed」。
    """
    def not_ready(owner, peer, text):
        raise ChannelNotReadyError("微信连接器未登录")

    sm, task = _sm_task(wechat_sender=not_ready)
    reminder = {
        "id": 41, "session_key": WECHAT_KEY, "content": "喝水",
        "trigger_time": "2026-09-27 06:00:00",
    }
    asyncio.run(task._deliver(reminder))
    assert sm.marks == [], f"通道未就绪却记账了失败配额: {sm.marks}"


def test_web_not_ready_skips_budget_end_to_end():
    """整链钉：装配出来的 ws 发送器（无服务）→ 投递层必须跳过本轮不记账。

    单独钉 `make_ws_sender` 或 `_deliver` 都留缝：只要 `_send_to_session` 的
    websocket 分支把 ChannelNotReadyError 吞进宽 `except Exception` 压成 False，
    「没开控制台」就会在 3 分钟后把提醒判死。
    """
    sm, task = _sm_task(ws_sender=make_ws_sender(lambda: None))
    asyncio.run(task._deliver({
        "id": 43, "session_key": WEB_KEY, "content": "喝水",
        "trigger_time": "2026-09-27 06:00:00",
    }))
    assert sm.marks == [], f"websocket 通道未就绪却记账了失败配额: {sm.marks}"


def test_real_send_failure_still_strikes():
    """反向钉：真实发送失败（连接器返回 False）仍按 3 次判死，不得借机改成无限重试。"""
    sm, task = _sm_task(wechat_sender=lambda owner, peer, text: False)
    asyncio.run(task._deliver({
        "id": 42, "session_key": WECHAT_KEY, "content": "喝水",
        "trigger_time": "2026-09-27 06:00:00",
    }))
    assert sm.marks == [(42, False)]


def test_main_wechat_sender_reports_not_ready_not_failure(orch):
    """main 的微信发送器必须区分「还没扫码」与「发了但失败」。"""
    import main as main_entry

    sched = _FakeScheduler()
    holder: dict = {}
    main_entry._wire_proactive_runtime(
        sched, orchestrator=orch, user_mgr=None, ws_holder={}, wechat_holder=holder,
    )
    sender = sched._reminder_task._wechat_sender
    with pytest.raises(ChannelNotReadyError):
        sender(7, "o9cq80wx@im.wechat", "早")

    class _NoToken:
        token = ""

        def send_text(self, text, to_user=None):  # noqa: ANN001
            raise AssertionError("未登录不得真的发送")

    holder["connector"] = _NoToken()
    with pytest.raises(ChannelNotReadyError):
        sender(7, "o9cq80wx@im.wechat", "早")


# ── 单一真源：run_api 不再自己拼任务 ──────────────────────


def test_run_api_uses_shared_assembly():
    src = Path("api/run_api.py").read_text(encoding="utf-8")
    assert "ReminderDeliveryTask(" not in src, (
        "run_api 仍自己构造 ReminderDeliveryTask —— 两入口各抄一份就是漂移的根"
    )
    assert "install_reminder_delivery" in src


def test_assembly_module_does_not_import_api_eagerly():
    """装配层 import 期不得拉 `api.byok`：main 的 console 形态没有 FastAPI 栈。"""
    import proactive.runtime_assembly as ra

    source = Path(ra.__file__).read_text(encoding="utf-8")
    module_head = source.split("\ndef ", 1)[0]
    assert "from api.byok" not in module_head
    assert "import api.byok" not in module_head
