"""W3 块3：出站生产者接线（提醒走控制面 + run_api 装配形态）。

缺陷 A/C/E 的**发起侧**判据：所有出站（主动/提醒/手动）都经控制面 outbox
入队（保留 owner/slot/peer/character/turn 快照），并**等待受理回执**才判成功；
run_api 对 owner 全 slot 逐一发不 break 的旧循环必须废除。

`api/run_api.py` 在 import 期就初始化编排器与线程，测试不可导入 →
装配形态用 AST 逐点钉住（同 `test_async_bridge_contract.py` 口径）；
提醒投递路径为真实行为测试（临时数据根 + 合成账号）。
"""

from __future__ import annotations

import ast
import asyncio
import threading
import time
from pathlib import Path

import pytest

from proactive import reminder_delivery as rd
from proactive import runtime_plane as rp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_API = PROJECT_ROOT / "api" / "run_api.py"


@pytest.fixture(autouse=True)
def _plane_sandbox(tmp_path, monkeypatch):
    rp.set_db_path(tmp_path / "runtime_plane.db")
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    yield
    rp.set_db_path(None)


# ── 提醒投递：真实行为 ────────────────────────────────────────


class _FakeSM:
    def __init__(self):
        self.results: list[tuple[int, bool]] = []

    def mark_reminder_result(self, rid, ok):  # noqa: ANN001
        self.results.append((int(rid), bool(ok)))


def _task(plane):
    return rd.ReminderDeliveryTask(
        _FakeSM(), llm=None, memory=None,
        wechat_sender=lambda *a: (_ for _ in ()).throw(AssertionError("plane 在时不应直发")),
        ws_sender=lambda *a: (_ for _ in ()).throw(AssertionError("plane 在时不应直发")),
        plane=plane,
    )


def test_reminder_wechat_delivery_enqueues_snapshot_and_acks():
    task = _task(rp)
    cid_box = {}

    def _host():
        deadline = time.time() + 6
        while time.time() < deadline:
            row = rp.claim_next_send(channel="wechat", owner_id=7, slot=1)
            if row:
                cid_box["row"] = row
                rp.complete_send(int(row["id"]), ok=True, reason="delivered=1", slot=1)
                return
            time.sleep(0.05)

    th = threading.Thread(target=_host)
    th.start()
    ok = asyncio.run(task._send_via_plane("7:wx_b@im.wechat", "该喝水了", "micai"))
    th.join()
    assert ok is True
    row = cid_box["row"]
    assert row["kind"] == "reminder"
    assert int(row["owner_id"]) == 7
    assert row["peer"] == "wx_b@im.wechat"
    assert row["character_id"] == "micai"
    assert row["session_key"] == "7:wx_b@im.wechat"
    assert row["message"] == "该喝水了"


def test_reminder_websocket_uses_plane_channel():
    task = _task(rp)

    def _host():
        deadline = time.time() + 6
        while time.time() < deadline:
            row = rp.claim_next_send(channel="websocket")
            if row:
                rp.complete_send(int(row["id"]), ok=True, reason="delivered=2")
                return
            time.sleep(0.05)

    th = threading.Thread(target=_host)
    th.start()
    ok = asyncio.run(task._send_via_plane("7:web:aa", "开会提醒", "lin"))
    th.join()
    assert ok is True
    sends = rp.recent_sends("reminder", limit=5)
    assert sends and sends[0]["channel"] == "websocket"


def test_reminder_unconfirmed_receipt_is_not_success():
    """无任何宿主受理 → 不得判送达（提醒 fail_count 继续累计，3 次判死语义保留）。"""
    task = _task(rp)
    ok = asyncio.run(
        task._send_via_plane(
            "7:wx_b@im.wechat", "早", "micai", receipt_timeout=0.3
        )
    )
    assert ok is False
    rows = rp.recent_sends("reminder", limit=5)
    assert rows and rows[0]["status"] == "pending"


def test_reminder_failed_receipt_reason_visible():
    task = _task(rp)

    def _host():
        deadline = time.time() + 6
        while time.time() < deadline:
            row = rp.claim_next_send(channel="wechat", owner_id=7, slot=0)
            if row:
                rp.complete_send(int(row["id"]), ok=False, reason="send_api_rejected")
                return
            time.sleep(0.05)

    th = threading.Thread(target=_host)
    th.start()
    ok = asyncio.run(task._send_via_plane("7:wx_b@im.wechat", "早", "micai"))
    th.join()
    assert ok is False
    assert rp.recent_sends("reminder", limit=5)[0]["fail_reason"] == "send_api_rejected"


def test_reminder_deliver_goes_through_plane_and_marks_result():
    """_deliver 层：plane 在位时走控制面，受理成功才 mark delivered。"""
    sm_task = rd.ReminderDeliveryTask(
        _FakeSM(), llm=None, memory=None, plane=rp,
        character_id_resolver=lambda _sk: "micai",
    )

    def _host():
        deadline = time.time() + 6
        while time.time() < deadline:
            row = rp.claim_next_send(channel="wechat", owner_id=7, slot=0)
            if row:
                rp.complete_send(int(row["id"]), ok=True, reason="delivered=1", slot=0)
                return
            time.sleep(0.05)

    th = threading.Thread(target=_host)
    th.start()
    asyncio.run(
        sm_task._deliver(
            {"id": 3, "session_key": "7:wx_b@im.wechat", "content": "喝水"}
        )
    )
    th.join()
    assert sm_task._sm.results == [(3, True)]
    row = rp.recent_sends("reminder", limit=5)[0]
    assert row["character_id"] == "micai" and row["status"] == "accepted"


def test_plane_default_is_class_level():
    """未注入平面的实例（含绕过 __init__ 构造的）必须落到直发分支，而非 AttributeError。"""
    assert rd.ReminderDeliveryTask._plane is None


def test_reminder_deliver_without_plane_uses_direct_senders():
    """平面不可用（Windows 开发/测试）→ 保留原直发注入，不得静默丢提醒。"""
    calls: list[tuple] = []
    sm = _FakeSM()
    task = rd.ReminderDeliveryTask(
        sm, llm=None, memory=None,
        wechat_sender=lambda owner, peer, text: calls.append((owner, peer, text)) or True,
        ws_sender=lambda key, text: calls.append(("ws", key, text)) or True,
        character_id_resolver=lambda _sk: "micai",
        plane=None,
    )
    asyncio.run(
        task._deliver({"id": 5, "session_key": "7:wx_b@im.wechat", "content": "喝水"})
    )
    assert calls[0][0] == 7 and calls[0][1] == "wx_b@im.wechat"
    assert sm.results == [(5, True)]


# ── run_api 装配形态：AST 钉住 ────────────────────────────────


def _tree():
    return ast.parse(RUN_API.read_text(encoding="utf-8"))


def _func_source(name: str) -> str:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(RUN_API.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"run_api 缺少函数 {name}")


def test_wechat_sender_targeted_branch_goes_through_plane():
    src = _func_source("_wechat_sender_factory")
    assert "enqueue_and_wait" in src, "微信定向投递未接入控制面（仍本地直发）"
    assert 'kind="proactive"' in src or "kind='proactive'" in src


def test_wechat_sender_no_longer_sends_to_every_slot_of_owner():
    """旧缺陷 C：targets × registry.all() 逐 slot 发送且不 break → 同 owner 双发。

    直发回退路径若保留，命中归属通道后必须立即 break（结构判据：嵌套 _send
    里存在 Break 节点）。
    """
    tree = _tree()
    factory = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_wechat_sender_factory"
    )
    inner = next(
        n for n in ast.walk(factory)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_send"
    )
    breaks = [n for n in ast.walk(inner) if isinstance(n, ast.Break)]
    assert breaks, "微信直发回退路径仍会向同 owner 的每个 slot 重发（缺 break）"


def test_websocket_sender_targeted_branch_goes_through_plane():
    src = _func_source("_websocket_sender_factory")
    assert "enqueue_and_wait" in src
    assert "broadcast_proactive" in src, "无 session_key 的系统级广播路径按裁决保留"


def test_ws_server_hosts_outbox_consumer():
    src = _func_source("_run_ws_server")
    assert "drain_once_async" in src or "_ws_outbox_drain" in src, (
        "WS 持有者未挂 websocket 通道消费者：入队后无人投递，主动消息/提醒将全量超时"
    )


def test_reminder_delivery_is_assembled_with_plane():
    src = _func_source("_install_reminder_delivery")
    assert "plane=" in src, "提醒投递未注入控制面（仍走本地 registry 直发）"
    assert "character_id" in src
