"""W3 块1：控制面底座（runtime_plane / json_state）行为契约。

对应缺陷 A/B/C/E/G 的公共基础设施；四 worker 拓扑的投递平面判据。
"""

from __future__ import annotations

import threading
import time

import pytest

from proactive import runtime_plane as rp


@pytest.fixture(autouse=True)
def _plane_sandbox(tmp_path, monkeypatch):
    rp.set_db_path(tmp_path / "runtime_plane.db")
    yield
    rp.set_db_path(None)


class TestDesiredState:
    def test_default_connected_until_explicit_disabled(self):
        assert rp.get_desired(7, 0) == "connected"
        rp.set_desired(7, 0, "disabled")
        assert rp.get_desired(7, 0) == "disabled"

    def test_per_slot_independent(self):
        rp.set_desired(7, 0, "disabled")
        assert rp.get_desired(7, 1) == "connected"

    def test_disabled_can_be_reenabled(self):
        rp.set_desired(7, 0, "disabled")
        rp.set_desired(7, 0, "connected")
        assert rp.get_desired(7, 0) == "connected"


class TestPresence:
    def test_heartbeat_then_live(self):
        assert not rp.presence_live(3, 0)
        rp.heartbeat(3, 0, pid="hostA")
        assert rp.presence_live(3, 0)
        assert rp.live_slots(3) == [0]

    def test_stale_heartbeat_not_live(self):
        rp.heartbeat(3, 1, pid="hostB")
        # 直接把心跳拨旧（等价另一进程宿主失联）
        with rp._conn() as conn:
            conn.execute(
                "UPDATE channel_presence SET heartbeat=? WHERE owner_id=3 AND slot=1",
                (time.time() - rp.PRESENCE_TTL - 5,),
            )
        assert not rp.presence_live(3, 1)

    def test_release(self):
        rp.heartbeat(3, 0)
        rp.presence_release(3, 0)
        assert not rp.presence_live(3, 0)


class TestOutbound:
    def test_one_row_claimed_exactly_once_under_race(self):
        """同 owner 两 slot 宿主并发认领：只有一方拿到（缺陷 C 根治判据）。"""
        cid = rp.enqueue_send(
            kind="proactive", channel="wechat", session_key="7:peer@im.wechat",
            owner_id=7, peer="peer@im.wechat", message="在吗",
        )
        got: list[tuple[int, dict]] = []
        barrier = threading.Barrier(2)

        def _host(slot: int):
            barrier.wait()
            row = rp.claim_next_send(channel="wechat", owner_id=7, slot=slot)
            if row:
                got.append((slot, row))

        ts = [threading.Thread(target=_host, args=(s,)) for s in (0, 1)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(got) == 1
        winner_slot, row = got[0]
        assert row["id"] == cid
        # 认领**不**绑 slot（未点名命令退回后他宿主仍可投）；归属由受理回执落定
        assert row["slot"] is None
        assert row["owner_id"] == 7 and row["peer"] == "peer@im.wechat"
        rp.complete_send(cid, ok=True, reason="delivered", slot=winner_slot)
        snap = rp.send_status(cid)
        assert snap["status"] == "accepted" and snap["slot"] == winner_slot

    def test_named_slot_not_stolen_by_other_slot(self):
        rp.enqueue_send(
            kind="reminder", channel="wechat", owner_id=7, slot=1,
            session_key="7:peer", message="点名 slot1",
        )
        assert rp.claim_next_send(channel="wechat", owner_id=7, slot=0) is None
        row = rp.claim_next_send(channel="wechat", owner_id=7, slot=1)
        assert row is not None and row["slot"] == 1

    def test_other_owner_cannot_claim(self):
        rp.enqueue_send(kind="proactive", channel="wechat", owner_id=9, message="x")
        assert rp.claim_next_send(channel="wechat", owner_id=8, slot=0) is None

    def test_receipt_accepted(self):
        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="1:web:abc", message="hi")
        row = rp.claim_next_send(channel="websocket")
        assert row and row["id"] == cid
        rp.complete_send(cid, ok=True)
        final = rp.wait_for_receipt(cid, timeout=2)
        assert final["status"] == "accepted"

    def test_wait_timeout_is_not_success(self):
        """等待方超时读数仍是 pending —— 调用方必须按未确认处理。"""
        cid = rp.enqueue_send(kind="proactive", channel="websocket", message="hi")
        final = rp.wait_for_receipt(cid, timeout=0.3, poll=0.1)
        assert final["status"] == "pending"

    def test_claim_lease_expires_and_requeued(self):
        cid = rp.enqueue_send(kind="proactive", channel="websocket", message="hi")
        assert rp.claim_next_send(channel="websocket") is not None
        with rp._conn() as conn:
            conn.execute(
                "UPDATE outbound_commands SET claim_expires=? WHERE id=?",
                (time.time() - 1, cid),
            )
        # 认领方崩溃 → 下一宿主可重新认领
        assert rp.claim_next_send(channel="websocket") is not None

    def test_unclaimed_pending_expires_to_failed(self):
        cid = rp.enqueue_send(kind="proactive", channel="websocket", message="hi")
        with rp._conn() as conn:
            conn.execute(
                "UPDATE outbound_commands SET created_at=? WHERE id=?",
                (time.time() - rp.PENDING_EXPIRE - 1, cid),
            )
        rp.claim_next_send(channel="wechat")  # 触发 reaper
        assert rp.send_status(cid)["status"] == "failed"
        assert rp.send_status(cid)["fail_reason"] == "expired_unclaimed"

    def test_recent_sends_cross_process_visible(self):
        cid = rp.enqueue_send(kind="manual", channel="wechat", owner_id=1, message="m")
        rp.complete_send(cid, ok=False, reason="no_host")
        hist = rp.recent_sends("manual")
        assert hist and hist[0]["status"] == "failed"


class TestControlCommands:
    def test_enqueue_claim_complete_roundtrip(self):
        cid = rp.enqueue_control("proactive_manual", session_key="7:peer", payload="{}")
        row = rp.claim_next_control("proactive_manual")
        assert row and row["id"] == cid and row["session_key"] == "7:peer"
        rp.complete_control(cid, ok=True, result='{"message":"早"}')
        final = rp.wait_for_control(cid, timeout=1)
        assert final["status"] == "accepted" and "早" in final["result"]

    def test_wrong_kind_not_claimed(self):
        rp.enqueue_control("other", session_key="k")
        assert rp.claim_next_control("proactive_manual") is None


class TestInboundIdempotency:
    def test_first_claim_true_second_false(self):
        assert rp.inbound_claim("u1:1001", "7:peer") is True
        assert rp.inbound_claim("u1:1001", "7:peer") is False

    def test_done_then_replay_blocked(self):
        rp.inbound_claim("m1")
        rp.inbound_done("m1")
        assert rp.inbound_claim("m1") is False

    def test_release_allows_retry_after_failure(self):
        """处理前认领、失败撤销 —— "处理前不标记成功"语义。"""
        assert rp.inbound_claim("m2") is True
        rp.inbound_release("m2")
        assert rp.inbound_claim("m2") is True

    def test_stale_claim_reclaimable_after_ttl(self):
        rp.inbound_claim("m3")
        with rp._conn() as conn:
            conn.execute("UPDATE inbound_seen SET ts=? WHERE message_id='m3'",
                         (time.time() - rp.CLAIM_TTL - 1,))
        assert rp.inbound_claim("m3") is True

    def test_empty_id_never_blocks(self):
        assert rp.inbound_claim("") is True
        assert rp.inbound_claim("") is True

    def test_concurrent_only_one_winner(self):
        results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def _one():
            barrier.wait()
            ok = rp.inbound_claim("race1")
            with lock:
                results.append(ok)

        ts = [threading.Thread(target=_one) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert sum(results) == 1


class TestFollowupBudget:
    def test_spend_persists_across_reads(self):
        assert rp.followup_used("7:peer", "2026-09-27") == 0
        assert rp.followup_spend("7:peer", "2026-09-27") == 1
        assert rp.followup_spend("7:peer", "2026-09-27") == 2
        assert rp.followup_used("7:peer", "2026-09-27") == 2
        # 另一日/另一会话不串
        assert rp.followup_used("7:peer", "2026-09-28") == 0
        assert rp.followup_used("8:peer", "2026-09-27") == 0


class TestEffectDedup:
    def test_producer_runs_once(self):
        calls = []

        def _producer():
            calls.append(1)
            return 42

        first, v1 = rp.effect_once("set_reminder:7:peer:m1:c", _producer)
        second, v2 = rp.effect_once("set_reminder:7:peer:m1:c", _producer)
        assert (first, v1) == (True, 42)
        assert second is False and v2 == "42"
        assert len(calls) == 1

    def test_producer_failure_releases_key(self):
        def _boom():
            raise RuntimeError("llm down")

        with pytest.raises(RuntimeError):
            rp.effect_once("k:fail", _boom)
        # 失败不留占位 → 重试可执行
        ok, val = rp.effect_once("k:fail", lambda: "reminded")
        assert ok is True and val == "reminded"


class TestJsonStateAtomicWrite:
    """缺陷 D 尾巴：atomic_write_json 必须持锁 + 唯一 tmp（并发双写互不踩）。"""

    def test_concurrent_writers_no_stray_tmp_and_valid_json(self, tmp_path):
        import json as _json

        from utils.json_state import atomic_write_json

        target = tmp_path / "state.json"
        errors: list[Exception] = []

        def _writer(n: int):
            try:
                for i in range(30):
                    atomic_write_json(target, {"writer": n, "seq": i})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=_writer, args=(k,)) for k in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors
        # 读者永远看到完整 JSON；不留 .tmp 残骸
        data = _json.loads(target.read_text(encoding="utf-8"))
        assert "writer" in data and "seq" in data
        assert not list(tmp_path.glob("*.tmp"))

    def test_update_json_roundtrip(self, tmp_path):
        from utils.json_state import update_json

        target = tmp_path / "cfg.json"
        update_json(target, lambda d: d.update({"a": 1}))
        update_json(target, lambda d: d.update({"b": 2}))
        out = update_json(target, lambda d: None, default={})
        assert out == {"a": 1, "b": 2}

    def test_transient_replace_denial_retries_then_lands(self, tmp_path, monkeypatch):
        """Windows 并发时 os.replace 可瞬时 PermissionError(13) → 重试而非丢状态。

        实测判据（2026-09-27 本机 4 线程并发写同文件，约 1/3 运行命中）：
        旧实现一次失败即上抛，调用方宽 except 吞掉 → 配置整段不落盘。
        """
        import json as _json
        import os as _os

        from utils.json_state import atomic_write_json

        target = tmp_path / "state.json"
        real = _os.replace
        state = {"left": 2}

        def _flaky(src, dst, *a, **k):
            if state["left"] > 0:
                state["left"] -= 1
                raise PermissionError(13, "拒绝访问。")
            return real(src, dst, *a, **k)

        monkeypatch.setattr(_os, "replace", _flaky)
        atomic_write_json(target, {"level": "low", "count": 1})
        assert _json.loads(target.read_text(encoding="utf-8")) == {"level": "low", "count": 1}

    def test_persistent_replace_failure_still_raises(self, tmp_path, monkeypatch):
        """重试上限后仍失败必须上抛——静默成功等于把丢状态藏起来。"""
        import os as _os

        from utils.json_state import atomic_write_json

        target = tmp_path / "state.json"

        def _denied(src, dst, *a, **k):
            raise PermissionError(13, "拒绝访问。")

        monkeypatch.setattr(_os, "replace", _denied)
        with pytest.raises(PermissionError):
            atomic_write_json(target, {"a": 1})


class TestOutboxProducerConsumer:
    """块3：生产者「入队—等回执」与消费者「认领—投递—回执/退回」契约。

    对应任务书：所有出站保留 owner/slot/peer/character/turn 快照与受理回执；
    取消/失败分类可见；**不承诺 exactly-once**；非归属连接 0 送达不得判死。
    """

    def test_requeue_returns_claimed_row_to_pending(self):
        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="7:web:a", message="早")
        row = rp.claim_next_send(channel="websocket")
        assert row and int(row["id"]) == cid
        assert rp.requeue_send(cid, reason="no_local_connection") is True
        again = rp.send_status(cid)
        assert again["status"] == "pending"
        assert "no_local_connection" in (again["fail_reason"] or "")
        # 另一个消费者（别的 worker 有该会话连接）可再认领
        assert rp.claim_next_send(channel="websocket") is not None

    def test_requeue_caps_attempts_then_fails(self):
        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="7:web:a", message="早")
        for _ in range(rp.REQUEUE_MAX_ATTEMPTS + 2):
            if rp.claim_next_send(channel="websocket") is None:
                break
            if not rp.requeue_send(cid, reason="no_local_connection"):
                break
        assert rp.send_status(cid)["status"] == "failed"
        assert rp.claim_next_send(channel="websocket") is None

    def test_requeue_on_already_acked_row_is_noop(self):
        cid = rp.enqueue_send(kind="reminder", channel="wechat",
                              session_key="7:o@im.wechat", owner_id=7, message="起床")
        rp.claim_next_send(channel="wechat", owner_id=7, slot=0)
        rp.complete_send(cid, ok=True, reason="delivered")
        assert rp.requeue_send(cid, reason="late_requeue") is False
        assert rp.send_status(cid)["status"] == "accepted"

    def test_enqueue_and_wait_true_on_accepted(self):
        def _consumer():
            # 轮询认领：生产者入队与消费者线程存在先后竞争，不能假设先入队
            deadline = time.time() + 6
            while time.time() < deadline:
                row = rp.claim_next_send(channel="wechat", owner_id=7, slot=0)
                if row:
                    rp.complete_send(
                        int(row["id"]), ok=True, reason="delivered=1", slot=0,
                    )
                    return
                time.sleep(0.05)

        th = threading.Thread(target=_consumer)
        th.start()
        ok, reason, cid = rp.enqueue_and_wait(
            kind="proactive", channel="wechat", session_key="7:o@im.wechat",
            owner_id=7, peer="o", character_id="micai", message="早", timeout=10.0)
        th.join()
        assert ok is True
        assert "delivered" in reason
        snap = rp.send_status(cid)
        assert snap["character_id"] == "micai" and snap["slot"] == 0

    def test_enqueue_and_wait_timeout_is_not_success(self):
        t0 = time.time()
        ok, reason, cid = rp.enqueue_and_wait(
            kind="proactive", channel="websocket", session_key="7:web:a",
            message="早", timeout=0.4)
        assert ok is False
        assert "unconfirmed" in reason
        assert time.time() - t0 < 3.0
        # 超时只表示「未确认」，行不得被判成功，仍可被后续消费者投递
        assert rp.send_status(cid)["status"] == "pending"

    def test_enqueue_and_wait_false_on_failed_receipt(self):
        def _consumer():
            deadline = time.time() + 6
            while time.time() < deadline:
                row = rp.claim_next_send(channel="wechat", owner_id=7, slot=1)
                if row:
                    rp.complete_send(
                        int(row["id"]), ok=False, reason="channel_offline",
                    )
                    return
                time.sleep(0.05)

        th = threading.Thread(target=_consumer)
        th.start()
        ok, reason, _cid = rp.enqueue_and_wait(
            kind="reminder", channel="wechat", session_key="7:o@im.wechat",
            owner_id=7, peer="o", message="喝水", timeout=10.0)
        th.join()
        assert ok is False
        assert "channel_offline" in reason

    def test_drain_once_accepts_when_delivered(self):
        import asyncio

        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="7:web:a", message="早")
        seen = []

        async def _send(session_key, message):
            seen.append((session_key, message))
            return 2

        async def _run():
            return await rp.drain_once_async(channel="websocket", send_async=_send)

        assert asyncio.run(_run()) == 1
        assert seen == [("7:web:a", "早")]
        assert rp.send_status(cid)["status"] == "accepted"

    def test_drain_once_requeues_when_no_local_connection(self):
        import asyncio

        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="7:web:a", message="早")

        async def _send(_sk, _msg):
            return 0

        assert asyncio.run(rp.drain_once_async(channel="websocket", send_async=_send)) == 0
        assert rp.send_status(cid)["status"] == "pending"
        # 有归属连接的消费者随后仍可受理
        async def _ok(_sk, _msg):
            return 1
        asyncio.run(rp.drain_once_async(channel="websocket", send_async=_ok))
        assert rp.send_status(cid)["status"] == "accepted"

    def test_drain_once_fails_invalid_payload(self):
        import asyncio

        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="", message="")

        async def _send(_sk, _msg):
            raise AssertionError("无效载荷不得被投递")

        assert asyncio.run(rp.drain_once_async(channel="websocket", send_async=_send)) == 0
        row = rp.send_status(cid)
        assert row["status"] == "failed" and "invalid_payload" in row["fail_reason"]

    def test_drain_once_send_exception_is_failed_not_hang(self):
        import asyncio

        cid = rp.enqueue_send(kind="proactive", channel="websocket",
                              session_key="7:web:a", message="早")

        async def _send(_sk, _msg):
            raise RuntimeError("连接已断开")

        assert asyncio.run(rp.drain_once_async(channel="websocket", send_async=_send)) == 0
        row = rp.send_status(cid)
        assert row["status"] == "failed" and "send_error" in row["fail_reason"]

    def test_drain_once_only_touches_its_channel(self):
        import asyncio

        wc_id = rp.enqueue_send(kind="proactive", channel="wechat",
                                session_key="7:o@im.wechat", owner_id=7, message="微信")

        async def _send(_sk, _msg):
            return 1

        asyncio.run(rp.drain_once_async(channel="websocket", send_async=_send))
        assert rp.send_status(wc_id)["status"] == "pending"
