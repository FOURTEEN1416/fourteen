"""W3 块2/9/10：通道期望态跨进程一致 + 连接器控制面接线回归。

验收判据（任务书原句拆条）：
- 从任一 worker 断连同一 slot 均能停止入站与追问、另一 slot 不受影响
- 重启遵循用户停用选择（restore_on_boot 不看凭证看 desired）
- 重连（start_login）恢复期望态 connected
- 相同 message_id 重放只处理一次；处理异常释放认领允许合法重试
- 追问日预算跨重启不超额（缺陷 J）
- 未点名 slot 的投递由认领宿主回写 slot；点名 slot 不被他 slot 消费（缺陷 C）
"""

from __future__ import annotations

import time

import pytest

from proactive import runtime_plane as rp
from wechat_direct import channel_paths
from wechat_direct import wechat_connector as wc
from wechat_direct.connector_registry import ConnectorRegistry


@pytest.fixture
def chan(tmp_path, monkeypatch):
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    return tmp_path


def _mk_conn(tmp, uid: int = 7, slot: int = 0) -> wc.WeChatConnector:
    sd = tmp / "sess" / str(uid) / f"slot{slot}"
    sd.mkdir(parents=True, exist_ok=True)
    return wc.WeChatConnector(
        None,
        owner_user_id=uid,
        slot=slot,
        session_dir=sd,
        credentials_path=str(sd / "credentials.json"),
        state_path=str(sd / "state.json"),
        qrcode_path=str(sd / "qrcode.json"),
        context_tokens_path=str(sd / "context_tokens.json"),
    )


# ── 缺陷 B：期望态跨进程 + 重启遵循 ──────────────────────────


def test_disconnect_marks_desired_globally_even_without_local_object():
    """无本地对象的 worker 调 disconnect 也必须落全局停用命令。

    旧实现：conn is None → return False，宿主进程 poller 照跑，"断连"点了个寂寞。
    """
    reg = ConnectorRegistry()
    rp.heartbeat(7, 0, pid="other-worker")
    assert rp.presence_live(7, 0)
    assert reg.disconnect(7, 0) is True
    assert rp.get_desired(7, 0) == "disabled"
    assert not rp.presence_live(7, 0)
    # 另一 slot 不受影响
    assert rp.get_desired(7, 1) == "connected"


def test_disconnect_stops_local_connector():
    reg = ConnectorRegistry()

    class Dummy:
        stopped = False

        def stop(self):
            self.stopped = True

    d = Dummy()
    reg._connectors[(7, 0)] = d
    assert reg.disconnect(7, 0) is True
    assert d.stopped
    assert (7, 0) not in reg._connectors
    assert rp.get_desired(7, 0) == "disabled"


def test_restore_on_boot_skips_disabled_slot(chan, monkeypatch):
    started: list[tuple[int, int]] = []

    class Dummy:
        def __init__(self, uid, slot):
            self.owner_user_id, self.slot, self.token = uid, slot, ""

        def run(self):
            started.append((self.owner_user_id, self.slot))

    monkeypatch.setattr(
        wc, "WeChatConnector",
        lambda um=None, **kw: Dummy(kw["owner_user_id"], kw.get("slot", 0)),
    )
    monkeypatch.setattr(channel_paths, "list_user_slots_with_credentials", lambda uid: [0, 1])
    # restore_on_boot 以 sessions_root 下存在的数字用户目录为枚举起点
    (chan / "data" / "wechat_sessions" / "7").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ConnectorRegistry, "_try_acquire_poll_lock", staticmethod(lambda u, s: 0))
    rp.set_desired(7, 0, "disabled")

    reg = ConnectorRegistry()
    n = reg.restore_on_boot()
    assert n == 1  # 只有 slot1 恢复
    deadline = time.time() + 2.0
    while not started and time.time() < deadline:
        time.sleep(0.05)
    assert (7, 1) in started
    assert (7, 0) not in started


def test_start_login_restores_desired_connected(chan, monkeypatch):
    class Dummy:
        slot = 0
        owner_user_id = 7

        def run(self):
            pass

    monkeypatch.setattr(wc, "WeChatConnector", lambda um=None, **kw: Dummy())
    monkeypatch.setattr(ConnectorRegistry, "_try_acquire_poll_lock", staticmethod(lambda u, s: 0))
    rp.set_desired(7, 0, "disabled")

    reg = ConnectorRegistry()
    reg.start_login(7, slot=0)
    assert rp.get_desired(7, 0) == "connected"


def test_poll_loop_self_stops_on_disabled(chan):
    """宿主在另一进程：desired=disabled 后其轮询循环下一圈自停并释放租约。"""
    conn = _mk_conn(chan)
    rp.heartbeat(7, 0, pid="stale-host")
    rp.set_desired(7, 0, "disabled")
    conn._poll_loop()  # 不应发出任何 _get_updates（无 token，真调会走错误分支退避）
    assert conn._stop is True
    assert not rp.presence_live(7, 0)


def test_poll_loop_self_stop_surrenders_local_registry_and_lock(chan):
    """自停必须回收本进程实例与通道 flock —— 否则别的 worker 重连恒 429。"""
    from wechat_direct.connector_registry import get_registry

    conn = _mk_conn(chan)
    reg = get_registry()
    reg._connectors[(7, 0)] = conn
    reg._poll_lock_fds[(7, 0)] = -1  # 模拟持锁 fd（Windows 下 close 失败被 suppress）
    rp.set_desired(7, 0, "disabled")
    conn._poll_loop()
    assert conn._stop is True
    assert (7, 0) not in reg._connectors
    assert (7, 0) not in reg._poll_lock_fds


def test_poll_loop_writes_presence_heartbeat(chan, monkeypatch):
    conn = _mk_conn(chan)

    def fake(*_a, **_k):
        conn._stop = True
        return {"ret": 0, "msgs": []}

    monkeypatch.setattr(wc, "_get_updates", fake)
    conn._poll_loop()
    assert rp.presence_live(7, 0)


# ── 缺陷 A/C：连接器作为宿主消费 outbox ──────────────────────


def test_drain_outbox_claims_unnamed_and_respects_named_slot(chan, monkeypatch):
    conn = _mk_conn(chan, slot=0)
    conn.token = "tok"
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn, "send_text",
        lambda text, to_user="": (sent.append((text, to_user)) or True),
    )
    c1 = rp.enqueue_send(
        kind="proactive", channel="wechat", session_key="7:wx_b",
        owner_id=7, peer="wx_b", message="在忙吗",
    )
    c2 = rp.enqueue_send(
        kind="reminder", channel="wechat", owner_id=7, slot=1,
        peer="wx_c", message="点名 slot1",
    )
    conn._drain_outbox()
    assert ("在忙吗", "wx_b") in sent
    st1 = rp.send_status(c1)
    assert st1["status"] == "accepted"
    assert st1["slot"] == 0  # 认领宿主回写 slot
    assert rp.send_status(c2)["status"] == "pending"  # 点名 slot1 不被 slot0 消费


def test_drain_outbox_peer_falls_back_to_session_key(chan, monkeypatch):
    conn = _mk_conn(chan, slot=0)
    conn.token = "tok"
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn, "send_text",
        lambda text, to_user="": (sent.append((text, to_user)) or True),
    )
    rp.enqueue_send(
        kind="proactive", channel="wechat", session_key="7:wx_d",
        owner_id=7, message="裸 peer 缺省",
    )
    conn._drain_outbox()
    assert ("裸 peer 缺省", "wx_d") in sent


def test_drain_outbox_offline_channel_requeues_for_other_host(chan, monkeypatch):
    """本 slot 无 token **不等于**这条该死：退回 pending，让同 owner 的在线 slot 投。

    旧实现把"宿主离线"当投递失败直接判死 —— 多 worker 下离线的那个宿主先认领，
    在线的那个宿主就永远收不到（提醒 3 次判死的同类误杀）。
    """
    conn = _mk_conn(chan, slot=0)
    conn.token = ""
    cid = rp.enqueue_send(
        kind="proactive", channel="wechat", owner_id=7, peer="wx_b", message="x",
    )
    conn._drain_outbox()
    assert rp.send_status(cid)["status"] == "pending"

    other = _mk_conn(chan, slot=1)
    other.token = "tok"
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        other, "send_text",
        lambda text, to_user="": (sent.append((text, to_user)) or True),
    )
    other._drain_outbox()
    assert ("x", "wx_b") in sent
    st = rp.send_status(cid)
    assert st["status"] == "accepted" and st["slot"] == 1


def test_drain_outbox_no_target_is_failed(chan):
    """既无 peer 又解不出会话键 → 明确失败回执（不无限重投、不假成功）。"""
    conn = _mk_conn(chan)
    conn.token = "tok"
    cid = rp.enqueue_send(kind="proactive", channel="wechat", owner_id=7, message="x")
    conn._drain_outbox()
    st = rp.send_status(cid)
    assert st["status"] == "failed" and st["fail_reason"] == "no_target"


# ── 缺陷 I：入站 message_id 幂等 ─────────────────────────────


def _raw(mid: str) -> dict:
    return {"message_type": 1, "from_user_id": "wx_b", "message_id": mid, "item_list": []}


def test_inbound_replay_processed_once(chan, monkeypatch):
    conn = _mk_conn(chan)
    calls: list[str] = []
    monkeypatch.setattr(
        conn, "_handle_message_serial",
        lambda raw, mid, fu, ct, rev: calls.append(mid),
    )
    conn._handle_message(_raw("m-1"))
    conn._handle_message(_raw("m-1"))  # get_updates 重发 / 新宿主接管重放
    assert calls == ["m-1"]


def test_inbound_failure_releases_claim_for_retry(chan, monkeypatch):
    conn = _mk_conn(chan)
    flag = {"fail": True}

    def boom(*_a):
        if flag["fail"]:
            raise RuntimeError("下游炸了")

    monkeypatch.setattr(conn, "_handle_message_serial", boom)
    with pytest.raises(RuntimeError):
        conn._handle_message(_raw("m-2"))
    flag["fail"] = False
    done: list[str] = []
    monkeypatch.setattr(
        conn, "_handle_message_serial",
        lambda raw, mid, fu, ct, rev: done.append(mid),
    )
    conn._handle_message(_raw("m-2"))  # 处理失败不该吞掉这条消息
    assert done == ["m-2"]


def test_inbound_memory_fallback_without_plane(chan, monkeypatch):
    """控制面不可用（_plane()→None）：降级旧内存去重，行为不劣化。"""
    conn = _mk_conn(chan)
    calls: list[str] = []
    monkeypatch.setattr(
        conn, "_handle_message_serial",
        lambda raw, mid, fu, ct, rev: calls.append(mid),
    )
    monkeypatch.setattr(wc, "_plane", lambda: None)
    conn._handle_message(_raw("m-3"))
    conn._handle_message(_raw("m-3"))
    assert calls == ["m-3"]


# ── 缺陷 J：追问日预算跨重启 ─────────────────────────────────


def test_followup_budget_persists_across_restart(chan, monkeypatch):
    monkeypatch.setattr(
        wc, "read_follow_up_config",
        lambda: {"enabled": True, "daily_max": 2,
                 "delay1_seconds": 60, "delay2_seconds": 60},
    )
    conn = _mk_conn(chan)
    assert conn._followup_budget_ok("7:wx_b")
    conn._followup_spend("7:wx_b")
    conn._followup_spend("7:wx_b")
    assert not conn._followup_budget_ok("7:wx_b")
    # 模拟重启 / 换宿主：新实例读同一持久账
    conn2 = _mk_conn(chan)
    assert not conn2._followup_budget_ok("7:wx_b")
    # 另一会话不受影响
    assert conn2._followup_budget_ok("7:wx_other")
